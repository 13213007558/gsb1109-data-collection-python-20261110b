""" Tablib - XLSX Support.
"""

__lazy_modules__ = {
    "io",
    "openpyxl",
    "openpyxl.reader",
    "openpyxl.reader.excel",
    "openpyxl.styles",
    "openpyxl.utils",
    "openpyxl.workbook",
    "tablib.sheet_mapping",
}

import re
from io import BytesIO

from openpyxl.reader.excel import ExcelReader, load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook

import tablib

from ..exceptions import SheetTitleError
from ..sheet_mapping import (
    DEFAULT_SHORT_TITLE_LENGTH,
    LEGEND_ORIGINAL_HEADER,
    LEGEND_SHORT_HEADER,
    LEGEND_SHEET_TITLE,
    SheetTitleImportResult,
    SheetTitleMapping,
    unique_sheet_title,
)

INVALID_TITLE_REGEX = re.compile(r'[\\*?:/\[\]]')


def safe_xlsx_sheet_title(s, replace="-"):
    return re.sub(INVALID_TITLE_REGEX, replace, s)[:31]


class XLSXFormat:
    title = 'xlsx'
    extensions = ('xlsx',)

    @classmethod
    def detect(cls, stream):
        """Returns True if given stream is a readable excel file."""
        try:
            # No need to fully load the file, it should be enough to be able to
            # read the manifest.
            reader = ExcelReader(stream, read_only=False)
            reader.read_manifest()
            return True
        except Exception:
            return False

    @classmethod
    def export_set(cls, dataset, freeze_panes=True, invalid_char_subst="-",
                   escape=False, column_width="adaptive"):
        """Returns XLSX representation of Dataset.

        If ``freeze_panes`` is True, Export will freeze panes only after first line.

        If ``dataset.title`` contains characters which are
        considered invalid for an XLSX file sheet name
        (https://web.archive.org/web/20230323081941/https://www.excelcodex.com/2012/06/worksheets-naming-conventions/),
        they will be replaced with ``invalid_char_subst``.

        If ``escape`` is True, formulae will have the leading '=' character removed.
        This is a security measure to prevent formulae from executing by default
        in exported XLSX files.

        If ``column_width`` is set to "adaptive", the column width will be set to the maximum
        width of the content in each column. If it is set to an integer, the column width will be
        set to that integer value. If it is set to None, the column width will be set as the
        default openpyxl.Worksheet width value.

        """
        wb = Workbook()
        ws = wb.worksheets[0]

        ws.title = (
            safe_xlsx_sheet_title(dataset.title, invalid_char_subst)
            if dataset.title else 'Tablib Dataset'
        )

        cls.dset_sheet(dataset, ws, freeze_panes=freeze_panes, escape=escape)

        cls._adapt_column_width(ws, column_width)

        stream = BytesIO()
        wb.save(stream)
        return stream.getvalue()

    @classmethod
    def export_book(cls, databook, freeze_panes=True, invalid_char_subst="-",
                    escape=False, column_width=None, sheet_name_legend=False,
                    short_title_length=DEFAULT_SHORT_TITLE_LENGTH):
        """Returns XLSX representation of DataBook.
        See export_set().

        If ``sheet_name_legend`` is True, every dataset is exported under a
        short, unique sheet title and an additional worksheet named
        :data:`~tablib.sheet_mapping.LEGEND_SHEET_TITLE` maps the short titles
        back to the original dataset titles. Sheet order is preserved. The
        legend lets :meth:`import_book` restore the original titles, including
        titles longer than the 31-character Excel sheet name limit.
        ``short_title_length`` sets the maximum length of the generated short
        titles.
        """

        wb = Workbook()
        for sheet in wb.worksheets:
            wb.remove(sheet)

        mapping = None
        if sheet_name_legend:
            mapping = SheetTitleMapping.from_titles(
                [dset.title for dset in databook._datasets],
                max_length=short_title_length,
            )

        for i, dset in enumerate(databook._datasets):
            ws = wb.create_sheet()
            if mapping is not None:
                ws.title = mapping.pairs[i][0]
            else:
                ws.title = (
                    safe_xlsx_sheet_title(dset.title, invalid_char_subst)
                    if dset.title else f"Sheet{i}"
                )

            cls.dset_sheet(dset, ws, freeze_panes=freeze_panes, escape=escape)

            cls._adapt_column_width(ws, column_width)

        if mapping is not None:
            legend = wb.create_sheet()
            used = set(mapping.short_titles())
            legend.title = unique_sheet_title(LEGEND_SHEET_TITLE, used)
            bold = Font(bold=True)
            for row_index, row in enumerate(mapping.as_rows(), start=1):
                legend.append(list(row))
                if row_index == 1:
                    for cell in legend[1]:
                        cell.font = bold

        stream = BytesIO()
        wb.save(stream)
        return stream.getvalue()

    @classmethod
    def import_sheet(cls, dset, sheet, headers=True, skip_lines=0):
        """Populates dataset with sheet."""

        dset.title = sheet.title

        for i, row in enumerate(sheet.rows):
            if i < skip_lines:
                continue
            row_vals = [c.value for c in row]
            if i == skip_lines and headers:
                dset.headers = row_vals
            else:
                if i > skip_lines and len(row_vals) < dset.width:
                    row_vals += [''] * (dset.width - len(row_vals))
                dset.append(row_vals)

    @classmethod
    def import_set(cls, dset, in_stream, headers=True, read_only=True, skip_lines=0):
        """Returns databook from XLS stream."""

        dset.wipe()

        xls_book = load_workbook(in_stream, read_only=read_only, data_only=True)
        sheet = xls_book.active
        cls.import_sheet(dset, sheet, headers, skip_lines)

    @staticmethod
    def _find_legend_sheet(xls_book):
        """Locate the sheet name legend by its header row, not its position.

        Raises :class:`~tablib.exceptions.SheetTitleError` when a worksheet
        named :data:`~tablib.sheet_mapping.LEGEND_SHEET_TITLE` exists but does
        not start with the expected legend header row, which indicates a
        damaged legend.
        """
        for sheet in xls_book.worksheets:
            first_row = next(sheet.iter_rows(min_row=1, max_row=1), None)
            values = [cell.value for cell in first_row] if first_row else []
            header = [("" if value is None else str(value)) for value in values]
            if header[:2] == [LEGEND_SHORT_HEADER, LEGEND_ORIGINAL_HEADER]:
                return sheet
            if sheet.title == LEGEND_SHEET_TITLE:
                raise SheetTitleError(
                    f"Worksheet {sheet.title!r} looks like a sheet name legend "
                    f"but its header row is {header[:2]!r}; expected "
                    f"[{LEGEND_SHORT_HEADER!r}, {LEGEND_ORIGINAL_HEADER!r}]."
                )
        return None

    @classmethod
    def import_book(cls, dbook, in_stream, headers=True, read_only=True, strict=False):
        """Returns databook from XLS stream.

        Workbooks exported with ``sheet_name_legend=True`` carry a sheet name
        legend. Original dataset titles are then restored by matching each
        worksheet title against the legend. Matching never relies on sheet
        position: a deleted or renamed worksheet cannot be paired with an
        unrelated legend entry. The matching report is stored on
        ``dbook.sheet_title_result``.

        If ``strict`` is True, a :class:`~tablib.exceptions.SheetTitleLookupError`
        is raised when any worksheet cannot be matched reliably or a legend
        entry has no worksheet left.
        """

        dbook.wipe()

        xls_book = load_workbook(in_stream, read_only=read_only, data_only=True)
        legend_sheet = cls._find_legend_sheet(xls_book)

        if legend_sheet is None:
            for sheet in xls_book.worksheets:
                dset = tablib.Dataset()
                cls.import_sheet(dset, sheet, headers)
                dbook.add_sheet(dset)
            return

        mapping = SheetTitleMapping.from_legend_sheet(legend_sheet)
        data_sheets = [
            sheet for sheet in xls_book.worksheets if sheet is not legend_sheet
        ]
        matches = mapping.resolve(data_sheets)

        for match in matches:
            dset = tablib.Dataset()
            cls.import_sheet(dset, match.worksheet, headers)
            if match.matched:
                dset.title = match.original_title
            dbook.add_sheet(dset)

        dbook.sheet_title_result = SheetTitleImportResult(
            mapping=mapping, matches=matches
        )
        if strict:
            dbook.sheet_title_result.raise_if_incomplete()

    @classmethod
    def dset_sheet(cls, dataset, ws, freeze_panes=True, escape=False):
        """Completes given worksheet from given Dataset."""
        _package = dataset._package(dicts=False)

        for i, sep in enumerate(dataset._separators):
            _offset = i
            _package.insert((sep[0] + _offset), (sep[1],))

        bold = Font(bold=True)
        wrap_text = Alignment(wrap_text=True)

        for i, row in enumerate(_package):
            row_number = i + 1
            for j, col in enumerate(row):
                col_idx = get_column_letter(j + 1)
                cell = ws[f'{col_idx}{row_number}']

                # bold headers
                if (row_number == 1) and dataset.headers:
                    cell.font = bold
                    if freeze_panes:
                        #  Export Freeze only after first Line
                        ws.freeze_panes = 'A2'

                # bold separators
                elif len(row) < dataset.width:
                    cell.font = bold

                # wrap the rest
                else:
                    if '\n' in str(col):
                        cell.alignment = wrap_text

                try:
                    cell.value = col
                except ValueError:
                    cell.value = str(col)

                if escape and cell.data_type == 'f' and cell.value.startswith('='):
                    cell.value = cell.value[1:]

    @classmethod
    def _adapt_column_width(cls, worksheet, width):
        if isinstance(width, str) and width != "adaptive":
            msg = (
                f"Invalid value for column_width: {width}. "
                "Must be 'adaptive' or an integer."
            )
            raise ValueError(msg)

        if width is None:
            return

        column_widths = []
        if width == "adaptive":
            for row in worksheet.values:
                for i, cell in enumerate(row):
                    cell_width = len(str(cell))
                    if len(column_widths) > i:
                        if cell_width > column_widths[i]:
                            column_widths[i] = cell_width
                    else:
                        column_widths.append(cell_width)
        else:
            column_widths = [width] * worksheet.max_column

        for i, column_width in enumerate(column_widths, 1):  # start at 1
            worksheet.column_dimensions[get_column_letter(i)].width = column_width
