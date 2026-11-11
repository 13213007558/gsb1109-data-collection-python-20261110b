# -*- coding: utf-8 -*-

"""
Unit tests for reading and writing files with multi-character delimiters.

"""

import io
import unittest

from typing import Iterable
from typing import List

import clevercsv

from clevercsv.dialect import SimpleDialect


class MultiCharDelimiterReadTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dialect = SimpleDialect("||", '"', "")

    def _read_test(
        self, input: Iterable[str], expect: List[List[str]], **kwargs
    ) -> None:
        reader = clevercsv.reader(input, **kwargs)
        self.assertEqual(list(reader), expect)

    def test_basic(self) -> None:
        self._read_test(
            io.StringIO("a||b\nc||d\n", newline=""),
            [["a", "b"], ["c", "d"]],
            dialect=self.dialect,
        )

    def test_single_pipe_is_content(self) -> None:
        self._read_test(
            io.StringIO("a|b||c\n", newline=""),
            [["a|b", "c"]],
            dialect=self.dialect,
        )

    def test_repeated_prefix(self) -> None:
        # The delimiter is matched greedily, left to right
        self._read_test(
            io.StringIO("a|||b\n", newline=""),
            [["a", "|b"]],
            dialect=self.dialect,
        )
        self._read_test(
            io.StringIO("a|||||b\n", newline=""),
            [["a", "", "|b"]],
            dialect=self.dialect,
        )

    def test_empty_columns(self) -> None:
        self._read_test(
            io.StringIO("a||||b\n", newline=""),
            [["a", "", "b"]],
            dialect=self.dialect,
        )
        self._read_test(
            io.StringIO("||a||\n", newline=""),
            [["", "a", ""]],
            dialect=self.dialect,
        )
        self._read_test(
            io.StringIO("||\n", newline=""), [["", ""]], dialect=self.dialect
        )

    def test_quoted_delimiter(self) -> None:
        self._read_test(
            io.StringIO('"a||b"||c\n', newline=""),
            [["a||b", "c"]],
            dialect=self.dialect,
        )

    def test_quoted_doublequote(self) -> None:
        self._read_test(
            io.StringIO('"a""b"||c\n', newline=""),
            [['a"b', "c"]],
            dialect=self.dialect,
        )

    def test_quoted_newline(self) -> None:
        self._read_test(
            io.StringIO('"a\nb"||c\n', newline=""),
            [["a\nb", "c"]],
            dialect=self.dialect,
        )

    def test_delimiter_spanning_chunks(self) -> None:
        self._read_test(iter(["a|", "|b\n"]), [["a", "b"]], dialect=self.dialect)
        self._read_test(
            iter(["a|", "|b|c", "||d\n"]),
            [["a", "b|c"], ["", "d"]],
            dialect=self.dialect,
        )
        # delimiter split over three chunks
        dialect = SimpleDialect("<|>", '"', "")
        self._read_test(
            iter(["a<", "|", ">b\n"]), [["a", "b"]], dialect=dialect
        )

    def test_quoted_field_spanning_chunks(self) -> None:
        self._read_test(
            iter(['"a|', '|b"||c\n']),
            [["a||b", "c"]],
            dialect=self.dialect,
        )

    def test_dangling_prefix_at_eof(self) -> None:
        self._read_test(iter(["a|"]), [["a|"]], dialect=self.dialect)

    def test_rows_without_newline(self) -> None:
        self._read_test(
            iter(["a||b", "c||d"]),
            [["a", "b"], ["c", "d"]],
            dialect=self.dialect,
        )

    def test_delimiter_fmtparam(self) -> None:
        self._read_test(io.StringIO("x||y\n", newline=""), [["x", "y"]], delimiter="||")

    def test_parse_string(self) -> None:
        from clevercsv.cparser_util import parse_string

        rows = list(parse_string("a||b\nc||d\n", dialect=self.dialect))
        self.assertEqual(rows, [["a", "b"], ["c", "d"]])

    def test_parse_data_return_quoted(self) -> None:
        from clevercsv.cparser_util import parse_data

        rows = list(
            parse_data(
                ['"a||b"||c\n'], dialect=self.dialect, return_quoted=True
            )
        )
        self.assertEqual(rows, [[("a||b", True), ("c", False)]])

    def test_strict(self) -> None:
        dialect = SimpleDialect("||", '"', "", strict=True)
        with self.assertRaises(clevercsv.Error):
            list(clevercsv.reader(['"a'], dialect=dialect))

    def test_dict_reader(self) -> None:
        reader = clevercsv.DictReader(
            io.StringIO("a||b\n1||2\n", newline=""), dialect=self.dialect
        )
        self.assertEqual(list(reader), [{"a": "1", "b": "2"}])


class MultiCharDelimiterWriteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dialect = SimpleDialect("||", '"', "")

    def _write_test(self, rows, expect, **kwargs) -> None:
        buf = io.StringIO()
        w = clevercsv.writer(buf, **kwargs)
        w.writerows(rows)
        self.assertEqual(buf.getvalue(), expect)

    def test_basic(self) -> None:
        self._write_test([["a", "b"], ["c", "d"]], "a||b\nc||d\n", dialect=self.dialect)

    def test_quote_field_with_delimiter(self) -> None:
        self._write_test([["a||b", "c"]], '"a||b"||c\n', dialect=self.dialect)

    def test_no_quote_single_pipe(self) -> None:
        self._write_test([["a|b", "c"]], "a|b||c\n", dialect=self.dialect)

    def test_quote_doublequote(self) -> None:
        self._write_test([['a"b', "c"]], '"a""b"||c\n', dialect=self.dialect)

    def test_empty_field(self) -> None:
        self._write_test([["", "x"]], "||x\n", dialect=self.dialect)
        self._write_test([[""]], '""\n', dialect=self.dialect)
        self._write_test([[None]], '""\n', dialect=self.dialect)

    def test_delimiter_fmtparam(self) -> None:
        self._write_test([["x", "y"]], "x||y\n", delimiter="||", lineterminator="\n")

    def test_roundtrip(self) -> None:
        rows = [
            ["a", "b||c", "d|e"],
            ["", "x", 'q"q'],
            ["n\nl", "", "||"],
            ["|||", "a|", "|b"],
        ]
        buf = io.StringIO()
        w = clevercsv.writer(buf, dialect=self.dialect)
        w.writerows(rows)
        back = list(clevercsv.reader(io.StringIO(buf.getvalue()), dialect=self.dialect))
        self.assertEqual(back, rows)

    def test_roundtrip_quote_none(self) -> None:
        dialect = SimpleDialect("||", "", "\\")
        rows = [["a||b", "c|d"], ["e\\f", "g"], ["a|||b", "x"]]
        buf = io.StringIO()
        w = clevercsv.writer(buf, dialect=dialect)
        w.writerows(rows)
        back = list(clevercsv.reader(io.StringIO(buf.getvalue()), dialect=dialect))
        self.assertEqual(back, rows)

    def test_quote_none_no_escapechar(self) -> None:
        dialect = SimpleDialect("||", "", "")
        buf = io.StringIO()
        w = clevercsv.writer(buf, dialect=dialect)
        with self.assertRaises(clevercsv.Error):
            w.writerow(["a||b"])

    def test_dict_writer(self) -> None:
        buf = io.StringIO()
        w = clevercsv.DictWriter(buf, fieldnames=["a", "b"], dialect=self.dialect)
        w.writeheader()
        w.writerow({"a": "1", "b": "2"})
        self.assertEqual(buf.getvalue(), "a||b\n1||2\n")


class MultiCharDelimiterDialectTestCase(unittest.TestCase):
    def test_validate(self) -> None:
        dialect = SimpleDialect("||", '"', "")
        dialect.validate()
        self.assertEqual(dialect.delimiter, "||")

    def test_serialize_roundtrip(self) -> None:
        dialect = SimpleDialect("||", '"', "")
        other = SimpleDialect.deserialize(dialect.serialize())
        self.assertEqual(dialect, other)

    def test_to_csv_dialect_raises(self) -> None:
        dialect = SimpleDialect("||", '"', "")
        with self.assertRaises(ValueError):
            dialect.to_csv_dialect()

    def test_single_char_still_supported(self) -> None:
        # The single-character entry points are unchanged
        self.assertEqual(
            list(clevercsv.reader(io.StringIO("a,b\n"), delimiter=",")),
            [["a", "b"]],
        )
        buf = io.StringIO()
        w = clevercsv.writer(buf, delimiter=",", lineterminator="\n")
        w.writerow(["a", "b"])
        self.assertEqual(buf.getvalue(), "a,b\n")


if __name__ == "__main__":
    unittest.main()
