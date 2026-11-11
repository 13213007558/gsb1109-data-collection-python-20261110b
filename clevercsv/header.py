# -*- coding: utf-8 -*-

"""
Locate the header row of a CSV file that may start with a preamble.

Exported CSV files often begin with a few lines of metadata (an export date,
a user notice, ...) before the actual table starts. Those preamble lines are
not safe to treat as data: they may contain commas, quote characters, or even
the name of one of the columns. This module therefore never assumes that the
first parseable line is the header. Instead, the caller supplies the columns
that must be present in a header, and every parsed row is checked against
that requirement.

All rows that contain the required columns are recorded, together with their
position, so that a caller faced with several candidates can pick one. The
:class:`~clevercsv.reader` class is used both to locate the header and to read
the body that follows it, which keeps the two steps consistent.

Author: Gertjan van den Burg

"""

from __future__ import annotations

import csv
import io

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import Union

from .detect import Detector
from .dialect import SimpleDialect
from .exceptions import Error
from .exceptions import HeaderNotFoundError
from .exceptions import NoDetectionResult
from .exceptions import TableParseError
from .read import reader

if TYPE_CHECKING:
    from ._types import _DialectLike


@dataclass(frozen=True)
class HeaderLocation:
    """Location of a row that can serve as the table header.

    Attributes
    ----------
    line : int
        One-based physical line number of the header in the input, counting
        every line including the preamble and blank lines.

    row_index : int
        Zero-based index of the header among the parsed (non-empty) rows.

    fields : list
        The cells of the header row, in file order.

    dialect : SimpleDialect
        The dialect used to parse the row and to read the body.

    """

    line: int
    row_index: int
    fields: List[str]
    dialect: SimpleDialect

    @property
    def line_index(self) -> int:
        """Zero-based physical line index of the header."""
        return self.line - 1


class _LineCountingIterator(Iterator[str]):
    """Wrap a line iterator and count every physical line pulled.

    The C parser pulls one physical line at a time, including lines that end
    up inside a quoted, multi-line field, so the counter records real line
    numbers rather than parsed-row numbers.
    """

    def __init__(self, lines: Iterable[str]) -> None:
        self._iterator = iter(lines)
        self.line_num = 0

    def __iter__(self) -> "_LineCountingIterator":
        return self

    def __next__(self) -> str:
        line = next(self._iterator)
        self.line_num += 1
        return line


def _as_text(data: Union[str, Iterable[str]]) -> str:
    if isinstance(data, str):
        return data
    return "".join(data)


def _normalize_cell(cell: str) -> str:
    return cell.strip()


def _is_header_row(fields: Sequence[str], required: Sequence[str]) -> bool:
    if not fields:
        return False
    normalized = {_normalize_cell(cell) for cell in fields}
    return all(column in normalized for column in required)


def locate_headers(
    data: Union[str, Iterable[str]],
    required_columns: Sequence[str],
    dialect: Optional[_DialectLike] = None,
    num_lines: Optional[int] = None,
    verbose: bool = False,
    **detect_kwargs: Any,
) -> List[HeaderLocation]:
    """Find every row that contains all of the required columns.

    The input is parsed with CleverCSV and each resulting row is checked for
    the presence of the required columns; the first parseable row is *not*
    assumed to be the header. Rows are compared after stripping surrounding
    whitespace from their cells. Every matching row is returned so that a
    file with more than one plausible header can be resolved by the caller.

    Parameters
    ----------
    data : str or iterable of str
        The CSV contents. A string is parsed directly; any other iterable is
        consumed and joined first.

    required_columns : sequence of str
        Column names that a row must contain to qualify as a header. A header
        may contain additional columns.

    dialect : str, SimpleDialect, or csv.Dialect object
        The dialect to use. If None, the dialect is detected from the data
        with :class:`~clevercsv.Detector`.

    num_lines : int
        Optional limit on the number of physical lines to search. Preamble
        and blank lines count towards this limit. If None, the whole input is
        searched.

    verbose : bool
        Passed to dialect detection.

    **detect_kwargs
        Additional keyword arguments for
        :meth:`clevercsv.Detector.detect` (e.g. ``method`` or ``skip``).

    Returns
    -------
    list of HeaderLocation
        The matching header locations, ordered from top to bottom of the
        file. The list is empty when no row contains all of the required
        columns.

    Raises
    ------
    ValueError
        When ``required_columns`` is empty or contains an empty name.

    TableParseError
        When the data cannot be parsed with the detected or supplied
        dialect.

    """
    required = list(required_columns)
    if not required:
        raise ValueError("At least one required column must be provided.")
    if any(not isinstance(column, str) or not column for column in required):
        raise ValueError("Required column names must be non-empty strings.")

    text = _as_text(data)
    simple_dialect = _resolve_dialect(
        text, dialect, verbose=verbose, **detect_kwargs
    )

    if num_lines is not None:
        text = _take_lines(text, num_lines)

    locations: List[HeaderLocation] = []
    row_index = 0
    line_counter = _LineCountingIterator(io.StringIO(text, newline=""))
    csv_reader = reader(line_counter, simple_dialect)
    try:
        for fields in csv_reader:
            if not fields:
                continue
            if _is_header_row(fields, required):
                locations.append(
                    HeaderLocation(
                        line=line_counter.line_num,
                        row_index=row_index,
                        fields=list(fields),
                        dialect=simple_dialect,
                    )
                )
            row_index += 1
    except Error as exc:
        raise TableParseError(
            "Failed to parse the table while looking for a header row: %s"
            % exc
        ) from exc
    return locations


def read_rows_with_header(
    data: Union[str, Iterable[str]],
    required_columns: Sequence[str],
    dialect: Optional[_DialectLike] = None,
    num_lines: Optional[int] = None,
    verbose: bool = False,
    **detect_kwargs: Any,
) -> Tuple[HeaderLocation, List[List[str]]]:
    """Locate the header and read the table body starting at that header.

    When several rows qualify as a header, the first one is used. The other
    candidates can be retrieved separately with :func:`locate_headers`.

    Parameters
    ----------
    data : str or iterable of str
        The CSV contents.

    required_columns : sequence of str
        Column names that the header row must contain.

    dialect : str, SimpleDialect, or csv.Dialect object
        The dialect to use. If None, the dialect is detected automatically.

    num_lines : int
        Optional limit on the number of physical lines to search for a
        header. The body below the header is always read in full.

    verbose : bool
        Passed to dialect detection.

    **detect_kwargs
        Additional keyword arguments for
        :meth:`clevercsv.Detector.detect`.

    Returns
    -------
    tuple
        ``(location, rows)`` where ``location`` is the selected
        :class:`HeaderLocation` and ``rows`` is the table starting with the
        header row, including every later row.

    Raises
    ------
    HeaderNotFoundError
        When no row contains all of the required columns. This is distinct
        from :class:`TableParseError`, so callers can tell a missing or
        mislabeled header apart from an unreadable table.

    TableParseError
        When the data cannot be parsed with the detected or supplied
        dialect.

    """
    location, row_iter = iter_rows_with_header(
        data,
        required_columns,
        dialect=dialect,
        num_lines=num_lines,
        verbose=verbose,
        **detect_kwargs,
    )
    rows = list(row_iter)
    return location, rows


def iter_rows_with_header(
    data: Union[str, Iterable[str]],
    required_columns: Sequence[str],
    dialect: Optional[_DialectLike] = None,
    num_lines: Optional[int] = None,
    verbose: bool = False,
    **detect_kwargs: Any,
) -> Tuple[HeaderLocation, Iterator[List[str]]]:
    """Streaming variant of :func:`read_rows_with_header`.

    Returns the selected :class:`HeaderLocation` together with a generator
    over the table rows, starting with the header row itself. The generator
    raises :class:`TableParseError` if the body cannot be parsed.

    Raises
    ------
    HeaderNotFoundError
        When no row contains all of the required columns.

    TableParseError
        When the data cannot be parsed while searching for the header.

    """
    text = _as_text(data)
    locations = locate_headers(
        text,
        required_columns,
        dialect=dialect,
        num_lines=num_lines,
        verbose=verbose,
        **detect_kwargs,
    )
    if not locations:
        raise HeaderNotFoundError(
            "No header row containing all required columns %r was found."
            % list(required_columns)
        )

    location = locations[0]
    simple_dialect = location.dialect

    def row_generator() -> Iterator[List[str]]:
        line_counter = _LineCountingIterator(io.StringIO(text, newline=""))
        csv_reader = reader(line_counter, simple_dialect)
        try:
            for row in csv_reader:
                if line_counter.line_num < location.line:
                    continue
                yield row
        except Error as exc:
            raise TableParseError(
                "Failed to parse the table body: %s" % exc
            ) from exc

    return location, row_generator()


def _resolve_dialect(
    text: str,
    dialect: Optional[_DialectLike],
    verbose: bool,
    **detect_kwargs: Any,
) -> SimpleDialect:
    if dialect is not None:
        return _coerce_dialect(dialect)
    detected = Detector().detect(text, verbose=verbose, **detect_kwargs)
    if detected is None:
        raise NoDetectionResult()
    return detected


def _coerce_dialect(dialect: _DialectLike) -> SimpleDialect:
    if isinstance(dialect, SimpleDialect):
        return dialect
    if isinstance(dialect, str):
        return SimpleDialect.from_csv_dialect(csv.get_dialect(dialect))
    if isinstance(dialect, csv.Dialect):
        return SimpleDialect.from_csv_dialect(dialect)
    raise ValueError("Unknown dialect type: %r" % dialect)


def _take_lines(text: str, num_lines: int) -> str:
    if num_lines <= 0:
        return ""
    lines = text.splitlines(keepends=True)
    return "".join(lines[:num_lines])
