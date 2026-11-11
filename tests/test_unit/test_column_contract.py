# -*- coding: utf-8 -*-

"""
Unit tests for optional column contracts in dialect detection.

Author: Gertjan van den Burg

"""

import os
import tempfile
import unittest

from clevercsv import ColumnContract
from clevercsv import ColumnContractResult
from clevercsv import Detector
from clevercsv import detect_dialect
from clevercsv.column_contract import evaluate_column_contracts
from clevercsv.consistency import detect_dialect_consistency
from clevercsv.cparser_util import parse_string
from clevercsv.detect_type import TypeDetector
from clevercsv.dialect import SimpleDialect
from clevercsv.potential_dialects import get_dialects


class ColumnContractTestCase(unittest.TestCase):
    # Both candidate delimiters split every row into three columns, but only
    # the comma dialect exposes valid dates in the middle column.
    sample = (
        "a;b,2023-01-05,c;d\n"
        "e;f,2023-02-06,g;h\n"
        "i;j,2023-03-07,k;l\n"
    )

    def setUp(self) -> None:
        self.dialects = get_dialects(
            self.sample, delimiters=[",", ";"]
        )

    def test_contract_validation(self) -> None:
        with self.assertRaises(ValueError):
            ColumnContract(index=-1)
        with self.assertRaises(ValueError):
            ColumnContract(index=0, value_type="not_a_type")
        contract = ColumnContract(
            index=0, allowed_values=["a", "b", "a"]
        )
        self.assertEqual(contract.allowed_values, frozenset(["a", "b"]))

    def test_matched_unique(self) -> None:
        contracts = [
            ColumnContract(
                index=1, name="when", required=True, value_type="date"
            )
        ]
        report = evaluate_column_contracts(
            self.sample, self.dialects, contracts
        )
        self.assertEqual(report.result, ColumnContractResult.MATCHED)
        self.assertEqual(
            report.matched, SimpleDialect(",", "", "")
        )
        comma_outcomes = report.details[SimpleDialect(",", "", "")]
        self.assertEqual(comma_outcomes[0].status, "matches")
        self.assertEqual(comma_outcomes[0].observed, 3)
        self.assertEqual(comma_outcomes[0].valid, 3)

    def test_no_match(self) -> None:
        contracts = [
            ColumnContract(index=1, required=True, value_type="number")
        ]
        report = evaluate_column_contracts(
            self.sample, self.dialects, contracts
        )
        self.assertEqual(report.result, ColumnContractResult.NO_MATCH)
        self.assertIsNone(report.matched)
        self.assertEqual(report.satisfying_candidates, [])
        semicolon = report.details[SimpleDialect(";", "", "")][0]
        self.assertEqual(semicolon.status, "mismatch")

    def test_required_column_missing(self) -> None:
        contracts = [ColumnContract(index=5, required=True)]
        report = evaluate_column_contracts(
            self.sample, self.dialects, contracts
        )
        self.assertEqual(
            report.result, ColumnContractResult.REQUIRED_MISSING
        )
        self.assertIsNone(report.matched)
        for outcomes in report.details.values():
            self.assertEqual(outcomes[0].status, "missing")

    def test_optional_missing_column_is_vacuous(self) -> None:
        # The one-column dialect lacks column 2; because the contract is
        # optional the absence is vacuously satisfied, while the three-column
        # dialects fail the date check on that column.
        contracts = [
            ColumnContract(index=2, required=False, value_type="date")
        ]
        report = evaluate_column_contracts(
            self.sample, self.dialects, contracts
        )
        self.assertEqual(report.result, ColumnContractResult.MATCHED)
        self.assertEqual(report.matched, SimpleDialect("", "", ""))

    def test_optional_column_missing_everywhere_is_insufficient(self) -> None:
        contracts = [ColumnContract(index=9, required=False)]
        report = evaluate_column_contracts(
            self.sample, self.dialects, contracts
        )
        self.assertEqual(
            report.result, ColumnContractResult.INSUFFICIENT_SAMPLE
        )

    def test_insufficient_sample_empty_cells(self) -> None:
        data = "a,,c\nb,,d\n"
        dialects = get_dialects(data, delimiters=[","])
        contracts = [
            ColumnContract(index=1, required=True, value_type="date")
        ]
        report = evaluate_column_contracts(data, dialects, contracts)
        self.assertEqual(
            report.result, ColumnContractResult.INSUFFICIENT_SAMPLE
        )

    def test_insufficient_sample_empty_data(self) -> None:
        contracts = [ColumnContract(index=0, required=True)]
        report = evaluate_column_contracts(
            "", [SimpleDialect(",", "", "")], contracts
        )
        self.assertEqual(
            report.result, ColumnContractResult.INSUFFICIENT_SAMPLE
        )

    def test_tie_abstains(self) -> None:
        data = "a,b\nc,d\ne,f\n"
        dialects = get_dialects(data, delimiters=[",", ";"])
        contracts = [ColumnContract(index=0, required=True)]
        report = evaluate_column_contracts(data, dialects, contracts)
        self.assertEqual(report.result, ColumnContractResult.TIE)
        self.assertIsNone(report.matched)
        self.assertGreaterEqual(len(report.satisfying_candidates), 2)

    def test_allowed_values(self) -> None:
        contracts = [
            ColumnContract(
                index=2,
                required=True,
                allowed_values=("c;d", "g;h", "k;l"),
            )
        ]
        report = evaluate_column_contracts(
            self.sample, self.dialects, contracts
        )
        self.assertEqual(report.result, ColumnContractResult.MATCHED)
        self.assertEqual(report.matched, SimpleDialect(",", "", ""))

    def test_allowed_values_rejects_unknown(self) -> None:
        contracts = [
            ColumnContract(index=2, required=True, allowed_values=("x",))
        ]
        report = evaluate_column_contracts(
            self.sample, self.dialects, contracts
        )
        self.assertEqual(report.result, ColumnContractResult.NO_MATCH)

    def test_type_and_allowed_values_combine(self) -> None:
        contract = ColumnContract(
            index=1,
            required=True,
            value_type="date",
            allowed_values=("2023-01-05",),
        )
        report = evaluate_column_contracts(
            self.sample, self.dialects, [contract]
        )
        # Only one of the three comma-dialect dates is in the allowed set.
        self.assertEqual(report.result, ColumnContractResult.NO_MATCH)

    def test_empty_cells_are_ignored(self) -> None:
        data = "a,2023-01-05\nb,\nc,2023-03-07\n"
        dialects = get_dialects(data, delimiters=[","])
        contracts = [
            ColumnContract(index=1, required=True, value_type="date")
        ]
        report = evaluate_column_contracts(data, dialects, contracts)
        self.assertEqual(report.result, ColumnContractResult.MATCHED)
        outcome = report.details[SimpleDialect(",", "", "")][0]
        self.assertEqual(outcome.observed, 2)
        self.assertEqual(outcome.valid, 2)

    def test_ragged_rows_use_modal_width(self) -> None:
        data = "a,2023-01-05,x\nb,2023-02-06\nc,2023-03-07,z\n"
        dialects = get_dialects(data, delimiters=[","])
        contracts = [ColumnContract(index=2, required=True)]
        report = evaluate_column_contracts(data, dialects, contracts)
        self.assertEqual(report.result, ColumnContractResult.MATCHED)
        outcome = report.details[SimpleDialect(",", "", "")][0]
        self.assertEqual(outcome.status, "matches")

    def test_contracts_do_not_mutate_cells(self) -> None:
        contracts = [
            ColumnContract(index=1, required=True, value_type="date")
        ]
        evaluate_column_contracts(
            self.sample, self.dialects, contracts
        )
        rows = [list(row) for row in parse_string(
            self.sample, SimpleDialect(",", "", "")
        )]
        self.assertEqual(
            rows,
            [
                ["a;b", "2023-01-05", "c;d"],
                ["e;f", "2023-02-06", "g;h"],
                ["i;j", "2023-03-07", "k;l"],
            ],
        )

    def test_empty_contract_sequence_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_column_contracts(
                self.sample, self.dialects, []
            )

    def test_known_type_detector_used(self) -> None:
        # Sanity check for the motivating example itself.
        type_detector = TypeDetector()
        self.assertTrue(type_detector.is_date("2023-01-05"))
        self.assertFalse(type_detector.is_date("b"))


class DetectorIntegrationTestCase(unittest.TestCase):
    sample = ColumnContractTestCase.sample

    def test_without_contracts_unchanged(self) -> None:
        detector = Detector()
        dialect = detector.detect(
            self.sample, delimiters=[",", ";"]
        )
        self.assertEqual(dialect, SimpleDialect(";", "", ""))
        self.assertIsNone(detector.column_contract_report_)

    def test_contract_selects_other_dialect(self) -> None:
        detector = Detector()
        contracts = [
            ColumnContract(index=1, required=True, value_type="date")
        ]
        dialect = detector.detect(
            self.sample,
            delimiters=[",", ";"],
            column_contracts=contracts,
            method="consistency",
        )
        self.assertEqual(dialect, SimpleDialect(",", "", ""))
        report = detector.column_contract_report_
        self.assertEqual(report.result, ColumnContractResult.MATCHED)

    def test_no_match_returns_none(self) -> None:
        detector = Detector()
        contracts = [
            ColumnContract(index=1, required=True, value_type="number")
        ]
        dialect = detector.detect(
            self.sample,
            delimiters=[",", ";"],
            column_contracts=contracts,
            method="consistency",
        )
        self.assertIsNone(dialect)
        self.assertEqual(
            detector.column_contract_report_.result,
            ColumnContractResult.NO_MATCH,
        )

    def test_tie_returns_none(self) -> None:
        detector = Detector()
        dialect = detector.detect(
            "a,b\nc,d\ne,f\n",
            column_contracts=[ColumnContract(index=0, required=True)],
            method="consistency",
        )
        self.assertIsNone(dialect)
        self.assertEqual(
            detector.column_contract_report_.result,
            ColumnContractResult.TIE,
        )

    def test_insufficient_sample_returns_none(self) -> None:
        detector = Detector()
        dialect = detector.detect(
            "a,,c\nb,,d\n",
            delimiters=[","],
            column_contracts=[
                ColumnContract(index=1, required=True, value_type="date")
            ],
            method="consistency",
        )
        self.assertIsNone(dialect)
        self.assertEqual(
            detector.column_contract_report_.result,
            ColumnContractResult.INSUFFICIENT_SAMPLE,
        )

    def test_required_missing_returns_none(self) -> None:
        detector = Detector()
        dialect = detector.detect(
            self.sample,
            delimiters=[",", ";"],
            column_contracts=[ColumnContract(index=5, required=True)],
            method="consistency",
        )
        self.assertIsNone(dialect)
        self.assertEqual(
            detector.column_contract_report_.result,
            ColumnContractResult.REQUIRED_MISSING,
        )

    def test_normal_form_accepted_by_contract(self) -> None:
        data = "1,2,3\n4,5,6\n7,8,9\n"
        detector = Detector()
        dialect = detector.detect(
            data,
            column_contracts=[
                ColumnContract(index=0, required=True, value_type="number")
            ],
        )
        self.assertEqual(dialect, SimpleDialect(",", "", ""))
        self.assertEqual(
            detector.column_contract_report_.result,
            ColumnContractResult.MATCHED,
        )

    def test_normal_form_rejected_falls_through(self) -> None:
        data = "1,2,3\n4,5,6\n7,8,9\n"
        detector = Detector()
        dialect = detector.detect(
            data,
            column_contracts=[
                ColumnContract(index=1, required=True, value_type="date")
            ],
        )
        self.assertIsNone(dialect)
        self.assertEqual(
            detector.column_contract_report_.result,
            ColumnContractResult.NO_MATCH,
        )

    def test_normal_only_method_abstains(self) -> None:
        data = "1,2,3\n4,5,6\n7,8,9\n"
        detector = Detector()
        dialect = detector.detect(
            data,
            method="normal",
            column_contracts=[
                ColumnContract(index=1, required=True, value_type="date")
            ],
        )
        self.assertIsNone(dialect)

    def test_consistency_helper_accepts_contracts(self) -> None:
        contracts = [
            ColumnContract(index=1, required=True, value_type="date")
        ]
        dialect = detect_dialect_consistency(
            self.sample,
            delimiters=[",", ";"],
            column_contracts=contracts,
        )
        self.assertEqual(dialect, SimpleDialect(",", "", ""))

    def test_wrapper_accepts_contracts(self) -> None:
        contracts = [
            ColumnContract(index=1, required=True, value_type="date")
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sample.csv")
            with open(path, "w", newline="", encoding="utf-8") as fid:
                fid.write(self.sample)
            dialect = detect_dialect(
                path,
                method="consistency",
                column_contracts=contracts,
            )
        self.assertEqual(dialect, SimpleDialect(",", "", ""))


if __name__ == "__main__":
    unittest.main()
