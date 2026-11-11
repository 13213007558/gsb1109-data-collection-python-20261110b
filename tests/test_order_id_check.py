import io
import json

import pytest

from order_id_check import (
    CheckComplete,
    InvalidId,
    MissingId,
    OrderDecrease,
    StreamTruncated,
    check_order_ids,
    iter_order_id_events,
)


def make_stream(payload):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    return io.BytesIO(payload.encode("utf-8"))


def groups(*group_list):
    return {"groups": [dict(id=gid, orders=[{"id": oid} for oid in oids]) for gid, oids in group_list]}


def test_all_groups_increasing_is_ok():
    report = check_order_ids(
        make_stream(groups(("g1", [1, 2, 3]), ("g2", [10, 20]))),
        id_kind="number",
    )
    assert report.complete
    assert not report.truncated
    assert report.ok
    assert report.violations == []
    assert report.groups_checked == 2
    assert report.records_checked == 5


def test_order_is_checked_per_group_not_across_groups():
    # 100 -> 5 would be a decrease globally, but each group restarts.
    report = check_order_ids(
        make_stream(groups(("g1", [50, 100]), ("g2", [5, 6]))),
        id_kind="number",
    )
    assert report.ok


def test_decrease_reports_group_and_record_pair():
    report = check_order_ids(
        make_stream(groups(("g1", [1, 2]), ("g2", [7, 9, 4, 12]))),
        id_kind="number",
    )
    assert report.complete
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert isinstance(violation, OrderDecrease)
    assert violation.group_index == 1
    assert violation.group_id == "g2"
    assert violation.prev_index == 1
    assert violation.prev_id == 9
    assert violation.curr_index == 2
    assert violation.curr_id == 4


def test_equal_ids_violate_strictly_increasing():
    report = check_order_ids(
        make_stream(groups(("g1", [3, 3]))),
        id_kind="number",
    )
    assert [type(v) for v in report.violations] == [OrderDecrease]


def test_missing_id_is_reported_and_not_treated_as_zero():
    payload = {
        "groups": [
            {"id": "g1", "orders": [{"id": 5}, {"note": "no id here"}, {"id": 6}]},
        ]
    }
    report = check_order_ids(make_stream(payload), id_kind="number")
    assert report.complete
    assert report.violations == [MissingId(group_index=0, group_id="g1", record_index=1)]
    # 5 < 6 across the gap: no OrderDecrease, and the gap was not treated as 0
    # (zero would have produced a decrease 5 -> 0 and then 0 -> 6 would pass).


def test_missing_id_does_not_hide_a_real_decrease():
    payload = {
        "groups": [
            {"id": "g1", "orders": [{"id": 5}, {"note": "gap"}, {"id": 3}]},
        ]
    }
    report = check_order_ids(make_stream(payload), id_kind="number")
    kinds = [type(v) for v in report.violations]
    assert kinds == [MissingId, OrderDecrease]
    decrease = report.violations[1]
    assert (decrease.prev_index, decrease.prev_id) == (0, 5)
    assert (decrease.curr_index, decrease.curr_id) == (2, 3)


def test_null_id_counts_as_missing_not_zero():
    payload = {"groups": [{"id": "g1", "orders": [{"id": 5}, {"id": None}, {"id": 6}]}]}
    report = check_order_ids(make_stream(payload), id_kind="number")
    assert report.violations == [MissingId(group_index=0, group_id="g1", record_index=1)]


def test_numeric_string_mode_compares_by_numeric_value():
    report = check_order_ids(
        make_stream(groups(("g1", ["9", "10", "11"]))),
        id_kind="numeric_string",
    )
    assert report.ok


def test_numeric_string_mode_detects_decrease():
    report = check_order_ids(
        make_stream(groups(("g1", ["10", "9"]))),
        id_kind="numeric_string",
    )
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert isinstance(violation, OrderDecrease)
    assert violation.prev_id == 10
    assert violation.curr_id == 9


def test_numeric_string_mode_rejects_numbers_and_junk():
    payload = {
        "groups": [
            {"id": "g1", "orders": [{"id": "1"}, {"id": 2}, {"id": "abc"}, {"id": "4"}]},
        ]
    }
    report = check_order_ids(make_stream(payload), id_kind="numeric_string")
    assert report.complete
    invalid = [v for v in report.violations if isinstance(v, InvalidId)]
    assert [(v.record_index, v.value) for v in invalid] == [(1, 2), (2, "abc")]
    assert not [v for v in report.violations if isinstance(v, OrderDecrease)]


def test_number_mode_rejects_numeric_strings():
    payload = {"groups": [{"id": "g1", "orders": [{"id": 1}, {"id": "2"}, {"id": 3}]}]}
    report = check_order_ids(make_stream(payload), id_kind="number")
    assert report.complete
    assert report.violations == [
        InvalidId(group_index=0, group_id="g1", record_index=1, value="2",
                  reason="expected a JSON number, got JSON string")
    ]


def test_invalid_id_kind_is_rejected():
    with pytest.raises(ValueError):
        list(iter_order_id_events(make_stream(groups()), id_kind="whatever"))


def test_truncated_stream_is_never_reported_as_complete():
    payload = json.dumps(groups(("g1", [1, 2, 3]), ("g2", [4, 5, 6])))
    truncated = payload[: len(payload) // 2]
    report = check_order_ids(make_stream(truncated), id_kind="number")
    assert report.truncated
    assert not report.complete
    assert not report.ok


def test_truncated_stream_keeps_violations_found_before_the_cut():
    payload = json.dumps(groups(("g1", [2, 1]), ("g2", [4, 5, 6])))
    cut = payload.index('"g2"')
    report = check_order_ids(make_stream(payload[:cut]), id_kind="number")
    assert report.truncated
    assert not report.complete
    assert [type(v) for v in report.violations] == [OrderDecrease]


def test_truncated_stream_yields_stream_truncated_event():
    payload = json.dumps(groups(("g1", [1, 2])))
    events = list(iter_order_id_events(make_stream(payload[:-5]), id_kind="number"))
    assert isinstance(events[-1], StreamTruncated)
    assert not any(isinstance(e, CheckComplete) for e in events)


class ChunkedReader:
    """File-like that serves at most ``chunk_size`` bytes per read."""

    def __init__(self, data, chunk_size=1024):
        self._data = data
        self._chunk_size = chunk_size
        self.served = 0

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._data) - self.served
        size = min(size, self._chunk_size, len(self._data) - self.served)
        chunk = self._data[self.served : self.served + size]
        self.served += len(chunk)
        return chunk


def test_violations_are_reported_before_the_whole_file_is_read():
    bad_group = {"id": "g1", "orders": [{"id": 2}, {"id": 1}]}
    big_group = {
        "id": "g2",
        "orders": [{"id": i, "padding": "x" * 100} for i in range(3000)],
    }
    payload = json.dumps({"groups": [bad_group, big_group]}).encode("utf-8")
    reader = ChunkedReader(payload)

    events = iter_order_id_events(reader, id_kind="number")
    first = next(events)

    assert isinstance(first, OrderDecrease)
    assert first.group_id == "g1"
    assert (first.prev_id, first.curr_id) == (2, 1)
    # the violation was reported long before the stream was consumed
    assert reader.served < len(payload)
    remaining = list(events)
    assert isinstance(remaining[-1], CheckComplete)
    assert remaining[-1].groups_checked == 2
