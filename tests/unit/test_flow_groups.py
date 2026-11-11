"""Tests for caller-declared flow groups on cassettes.

Flow groups keep concurrent flows that make identical requests (e.g. two
form-filling threads polling the same URL) from consuming each other's
recorded responses.
"""

import asyncio
import threading
from unittest import mock

import pytest
import yaml

from vcr import VCR, current_flow_group, flow_group
from vcr.cassette import Cassette
from vcr.errors import UnhandledHTTPRequestError
from vcr.request import Request
from vcr.serializers import jsonserializer, yamlserializer

POLL_URL = "http://poll.example.com/status"
GROUPS = ("form-a", "form-b")


def _poll_request(body="x"):
    return Request("POST", POLL_URL, body, {})


def _record_grouped_cassette(path=None, groups=GROUPS):
    """A cassette holding two flows' results for the same polling request.

    Recording order interleaves the flows, as one file recorded back to back
    would: A's first result, B's first result, A's second result.
    """
    cassette = Cassette(path or "grouped", flow_groups=groups, record_mode="none")
    cassette.append(_poll_request(), "A1", flow_group="form-a")
    cassette.append(_poll_request(), "B1", flow_group="form-b")
    cassette.append(_poll_request(), "A2", flow_group="form-a")
    cassette._loaded_count = len(cassette)
    cassette.rewound = True
    return cassette


def test_playback_stays_inside_the_declared_group():
    cassette = _record_grouped_cassette()

    with cassette.flow_group("form-a"):
        assert cassette.play_response(_poll_request()) == "A1"
    with cassette.flow_group("form-b"):
        # B must not get A's second interaction just because A already played.
        assert cassette.play_response(_poll_request()) == "B1"
    with cassette.flow_group("form-a"):
        assert cassette.play_response(_poll_request()) == "A2"


def test_identical_requests_are_consumed_in_recording_order_within_a_group():
    cassette = _record_grouped_cassette()

    with cassette.flow_group("form-a"):
        assert cassette.play_response(_poll_request()) == "A1"
        assert cassette.play_response(_poll_request()) == "A2"


def test_group_exhaustion_does_not_borrow_from_another_group():
    cassette = _record_grouped_cassette()

    with cassette.flow_group("form-a"):
        cassette.play_response(_poll_request())
        cassette.play_response(_poll_request())
        # A's own interactions are exhausted: B1 must not be handed out.
        assert not cassette.can_play_response_for(_poll_request())
        with pytest.raises(UnhandledHTTPRequestError):
            cassette.play_response(_poll_request())

    # The other group's interactions are untouched and still playable.
    with cassette.flow_group("form-b"):
        assert cassette.can_play_response_for(_poll_request())
        assert cassette.play_response(_poll_request()) == "B1"


def test_responses_of_is_scoped_to_the_active_group():
    cassette = _record_grouped_cassette()

    with cassette.flow_group("form-a"):
        assert cassette.responses_of(_poll_request()) == ["A1", "A2"]
    with cassette.flow_group("form-b"):
        assert cassette.responses_of(_poll_request()) == ["B1"]


def test_recording_labels_interactions_with_the_active_group():
    cassette = Cassette("recording", flow_groups=GROUPS)

    with cassette.flow_group("form-a"):
        cassette.append(_poll_request(), "A1")
    cassette.append(_poll_request(), "ungrouped")
    with cassette.flow_group("form-b"):
        cassette.append(_poll_request(), "B1")

    assert cassette._flow_groups == ["form-a", None, "form-b"]


def test_ungrouped_cassette_keeps_legacy_behavior():
    # No flow_groups configured: playback sees every interaction, in order,
    # exactly as cassettes recorded before this feature did.
    cassette = Cassette("legacy")
    cassette.append(_poll_request(), "r1")
    cassette.append(_poll_request(), "r2")
    cassette._loaded_count = len(cassette)
    cassette.rewound = True

    with cassette.flow_group("anything-at-all"):
        assert current_flow_group() is None
        assert cassette.play_response(_poll_request()) == "r1"
        assert cassette.play_response(_poll_request()) == "r2"


def test_legacy_cassette_file_without_flow_groups_loads(tmpdir):
    cassette_file = tmpdir.join("legacy.yaml")
    cassette_file.write(
        yaml.dump(
            {
                "interactions": [
                    {
                        "request": {"method": "POST", "uri": POLL_URL, "body": "x", "headers": {}},
                        "response": "r1",
                    },
                    {
                        "request": {"method": "POST", "uri": POLL_URL, "body": "x", "headers": {}},
                        "response": "r2",
                    },
                ],
            },
        ),
    )

    cassette = Cassette.load(path=str(cassette_file), flow_groups=GROUPS)
    assert cassette._flow_groups == [None, None]
    assert cassette.play_response(_poll_request()) == "r1"
    assert cassette.play_response(_poll_request()) == "r2"


def test_legacy_interactions_dont_match_a_declared_group(tmpdir):
    # An old cassette's interactions carry no group. Once a group is declared
    # they can't satisfy it (groups never fall back to ungrouped entries), so
    # the cassette has to be re-recorded with groups.
    cassette_file = tmpdir.join("legacy.yaml")
    cassette_file.write(
        yaml.dump(
            {
                "interactions": [
                    {
                        "request": {"method": "POST", "uri": POLL_URL, "body": "x", "headers": {}},
                        "response": "r1",
                    },
                ],
            },
        ),
    )

    cassette = Cassette.load(path=str(cassette_file), flow_groups=GROUPS)
    with cassette.flow_group("form-a"):
        assert not cassette.can_play_response_for(_poll_request())
        with pytest.raises(UnhandledHTTPRequestError):
            cassette.play_response(_poll_request())


@pytest.mark.parametrize("serializer", [yamlserializer, jsonserializer])
def test_flow_groups_round_trip_through_a_file(tmpdir, serializer):
    path = str(tmpdir.join("cassette"))
    cassette = Cassette(path, serializer=serializer, flow_groups=GROUPS)
    with cassette.flow_group("form-a"):
        cassette.append(_poll_request(), "A1")
    with cassette.flow_group("form-b"):
        cassette.append(_poll_request(), "B1")
    cassette._save(force=True)

    reloaded = Cassette.load(path=path, serializer=serializer, flow_groups=GROUPS)
    assert reloaded._flow_groups == ["form-a", "form-b"]
    with reloaded.flow_group("form-b"):
        assert reloaded.play_response(_poll_request()) == "B1"
    with reloaded.flow_group("form-a"):
        assert reloaded.play_response(_poll_request()) == "A1"


def test_ungrouped_recording_keeps_the_old_file_format(tmpdir):
    path = str(tmpdir.join("plain.yaml"))
    cassette = Cassette(path)
    cassette.append(_poll_request(), "r1")
    cassette._save(force=True)

    with open(path) as f:
        saved = yaml.safe_load(f)
    assert "flow_groups" not in saved


def test_drop_unused_requests_keeps_flow_groups(tmpdir):
    path = str(tmpdir.join("dropped.yaml"))
    recorded = Cassette(path, flow_groups=GROUPS)
    with recorded.flow_group("form-a"):
        recorded.append(_poll_request(), "A1")
    with recorded.flow_group("form-b"):
        recorded.append(_poll_request(), "B1")
    recorded._save(force=True)

    cassette = Cassette.load(path=path, flow_groups=GROUPS, drop_unused_requests=True)
    with cassette.flow_group("form-a"):
        cassette.play_response(_poll_request())
    cassette._save(force=True)

    with open(path) as f:
        saved = yaml.safe_load(f)
    assert saved["flow_groups"] == ["form-a"]
    reloaded = Cassette.load(path=path, flow_groups=GROUPS)
    with reloaded.flow_group("form-a"):
        assert reloaded.play_response(_poll_request()) == "A1"
    with reloaded.flow_group("form-b"):
        assert not reloaded.can_play_response_for(_poll_request())


def test_unknown_flow_group_is_rejected():
    cassette = Cassette("grouped", flow_groups=GROUPS)
    with pytest.raises(ValueError, match="Unknown flow group 'form-c'"), cassette.flow_group("form-c"):
        pass


def test_group_declared_outside_the_cassettes_groups_warns(caplog):
    cassette = Cassette("grouped", flow_groups=GROUPS)
    with caplog.at_level("WARNING", logger="vcr.cassette"), flow_group("form-c"):
        assert cassette._active_flow_group() is None
    assert "not one of the cassette's groups" in caplog.text


def test_nested_flow_groups_restore_the_outer_one():
    cassette = Cassette("grouped", flow_groups=GROUPS)
    with cassette.flow_group("form-a"):
        assert current_flow_group() == "form-a"
        with cassette.flow_group("form-b"):
            assert current_flow_group() == "form-b"
        assert current_flow_group() == "form-a"


def test_flow_groups_are_isolated_between_threads():
    cassette = _record_grouped_cassette()
    results = {}

    def consume(group, count):
        played = []
        with cassette.flow_group(group):
            for _ in range(count):
                played.append(cassette.play_response(_poll_request()))
        results[group] = played

    thread_a = threading.Thread(target=consume, args=("form-a", 2))
    thread_b = threading.Thread(target=consume, args=("form-b", 1))
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert results == {"form-a": ["A1", "A2"], "form-b": ["B1"]}


def test_concurrent_threads_cannot_take_the_same_interaction():
    # Each group holds one interaction for the same request. Two threads in the
    # same group must consume different interactions rather than both grabbing
    # the first one.
    cassette = Cassette("shared", flow_groups=("form-a",), record_mode="none")
    cassette.append(_poll_request(), "A1", flow_group="form-a")
    cassette.append(_poll_request(), "A2", flow_group="form-a")
    cassette._loaded_count = len(cassette)
    cassette.rewound = True

    taken = []
    barrier = threading.Barrier(2)

    def consume():
        with cassette.flow_group("form-a"):
            barrier.wait()
            taken.append(cassette.play_response(_poll_request()))

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(taken) == ["A1", "A2"]


def test_flow_group_option_validation():
    assert VCR(flow_groups=("a", "b")).flow_groups == ("a", "b")
    # A single string is one group, not an iterable of single characters.
    assert VCR(flow_groups="a").flow_groups == ("a",)
    assert VCR().flow_groups == ()

    with pytest.raises(ValueError, match="Duplicate flow group"):
        VCR(flow_groups=("a", "a"))
    with pytest.raises(ValueError, match="non-empty strings"):
        VCR(flow_groups=("a", ""))
    with pytest.raises(ValueError, match="non-empty strings"):
        VCR(flow_groups=(1,))


def test_use_cassette_passes_flow_groups_to_the_cassette():
    vcr = VCR(flow_groups=GROUPS)
    with vcr.use_cassette("test") as cassette:
        assert cassette.flow_groups == GROUPS
    # Per-cassette configuration overrides the VCR-level default.
    with vcr.use_cassette("test", flow_groups=("other",)) as cassette:
        assert cassette.flow_groups == ("other",)


def test_persister_with_only_requests_and_responses_is_treated_as_ungrouped():
    # Persisters written before flow groups return a 2-tuple; that must keep
    # loading as an ungrouped cassette.
    requests = [_poll_request()]
    persister = mock.Mock()
    persister.load_cassette.return_value = (requests, ["r1"])

    cassette = Cassette.load(path="legacy-persister", persister=persister, flow_groups=GROUPS)
    assert cassette._flow_groups == [None]
    assert cassette.play_response(_poll_request()) == "r1"


def test_record_mode_all_keeps_groups_of_unreplayed_interactions(tmpdir):
    path = str(tmpdir.join("all.yaml"))
    request_a = Request("GET", "http://x/a", "", {})
    request_b = Request("GET", "http://x/b", "", {})
    cassette = Cassette(path, flow_groups=GROUPS, record_mode="all")
    cassette.append(request_a, "A1", flow_group="form-a")
    cassette.append(request_b, "B1", flow_group="form-b")
    cassette._loaded_count = len(cassette)
    cassette.rewound = True

    # Re-record A; B is kept (with its group) because nothing matched it.
    cassette.append(request_a, "A-new", flow_group="form-a")
    cassette._save(force=True)

    with open(path) as f:
        saved = yaml.safe_load(f)
    assert saved["flow_groups"] == ["form-b", "form-a"]
    assert [interaction["request"]["uri"] for interaction in saved["interactions"]] == [
        "http://x/b",
        "http://x/a",
    ]


def test_save_hook_dropping_an_interaction_keeps_groups_aligned(tmpdir):
    path = str(tmpdir.join("hook.yaml"))

    def drop_b(request, response):
        return None if response == "drop-me" else (request, response)

    cassette = Cassette(path, flow_groups=GROUPS, before_save_interaction=drop_b)
    cassette.append(Request("GET", "http://x/1", "", {}), "A1", flow_group="form-a")
    cassette.append(Request("GET", "http://x/2", "", {}), "drop-me", flow_group="form-b")
    cassette.append(Request("GET", "http://x/3", "", {}), "A2", flow_group="form-a")
    cassette._save(force=True)

    with open(path) as f:
        saved = yaml.safe_load(f)
    assert saved["flow_groups"] == ["form-a", "form-a"]


def test_flow_groups_are_isolated_between_asyncio_tasks():
    cassette = _record_grouped_cassette()

    async def consume(group, count):
        played = []
        with cassette.flow_group(group):
            for _ in range(count):
                # Yield so the two tasks interleave between their lookups.
                await asyncio.sleep(0)
                played.append(cassette.play_response(_poll_request()))
        return played

    async def main():
        result_a, result_b = await asyncio.gather(
            consume("form-a", 2),
            consume("form-b", 1),
        )
        return result_a, result_b

    result_a, result_b = asyncio.run(main())
    assert result_a == ["A1", "A2"]
    assert result_b == ["B1"]
