import os

import pytest
import yaml

from vcr.persisters.filesystem import (
    CANDIDATE_SUFFIX,
    CassetteConflictError,
    CassetteNotFoundError,
    FilesystemPersister,
)
from vcr.request import Request
from vcr.serializers import jsonserializer, yamlserializer


def _cassette_dict(uri="http://example.com/", body="v1"):
    return {
        "requests": [Request("GET", uri, "", {})],
        "responses": [{"body": {"string": body.encode()}}],
    }


def _read(path):
    with open(path) as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("content", ["", "\n"])
@pytest.mark.parametrize("serializer", [jsonserializer, yamlserializer])
def test_load_empty_cassette(tmpdir, serializer, content):
    cassette_path = tmpdir.join("empty_cassette")
    cassette_path.write(content)
    with pytest.raises(CassetteNotFoundError):
        FilesystemPersister.load_cassette(str(cassette_path), serializer)


def test_save_candidate_does_not_touch_the_cassette(tmpdir):
    cassette_path = tmpdir.join("cassette.yaml")
    FilesystemPersister.save_cassette(str(cassette_path), _cassette_dict(body="old"), yamlserializer)
    original = cassette_path.read()

    candidate_path = FilesystemPersister.save_candidate(
        str(cassette_path),
        _cassette_dict(body="new"),
        yamlserializer,
    )

    # The working cassette is untouched and the candidate is a separate file
    # that never shares the cassette's path.
    assert cassette_path.read() == original
    assert candidate_path != str(cassette_path)
    assert str(candidate_path).endswith(CANDIDATE_SUFFIX)
    assert _read(candidate_path)["interactions"][0]["response"]["body"]["string"] == "new"


def test_each_candidate_gets_a_unique_path(tmpdir):
    cassette_path = tmpdir.join("cassette.yaml")
    first = FilesystemPersister.save_candidate(str(cassette_path), _cassette_dict(), yamlserializer)
    second = FilesystemPersister.save_candidate(str(cassette_path), _cassette_dict(), yamlserializer)
    assert first != second


def test_commit_candidate_replaces_the_cassette_when_baseline_matches(tmpdir):
    cassette_path = tmpdir.join("cassette.yaml")
    FilesystemPersister.save_cassette(str(cassette_path), _cassette_dict(body="old"), yamlserializer)
    baseline = FilesystemPersister.version_signature(str(cassette_path))
    candidate_path = FilesystemPersister.save_candidate(
        str(cassette_path),
        _cassette_dict(body="new"),
        yamlserializer,
    )

    FilesystemPersister.commit_candidate(str(cassette_path), candidate_path, expected_signature=baseline)

    assert _read(str(cassette_path))["interactions"][0]["response"]["body"]["string"] == "new"
    assert not os.path.exists(candidate_path)


def test_commit_candidate_allows_first_recording_with_missing_baseline(tmpdir):
    # A cassette that doesn't exist has a None signature, which a still-missing
    # cassette on commit matches: the first recording is a defined case.
    cassette_path = tmpdir.join("cassette.yaml")
    assert FilesystemPersister.version_signature(str(cassette_path)) is None
    candidate_path = FilesystemPersister.save_candidate(
        str(cassette_path),
        _cassette_dict(body="first"),
        yamlserializer,
    )

    FilesystemPersister.commit_candidate(str(cassette_path), candidate_path, expected_signature=None)

    assert _read(str(cassette_path))["interactions"][0]["response"]["body"]["string"] == "first"


def test_commit_candidate_rejects_a_stale_baseline_and_keeps_both_files(tmpdir):
    cassette_path = tmpdir.join("cassette.yaml")
    FilesystemPersister.save_cassette(str(cassette_path), _cassette_dict(body="old"), yamlserializer)
    stale_baseline = FilesystemPersister.version_signature(str(cassette_path))
    candidate_path = FilesystemPersister.save_candidate(
        str(cassette_path),
        _cassette_dict(body="late"),
        yamlserializer,
    )
    # Another process commits its own refresh first, replacing the cassette.
    FilesystemPersister.save_cassette(str(cassette_path), _cassette_dict(body="earlier"), yamlserializer)

    with pytest.raises(CassetteConflictError):
        FilesystemPersister.commit_candidate(
            str(cassette_path),
            candidate_path,
            expected_signature=stale_baseline,
        )

    # The earlier finisher wins and the late candidate is kept for inspection.
    assert _read(str(cassette_path))["interactions"][0]["response"]["body"]["string"] == "earlier"
    assert os.path.exists(candidate_path)


def test_commit_candidate_without_check_always_replaces(tmpdir):
    cassette_path = tmpdir.join("cassette.yaml")
    FilesystemPersister.save_cassette(str(cassette_path), _cassette_dict(body="old"), yamlserializer)
    candidate_path = FilesystemPersister.save_candidate(
        str(cassette_path),
        _cassette_dict(body="new"),
        yamlserializer,
    )

    FilesystemPersister.commit_candidate(str(cassette_path), candidate_path)

    assert _read(str(cassette_path))["interactions"][0]["response"]["body"]["string"] == "new"


def test_discard_candidate_leaves_the_cassette_alone(tmpdir):
    cassette_path = tmpdir.join("cassette.yaml")
    FilesystemPersister.save_cassette(str(cassette_path), _cassette_dict(body="old"), yamlserializer)
    candidate_path = FilesystemPersister.save_candidate(
        str(cassette_path),
        _cassette_dict(body="new"),
        yamlserializer,
    )

    FilesystemPersister.discard_candidate(candidate_path)

    assert not os.path.exists(candidate_path)
    assert _read(str(cassette_path))["interactions"][0]["response"]["body"]["string"] == "old"
    # Discarding an already-removed candidate is not an error.
    FilesystemPersister.discard_candidate(candidate_path)


@pytest.mark.parametrize(
    "cassette_path, serializer, expected_message",
    [
        # Old JSON cassettes are recognized as old-format and pointed at the
        # migration script.
        ("tests/fixtures/migration/old_cassette.json", jsonserializer, "run the migration script"),
        # Old YAML cassettes contain python object tags the safe loader
        # refuses, so they surface the unsupported-tag error instead.
        ("tests/fixtures/migration/old_cassette.yaml", yamlserializer, "problem loading the cassette"),
    ],
)
def test_load_cassette_with_old_cassettes(cassette_path, serializer, expected_message):
    with pytest.raises(ValueError) as excinfo:
        FilesystemPersister.load_cassette(cassette_path, serializer)
    assert expected_message in excinfo.exconly()


@pytest.mark.parametrize(
    "cassette_path, serializer",
    [
        ("tests/fixtures/migration/not_cassette.txt", jsonserializer),
        ("tests/fixtures/migration/not_cassette.txt", yamlserializer),
    ],
)
def test_load_cassette_with_invalid_cassettes(cassette_path, serializer):
    with pytest.raises(Exception) as excinfo:
        FilesystemPersister.load_cassette(cassette_path, serializer)
    assert "run the migration script" not in excinfo.exconly()
