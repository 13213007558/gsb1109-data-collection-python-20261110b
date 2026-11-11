# -*- coding: utf-8 -*-

"""
Offline detection of CSV files that concatenate multiple dialects.

Suppliers sometimes stitch together exports from different systems: the first
part of the file may use one dialect (for instance comma-delimited) and a
later part another (for instance semicolon-delimited). The functions in this
module locate the record boundaries between such contiguous parts and detect
the dialect of each part separately.

Boundaries are always located between complete records. Newlines inside
quoted fields are tracked, so a record that spans multiple physical lines is
never split in the middle. Rows that happen to miss a column are tolerated
and do not, on their own, cause a dialect change to be reported. When no
reliable boundary can be established, the functions return ``None`` so that
the file can be referred for manual review instead of guessing.

Author: Gertjan van den Burg

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Set
from typing import Tuple

from .cparser_util import parse_string
from .detect import Detector
from .dialect import SimpleDialect
from .exceptions import Error

#: Delimiters considered when locating dialect changes, unless overridden.
DEFAULT_DELIMITERS: Tuple[str, ...] = (",", ";")

#: Minimum number of records that clearly support each dialect.
DEFAULT_MIN_SUPPORT: int = 3

#: Maximum fraction of records on either side of a boundary that may fail to
#: fit the dialect of that side (for instance a row that misses a column).
DEFAULT_TOLERANCE: float = 0.1

#: How many records around a candidate boundary are inspected for sharp
#: dialect contrast.
CONTRAST_WINDOW: int = 10


@dataclass
class CSVSegment:
    """A contiguous segment of a multi-dialect CSV file

    Parameters
    ----------
    start_row : int
        Zero-based index of the first record of the segment in the original
        file.

    end_row : int
        Zero-based index one past the last record, so that the segment covers
        the records ``range(start_row, end_row)``.

    start_offset : int
        Character offset of the start of the first record in the original
        data.

    end_offset : int
        Character offset one past the end of the last record.

    dialect : SimpleDialect
        Dialect detected for the segment.

    data : str
        The raw text of the segment. It can be parsed directly with the
        detected dialect.

    """

    start_row: int
    end_row: int
    start_offset: int
    end_offset: int
    dialect: SimpleDialect
    data: str

    def rows(self) -> List[List[str]]:
        """Parse the segment with its detected dialect"""
        return list(parse_string(self.data, self.dialect))


def iter_record_offsets(data: str) -> List[Tuple[int, int]]:
    """Return the ``(start, end)`` character offsets of each CSV record

    Newlines inside quoted fields do not terminate a record. Both single and
    double quote characters are honored, as are doubled quotes and backslash
    escapes. A trailing line break does not produce an empty final record.

    """
    records: List[Tuple[int, int]] = []
    start = 0
    in_quotes = False
    quotechar: Optional[str] = None
    escape_next = False

    i = 0
    n = len(data)
    while i < n:
        char = data[i]

        if escape_next:
            escape_next = False
            i += 1
            continue
        if char == "\\":
            escape_next = True
            i += 1
            continue

        if not in_quotes:
            if char in ("'", '"'):
                in_quotes = True
                quotechar = char
            elif char in ("\n", "\r"):
                records.append((start, i))
                if char == "\r" and i + 1 < n and data[i + 1] == "\n":
                    i += 1
                start = i + 1
        else:
            assert quotechar is not None
            if char == quotechar:
                if i + 1 < n and data[i + 1] == quotechar:
                    # Doubled quote: stays inside the quoted field.
                    i += 1
                else:
                    in_quotes = False
                    quotechar = None
        i += 1

    if start < n:
        records.append((start, n))
    return records


def _record_width(text: str, dialect: SimpleDialect) -> Optional[int]:
    """Number of fields of a record under a dialect, or None if unparseable

    Blank records also yield None so that they do not count for or against a
    dialect.

    """
    if not text.strip():
        return None
    try:
        rows = list(parse_string(text, dialect))
    except Error:
        return None
    if not rows:
        return None
    return len(rows[0])


def _candidate_dialects(
    data: str, delimiters: Optional[Sequence[str]]
) -> List[SimpleDialect]:
    if delimiters is None:
        delimiters = DEFAULT_DELIMITERS
    seen = set()
    result: List[SimpleDialect] = []
    for delimiter in delimiters:
        if (
            len(delimiter) != 1
            or delimiter in seen
            or delimiter not in data
        ):
            continue
        seen.add(delimiter)
        result.append(SimpleDialect(delimiter, '"', ""))
    return result


def _width_table(
    data: str,
    records: Sequence[Tuple[int, int]],
    dialects: Sequence[SimpleDialect],
) -> Dict[SimpleDialect, List[Optional[int]]]:
    table: Dict[SimpleDialect, List[Optional[int]]] = {
        dialect: [] for dialect in dialects
    }
    for start, end in records:
        text = data[start:end]
        for dialect in dialects:
            table[dialect].append(_record_width(text, dialect))
    return table


def _supports(width: Optional[int]) -> bool:
    """A record supports a dialect if it is structured by its delimiter"""
    return width is not None and width >= 2


def _has_contrast(
    index: int,
    direction: int,
    own_dialect: SimpleDialect,
    other_dialect: SimpleDialect,
    widths: Dict[SimpleDialect, List[Optional[int]]],
) -> bool:
    """Check for a record structured by one dialect but not the other one"""
    n_records = len(next(iter(widths.values())))
    checked = 0
    i = index
    while 0 <= i < n_records and checked < CONTRAST_WINDOW:
        if _supports(widths[own_dialect][i]) and not _supports(
            widths[other_dialect][i]
        ):
            return True
        checked += 1
        i += direction
    return False


def _find_boundary(
    first: SimpleDialect,
    second: SimpleDialect,
    widths: Dict[SimpleDialect, List[Optional[int]]],
    min_support: int,
    tolerance: float,
) -> Optional[int]:
    """Locate a first->second dialect boundary or return None

    The boundary is the record index at which the second dialect starts. The
    whole record sequence is scored by counting records that fail to fit the
    dialect of their side of the boundary; records that parse under both
    dialects contribute a secondary preference penalty when they are
    structured into more fields by the other dialect. The best-scoring
    position is only accepted if both sides have enough supporting records,
    fit their dialect within the tolerance, and show sharp dialect contrast
    around the boundary.

    """
    first_widths = widths[first]
    second_widths = widths[second]
    n_records = len(first_widths)

    first_misfit = [0 if width is not None else 1 for width in first_widths]
    second_misfit = [0 if width is not None else 1 for width in second_widths]
    first_support = [1 if _supports(width) else 0 for width in first_widths]
    second_support = [1 if _supports(width) else 0 for width in second_widths]

    # A record that parses under both dialects is assigned a preference for
    # the dialect that splits it into more fields. Putting a record on the
    # "wrong" side of the boundary incurs a preference penalty, which is used
    # as a secondary score to break ties between otherwise equal positions.
    left_penalty = [0] * n_records
    right_penalty = [0] * n_records
    for i in range(n_records):
        first_width = first_widths[i]
        second_width = second_widths[i]
        if first_width is None or second_width is None:
            continue
        if second_width > first_width:
            left_penalty[i] = 1
        elif first_width > second_width:
            right_penalty[i] = 1

    suffix_second_misfit = [0] * (n_records + 1)
    suffix_second_support = [0] * (n_records + 1)
    suffix_right_penalty = [0] * (n_records + 1)
    for i in range(n_records - 1, -1, -1):
        suffix_second_misfit[i] = (
            suffix_second_misfit[i + 1] + second_misfit[i]
        )
        suffix_second_support[i] = (
            suffix_second_support[i + 1] + second_support[i]
        )
        suffix_right_penalty[i] = (
            suffix_right_penalty[i + 1] + right_penalty[i]
        )

    prefix_misfit = 0
    prefix_support = 0
    prefix_left_penalty = 0
    best: Optional[Tuple[Tuple[int, int], int]] = None
    for boundary in range(1, n_records):
        prefix_misfit += first_misfit[boundary - 1]
        prefix_support += first_support[boundary - 1]
        prefix_left_penalty += left_penalty[boundary - 1]

        if prefix_support < min_support:
            continue
        if suffix_second_support[boundary] < min_support:
            continue

        left_len = boundary
        right_len = n_records - boundary
        if prefix_misfit > tolerance * left_len:
            continue
        if suffix_second_misfit[boundary] > tolerance * right_len:
            continue

        misfits = prefix_misfit + suffix_second_misfit[boundary]
        preference_penalty = (
            prefix_left_penalty + suffix_right_penalty[boundary]
        )

        score = (misfits, preference_penalty)
        if best is None or score < best[0]:
            best = (score, boundary)

    if best is None:
        return None

    boundary = best[1]
    if not _has_contrast(boundary - 1, -1, first, second, widths):
        return None
    if not _has_contrast(boundary, 1, second, first, widths):
        return None
    return boundary


def detect_multipart(
    data: str,
    delimiters: Optional[Sequence[str]] = None,
    min_support: int = DEFAULT_MIN_SUPPORT,
    tolerance: float = DEFAULT_TOLERANCE,
    verbose: bool = False,
) -> Optional[List[CSVSegment]]:
    """Detect contiguous dialect segments in a CSV string

    The function is intentionally conservative: it only reports a result when
    a single, well-supported dialect boundary is found. Files that use one
    dialect throughout, or files for which the boundary cannot be determined
    reliably, yield ``None`` and should be handled with the regular
    single-dialect detection entry points or reviewed manually.

    Parameters
    ----------
    data : str
        The contents of the CSV file.

    delimiters : Optional[Sequence[str]]
        Delimiters to consider for the two dialects. Defaults to comma and
        semicolon. Delimiters that do not occur in the data are ignored.

    min_support : int
        Minimum number of records clearly fitting each dialect. Segments with
        less support cause the result to be inconclusive.

    tolerance : float
        Maximum fraction of records on either side of the boundary that may
        fail to fit the dialect of that side, for instance rows missing a
        column.

    verbose : bool
        Print diagnostic information while running.

    Returns
    -------
    segments : Optional[List[CSVSegment]]
        Ordered list of segments with their detected dialect, or ``None`` when
        no reliable boundary can be established.

    """
    if min_support < 1:
        raise ValueError("min_support should be a positive integer")
    if not 0.0 <= tolerance < 1.0:
        raise ValueError("tolerance should be between 0 and 1")

    candidate_dialects = _candidate_dialects(data, delimiters)
    if len(candidate_dialects) < 2:
        if verbose:
            print(
                "Need at least two occurring delimiters to detect a dialect "
                "boundary."
            )
        return None

    records = iter_record_offsets(data)
    if len(records) < 2 * min_support:
        if verbose:
            print("Not enough records to establish two dialect segments.")
        return None

    widths = _width_table(data, records, candidate_dialects)

    accepted: Dict[int, Set[Tuple[str, str]]] = {}
    for order, first in enumerate(candidate_dialects):
        for second in candidate_dialects[order + 1 :]:
            for forward, backward in (
                (first, second),
                (second, first),
            ):
                boundary = _find_boundary(
                    forward,
                    backward,
                    widths,
                    min_support=min_support,
                    tolerance=tolerance,
                )
                if boundary is None:
                    continue
                if verbose:
                    print(
                        "Boundary at record %i: %r -> %r"
                        % (boundary, forward.delimiter, backward.delimiter)
                    )
                first_delimiter = forward.delimiter
                second_delimiter = backward.delimiter
                assert first_delimiter is not None
                assert second_delimiter is not None
                accepted.setdefault(boundary, set()).add(
                    (first_delimiter, second_delimiter)
                )

    if len(accepted) != 1:
        if verbose:
            if accepted:
                print(
                    "Conflicting candidate boundaries: %r"
                    % sorted(accepted)
                )
            else:
                print("No dialect boundary could be established.")
        return None

    boundary = next(iter(accepted))
    boundaries = [0, boundary, len(records)]

    segments: List[CSVSegment] = []
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        start_offset = records[left][0]
        end_offset = records[right - 1][1]
        text = data[start_offset:end_offset]
        dialect = Detector().detect(text)
        if dialect is None:
            if verbose:
                print("Dialect detection failed for a segment.")
            return None
        segments.append(
            CSVSegment(
                start_row=left,
                end_row=right,
                start_offset=start_offset,
                end_offset=end_offset,
                dialect=dialect,
                data=text,
            )
        )

    if segments[0].dialect.delimiter == segments[1].dialect.delimiter:
        if verbose:
            print("Both segments use the same dialect.")
        return None
    return segments
