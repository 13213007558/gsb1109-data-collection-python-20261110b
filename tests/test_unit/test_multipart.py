# -*- coding: utf-8 -*-

"""
Unit tests for multi-dialect segment detection.

Author: Gertjan van den Burg

"""

import os
import tempfile
import unittest

from clevercsv.dialect import SimpleDialect
from clevercsv.multipart import CSVSegment
from clevercsv.multipart import detect_multipart
from clevercsv.multipart import iter_record_offsets
from clevercsv.wrappers import detect_multipart_dialect


def _block(delimiter: str, nrows: int, ncols: int = 3) -> str:
    row = delimiter.join(str(value) for value in range(ncols))
    return "\n".join(row for _ in range(nrows))


class RecordOffsetsTestCase(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(
            iter_record_offsets("a,b\nc,d\n"), [(0, 3), (4, 7)]
        )

    def test_no_trailing_newline(self) -> None:
        self.assertEqual(
            iter_record_offsets("a,b\nc,d"), [(0, 3), (4, 7)]
        )

    def test_crlf(self) -> None:
        self.assertEqual(
            iter_record_offsets("a,b\r\nc,d\r\n"), [(0, 3), (5, 8)]
        )

    def test_quoted_newline(self) -> None:
        data = 'a,"line1\nline2",c\nd,e,f\n'
        offsets = iter_record_offsets(data)
        self.assertEqual(len(offsets), 2)
        first_text = data[offsets[0][0] : offsets[0][1]]
        self.assertEqual(first_text, 'a,"line1\nline2",c')
        self.assertEqual(data[offsets[1][0] : offsets[1][1]], "d,e,f")

    def test_doubled_quote(self) -> None:
        data = '"a""b\nb",c\nd,e\n'
        offsets = iter_record_offsets(data)
        self.assertEqual(len(offsets), 2)
        self.assertEqual(data[offsets[0][0] : offsets[0][1]], '"a""b\nb",c')

    def test_single_quote(self) -> None:
        data = "a,'line1\nline2',c\nd,e,f\n"
        offsets = iter_record_offsets(data)
        self.assertEqual(len(offsets), 2)

    def test_escaped_quote(self) -> None:
        data = 'a,"x\\"y\nz",c\nd,e,f\n'
        offsets = iter_record_offsets(data)
        self.assertEqual(len(offsets), 2)


class DetectMultipartTestCase(unittest.TestCase):
    def test_simple_boundary(self) -> None:
        data = _block(",", 6, 5) + "\n" + _block(";", 5, 4)
        segments = detect_multipart(data)
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(len(segments), 2)

        first, second = segments
        self.assertEqual(first.start_row, 0)
        self.assertEqual(first.end_row, 6)
        self.assertEqual(second.start_row, 6)
        self.assertEqual(second.end_row, 11)
        self.assertEqual(first.dialect.delimiter, ",")
        self.assertEqual(second.dialect.delimiter, ";")

        self.assertEqual(len(first.rows()[0]), 5)
        self.assertEqual(len(second.rows()[0]), 4)

    def test_reverse_order(self) -> None:
        data = _block(";", 5) + "\n" + _block(",", 6)
        segments = detect_multipart(data)
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(segments[0].dialect.delimiter, ";")
        self.assertEqual(segments[1].dialect.delimiter, ",")

    def test_offsets_match_source(self) -> None:
        data = _block(",", 5) + "\n" + _block(";", 5)
        segments = detect_multipart(data)
        assert segments is not None
        for segment in segments:
            self.assertEqual(
                data[segment.start_offset : segment.end_offset],
                segment.data,
            )

    def test_single_dialect_returns_none(self) -> None:
        self.assertIsNone(detect_multipart(_block(",", 12)))

    def test_missing_column_does_not_trigger_boundary(self) -> None:
        rows = ["1,2,3,4"] * 9
        rows.insert(4, "1,2,3")
        self.assertIsNone(detect_multipart("\n".join(rows)))

    def test_missing_column_near_real_boundary(self) -> None:
        left = ["1,2,3,4"] * 6
        left[2] = "1,2,3"
        right = ["1;2;3"] * 5
        segments = detect_multipart("\n".join(left + right))
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(segments[0].end_row, 6)
        self.assertEqual(segments[0].dialect.delimiter, ",")
        self.assertEqual(segments[1].dialect.delimiter, ";")

    def test_quoted_multiline_record_not_split(self) -> None:
        left = (
            'a,b,"line1\nline2",d\n'
            + "\n".join("1,2,3,4" for _ in range(5))
        )
        right = "\n".join("1;2;3" for _ in range(5))
        data = left + "\n" + right

        offsets = iter_record_offsets(data)
        self.assertEqual(len(offsets), 11)

        segments = detect_multipart(data)
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(segments[0].end_row, 6)
        self.assertEqual(segments[0].rows()[0][2], "line1\nline2")

    def test_other_delimiter_inside_quotes(self) -> None:
        left = "\n".join("a,b,c" for _ in range(4)) + '\n"x;y",b,c'
        right = "\n".join("1;2;3" for _ in range(4))
        data = left + "\n" + right

        segments = detect_multipart(data)
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(segments[0].dialect.delimiter, ",")
        self.assertEqual(segments[1].dialect.delimiter, ";")
        self.assertEqual(segments[0].rows()[-1][0], "x;y")

    def test_interleaved_dialects_returns_none(self) -> None:
        rows = [
            "a,b,c" if index % 2 == 0 else "a;b;c" for index in range(12)
        ]
        self.assertIsNone(detect_multipart("\n".join(rows)))

    def test_too_few_records_returns_none(self) -> None:
        data = _block(",", 2) + "\n" + _block(";", 2)
        self.assertIsNone(detect_multipart(data))

    def test_min_support_parameter(self) -> None:
        data = _block(",", 6) + "\n" + _block(";", 2)
        self.assertIsNone(detect_multipart(data))
        segments = detect_multipart(data, min_support=2)
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(segments[1].end_row - segments[1].start_row, 2)

    def test_crlf_input(self) -> None:
        data = ("a,b,c\r\n" * 5) + ("a;b;c\r\n" * 5)
        segments = detect_multipart(data)
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(segments[0].dialect.delimiter, ",")
        self.assertEqual(segments[1].dialect.delimiter, ";")
        self.assertEqual(segments[0].rows(), [["a", "b", "c"]] * 5)

    def test_custom_delimiters(self) -> None:
        data = _block("|", 5) + "\n" + _block(",", 5)
        self.assertIsNone(detect_multipart(data))
        segments = detect_multipart(data, delimiters=("|", ","))
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(segments[0].dialect.delimiter, "|")
        self.assertEqual(segments[1].dialect.delimiter, ",")

    def test_masked_delimiter_returns_none(self) -> None:
        # Semicolons only occur inside quoted fields, so the file really has
        # a single comma dialect and no boundary should be reported.
        rows = ['a,"x;y",c', '1,"p;q",3', '4,"r;s",6'] * 3
        self.assertIsNone(detect_multipart("\n".join(rows)))

    def test_three_parts_returns_none(self) -> None:
        data = (
            _block(",", 5)
            + "\n"
            + _block(";", 5)
            + "\n"
            + _block(",", 5)
        )
        self.assertIsNone(detect_multipart(data))

    def test_empty_file_returns_none(self) -> None:
        self.assertIsNone(detect_multipart(""))

    def test_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            detect_multipart(_block(",", 6), min_support=0)
        with self.assertRaises(ValueError):
            detect_multipart(_block(",", 6), tolerance=1.0)

    def test_segment_dataclass(self) -> None:
        data = _block(",", 4) + "\n" + _block(";", 4)
        segments = detect_multipart(data)
        assert segments is not None
        self.assertIsInstance(segments[0], CSVSegment)
        self.assertEqual(
            segments[0].start_offset + len(segments[0].data),
            segments[0].end_offset,
        )


class WrapperTestCase(unittest.TestCase):
    def _write_temp(self, text: str) -> str:
        tmpfd, tmpfname = tempfile.mkstemp(prefix="ccsv_", suffix=".csv")
        with os.fdopen(tmpfd, "w", newline="", encoding="utf-8") as fid:
            fid.write(text)
        return tmpfname

    def test_detect_multipart_dialect(self) -> None:
        data = _block(",", 5) + "\n" + _block(";", 5)
        tmpfname = self._write_temp(data)
        try:
            segments = detect_multipart_dialect(tmpfname)
            self.assertIsNotNone(segments)
            assert segments is not None
            self.assertEqual(
                [segment.dialect.delimiter for segment in segments],
                [",", ";"],
            )
        finally:
            os.unlink(tmpfname)

    def test_detect_multipart_dialect_inconclusive(self) -> None:
        tmpfname = self._write_temp(_block(",", 10))
        try:
            self.assertIsNone(detect_multipart_dialect(tmpfname))
        finally:
            os.unlink(tmpfname)


if __name__ == "__main__":
    unittest.main()
