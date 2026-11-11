"""Tests for short sheet titles and the sheet name legend (xlsx)."""

import unittest
from io import BytesIO

from openpyxl import load_workbook

import tablib
from tablib.exceptions import SheetTitleError, SheetTitleLookupError
from tablib.sheet_mapping import (
    LEGEND_ORIGINAL_HEADER,
    LEGEND_SHORT_HEADER,
    LEGEND_SHEET_TITLE,
    SheetTitleMapping,
    SheetTitleMatch,
    build_short_titles,
    unique_sheet_title,
)


class ShortTitleTests(unittest.TestCase):
    def test_unique_sheet_title_truncates_and_disambiguates(self):
        existing = set()
        first = unique_sheet_title("a" * 40, existing)
        second = unique_sheet_title("a" * 40, existing)
        self.assertEqual(first, "a" * 31)
        self.assertEqual(second, "a" * 29 + "_1")
        self.assertEqual(len({first, second}), 2)
        self.assertIn(second, existing)

    def test_unique_sheet_title_falls_back_for_empty_title(self):
        self.assertEqual(unique_sheet_title("", set()), "Sheet")
        existing = {"Sheet"}
        self.assertEqual(unique_sheet_title(None, existing), "Sheet_1")

    def test_unique_sheet_title_respects_max_length(self):
        existing = set()
        self.assertEqual(unique_sheet_title("abcdef", existing, max_length=3), "abc")
        self.assertEqual(unique_sheet_title("abcxyz", existing, max_length=3), "a_1")
        with self.assertRaises(ValueError):
            unique_sheet_title("z", {"z"}, max_length=1)

    def test_build_short_titles_preserves_order_and_uniqueness(self):
        titles = [
            "crawler_order_detail_east_v1",
            "crawler_order_detail_east_v2",
            "crawler_order_detail_south_v1",
        ]
        short = build_short_titles(titles, max_length=12)
        self.assertEqual(len(short), 3)
        self.assertEqual(len(set(short)), 3)
        self.assertTrue(all(len(name) <= 12 for name in short))
        self.assertTrue(short[0].startswith("crawler_"))
        # Numeric suffixes keep colliding titles unambiguous.
        self.assertEqual([name.rsplit("_", 1)[1] for name in short[1:]], ["1", "2"])

    def test_build_short_titles_handles_similar_cjk_titles(self):
        # Titles differing only in the last character must not collapse.
        titles = ["采集端订单明细表甲", "采集端订单明细表乙"]
        short = build_short_titles(titles, max_length=6)
        self.assertEqual(len(set(short)), 2)

    def test_build_short_titles_replaces_invalid_excel_characters(self):
        short = build_short_titles(["bad/name?*"], max_length=31)
        self.assertNotIn("/", short[0])
        self.assertNotIn("?", short[0])

    def test_build_short_titles_rejects_non_positive_length(self):
        with self.assertRaises(ValueError):
            build_short_titles(["title"], max_length=0)

    def test_build_short_titles_handles_missing_titles(self):
        short = build_short_titles([None, ""], max_length=12)
        self.assertEqual(short, ["Sheet", "Sheet_1"])


class SheetTitleMappingTests(unittest.TestCase):
    def setUp(self):
        self.titles = [
            "very_long_original_title_one",
            "very_long_original_title_two",
            "other_title",
        ]
        self.mapping = SheetTitleMapping.from_titles(self.titles, max_length=10)

    def test_from_titles_keeps_original_titles(self):
        self.assertEqual(self.mapping.original_titles(), self.titles)
        self.assertEqual(len(set(self.mapping.short_titles())), 3)

    def test_as_rows_starts_with_header(self):
        rows = self.mapping.as_rows()
        self.assertEqual(rows[0], (LEGEND_SHORT_HEADER, LEGEND_ORIGINAL_HEADER))
        self.assertEqual(len(rows), 4)

    def test_resolve_matches_by_name_never_position(self):
        class FakeSheet:
            def __init__(self, title):
                self.title = title

        # Reverse the order and rename one sheet: position must not be used.
        sheets = [
            FakeSheet(self.mapping.short_titles()[2]),
            FakeSheet("renamed_by_someone"),
            FakeSheet(self.mapping.short_titles()[0]),
        ]
        matches = self.mapping.resolve(sheets)
        self.assertEqual(matches[0].original_title, self.titles[2])
        self.assertFalse(matches[1].matched)
        self.assertEqual(matches[1].reason, "not in legend")
        self.assertEqual(matches[2].original_title, self.titles[0])

    def test_resolve_flags_duplicate_worksheet_titles(self):
        class FakeSheet:
            def __init__(self, title):
                self.title = title

        short = self.mapping.short_titles()[0]
        matches = self.mapping.resolve([FakeSheet(short), FakeSheet(short)])
        self.assertFalse(any(match.matched for match in matches))
        self.assertEqual(
            matches[0].reason, "short title is used by multiple worksheets"
        )

    def test_resolve_flags_duplicate_legend_entries(self):
        class FakeSheet:
            def __init__(self, title):
                self.title = title

        mapping = SheetTitleMapping([("dup", "one"), ("dup", "two")])
        matches = mapping.resolve([FakeSheet("dup")])
        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0].matched)
        self.assertEqual(matches[0].reason, "duplicate short title in legend")

    def test_missing_titles_reports_deleted_sheets(self):
        class FakeSheet:
            def __init__(self, title):
                self.title = title

        remaining = [FakeSheet(self.mapping.short_titles()[0])]
        self.assertEqual(
            self.mapping.missing_titles(remaining),
            self.mapping.short_titles()[1:],
        )

    def test_from_legend_sheet_round_trip(self):
        class FakeCell:
            def __init__(self, value):
                self.value = value

        class FakeSheet:
            def __init__(self, rows):
                self.rows = [[FakeCell(value) for value in row] for row in rows]

        sheet = FakeSheet(
            [
                (LEGEND_SHORT_HEADER, LEGEND_ORIGINAL_HEADER),
                ("short_1", "original one"),
                ("short_2", ""),
                (None, None),
            ]
        )
        mapping = SheetTitleMapping.from_legend_sheet(sheet)
        self.assertEqual(
            mapping.pairs, [("short_1", "original one"), ("short_2", "")]
        )

    def test_from_legend_sheet_rejects_bad_header(self):
        class FakeCell:
            def __init__(self, value):
                self.value = value

        class FakeSheet:
            def __init__(self, rows):
                self.rows = [[FakeCell(value) for value in row] for row in rows]

        sheet = FakeSheet([("wrong", "header"), ("short_1", "original")])
        with self.assertRaises(SheetTitleError):
            SheetTitleMapping.from_legend_sheet(sheet)

    def test_from_legend_sheet_rejects_duplicate_short_titles(self):
        class FakeCell:
            def __init__(self, value):
                self.value = value

        class FakeSheet:
            def __init__(self, rows):
                self.rows = [[FakeCell(value) for value in row] for row in rows]

        sheet = FakeSheet(
            [
                (LEGEND_SHORT_HEADER, LEGEND_ORIGINAL_HEADER),
                ("dup", "one"),
                ("dup", "two"),
            ]
        )
        with self.assertRaises(SheetTitleError):
            SheetTitleMapping.from_legend_sheet(sheet)

    def test_from_legend_sheet_rejects_row_missing_column(self):
        class FakeCell:
            def __init__(self, value):
                self.value = value

        class FakeSheet:
            def __init__(self, rows):
                self.rows = [[FakeCell(value) for value in row] for row in rows]

        sheet = FakeSheet(
            [
                (LEGEND_SHORT_HEADER, LEGEND_ORIGINAL_HEADER),
                ("short_only",),
            ]
        )
        with self.assertRaises(SheetTitleError):
            SheetTitleMapping.from_legend_sheet(sheet)

    def test_require_match_raises_when_unmatched(self):
        match = SheetTitleMatch(
            worksheet=None, short_title="ghost", reason="not in legend"
        )
        with self.assertRaises(SheetTitleLookupError):
            match.require_match()


class XlsxLegendExportImportTests(unittest.TestCase):
    def setUp(self):
        self.titles = [
            "crawler_order_detail_east_quarter_one_2026",
            "crawler_order_detail_east_quarter_two_2026",
            "crawler_order_detail_south_quarter_one_2026",
        ]
        self.book = tablib.Databook()
        for index, title in enumerate(self.titles):
            dataset = tablib.Dataset(headers=("id", "name"), title=title)
            dataset.append((index, f"row {index}"))
            self.book.add_sheet(dataset)

    def _workbook(self, **kwargs):
        blob = self.book.export("xlsx", sheet_name_legend=True, **kwargs)
        return load_workbook(BytesIO(blob))

    def test_export_uses_short_unique_titles_and_appends_legend(self):
        workbook = self._workbook(short_title_length=12)
        names = workbook.sheetnames
        self.assertEqual(names[-1], LEGEND_SHEET_TITLE)
        data_names = names[:-1]
        self.assertEqual(len(data_names), 3)
        self.assertEqual(len(set(data_names)), 3)
        self.assertTrue(all(len(name) <= 12 for name in data_names))
        # Sheet order follows the databook order.
        self.assertEqual(
            [workbook[name]["A1"].value for name in data_names],
            ["id", "id", "id"],
        )
        legend = workbook[LEGEND_SHEET_TITLE]
        self.assertEqual(
            [cell.value for cell in legend[1]],
            [LEGEND_SHORT_HEADER, LEGEND_ORIGINAL_HEADER],
        )
        legend_originals = [row[1].value for row in legend.iter_rows(min_row=2)]
        self.assertEqual(legend_originals, self.titles)

    def test_legend_sheet_name_collision_is_disambiguated(self):
        dataset = tablib.Dataset(headers=("a",), title=LEGEND_SHEET_TITLE)
        dataset.append((1,))
        book = tablib.Databook((dataset,))
        blob = book.export("xlsx", sheet_name_legend=True)
        names = load_workbook(BytesIO(blob)).sheetnames
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)
        self.assertIn(LEGEND_SHEET_TITLE, names)

    def test_round_trip_restores_original_titles_and_data(self):
        blob = self.book.export("xlsx", sheet_name_legend=True)
        loaded = tablib.Databook().load(BytesIO(blob), "xlsx")
        self.assertEqual(
            [dataset.title for dataset in loaded.sheets()], self.titles
        )
        self.assertEqual(loaded.sheets()[0][0], (0, "row 0"))
        result = loaded.sheet_title_result
        self.assertTrue(result.ok)
        self.assertEqual(result.unmatched, [])
        self.assertEqual(result.missing_titles, [])

    def test_titles_longer_than_excel_limit_are_recoverable(self):
        long_title = "x" * 100
        book = tablib.Databook()
        dataset = tablib.Dataset(headers=("id",), title=long_title)
        dataset.append((1,))
        book.add_sheet(dataset)
        blob = book.export("xlsx", sheet_name_legend=True)
        loaded = tablib.Databook().load(BytesIO(blob), "xlsx")
        self.assertEqual(loaded.sheets()[0].title, long_title)

    def test_default_export_has_no_legend(self):
        blob = self.book.export("xlsx")
        workbook = load_workbook(BytesIO(blob))
        self.assertNotIn(LEGEND_SHEET_TITLE, workbook.sheetnames)
        loaded = tablib.Databook().load(BytesIO(blob), "xlsx")
        self.assertIsNone(loaded.sheet_title_result)

    def test_renamed_sheet_is_reported_not_forced_to_a_title(self):
        blob = self.book.export("xlsx", sheet_name_legend=True)
        workbook = load_workbook(BytesIO(blob))
        original_names = workbook.sheetnames[:-1]
        workbook[original_names[0]].title = "renamed_by_human"
        buffer = BytesIO()
        workbook.save(buffer)

        loaded = tablib.Databook().load(BytesIO(buffer.getvalue()), "xlsx")
        result = loaded.sheet_title_result
        self.assertFalse(result.ok)
        self.assertEqual(
            [match.short_title for match in result.unmatched],
            ["renamed_by_human"],
        )
        # The renamed dataset keeps the sheet title; no original title is guessed.
        self.assertEqual(loaded.sheets()[0].title, "renamed_by_human")
        # The untouched sheets are still restored to their original titles.
        self.assertEqual(loaded.sheets()[1].title, self.titles[1])

        with self.assertRaises(SheetTitleLookupError):
            tablib.Databook().load(
                BytesIO(buffer.getvalue()), "xlsx", strict=True
            )

    def test_deleted_sheet_is_reported_as_missing(self):
        blob = self.book.export("xlsx", sheet_name_legend=True)
        workbook = load_workbook(BytesIO(blob))
        deleted_short = workbook.sheetnames[1]
        del workbook[deleted_short]
        buffer = BytesIO()
        workbook.save(buffer)

        loaded = tablib.Databook().load(BytesIO(buffer.getvalue()), "xlsx")
        result = loaded.sheet_title_result
        self.assertIn(deleted_short, result.missing_titles)
        self.assertFalse(result.ok)
        with self.assertRaises(SheetTitleLookupError):
            tablib.Databook().load(
                BytesIO(buffer.getvalue()), "xlsx", strict=True
            )

    def test_reordered_sheets_still_match_by_name(self):
        blob = self.book.export("xlsx", sheet_name_legend=True)
        workbook = load_workbook(BytesIO(blob))
        workbook.move_sheet(workbook.worksheets[0], offset=2)
        buffer = BytesIO()
        workbook.save(buffer)

        loaded = tablib.Databook().load(BytesIO(buffer.getvalue()), "xlsx")
        self.assertTrue(loaded.sheet_title_result.ok)
        self.assertEqual(
            {dataset.title for dataset in loaded.sheets()}, set(self.titles)
        )

    def test_legend_found_even_when_its_sheet_is_renamed_or_moved(self):
        blob = self.book.export("xlsx", sheet_name_legend=True)
        workbook = load_workbook(BytesIO(blob))
        workbook[LEGEND_SHEET_TITLE].title = "renamed legend"
        workbook.move_sheet(workbook["renamed legend"], offset=-3)
        buffer = BytesIO()
        workbook.save(buffer)

        loaded = tablib.Databook().load(BytesIO(buffer.getvalue()), "xlsx")
        self.assertTrue(loaded.sheet_title_result.ok)
        self.assertEqual(
            [dataset.title for dataset in loaded.sheets()], self.titles
        )

    def test_deleted_legend_falls_back_to_plain_import(self):
        blob = self.book.export("xlsx", sheet_name_legend=True)
        workbook = load_workbook(BytesIO(blob))
        del workbook[LEGEND_SHEET_TITLE]
        buffer = BytesIO()
        workbook.save(buffer)

        loaded = tablib.Databook().load(BytesIO(buffer.getvalue()), "xlsx")
        self.assertIsNone(loaded.sheet_title_result)
        self.assertEqual(loaded.size, 3)

    def test_corrupted_legend_raises_instead_of_silent_fallback(self):
        blob = self.book.export("xlsx", sheet_name_legend=True)
        workbook = load_workbook(BytesIO(blob))
        workbook[LEGEND_SHEET_TITLE]["A1"] = "broken"
        buffer = BytesIO()
        workbook.save(buffer)

        with self.assertRaises(SheetTitleError):
            tablib.Databook().load(BytesIO(buffer.getvalue()), "xlsx")

    def test_load_resets_stale_sheet_title_result(self):
        blob = self.book.export("xlsx", sheet_name_legend=True)
        loaded = tablib.Databook().load(BytesIO(blob), "xlsx")
        self.assertIsNotNone(loaded.sheet_title_result)
        plain_blob = self.book.export("xlsx")
        loaded.load(BytesIO(plain_blob), "xlsx")
        self.assertIsNone(loaded.sheet_title_result)


if __name__ == "__main__":
    unittest.main()
