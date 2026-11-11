"""Streaming check that order IDs are strictly increasing within each group.

Expected response shape (extra fields are ignored)::

    {
      "groups": [
        {"id": <group id>, "orders": [{"id": <order id>, ...}, ...]},
        ...
      ]
    }

The caller declares, via ``id_kind``, how IDs inside a group must be
represented:

- ``"number"``: every order ID must be a JSON number; anything else is
  reported as an ``InvalidId`` violation.
- ``"numeric_string"``: every order ID must be a string holding an integer
  (e.g. ``"1024"``); IDs are compared by numeric value, anything else is
  reported as an ``InvalidId`` violation.

Violations are yielded as soon as they are detected while the stream is
being parsed, so callers can react before the whole response is consumed.
A ``CheckComplete`` event is emitted only when the stream was parsed to
the very end without errors; a truncated stream yields ``StreamTruncated``
instead, so a partial file is never reported as fully verified.

Records without an ``id`` field (or with ``"id": null``) are reported as
``MissingId`` and are excluded from the ordering comparison -- a missing
ID is never treated as zero.
"""

import re
import sys
from dataclasses import dataclass
from typing import Any, BinaryIO, List, Optional, Union

import ijson

ID_KIND_NUMBER = "number"
ID_KIND_NUMERIC_STRING = "numeric_string"
ID_KINDS = (ID_KIND_NUMBER, ID_KIND_NUMERIC_STRING)

_NUMERIC_STRING_RE = re.compile(r"[+-]?\d+")


@dataclass(frozen=True)
class OrderDecrease:
    """Two adjacent comparable records violate the strictly-increasing rule."""

    group_index: int
    group_id: Any
    prev_index: int
    prev_id: Any
    curr_index: int
    curr_id: Any


@dataclass(frozen=True)
class MissingId:
    """A record has no ``id`` field (or it is null). Never treated as zero."""

    group_index: int
    group_id: Any
    record_index: int


@dataclass(frozen=True)
class InvalidId:
    """A record's ID does not match the caller-declared ``id_kind``."""

    group_index: int
    group_id: Any
    record_index: int
    value: Any
    reason: str


@dataclass(frozen=True)
class StreamTruncated:
    """The stream ended before the JSON document was complete."""

    message: str


@dataclass(frozen=True)
class CheckComplete:
    """The whole stream was parsed and checked; only emitted on full success."""

    groups_checked: int
    records_checked: int


Violation = Union[OrderDecrease, MissingId, InvalidId]
CheckEvent = Union[Violation, StreamTruncated, CheckComplete]


@dataclass
class Report:
    violations: List[Violation]
    truncated: bool
    complete: bool
    groups_checked: int = 0
    records_checked: int = 0

    @property
    def ok(self) -> bool:
        return self.complete and not self.violations


def _coerce_id(value: Any, event: str, id_kind: str):
    """Return ``(True, comparable)`` or ``(False, reason)`` per ``id_kind``."""
    if id_kind == ID_KIND_NUMBER:
        if event != "number":
            return False, "expected a JSON number, got JSON %s" % event
        return True, value
    if event != "string":
        return False, "expected a numeric string, got JSON %s" % event
    if not _NUMERIC_STRING_RE.fullmatch(value):
        return False, "string %r is not an integer" % (value,)
    return True, int(value)


def iter_order_id_events(source: BinaryIO, *, id_kind: str):
    """Yield ``CheckEvent`` items while streaming ``source`` through ijson.

    ``source`` is a binary file-like object (or anything ``ijson.parse``
    accepts). Violations are yielded as soon as they are known; the final
    event is ``CheckComplete`` if, and only if, the stream parsed cleanly
    to the end, otherwise ``StreamTruncated``.
    """
    if id_kind not in ID_KINDS:
        raise ValueError("id_kind must be one of %r, got %r" % (ID_KINDS, id_kind))

    group_index = -1
    group_id = None
    record_index = -1
    record_has_id = False
    last_id: Optional[Any] = None
    last_index = -1
    total_records = 0

    try:
        for prefix, event, value in ijson.parse(source):
            if prefix == "groups.item":
                if event == "start_map":
                    group_index += 1
                    group_id = None
                    record_index = -1
                    last_id = None
                    last_index = -1
            elif prefix == "groups.item.id" and event in ("string", "number"):
                group_id = value
            elif prefix == "groups.item.orders.item":
                if event == "start_map":
                    record_index += 1
                    record_has_id = False
                elif event == "end_map":
                    if not record_has_id:
                        yield MissingId(group_index, group_id, record_index)
                    total_records += 1
            elif prefix == "groups.item.orders.item.id":
                if event == "null":
                    continue  # explicit null counts as missing, not as zero
                if event not in ("string", "number"):
                    continue
                record_has_id = True
                ok, coerced = _coerce_id(value, event, id_kind)
                if not ok:
                    yield InvalidId(group_index, group_id, record_index, value, coerced)
                    continue
                if last_id is not None and coerced <= last_id:
                    yield OrderDecrease(
                        group_index=group_index,
                        group_id=group_id,
                        prev_index=last_index,
                        prev_id=last_id,
                        curr_index=record_index,
                        curr_id=coerced,
                    )
                else:
                    last_id = coerced
                    last_index = record_index
    except ijson.IncompleteJSONError as exc:
        yield StreamTruncated(str(exc))
        return

    yield CheckComplete(groups_checked=group_index + 1, records_checked=total_records)


def check_order_ids(source: BinaryIO, *, id_kind: str) -> Report:
    """Consume ``iter_order_id_events`` and return an aggregated ``Report``.

    ``report.complete`` is True only when the entire stream was parsed and
    checked; a truncated stream yields ``complete=False, truncated=True``.
    """
    violations: List[Violation] = []
    truncated = False
    complete = False
    groups_checked = 0
    records_checked = 0
    for event in iter_order_id_events(source, id_kind=id_kind):
        if isinstance(event, (OrderDecrease, MissingId, InvalidId)):
            violations.append(event)
        elif isinstance(event, StreamTruncated):
            truncated = True
        elif isinstance(event, CheckComplete):
            complete = True
            groups_checked = event.groups_checked
            records_checked = event.records_checked
    return Report(
        violations=violations,
        truncated=truncated,
        complete=complete,
        groups_checked=groups_checked,
        records_checked=records_checked,
    )


def main(argv) -> int:
    if len(argv) != 3 or argv[2] not in ID_KINDS:
        print("usage: %s <response.json> {%s}" % (argv[0], "|".join(ID_KINDS)), file=sys.stderr)
        return 2
    with open(argv[1], "rb") as stream:
        report = check_order_ids(stream, id_kind=argv[2])
    for violation in report.violations:
        print(violation)
    if report.truncated:
        print("stream truncated: file was NOT fully verified", file=sys.stderr)
        return 1
    if report.violations:
        return 1
    print("ok: %d groups, %d records, order verified" % (report.groups_checked, report.records_checked))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
