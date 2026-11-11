# -*- coding: utf-8 -*-

"""
Optional column contracts for dialect detection.

A column contract captures what the caller expects from a single column of
the parsed table: a name (used only for reporting), whether the column is
required, and an optional bounded test on its values.

Contracts are assertions, not transformations. They are used solely to select
or report candidate dialects: the parsed cells are never rewritten.

Author: Gertjan van den Burg

"""

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple

from .cparser_util import parse_string
from .detect_type import TypeDetector
from .dialect import SimpleDialect


class ColumnContractResult(str, Enum):
    """Outcome of evaluating column contracts against candidate dialects.

    Valid options are:

    - ``"matched"``: exactly one candidate satisfies all contracts.
    - ``"tie"``: several candidates satisfy the contracts, so the contracts
      can't distinguish them. No dialect is returned.
    - ``"no_match"``: the sample contains evidence, but no candidate
      satisfies the contracts.
    - ``"required_missing"``: every candidate is missing a column marked as
      required.
    - ``"insufficient_sample"``: the sample is empty or provides no values
      for the contracted columns, so nothing can be asserted.

    """

    MATCHED = "matched"
    TIE = "tie"
    NO_MATCH = "no_match"
    REQUIRED_MISSING = "required_missing"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


# Status of a single contract for a single candidate dialect.
STATUS_MATCHES = "matches"
STATUS_MISMATCH = "mismatch"
STATUS_MISSING = "missing"
STATUS_UNOBSERVED = "unobserved"


@dataclass(frozen=True)
class ColumnContract:
    """An expectation about one column of the parsed table.

    Parameters
    ----------
    index : int
        Zero-based position of the column in rows parsed with a candidate
        dialect. Positions, rather than names, identify columns because
        dialects only define how cells are laid out.

    name : Optional[str]
        Human-readable label for the column, used in reports only. It is not
        matched against a header row.

    required : bool
        Whether the column must be present for a candidate to be acceptable.
        Optional columns that are absent are satisfied vacuously.

    value_type : Optional[str]
        Name of a known type from
        :meth:`clevercsv.detect_type.TypeDetector.list_known_types` (for
        example ``"date"`` or ``"number"``). When set, every observed
        non-empty cell in the column must be detected as this type.

    allowed_values : Optional[Sequence[str]]
        Optional closed set of acceptable (whitespace-stripped) cell values.
        When set, every observed non-empty cell must be in the set. Can be
        combined with ``value_type``, in which case both conditions apply.

    """

    index: int
    name: Optional[str] = None
    required: bool = False
    value_type: Optional[str] = None
    allowed_values: Optional[Sequence[str]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or self.index < 0:
            raise ValueError(
                "Column contract index must be a non-negative integer, "
                "got: %r" % self.index
            )
        if self.value_type is not None:
            known = TypeDetector().list_known_types()
            if self.value_type not in known:
                raise ValueError(
                    "Unknown value type %r, choose one of %s"
                    % (self.value_type, ", ".join(known))
                )
        if self.allowed_values is not None:
            object.__setattr__(
                self, "allowed_values", frozenset(self.allowed_values)
            )


@dataclass
class ColumnContractOutcome:
    """Result of one contract evaluated against one candidate dialect."""

    index: int
    name: Optional[str]
    required: bool
    status: str
    observed: int
    valid: int

    @property
    def satisfied(self) -> bool:
        """Whether the contract holds for this candidate."""
        if self.status == STATUS_MISSING:
            return not self.required
        return self.status != STATUS_MISMATCH


@dataclass
class ColumnContractReport:
    """Full report of contract evaluation across the candidate dialects.

    Parameters
    ----------
    result : ColumnContractResult
        The overall result.

    contracts : List[ColumnContract]
        The contracts that were evaluated.

    details : Dict[SimpleDialect, List[ColumnContractOutcome]]
        The outcome of each contract for every candidate dialect.

    matched : Optional[SimpleDialect]
        The unique satisfying dialect, or None when the result is not
        :attr:`ColumnContractResult.MATCHED`.

    """

    result: ColumnContractResult
    contracts: List[ColumnContract]
    details: Dict[SimpleDialect, List[ColumnContractOutcome]]
    matched: Optional[SimpleDialect] = None

    @property
    def candidates(self) -> List[SimpleDialect]:
        """The candidate dialects that were evaluated."""
        return list(self.details.keys())

    @property
    def satisfying_candidates(self) -> List[SimpleDialect]:
        """Candidates for which every contract is satisfied.

        When :attr:`result` is :attr:`ColumnContractResult.TIE`, this holds
        the tied candidates the caller may want to inspect manually or
        disambiguate with a larger sample.
        """
        return [
            dialect
            for dialect, outcomes in self.details.items()
            if all(outcome.satisfied for outcome in outcomes)
        ]


def _modal_width(rows: List[List[Tuple[str, bool]]]) -> int:
    if not rows:
        return 0
    counts: Counter[int] = Counter(len(row) for row in rows)
    # Most common row length; ties are resolved toward the smallest width,
    # so a required column is only considered present when most rows have it.
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _check_cell(
    cell: str,
    contract: ColumnContract,
    type_detector: TypeDetector,
) -> Optional[bool]:
    """Check a single cell.

    Returns True/False for observed cells, or None for empty cells, which
    carry no evidence and are skipped.
    """
    cell = cell.strip()
    if cell == "":
        return None
    if (
        contract.allowed_values is not None
        and cell not in contract.allowed_values
    ):
        return False
    if contract.value_type is not None:
        if type_detector.detect_type(cell) != contract.value_type:
            return False
    return True


def _evaluate_contract(
    rows: List[List[Tuple[str, bool]]],
    width: int,
    contract: ColumnContract,
    type_detector: TypeDetector,
) -> ColumnContractOutcome:
    if contract.index >= width:
        return ColumnContractOutcome(
            index=contract.index,
            name=contract.name,
            required=contract.required,
            status=STATUS_MISSING,
            observed=0,
            valid=0,
        )

    observed = 0
    valid = 0
    status = STATUS_MATCHES
    for row in rows:
        if contract.index >= len(row):
            continue
        cell, _is_quoted = row[contract.index]
        verdict = _check_cell(cell, contract, type_detector)
        if verdict is None:
            continue
        observed += 1
        if verdict:
            valid += 1
        else:
            status = STATUS_MISMATCH

    if observed == 0:
        status = STATUS_UNOBSERVED

    return ColumnContractOutcome(
        index=contract.index,
        name=contract.name,
        required=contract.required,
        status=status,
        observed=observed,
        valid=valid,
    )


def _has_required_missing(outcomes: List[ColumnContractOutcome]) -> bool:
    return any(
        outcome.required and outcome.status == STATUS_MISSING
        for outcome in outcomes
    )


def evaluate_column_contracts(
    data: str,
    dialects: List[SimpleDialect],
    contracts: Sequence[ColumnContract],
    type_detector: Optional[TypeDetector] = None,
) -> ColumnContractReport:
    """Evaluate column contracts against candidate dialects.

    Each candidate dialect is used to parse the sample and every contract is
    checked against the parsed rows. Contracts only select (or report on)
    candidates; they never modify parsed cells.

    Parameters
    ----------
    data : str
        The CSV sample as a single string.

    dialects : List[SimpleDialect]
        The candidate dialects to evaluate.

    contracts : Sequence[ColumnContract]
        Non-empty sequence of column contracts.

    type_detector : Optional[TypeDetector]
        Type detector used for ``value_type`` checks. A default one is
        created when omitted.

    Returns
    -------
    report : ColumnContractReport
        A report with the overall result, per-candidate details and the
        unique matching dialect when the result is
        :attr:`ColumnContractResult.MATCHED`.

    """
    contracts = list(contracts)
    if not contracts:
        raise ValueError("At least one column contract is required.")

    if type_detector is None:
        type_detector = TypeDetector()
    known_types = type_detector.list_known_types()
    for contract in contracts:
        if (
            contract.value_type is not None
            and contract.value_type not in known_types
        ):
            raise ValueError(
                "Unknown value type %r, choose one of %s"
                % (contract.value_type, ", ".join(known_types))
            )

    details: Dict[SimpleDialect, List[ColumnContractOutcome]] = {}
    any_rows = False
    any_observations = False

    for dialect in dialects:
        rows = list(parse_string(data, dialect, return_quoted=True))
        if rows:
            any_rows = True
        width = _modal_width(rows)
        outcomes = [
            _evaluate_contract(rows, width, contract, type_detector)
            for contract in contracts
        ]
        details[dialect] = outcomes
        if any(outcome.observed > 0 for outcome in outcomes):
            any_observations = True

    matches: List[SimpleDialect] = []
    if not any_rows:
        result = ColumnContractResult.INSUFFICIENT_SAMPLE
    elif dialects and all(
        _has_required_missing(outcomes) for outcomes in details.values()
    ):
        result = ColumnContractResult.REQUIRED_MISSING
    elif not any_observations:
        result = ColumnContractResult.INSUFFICIENT_SAMPLE
    else:
        matches = [
            dialect
            for dialect, outcomes in details.items()
            if all(outcome.satisfied for outcome in outcomes)
        ]
        if not matches:
            result = ColumnContractResult.NO_MATCH
        elif len(matches) == 1:
            result = ColumnContractResult.MATCHED
        else:
            result = ColumnContractResult.TIE

    matched = matches[0] if result == ColumnContractResult.MATCHED else None
    return ColumnContractReport(
        result=result,
        contracts=contracts,
        details=details,
        matched=matched,
    )
