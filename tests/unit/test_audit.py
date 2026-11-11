import pytest
import yaml

from vcr.audit import UnusedInteraction, UnusedInteractionReason
from vcr.cassette import Cassette
from vcr.matchers import body, headers, method, uri
from vcr.request import Request

BODY_MATCHERS = (method, uri, body)
HEADER_MATCHERS = (method, uri, headers)


def _interaction(uri, method="GET", body="", response=None):
    return {
        "request": {"body": body, "uri": uri, "method": method, "headers": {}},
        "response": response if response is not None else {"status": {"code": 200, "message": "OK"}},
    }


def _load_cassette(tmpdir, interactions, **kwargs):
    file = tmpdir.join("cassette.yml")
    file.write(yaml.dump({"interactions": interactions}))
    return Cassette.load(path=str(file), **kwargs), file


def _request(interaction):
    return Request._from_dict(interaction["request"])


def test_audit_empty_cassette(tmpdir):
    cassette, _ = _load_cassette(tmpdir, [])
    assert cassette.unused_interactions() == []


def test_audit_all_played_reports_nothing(tmpdir):
    interactions = [_interaction("http://example.com/a"), _interaction("http://example.com/b")]
    cassette, _ = _load_cassette(tmpdir, interactions)
    for interaction in interactions:
        cassette.play_response(_request(interaction))

    assert cassette.all_played
    assert cassette.unused_interactions() == []


def test_audit_branch_never_ran(tmpdir):
    interactions = [
        _interaction("http://example.com/ran"),
        _interaction("http://example.com/never-ran"),
    ]
    cassette, _ = _load_cassette(tmpdir, interactions)
    cassette.play_response(_request(interactions[0]))

    unused = cassette.unused_interactions()

    assert len(unused) == 1
    finding = unused[0]
    assert isinstance(finding, UnusedInteraction)
    assert finding.index == 1
    assert finding.reason is UnusedInteractionReason.NEVER_PLAYED
    assert finding.request is cassette.data[1][0]
    assert finding.response is cassette.data[1][1]
    assert "never-ran" in finding.detail
    assert "never ran" in finding.detail


def test_audit_duplicate_recorded_more_than_consumed(tmpdir):
    interactions = [_interaction("http://example.com/same") for _ in range(3)]
    cassette, _ = _load_cassette(tmpdir, interactions)

    # The address is requested twice, although it was recorded three times.
    cassette.play_response(_request(interactions[0]))
    cassette.play_response(_request(interactions[1]))

    unused = cassette.unused_interactions()

    assert [finding.index for finding in unused] == [2]
    finding = unused[0]
    assert finding.reason is UnusedInteractionReason.NEVER_PLAYED
    assert "recorded 3 times" in finding.detail
    assert "only 2 of them were ever consumed" in finding.detail


def test_audit_duplicate_address_never_requested(tmpdir):
    interactions = [_interaction("http://example.com/same") for _ in range(3)]
    cassette, _ = _load_cassette(tmpdir, interactions)

    unused = cassette.unused_interactions()

    assert [finding.index for finding in unused] == [0, 1, 2]
    assert {finding.reason for finding in unused} == {UnusedInteractionReason.NEVER_PLAYED}
    assert all("recorded 3 times" in finding.detail for finding in unused)


def test_audit_changed_body_distinguished_from_unplayed(tmpdir):
    interactions = [
        _interaction("http://example.com/submit", method="POST", body="alpha"),
        _interaction("http://example.com/submit", method="POST", body="beta"),
    ]
    cassette, _ = _load_cassette(tmpdir, interactions, match_on=BODY_MATCHERS)

    # Only the alpha variant is exercised now; the beta interaction's body has no consumer.
    cassette.play_response(_request(interactions[0]))

    unused = cassette.unused_interactions()

    assert [finding.index for finding in unused] == [1]
    finding = unused[0]
    assert finding.reason is UnusedInteractionReason.BODY_CHANGED
    assert "body" in finding.detail
    assert "submit" in finding.detail


def test_audit_changed_headers_reported_as_changed(tmpdir):
    interactions = [
        _interaction("http://example.com/submit", method="POST"),
        _interaction("http://example.com/submit", method="POST"),
    ]
    interactions[0]["request"]["headers"] = {"X-Variant": "alpha"}
    interactions[1]["request"]["headers"] = {"X-Variant": "beta"}
    cassette, _ = _load_cassette(tmpdir, interactions, match_on=HEADER_MATCHERS)

    cassette.play_response(_request(interactions[0]))

    unused = cassette.unused_interactions()
    assert len(unused) == 1
    assert unused[0].reason is UnusedInteractionReason.BODY_CHANGED
    assert "headers" in unused[0].detail


def test_audit_preserves_positions_in_recording_order(tmpdir):
    interactions = [
        _interaction("http://example.com/a"),
        _interaction("http://example.com/b"),
        _interaction("http://example.com/c"),
        _interaction("http://example.com/d"),
    ]
    cassette, _ = _load_cassette(tmpdir, interactions)
    cassette.play_response(_request(interactions[2]))

    unused = cassette.unused_interactions()

    assert [finding.index for finding in unused] == [0, 1, 3]
    assert [finding.request.uri for finding in unused] == [
        "http://example.com/a",
        "http://example.com/b",
        "http://example.com/d",
    ]


def test_audit_ignores_interactions_recorded_this_session(tmpdir):
    interactions = [_interaction("http://example.com/old")]
    cassette, _ = _load_cassette(tmpdir, interactions)
    cassette.play_response(_request(interactions[0]))
    cassette.append(
        Request._from_dict(_interaction("http://example.com/new")["request"]),
        _interaction("http://example.com/new")["response"],
    )

    unused = cassette.unused_interactions()

    assert unused == []


def test_audit_superseded_by_new_recording_at_same_address(tmpdir):
    interactions = [
        _interaction("http://example.com/submit", method="POST", body="alpha"),
    ]
    cassette, _ = _load_cassette(tmpdir, interactions, match_on=BODY_MATCHERS)

    # new_episodes: the changed request doesn't match the old recording, so it is newly recorded.
    changed = _interaction("http://example.com/submit", method="POST", body="beta")
    cassette.append(Request._from_dict(changed["request"]), changed["response"])

    unused = cassette.unused_interactions()

    assert [finding.index for finding in unused] == [0]
    assert unused[0].reason is UnusedInteractionReason.BODY_CHANGED
    assert "body" in unused[0].detail


def test_audit_is_read_only(tmpdir):
    interactions = [
        _interaction("http://example.com/ran"),
        _interaction("http://example.com/never-ran"),
    ]
    cassette, file = _load_cassette(tmpdir, interactions)
    cassette.play_response(_request(interactions[0]))

    before = file.read()
    play_counts = dict(cassette.play_counts)
    data = list(cassette.data)
    dirty = cassette.dirty

    unused = cassette.unused_interactions()

    assert len(unused) == 1
    assert dict(cassette.play_counts) == play_counts
    assert cassette.data == data
    assert cassette.dirty == dirty
    assert file.read() == before


def test_audit_distinguishes_all_three_cases_together(tmpdir):
    interactions = [
        # 0-2: same address recorded three times, only two are consumed.
        _interaction("http://example.com/repeated"),
        _interaction("http://example.com/repeated"),
        _interaction("http://example.com/repeated"),
        # 3-4: same endpoint, two different POST bodies; only the first is used.
        _interaction("http://example.com/submit", method="POST", body="alpha"),
        _interaction("http://example.com/submit", method="POST", body="beta"),
        # 5: a branch that never runs at all.
        _interaction("http://example.com/never-ran"),
    ]
    cassette, _ = _load_cassette(tmpdir, interactions, match_on=BODY_MATCHERS)
    cassette.play_response(_request(interactions[0]))
    cassette.play_response(_request(interactions[1]))
    cassette.play_response(_request(interactions[3]))

    unused = cassette.unused_interactions()
    by_index = {finding.index: finding for finding in unused}

    assert sorted(by_index) == [2, 4, 5]
    assert by_index[2].reason is UnusedInteractionReason.NEVER_PLAYED
    assert "recorded 3 times" in by_index[2].detail
    assert "only 2 of them were ever consumed" in by_index[2].detail
    assert by_index[4].reason is UnusedInteractionReason.BODY_CHANGED
    assert by_index[5].reason is UnusedInteractionReason.NEVER_PLAYED
    assert "never ran" in by_index[5].detail


def test_audit_duplicate_with_playback_repeats(tmpdir):
    interactions = [_interaction("http://example.com/same") for _ in range(3)]
    cassette, _ = _load_cassette(tmpdir, interactions, allow_playback_repeats=True)

    # One recording is replayed repeatedly; the other two duplicates stay unplayed.
    cassette.play_response(_request(interactions[0]))
    cassette.play_response(_request(interactions[0]))

    unused = cassette.unused_interactions()

    assert [finding.index for finding in unused] == [1, 2]
    assert {finding.reason for finding in unused} == {UnusedInteractionReason.NEVER_PLAYED}
    assert all("only 1 of them was ever consumed" in finding.detail for finding in unused)


def test_unused_interaction_reason_values():
    assert UnusedInteractionReason.NEVER_PLAYED.value == "never_played"
    assert UnusedInteractionReason.BODY_CHANGED.value == "body_changed"
    with pytest.raises(ValueError):
        UnusedInteractionReason("played")
