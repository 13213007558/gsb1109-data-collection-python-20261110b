# -*- coding: utf-8 -*-

"""
Unit tests for header location in files with a preamble.

Author: Gertjan van den Burg

"""

import unittest

from typing import Iterable

from clevercsv import HeaderLocation
from clevercsv import HeaderNotFoundError
from clevercsv import TableParseError
from clevercsv import iter_rows_with_header
from clevercsv import locate_headers
from clevercsv import read_rows_with_header
from clevercsv.dialect import SimpleDialect


class HeaderTestCase(unittest.TestCase):
    def _sample(self) -> str:
        # The preamble contains commas and even a required column name, yet
        # it must not be mistaken for the header.
        return (
            "导出日期: 2026-11-11, 说明: 请核对库存列, "
            "操作员: admin\n"
            "注意,库存一词出现在这里也不代表这是表头\n"
            "\n"
            "商品编号,库存,仓库\n"
            "A001,12,北京\n"
            "A002,0,上海\n"
        )

    def test_locate_header_after_preamble(self) -> None:
        locations = locate_headers(
            self._sample(), ["商品编号", "库存", "仓库"]
        )
        self.assertEqual(1, len(locations))
        location = locations[0]
        self.assertEqual(4, location.line)
        self.assertEqual(3, location.line_index)
        self.assertEqual(2, location.row_index)
        self.assertEqual(["商品编号", "库存", "仓库"], location.fields)

    def test_read_rows_skips_preamble(self) -> None:
        location, rows = read_rows_with_header(
            self._sample(), ["商品编号", "库存"]
        )
        self.assertEqual(4, location.line)
        self.assertEqual(
            [
                ["商品编号", "库存", "仓库"],
                ["A001", "12", "北京"],
                ["A002", "0", "上海"],
            ],
            rows,
        )

    def test_preamble_mentioning_column_is_not_header(self) -> None:
        text = "导出信息,库存只是说明文字\n商品编号,库存,仓库\nA,1,W\n"
        locations = locate_headers(text, ["商品编号", "仓库"])
        self.assertEqual([2], [location.line for location in locations])

    def test_multiple_candidates_keep_all_positions(self) -> None:
        text = "商品编号,库存,仓库\n1,2,3\n商品编号,库存,仓库\n4,5,6\n"
        locations = locate_headers(text, ["库存"])
        self.assertEqual(
            [(1, 0), (3, 2)],
            [(location.line, location.row_index) for location in locations],
        )
        self.assertTrue(
            all(isinstance(location, HeaderLocation) for location in locations)
        )

    def test_first_candidate_is_used_for_reading(self) -> None:
        text = "商品编号,库存\n1,2\n商品编号,库存\n3,4\n"
        location, rows = read_rows_with_header(text, ["库存"])
        self.assertEqual(1, location.line)
        self.assertEqual(
            [
                ["商品编号", "库存"],
                ["1", "2"],
                ["商品编号", "库存"],
                ["3", "4"],
            ],
            rows,
        )

    def test_blank_and_multiline_preamble_line_numbers(self) -> None:
        text = '前言,备注\n"多行\n说明",x\n\n商品编号,库存,仓库\nA,1,W\n'
        locations = locate_headers(text, ["库存"])
        self.assertEqual(1, len(locations))
        # The quoted field spans physical lines 2 and 3; the blank line 4 is
        # counted too, so the header is on physical line 5.
        self.assertEqual(5, locations[0].line)

        location, rows = read_rows_with_header(text, ["仓库"])
        self.assertEqual(5, location.line)
        self.assertEqual([["商品编号", "库存", "仓库"], ["A", "1", "W"]], rows)

    def test_cell_whitespace_is_normalized(self) -> None:
        text = " 商品编号 , 库存 ,仓库 \n1,2,3\n"
        locations = locate_headers(text, ["商品编号", "仓库"])
        self.assertEqual(1, len(locations))
        self.assertEqual(
            [" 商品编号 ", " 库存 ", "仓库 "], locations[0].fields
        )

    def test_extra_columns_are_allowed(self) -> None:
        text = "商品编号,库存,仓库,备注\n1,2,3,x\n"
        locations = locate_headers(text, ["库存", "仓库"])
        self.assertEqual(1, len(locations))
        self.assertEqual(
            ["商品编号", "库存", "仓库", "备注"], locations[0].fields
        )

    def test_header_not_found(self) -> None:
        text = "a,b\n1,2\n"
        self.assertEqual([], locate_headers(text, ["商品编号"]))
        with self.assertRaises(HeaderNotFoundError):
            read_rows_with_header(text, ["商品编号"])

    def test_unparseable_table_raises_table_parse_error(self) -> None:
        # A quote followed by stray characters is rejected in strict mode.
        dialect = SimpleDialect(",", '"', "", strict=True)
        text = '"ab"c,1\n'
        with self.assertRaises(TableParseError):
            locate_headers(text, ["ab"], dialect=dialect)

    def test_body_parse_error_is_distinct_from_missing_header(self) -> None:
        dialect = SimpleDialect(",", '"', "", strict=True)
        text = '商品编号,库存\n"ab"c,1\n'
        # The header itself is within the searched prefix and is valid.
        location, row_iter = iter_rows_with_header(
            text, ["商品编号"], dialect=dialect, num_lines=1
        )
        self.assertEqual(1, location.line)
        with self.assertRaises(TableParseError):
            list(row_iter)

    def test_dialect_is_detected(self) -> None:
        text = "导出日期; 2026\n商品编号;库存;仓库\nA;1;W\n"
        locations = locate_headers(text, ["库存", "仓库"])
        self.assertEqual(1, len(locations))
        self.assertEqual(2, locations[0].line)
        self.assertEqual(";", locations[0].dialect.delimiter)

        location, rows = read_rows_with_header(text, ["仓库"])
        self.assertEqual([["商品编号", "库存", "仓库"], ["A", "1", "W"]], rows)

    def test_supplied_dialect_is_used(self) -> None:
        dialect = SimpleDialect(";", "", "")
        text = "商品编号;库存;仓库\nA;1;W\n"
        locations = locate_headers(text, ["库存"], dialect=dialect)
        self.assertEqual(";", locations[0].dialect.delimiter)

    def test_num_lines_limits_the_search(self) -> None:
        text = "前言\n商品编号,库存\n1,2\n"
        self.assertEqual([], locate_headers(text, ["库存"], num_lines=1))
        locations = locate_headers(text, ["库存"], num_lines=2)
        self.assertEqual(2, locations[0].line)

    def test_iterable_input(self) -> None:
        lines: Iterable[str] = (
            "导出日期: x, y\n",
            "\n",
            "商品编号,库存,仓库\n",
            "A,1,W\n",
        )
        location, rows = read_rows_with_header(lines, ["商品编号", "仓库"])
        self.assertEqual(3, location.line)
        self.assertEqual([["商品编号", "库存", "仓库"], ["A", "1", "W"]], rows)

    def test_iter_rows_returns_iterator(self) -> None:
        import types

        text = "商品编号,库存\n1,2\n3,4\n"
        location, row_iter = iter_rows_with_header(text, ["库存"])
        self.assertIsInstance(row_iter, types.GeneratorType)
        self.assertEqual(1, location.line)
        self.assertEqual(
            [["商品编号", "库存"], ["1", "2"], ["3", "4"]], list(row_iter)
        )

    def test_invalid_required_columns(self) -> None:
        with self.assertRaises(ValueError):
            locate_headers("a\n", [])
        with self.assertRaises(ValueError):
            locate_headers("a\n", [""])


if __name__ == "__main__":
    unittest.main()
