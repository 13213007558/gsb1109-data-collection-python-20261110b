#!/usr/bin/env python3
"""Generate the dateedge_1900 / dateedge_1904 reference workbooks.

The main test suite only compares xlsx2csv output against frozen CSV files and
cannot explain *why* a boundary value is expected. These two minimal workbooks
pin down how a serial number becomes a date so the financial-report import can
rely on it without guessing:

* The conversion is pure calendar arithmetic on a fixed epoch
  (1899-12-30 for the 1900 system, 1904-01-01 for the 1904 system). No host
  timezone is ever consulted, so the same input must produce the same CSV on
  any machine. Do not "fix" these expectations by routing the value through
  local time; that would break the documented compatibility contract.
* Serial 60 in the 1900 system is the fictitious leap day 1900-02-29 that
  Excel inherits from Lotus 1-2-3. The fixed epoch deliberately maps it to
  1900-02-28, matching what Excel displays for every serial from 60 onwards.
* A serial below 1 only carries a time-of-day fraction and is rendered with
  the time format, never as a date. The test runner passes
  --timeformat=%H:%M:%S so the seconds are visible; the built-in default is
  %H:%M, already covered by the timeformat test case.
* The number format attached to the cell decides the type: the identical
  serial stored with a plain numeric format (0.00) is kept as a number. An
  integer-valued float is emitted without decimal places ("43830", not
  "43830.00") unless --floatformat is given; pin that convention here so it is
  not mistaken for date handling.

date1904 is a workbook-level switch, so the two date systems live in two
files. Both files are built from explicit XML with the standard library only;
regenerate them with:

    python3 test/make_dateedge_xlsx.py
"""

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="22" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="21" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""

CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""

ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""

WORKBOOK_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""

# Style indexes into cellXfs above.
STYLE_FLOAT = 1
STYLE_DATE = 2
STYLE_DATETIME = 3
STYLE_TIME = 4

HEADERS = ("excel_serial", "number_format", "csv_output", "note")


def workbook_xml(date1904):
    flag = '<workbookPr date1904="1"/>' if date1904 else '<workbookPr/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        + flag
        + '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )


def col_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def text_cell(ref, text):
    return (
        '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, text)
    )


def number_cell(ref, serial, style):
    return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, serial)


def sheet_xml(rows):
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        '<sheetData>',
    ]
    for row_index, cells in enumerate(rows, start=1):
        out.append('<row r="%d">' % row_index)
        for col_index, cell in enumerate(cells, start=1):
            ref = col_name(col_index) + str(row_index)
            if isinstance(cell, str):
                out.append(text_cell(ref, cell))
            else:
                serial, style = cell
                out.append(number_cell(ref, serial, style))
        out.append('</row>')
    out.append('</sheetData></worksheet>')
    return "".join(out)


def write_workbook(path, date1904, rows):
    parts = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "_rels/.rels": ROOT_RELS_XML,
        "xl/workbook.xml": workbook_xml(date1904),
        "xl/_rels/workbook.xml.rels": WORKBOOK_RELS_XML,
        "xl/styles.xml": STYLES_XML,
        "xl/worksheets/sheet1.xml": sheet_xml(rows),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            # Fixed timestamp keeps regenerated fixtures byte-for-byte identical.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


ROWS_1900 = [
    HEADERS,
    [
        ("45292", STYLE_DATE), "mm-dd-yy", "2024-01-01 00:00:00",
        "1900 system epoch 1899-12-30: serial 45292 is 2024-01-01",
    ],
    [
        ("45292.5", STYLE_DATETIME), "m/d/yy h:mm", "2024-01-01 12:00:00",
        "fractional serial .5 is noon of the same calendar day",
    ],
    [
        ("0.5", STYLE_TIME), "h:mm:ss", "12:00:00",
        "serial below 1 carries only a time fraction and renders as time of day",
    ],
    [
        ("60", STYLE_DATE), "mm-dd-yy", "1900-02-28 00:00:00",
        "serial 60 is the fictitious Lotus 1900-02-29; fixed epoch maps it to 1900-02-28",
    ],
    [
        ("59", STYLE_DATE), "mm-dd-yy", "1900-02-27 00:00:00",
        "serial 59 is 1900-02-27, the day before the fictitious leap day",
    ],
    [
        ("61", STYLE_DATE), "mm-dd-yy", "1900-03-01 00:00:00",
        "serial 61 is 1900-03-01 and stays in sync with Excel after the gap",
    ],
    [
        ("43830", STYLE_FLOAT), "0.00", "43830",
        "same serial with a plain numeric format stays a number; integer floats lose the decimal places",
    ],
]

ROWS_1904 = [
    HEADERS,
    [
        ("43830", STYLE_DATE), "mm-dd-yy", "2024-01-01 00:00:00",
        "1904 system epoch 1904-01-01: serial 43830 is the same day as 45292 in the 1900 system",
    ],
    [
        ("45292", STYLE_DATE), "mm-dd-yy", "2028-01-02 00:00:00",
        "same serial 45292 as the 1900 workbook but 1462 days later; the date system drives the gap, not timezone",
    ],
    [
        ("0.5", STYLE_TIME), "h:mm:ss", "12:00:00",
        "fractional serial renders as time of day in both date systems",
    ],
    [
        ("43830", STYLE_FLOAT), "0.00", "43830",
        "numeric format keeps the raw serial regardless of the workbook date system; integer floats lose decimal places",
    ],
]


def main():
    write_workbook(os.path.join(HERE, "dateedge_1900.xlsx"), False, ROWS_1900)
    write_workbook(os.path.join(HERE, "dateedge_1904.xlsx"), True, ROWS_1904)


if __name__ == "__main__":
    main()
