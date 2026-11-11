# -*- coding: utf-8 -*-

"""
Regression tests for dialect detection under small structural edits.

The starting point is a small, unambiguous table written with a known
dialect. Three minimal structural changes are then applied, one at a time:

1. an empty trailing column on every row;
2. a fixed text column whose values are not "elementary" (they contain a
   ``#`` character);
3. a quoted remark column whose values contain the delimiter inside quotes.

For every version we check

* the rows that are actually recoverable with the true dialect and with the
  detected dialect, so the before/after row reconstructions can be compared;
* whether the detected dialect changes, and if so whether the change happens
  in the fast normal-form check or in the full consistency scoring;
* whether another *legal* reading exists, i.e. a candidate dialect that still
  parses the sample into a rectangular table with more than one column.

The point is not to force every edit to keep the same winner. In fact the
fixed text column pushes detection out of the fast path (the normal form no
longer matches because of the non-elementary ``#`` cell) into the full
consistency scoring, while the winner itself stays the same. None of the
edits introduces a second legal rectangular reading.

Author: Gertjan van den Burg

"""

import unittest

from io import StringIO

from typing import List
from typing import Tuple

from clevercsv.cparser_util import parse_string
from clevercsv.detect import DetectionMethod
from clevercsv.detect import Detector
from clevercsv.dialect import SimpleDialect
from clevercsv.normal_form import detect_dialect_normal
from clevercsv.potential_dialects import get_dialects
from clevercsv.read import reader


# The known dialect of every table below.
TRUE_DIALECT = SimpleDialect(delimiter=",", quotechar="", escapechar="")
QUOTED_DIALECT = SimpleDialect(delimiter=",", quotechar='"', escapechar="")


class StructuralEditTestCase(unittest.TestCase):
    # An unambiguous comma-separated table: three columns, four rows, all
    # cells elementary and unquoted. It matches normal form 2.
    baseline = (
        "name,age,city\n"
        "Alice,30,Berlin\n"
        "Bob,25,Madrid\n"
        "Cara,41,Oslo\n"
    )
    baseline_rows = [
        ["name", "age", "city"],
        ["Alice", "30", "Berlin"],
        ["Bob", "25", "Madrid"],
        ["Cara", "41", "Oslo"],
    ]

    # Edit 1: an empty trailing column. Still normal form 2 (empty unquoted
    # cells are allowed), so the fast path keeps winning.
    empty_tail = (
        "name,age,city,\n"
        "Alice,30,Berlin,\n"
        "Bob,25,Madrid,\n"
        "Cara,41,Oslo,\n"
    )
    empty_tail_rows = [
        ["name", "age", "city", ""],
        ["Alice", "30", "Berlin", ""],
        ["Bob", "25", "Madrid", ""],
        ["Cara", "41", "Oslo", ""],
    ]

    # Edit 2: a fixed text column. The constant value "#active" is not
    # elementary because of the "#", which disqualifies every normal form, so
    # detection has to rely on the full consistency measure.
    fixed_text = (
        "name,age,city,tag\n"
        "Alice,30,Berlin,#active\n"
        "Bob,25,Madrid,#active\n"
        "Cara,41,Oslo,#active\n"
    )
    fixed_text_rows = [
        ["name", "age", "city", "tag"],
        ["Alice", "30", "Berlin", "#active"],
        ["Bob", "25", "Madrid", "#active"],
        ["Cara", "41", "Oslo", "#active"],
    ]

    # Edit 3: a quoted remark column with the delimiter embedded in the
    # quoted text. Matches normal form 3 (mixed quoted/unquoted cells).
    quoted_note = (
        "name,age,city,note\n"
        'Alice,30,Berlin,"hello, world"\n'
        'Bob,25,Madrid,"see, you"\n'
        'Cara,41,Oslo,"fine, thanks"\n'
    )
    quoted_note_rows = [
        ["name", "age", "city", "note"],
        ["Alice", "30", "Berlin", "hello, world"],
        ["Bob", "25", "Madrid", "see, you"],
        ["Cara", "41", "Oslo", "fine, thanks"],
    ]

    def _recover(self, sample: str, dialect: SimpleDialect) -> List[List[str]]:
        return list(reader(StringIO(sample), dialect))

    def _legal_readings(
        self, sample: str
    ) -> List[Tuple[SimpleDialect, int]]:
        """Candidate dialects yielding a rectangular multi-column table."""
        readings: List[Tuple[SimpleDialect, int]] = []
        for dialect in get_dialects(sample):
            try:
                rows = list(parse_string(sample, dialect))
            except Exception:
                continue
            if not rows or len(rows[0]) <= 1:
                continue
            width = len(rows[0])
            if all(len(row) == width for row in rows):
                readings.append((dialect, width))
        return readings

    def _detect(self, sample: str) -> Tuple[SimpleDialect, DetectionMethod]:
        detector = Detector()
        dialect = detector.detect(sample)
        self.assertIsNotNone(dialect)
        assert dialect is not None
        return dialect, detector.method_

    def test_baseline_is_unambiguous(self) -> None:
        dialect, method = self._detect(self.baseline)
        self.assertEqual(dialect, TRUE_DIALECT)
        self.assertEqual(method, DetectionMethod.NORMAL)
        self.assertIsNotNone(detect_dialect_normal(self.baseline))
        # The known dialect fully reconstructs the intended table.
        self.assertEqual(
            self._recover(self.baseline, TRUE_DIALECT), self.baseline_rows
        )
        # There is exactly one legal reading and it is the true dialect.
        self.assertEqual(
            self._legal_readings(self.baseline), [(TRUE_DIALECT, 3)]
        )

    def test_empty_trailing_column_keeps_fast_path_winner(self) -> None:
        before = self._detect(self.baseline)
        dialect, method = self._detect(self.empty_tail)

        # The winner does not change and is still found on the fast path.
        self.assertEqual(dialect, before[0])
        self.assertEqual(dialect, TRUE_DIALECT)
        self.assertEqual(method, DetectionMethod.NORMAL)
        self.assertIsNotNone(detect_dialect_normal(self.empty_tail))

        # Row reconstruction before/after: the same rows gain one empty cell.
        self.assertEqual(
            self._recover(self.empty_tail, dialect), self.empty_tail_rows
        )
        for base_row, edited_row in zip(
            self.baseline_rows, self.empty_tail_rows
        ):
            self.assertEqual(edited_row[:-1], base_row)
            self.assertEqual(edited_row[-1], "")

        # No alternative legal reading is introduced.
        self.assertEqual(
            self._legal_readings(self.empty_tail), [(TRUE_DIALECT, 4)]
        )

    def test_fixed_text_column_falls_back_to_full_scoring(self) -> None:
        before = self._detect(self.baseline)
        dialect, method = self._detect(self.fixed_text)

        # The fast normal-form check now fails because "#active" is not
        # elementary, so the change registers in the full consistency scoring
        # rather than in the quick judgement.
        self.assertIsNone(detect_dialect_normal(self.fixed_text))
        self.assertEqual(method, DetectionMethod.CONSISTENCY)

        # The winner itself does not change: full scoring still picks comma.
        self.assertEqual(dialect, before[0])
        self.assertEqual(dialect, TRUE_DIALECT)

        # The intended four-column table is still fully recoverable.
        self.assertEqual(
            self._recover(self.fixed_text, dialect), self.fixed_text_rows
        )

        # The "#" candidate is a tempting but illegal reading: it produces
        # ragged rows, so the comma reading is the only rectangular one.
        readings = self._legal_readings(self.fixed_text)
        self.assertEqual(readings, [(TRUE_DIALECT, 4)])
        hash_dialect = SimpleDialect(
            delimiter="#", quotechar="", escapechar=""
        )
        hash_rows = list(parse_string(self.fixed_text, hash_dialect))
        self.assertEqual(len({len(row) for row in hash_rows}), 2)

    def test_quoted_remark_with_delimiter_stays_on_fast_path(self) -> None:
        before = self._detect(self.baseline)
        dialect, method = self._detect(self.quoted_note)

        # The fast path still resolves the sample, but now through the mixed
        # quoted/unquoted normal form, so the quotechar becomes '"'. This is
        # an expected winner refinement, not a forced hold-out.
        self.assertEqual(method, DetectionMethod.NORMAL)
        self.assertIsNotNone(detect_dialect_normal(self.quoted_note))
        self.assertEqual(dialect, QUOTED_DIALECT)

        # The delimiter is unchanged but the quote character is refined from
        # no quote to '"'; this winner is not forced to equal the baseline.
        self.assertEqual(dialect.delimiter, before[0].delimiter)
        self.assertNotEqual(dialect, before[0])

        # The delimiter inside the quotes must not split a cell: all four
        # rows are reconstructed with the remark as a single field.
        self.assertEqual(
            self._recover(self.quoted_note, dialect), self.quoted_note_rows
        )
        self.assertTrue(
            all("," in row[-1] for row in self.quoted_note_rows[1:])
        )

        # Dropping the quotechar is an illegal alternative reading because
        # the embedded commas then make the data rows five columns wide
        # instead of four. Only the quoted dialect reads rectangularly.
        readings = self._legal_readings(self.quoted_note)
        self.assertEqual(readings, [(QUOTED_DIALECT, 4)])
        unquoted = SimpleDialect(delimiter=",", quotechar="", escapechar="")
        unquoted_rows = list(parse_string(self.quoted_note, unquoted))
        self.assertEqual(len(unquoted_rows[0]), 4)
        self.assertTrue(
            any(len(row) != 4 for row in unquoted_rows[1:])
        )


if __name__ == "__main__":
    unittest.main()
