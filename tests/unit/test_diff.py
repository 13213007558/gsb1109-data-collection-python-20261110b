import json

import pytest

from vcr.diff import compare_cassettes, main
from vcr.request import Request
from vcr.serialize import serialize
from vcr.serializers import jsonserializer, yamlserializer


def make_request(method="GET", uri="http://example.com/", body=None, headers=None):
    return Request(method, uri, body, headers or {})


def make_response(code=200, body=b"", headers=None, message="OK"):
    return {
        "status": {"code": code, "message": message},
        "headers": headers or {},
        "body": {"string": body},
    }


def write_cassette(path, interactions, serializer=yamlserializer):
    cassette_dict = {
        "requests": [request for request, _ in interactions],
        "responses": [response for _, response in interactions],
    }
    path.write_text(serialize(cassette_dict, serializer))
    return str(path)


@pytest.fixture
def cassette(tmp_path):
    def make(name, interactions, serializer=yamlserializer):
        return write_cassette(tmp_path / name, interactions, serializer)

    return make


def test_identical_cassettes(cassette):
    interactions = [
        (make_request(uri="http://example.com/a"), make_response(body=b"a")),
        (make_request("POST", "http://example.com/b", b"x=1"), make_response(body=b"b")),
    ]
    old = cassette("old.yaml", interactions)
    new = cassette("new.yaml", interactions)

    diff = compare_cassettes(old, new)

    assert diff.identical
    assert len(diff.unchanged) == 2
    assert [(p.old_index, p.new_index) for p in diff.unchanged] == [(0, 0), (1, 1)]
    assert not diff.ambiguous


def test_query_order_and_serialization_noise_ignored(cassette):
    old = cassette(
        "old.yaml",
        [(make_request(uri="http://example.com/?a=1&b=2"), make_response(body=b"same"))],
    )
    # Reordered query parameters and a different serializer: not a change.
    new = cassette(
        "new.json",
        [(make_request(uri="http://example.com/?b=2&a=1"), make_response(body=b"same"))],
        serializer=jsonserializer,
    )

    diff = compare_cassettes(old, new)

    assert diff.identical
    assert len(diff.unchanged) == 1


def test_changed_response(cassette):
    old = cassette(
        "old.yaml",
        [
            (make_request(uri="http://example.com/a"), make_response(body=b"old body")),
            (make_request(uri="http://example.com/b"), make_response(body=b"same")),
        ],
    )
    new = cassette(
        "new.yaml",
        [
            (make_request(uri="http://example.com/a"), make_response(code=500, body=b"new body")),
            (make_request(uri="http://example.com/b"), make_response(body=b"same")),
        ],
    )

    diff = compare_cassettes(old, new)

    assert not diff.identical
    assert len(diff.changed) == 1
    pair = diff.changed[0]
    assert (pair.old_index, pair.new_index) == (0, 0)
    assert any("status code" in difference for difference in pair.differences)
    assert any("body" in difference for difference in pair.differences)
    assert len(diff.unchanged) == 1


def test_added_and_removed(cassette):
    old = cassette(
        "old.yaml",
        [
            (make_request(uri="http://example.com/kept"), make_response()),
            (make_request(uri="http://example.com/gone"), make_response()),
        ],
    )
    new = cassette(
        "new.yaml",
        [
            (make_request(uri="http://example.com/kept"), make_response()),
            (make_request("POST", "http://example.com/new"), make_response()),
        ],
    )

    diff = compare_cassettes(old, new)

    assert [entry.index for entry in diff.removed] == [1]
    assert diff.removed[0].request == "<Request (GET) http://example.com/gone>"
    assert [entry.index for entry in diff.added] == [1]
    assert diff.added[0].request == "<Request (POST) http://example.com/new>"
    assert not diff.ambiguous


def test_duplicate_requests_are_kept_and_paired_in_order(cassette):
    old = cassette(
        "old.yaml",
        [
            (make_request(uri="http://example.com/poll"), make_response(body=b"first")),
            (make_request(uri="http://example.com/poll"), make_response(body=b"second")),
        ],
    )
    new = cassette(
        "new.yaml",
        [
            (make_request(uri="http://example.com/poll"), make_response(body=b"first")),
            (make_request(uri="http://example.com/poll"), make_response(body=b"changed")),
        ],
    )

    diff = compare_cassettes(old, new)

    # Both occurrences are kept, each paired with a distinct counterpart.
    assert [(p.old_index, p.new_index) for p in diff.pairs] == [(0, 0), (1, 1)]
    assert not diff.pairs[0].changed
    assert diff.pairs[1].changed
    # Either occurrence could pair with either counterpart, so neither pairing is reliable.
    assert all(pair.ambiguous for pair in diff.pairs)


def test_unpairable_duplicates_are_marked(cassette):
    old = cassette("old.yaml", [(make_request(), make_response())])
    new = cassette(
        "new.yaml",
        [(make_request(), make_response()), (make_request(), make_response(body=b"extra"))],
    )

    diff = compare_cassettes(old, new)

    assert len(diff.pairs) == 1
    assert diff.pairs[0].ambiguous
    assert len(diff.added) == 1
    assert diff.added[0].index == 1
    assert diff.added[0].ambiguous
    assert "already paired" in diff.added[0].note


def test_json_response_body_key_order_ignored(cassette):
    old = cassette(
        "old.yaml",
        [(make_request(), make_response(body=b'{"a": 1, "b": 2}'))],
    )
    new = cassette(
        "new.yaml",
        [(make_request(), make_response(body=b'{"b": 2, "a": 1}'))],
    )

    assert compare_cassettes(old, new).identical


def test_custom_matchers(cassette):
    old = cassette("old.yaml", [(make_request(uri="http://old.example.com/"), make_response())])
    new = cassette("new.yaml", [(make_request(uri="http://new.example.com/"), make_response())])

    assert compare_cassettes(old, new).removed  # default matchers: no pairing
    diff = compare_cassettes(old, new, match_on=["method"])
    assert len(diff.pairs) == 1
    assert diff.matcher_names == ["method"]


def test_unknown_matcher_rejected(cassette):
    path = cassette("old.yaml", [(make_request(), make_response())])
    with pytest.raises(KeyError, match="Matcher"):
        compare_cassettes(path, path, match_on=["nope"])


def test_render_mentions_positions_and_summary(cassette):
    old = cassette("old.yaml", [(make_request(), make_response(body=b"old"))])
    new = cassette("new.yaml", [(make_request(), make_response(body=b"new"))])

    report = compare_cassettes(old, new).render()

    assert "old #0 <-> new #0" in report
    assert "CHANGED" in report
    assert "Summary: 1 changed, 0 added, 0 removed, 0 unchanged, 0 ambiguous" in report


def test_to_dict_round_trip(cassette):
    old = cassette("old.yaml", [(make_request(), make_response(body=b"old"))])
    new = cassette("new.yaml", [(make_request(), make_response(body=b"new"))])

    data = compare_cassettes(old, new).to_dict()

    assert data["changed"][0]["old_index"] == 0
    assert data["changed"][0]["new_index"] == 0
    json.dumps(data)  # JSON-serializable


def test_cli_exit_codes_and_json(cassette, capsys):
    same_a = cassette("a.yaml", [(make_request(), make_response())])
    same_b = cassette("b.yaml", [(make_request(), make_response())])
    different = cassette("c.yaml", [(make_request(), make_response(code=404))])

    assert main([same_a, same_b]) == 0
    assert main([same_a, different]) == 1
    capsys.readouterr()
    assert main([same_a, different, "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["changed"][0]["differences"]
    assert main(["missing.yaml", different]) == 2
    assert main([same_a, different, "--match-on", "method", "uri"]) == 1


def test_positions_point_back_into_files(cassette):
    old = cassette(
        "old.yaml",
        [
            (make_request(uri="http://example.com/0"), make_response()),
            (make_request(uri="http://example.com/1"), make_response()),
            (make_request(uri="http://example.com/2"), make_response(body=b"old")),
        ],
    )
    new = cassette(
        "new.yaml",
        [
            (make_request(uri="http://example.com/new"), make_response()),
            (make_request(uri="http://example.com/0"), make_response()),
            (make_request(uri="http://example.com/1"), make_response()),
            (make_request(uri="http://example.com/2"), make_response(body=b"new")),
        ],
    )

    diff = compare_cassettes(old, new)

    assert [(p.old_index, p.new_index) for p in diff.pairs] == [(0, 1), (1, 2), (2, 3)]
    assert (diff.changed[0].old_index, diff.changed[0].new_index) == (2, 3)
    assert [entry.index for entry in diff.added] == [0]
