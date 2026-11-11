"""
    tablib.sheet_mapping
    ~~~~~~~~~~~~~~~~~~~~~

    Generation and resolution of short, non-conflicting Excel sheet titles,
    backed by a "legend" worksheet that maps every short title back to the
    original dataset title.

    Matching on import is always done by the short title, never by sheet
    position: a renamed or deleted worksheet is reported as unmatched instead
    of being silently paired with the wrong original title.

    :copyright: (c) 2026 Jazzband.
    :license: MIT, see LICENSE for more details.
"""

import re
from dataclasses import dataclass, field

from .exceptions import SheetTitleError, SheetTitleLookupError

LEGEND_SHEET_TITLE = "Sheet Names"
LEGEND_SHORT_HEADER = "Sheet Name"
LEGEND_ORIGINAL_HEADER = "Original Title"
DEFAULT_SHORT_TITLE_LENGTH = 12

# Characters forbidden in Excel sheet names. Kept in sync with
# XLSXFormat.safe_xlsx_sheet_title so this module does not need openpyxl.
_INVALID_TITLE_REGEX = re.compile(r'[\\*?:/\[\]]')


def sanitize_sheet_title(title, replace="-"):
    """Replace characters that are not allowed in an Excel sheet title."""
    return _INVALID_TITLE_REGEX.sub(replace, title)


def unique_sheet_title(title, existing, max_length=31):
    """Return an Excel-safe variant of ``title`` not present in ``existing``.

    ``existing`` is updated in place with the returned title. Titles longer
    than ``max_length`` (defaulting to the Excel limit of 31 characters) are
    truncated; if the truncated title is already taken, a numeric suffix is
    appended while keeping the result within ``max_length``.
    """
    base = ((title or "")[:max_length] or "Sheet")[:max_length]
    candidate = base
    suffix = 1
    while candidate in existing:
        marker = f"_{suffix}"
        if len(marker) > max_length:
            raise ValueError(
                f"Cannot build a unique sheet title within {max_length} "
                "characters."
            )
        candidate = base[: max_length - len(marker)] + marker
        suffix += 1
    existing.add(candidate)
    return candidate


def build_short_titles(titles, max_length=DEFAULT_SHORT_TITLE_LENGTH):
    """Build unique, Excel-safe short titles for ``titles``, preserving order.

    Each short title keeps the leading characters of the corresponding title
    (invalid Excel characters are replaced). Titles that collide after
    truncation are disambiguated with a numeric suffix, so the input order is
    preserved and no two short titles are equal.
    """
    if max_length < 1:
        raise ValueError("max_length must be a positive integer")

    used = set()
    short_titles = []
    for title in titles:
        base = sanitize_sheet_title(title or "")[:max_length] or "Sheet"
        short_titles.append(unique_sheet_title(base, used, max_length=max_length))
    return short_titles


@dataclass
class SheetTitleMapping:
    """Ordered mapping between short sheet titles and original dataset titles.

    ``pairs`` is a list of ``(short_title, original_title)`` tuples in sheet
    order.
    """

    pairs: list = field(default_factory=list)

    def short_titles(self):
        return [short for short, _ in self.pairs]

    def original_titles(self):
        return [original for _, original in self.pairs]

    def as_rows(self):
        """Return the legend worksheet rows, including the header row."""
        return [
            (LEGEND_SHORT_HEADER, LEGEND_ORIGINAL_HEADER),
            *self.pairs,
        ]

    @classmethod
    def from_titles(cls, titles, max_length=DEFAULT_SHORT_TITLE_LENGTH):
        normalized = ["" if title is None else title for title in titles]
        short_titles = build_short_titles(normalized, max_length=max_length)
        return cls(list(zip(short_titles, normalized, strict=True)))

    @classmethod
    def from_legend_sheet(cls, sheet):
        """Build a mapping from a legend worksheet read by openpyxl."""
        rows = [[cell.value for cell in row] for row in sheet.rows]
        if not rows:
            raise SheetTitleError("The sheet name legend is empty.")

        header = [("" if value is None else str(value)) for value in rows[0]]
        if header[:2] != [LEGEND_SHORT_HEADER, LEGEND_ORIGINAL_HEADER]:
            raise SheetTitleError(
                "The sheet name legend has an unexpected header row: "
                f"{header[:2]!r}, expected "
                f"[{LEGEND_SHORT_HEADER!r}, {LEGEND_ORIGINAL_HEADER!r}]."
            )

        pairs = []
        seen = set()
        for row in rows[1:]:
            if len(row) < 2:
                raise SheetTitleError(
                    "The sheet name legend contains a row missing a short "
                    f"title or an original title: {row!r}."
                )
            # Empty cells are read back as None; they represent empty strings.
            short = "" if row[0] is None else str(row[0])
            original = "" if row[1] is None else str(row[1])
            if not short and not original:
                continue
            if not short:
                raise SheetTitleError(
                    "The sheet name legend contains a row with an empty short "
                    f"title: {row!r}."
                )
            if short in seen:
                raise SheetTitleError(
                    f"The sheet name legend lists the short title {short!r} "
                    "more than once."
                )
            seen.add(short)
            pairs.append((short, original))
        return cls(pairs)

    def resolve(self, worksheets):
        """Match ``worksheets`` against the legend by their titles.

        Returns a list of :class:`SheetTitleMatch` in the order of
        ``worksheets``. A worksheet whose title is not listed exactly once in
        the legend, or whose title is shared by several worksheets, is
        reported as unmatched; sheet positions are never used to guess a
        mapping.
        """
        by_short = {}
        for short, original in self.pairs:
            by_short.setdefault(short, []).append(original)

        worksheet_counts = {}
        for worksheet in worksheets:
            worksheet_counts[worksheet.title] = worksheet_counts.get(worksheet.title, 0) + 1

        matches = []
        for worksheet in worksheets:
            originals = by_short.get(worksheet.title)
            if worksheet_counts[worksheet.title] > 1:
                reason = "short title is used by multiple worksheets"
                original = None
            elif not originals:
                reason = "not in legend"
                original = None
            elif len(originals) > 1:
                reason = "duplicate short title in legend"
                original = None
            else:
                reason = None
                original = originals[0]
            matches.append(
                SheetTitleMatch(
                    worksheet=worksheet,
                    short_title=worksheet.title,
                    original_title=original,
                    reason=reason,
                )
            )
        return matches

    def missing_titles(self, worksheets):
        """Legend entries whose short title no longer exists in the workbook."""
        present = {worksheet.title for worksheet in worksheets}
        return [short for short, _ in self.pairs if short not in present]


@dataclass
class SheetTitleMatch:
    """Result of matching one worksheet against the legend."""

    worksheet: object
    short_title: str
    original_title: object = None
    reason: object = None

    @property
    def matched(self):
        return self.original_title is not None

    def require_match(self):
        if not self.matched:
            raise SheetTitleLookupError(
                f"Worksheet {self.short_title!r} cannot be matched reliably: "
                f"{self.reason}."
            )
        return self.original_title


@dataclass
class SheetTitleImportResult:
    """Outcome of importing a workbook that carries a sheet name legend."""

    mapping: SheetTitleMapping
    matches: list = field(default_factory=list)

    @property
    def matched(self):
        return [match for match in self.matches if match.matched]

    @property
    def unmatched(self):
        return [match for match in self.matches if not match.matched]

    @property
    def missing_titles(self):
        data_sheets = [match.worksheet for match in self.matches]
        return self.mapping.missing_titles(data_sheets)

    @property
    def ok(self):
        return not self.unmatched and not self.missing_titles

    def raise_if_incomplete(self):
        """Raise :class:`SheetTitleLookupError` if any title cannot be restored."""
        problems = [
            f"worksheet {match.short_title!r}: {match.reason}"
            for match in self.unmatched
        ]
        problems.extend(
            f"legend entry {short!r} has no matching worksheet"
            for short in self.missing_titles
        )
        if problems:
            raise SheetTitleLookupError(
                "Some worksheets cannot be matched reliably against the sheet "
                "name legend: " + "; ".join(problems)
            )
        return self
