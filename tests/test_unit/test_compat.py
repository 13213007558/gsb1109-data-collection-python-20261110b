# -*- coding: utf-8 -*-

"""
Unit tests for the csv module compatibility report.

Author: Gertjan van den Burg

"""

import csv
import io
import json
import unittest

import clevercsv

from clevercsv.compat import DIRECT
from clevercsv.compat import IMPOSSIBLE
from clevercsv.compat import PARTIAL
from clevercsv.compat import WORKAROUND
from clevercsv.compat import csv_compat_report
from clevercsv.dialect import SimpleDialect


class CompatTestCase(unittest.TestCase):
    def _read_with_csv(self, text, fmtparams):
        return list(csv.reader(io.StringIO(text), **fmtparams))

    def _read_with_clevercsv(self, text, dialect):
        return list(clevercsv.reader(io.StringIO(text), dialect=dialect))

    def _write_with_csv(self, rows, fmtparams):
        buffer = io.StringIO()
        writer = csv.writer(buffer, **fmtparams)
        writer.writerows(rows)
        return buffer.getvalue()

    def _assert_write_round_trip(self, rows, dialect, report):
        written = self._write_with_csv(rows, report.write.fmtparams)
        self.assertEqual(
            rows, self._read_with_csv(written, report.read.fmtparams)
        )
        self.assertEqual(rows, self._read_with_clevercsv(written, dialect))

    def test_excel_dialect(self):
        dialect = SimpleDialect(",", '"', "")
        report = csv_compat_report(dialect)
        # reading is partial: a quotechar in the middle of an unquoted
        # field is handled differently by the two parsers
        self.assertEqual(report.read.status, PARTIAL)
        self.assertEqual(len(report.read.divergences), 1)
        divergence = report.read.divergences[0]
        self.assertIn("quotechar", divergence.description)
        # the recorded sample really is misread by the csv module
        self.assertEqual(
            divergence.expected,
            self._read_with_clevercsv(divergence.sample, dialect),
        )
        self.assertEqual(
            divergence.actual,
            self._read_with_csv(divergence.sample, report.read.fmtparams),
        )
        self.assertNotEqual(divergence.expected, divergence.actual)
        # files without mid-field quotes read identically; not a row of
        # numbers: quotes, double quotes, embedded delimiters, newlines,
        # empty fields, and backslashes
        text = 'a,"b ""quoted""","c,d"\n"e\nf",,"g\\h"\n'
        expected = self._read_with_clevercsv(text, dialect)
        self.assertEqual(
            expected, self._read_with_csv(text, report.read.fmtparams)
        )
        # writing is lossless
        self.assertEqual(report.write.status, DIRECT)
        self.assertEqual(report.write.divergences, [])
        rows = [['a"b', "c,d", "e\nf", "", "g\\h", ""]]
        self._assert_write_round_trip(rows, dialect, report)

    def test_escapechar_dialect(self):
        dialect = SimpleDialect(",", '"', "\\")
        report = csv_compat_report(dialect)
        # reading is only partial: the csv module drops the escapechar
        # before ordinary characters, CleverCSV keeps it; and mid-field
        # quotes are handled differently
        self.assertEqual(report.read.status, PARTIAL)
        self.assertEqual(len(report.read.divergences), 2)
        divergence = report.read.divergences[0]
        self.assertIn("escapechar", divergence.description)
        self.assertNotEqual(divergence.expected, divergence.actual)
        # the recorded sample really is misread by the csv module
        self.assertEqual(
            divergence.expected,
            self._read_with_clevercsv(divergence.sample, dialect),
        )
        self.assertEqual(
            divergence.actual,
            self._read_with_csv(divergence.sample, report.read.fmtparams),
        )
        # files without escapechar-before-ordinary-char read fine
        text = '"a\\"b",c\\,d,"e""f"\n'
        expected = self._read_with_clevercsv(text, dialect)
        self.assertEqual(
            expected, self._read_with_csv(text, report.read.fmtparams)
        )
        # writing round-trips iff this Python's writer escapes the
        # escapechar (verified by the report itself)
        if report.write.status == DIRECT:
            self.assertEqual(report.write.divergences, [])
            rows = [["a\\b", "c\\\\d", 'e"f', "g,h", "i\nj"]]
            self._assert_write_round_trip(
                rows, dialect, report
            )
        else:
            self.assertEqual(report.write.status, PARTIAL)
            self.assertTrue(report.write.divergences)

    def test_empty_quotechar_with_escapechar(self):
        dialect = SimpleDialect(",", "", "\\")
        report = csv_compat_report(dialect)
        # empty quotechar maps to QUOTE_NONE; escapechar divergence remains
        self.assertEqual(report.read.status, PARTIAL)
        self.assertEqual(report.read.fmtparams["quoting"], csv.QUOTE_NONE)
        self.assertIsNone(report.read.fmtparams["quotechar"])
        self.assertEqual(len(report.read.divergences), 1)
        # double quotes are ordinary data in this dialect
        text = 'say "hi",a\\,b,plain\n'
        expected = self._read_with_clevercsv(text, dialect)
        self.assertEqual(
            expected, self._read_with_csv(text, report.read.fmtparams)
        )
        # writing needs QUOTE_NONE with quotechar=None
        self.assertEqual(report.write.fmtparams["quoting"], csv.QUOTE_NONE)
        self.assertIsNone(report.write.fmtparams["quotechar"])
        if report.write.status == WORKAROUND:
            self.assertEqual(report.write.divergences, [])
            rows = [["a,b", 'q"x', "e\\f", "g\nh", "plain", ""]]
            self._assert_write_round_trip(
                rows, dialect, report
            )
        else:
            self.assertEqual(report.write.status, PARTIAL)
        # leaving the default quotechar in place is lossy: the writer
        # escapes double quotes, which this dialect reads back with a
        # spurious escapechar
        fmtparams = dict(report.write.fmtparams)
        del fmtparams["quotechar"]
        written = self._write_with_csv([['q"x']], fmtparams)
        self.assertNotEqual(
            [['q"x']],
            self._read_with_clevercsv(written, dialect),
        )

    def test_empty_quotechar_no_escapechar(self):
        dialect = SimpleDialect(",", "", "")
        report = csv_compat_report(dialect)
        # reading works through the QUOTE_NONE mapping
        self.assertEqual(report.read.status, WORKAROUND)
        self.assertEqual(report.read.divergences, [])
        text = 'say "hi",C:\\new,x\n'
        expected = self._read_with_clevercsv(text, dialect)
        self.assertEqual(
            expected, self._read_with_csv(text, report.read.fmtparams)
        )
        # writing plain fields works, but fields containing the delimiter
        # or a newline cannot be represented
        self.assertEqual(report.write.status, PARTIAL)
        self.assertTrue(report.write.divergences)
        for divergence in report.write.divergences:
            self.assertIn("error", divergence.actual)
        rows = [["abc", "de"], ["fg", ""]]
        self._assert_write_round_trip(rows, dialect, report)

    def test_no_delimiter(self):
        dialect = SimpleDialect("", "", "")
        report = csv_compat_report(dialect)
        self.assertEqual(report.read.status, IMPOSSIBLE)
        self.assertIsNone(report.read.fmtparams)
        self.assertEqual(report.write.status, IMPOSSIBLE)
        self.assertIsNone(report.write.fmtparams)
        self.assertTrue(report.read.divergences)
        self.assertTrue(report.write.divergences)

    def test_strict_flag_passed_through(self):
        dialect = SimpleDialect(",", '"', "", strict=True)
        report = csv_compat_report(dialect)
        self.assertTrue(report.read.fmtparams["strict"])

    def test_serialize_and_render(self):
        dialect = SimpleDialect(",", '"', "\\")
        report = csv_compat_report(dialect)
        data = json.loads(report.serialize())
        self.assertEqual(data["dialect"]["escapechar"], "\\")
        self.assertIn(
            data["read"]["status"], [DIRECT, WORKAROUND, PARTIAL, IMPOSSIBLE]
        )
        rendered = report.render()
        self.assertIn("Read: status=", rendered)
        self.assertIn("Write: status=", rendered)
        # booleans must not render as quoting constants (True == QUOTE_ALL)
        excel_report = csv_compat_report(SimpleDialect(",", '"', ""))
        self.assertIn("doublequote=True", excel_report.render())
        excel_data = json.loads(excel_report.serialize())
        self.assertIs(excel_data["read"]["fmtparams"]["doublequote"], True)

    def test_detected_dialect_end_to_end(self):
        # a file detected on the analysis machine, read on the edge machine
        text = 'name,value\n"a ""quoted"" thing","1,2"\nplain,"x\\y"\n'
        dialect = clevercsv.Detector().detect(text)
        self.assertIsNotNone(dialect)
        report = csv_compat_report(dialect)
        self.assertIsNotNone(report.read.fmtparams)
        # this file avoids the constructs listed in the report's
        # divergences, so the edge machine reads it identically
        expected = self._read_with_clevercsv(text, dialect)
        self.assertEqual(
            expected, self._read_with_csv(text, report.read.fmtparams)
        )
        rows = expected + [['new "row"', "with,delim"]]
        self._assert_write_round_trip(rows, dialect, report)


if __name__ == "__main__":
    unittest.main()
