import glob
import os

import pytest
import yaml

from vcr.cassette import Cassette
from vcr.config import VCR
from vcr.persisters.filesystem import CANDIDATE_SUFFIX, CassetteConflictError, CassetteNotFoundError
from vcr.request import Request
from vcr.update_strategy import UpdateStrategy, validate_update_strategy


def _interaction(uri="http://example.com/", body=b"hello"):
    return Request("GET", uri, "", {}), {"status": {"code": 200, "message": "OK"}, "body": {"string": body}}


def _write_cassette(path, body):
    cassette = Cassette(path=path)
    cassette.append(*_interaction(body=body))
    cassette._save(force=True)


def _saved_bodies(path):
    with open(path) as f:
        interactions = yaml.safe_load(f)["interactions"]
    return [interaction["response"]["body"]["string"].encode() for interaction in interactions]


def _candidate_files(path):
    return glob.glob(os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.*{CANDIDATE_SUFFIX}"))


def test_validate_update_strategy_accepts_enum_and_string():
    assert validate_update_strategy("replace_on_success") is UpdateStrategy.REPLACE_ON_SUCCESS
    assert validate_update_strategy(UpdateStrategy.REPLACE) is UpdateStrategy.REPLACE


def test_validate_update_strategy_rejects_unknown_value():
    with pytest.raises(ValueError, match="not a valid update_strategy"):
        validate_update_strategy("nope")


def test_default_strategy_writes_directly_and_leaves_no_candidate(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    cassette = Cassette(path=path)
    cassette.append(*_interaction())
    cassette._save()

    assert os.path.exists(path)
    assert _candidate_files(path) == []


def test_success_replaces_the_cassette_and_removes_the_candidate(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    _write_cassette(path, b"old")

    cassette = Cassette.load(path=path, update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS)
    cassette.append(*_interaction(uri="http://example.com/new", body=b"new"))
    cassette._save()

    assert _saved_bodies(path) == [b"old", b"new"]
    assert _candidate_files(path) == []


def test_exception_keeps_the_previous_cassette_and_stages_a_candidate(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    _write_cassette(path, b"old")

    cassette = Cassette.load(path=path, update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS)
    cassette.append(*_interaction(uri="http://example.com/half", body=b"halfway"))
    cassette._save(exception_was_raised=True)

    # The working cassette is still the previous usable recording.
    assert _saved_bodies(path) == [b"old"]
    candidates = _candidate_files(path)
    assert len(candidates) == 1
    with open(candidates[0]) as f:
        candidate_bodies = [
            interaction["response"]["body"]["string"].encode()
            for interaction in yaml.safe_load(f)["interactions"]
        ]
    assert candidate_bodies == [b"old", b"halfway"]

    # The next run loads the old cassette, never the failed candidate.
    reloaded = Cassette.load(path=path)
    assert [response["body"]["string"] for response in reloaded.responses] == [b"old"]


def test_first_recording_success_creates_the_cassette(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    assert not os.path.exists(path)

    cassette = Cassette.load(path=path, update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS)
    cassette.append(*_interaction(body=b"first"))
    cassette._save()

    assert os.path.exists(path)
    assert _saved_bodies(path) == [b"first"]
    assert _candidate_files(path) == []


def test_first_recording_exception_leaves_only_a_candidate(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    cassette = Cassette.load(path=path, update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS)
    cassette.append(*_interaction(body=b"halfway"))
    cassette._save(exception_was_raised=True)

    # No cassette is created, so a later run starts fresh rather than reading
    # the unfinished recording.
    assert not os.path.exists(path)
    assert len(_candidate_files(path)) == 1
    assert Cassette.load(path=path).data == []


def test_context_manager_stages_candidate_when_the_scenario_raises(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    _write_cassette(path, b"old")

    with (
        pytest.raises(RuntimeError),
        Cassette.use(
            path=path,
            update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS,
        ) as cassette,
    ):
        cassette.append(*_interaction(body=b"halfway"))
        raise RuntimeError("scenario blew up halfway")

    assert _saved_bodies(path) == [b"old"]
    assert len(_candidate_files(path)) == 1


def test_context_manager_replaces_the_cassette_on_success(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    with Cassette.use(path=path, update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS) as cassette:
        cassette.append(*_interaction(body=b"first"))

    assert _saved_bodies(path) == [b"first"]
    assert _candidate_files(path) == []


def test_record_on_exception_false_still_stages_a_candidate_without_replacing(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    _write_cassette(path, b"old")

    with (
        pytest.raises(RuntimeError),
        Cassette.use(
            path=path,
            update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS,
            record_on_exception=False,
        ) as cassette,
    ):
        cassette.append(*_interaction(body=b"halfway"))
        raise RuntimeError("boom")

    assert _saved_bodies(path) == [b"old"]
    assert len(_candidate_files(path)) == 1


def test_late_finisher_cannot_silently_overwrite_the_earlier_result(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    _write_cassette(path, b"v0")

    # Two refreshes start from the same old version.
    first = Cassette.load(path=path, update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS)
    second = Cassette.load(path=path, update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS)

    first.append(*_interaction(uri="http://example.com/a", body=b"A"))
    first._save()

    second.append(*_interaction(uri="http://example.com/b", body=b"B"))
    with pytest.raises(CassetteConflictError):
        second._save()

    # The earlier finisher's recording wins; the late one's candidate is kept.
    assert _saved_bodies(path) == [b"v0", b"A"]
    assert os.path.exists(second._candidate_path)


def test_context_manager_unpatches_even_when_commit_conflicts(tmpdir):
    import http.client as httplib

    from vcr.patch import _HTTPConnection

    path = str(tmpdir.join("cassette.yaml"))
    _write_cassette(path, b"v0")

    with (
        pytest.raises(CassetteConflictError),
        Cassette.use(
            path=path,
            update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS,
        ) as cassette,
    ):
        # VCR's HTTP patch is active inside the context.
        assert httplib.HTTPConnection is not _HTTPConnection
        cassette.append(*_interaction(uri="http://example.com/b", body=b"B"))
        # Another process commits a different version before this one exits.
        other = Cassette(path=path)
        other.append(*_interaction(uri="http://example.com/other", body=b"OTHER"))
        other._save(force=True)

    # Despite the conflict, the HTTP patch was removed on the way out.
    assert httplib.HTTPConnection is _HTTPConnection
    # The other process's recording stands; the conflicting candidate is kept.
    assert _saved_bodies(path) == [b"OTHER"]
    assert len(_candidate_files(path)) == 1


def test_vcr_passes_the_update_strategy_through_to_the_cassette(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    my_vcr = VCR(update_strategy="replace_on_success")
    with my_vcr.use_cassette(path) as cassette:
        assert cassette.update_strategy is UpdateStrategy.REPLACE_ON_SUCCESS


def test_vcr_rejects_an_unknown_update_strategy():
    with pytest.raises(ValueError, match="not a valid update_strategy"):
        VCR(update_strategy="nope")


class _DirectOnlyPersister:
    """A custom persister without the candidate/commit protocol."""

    @staticmethod
    def load_cassette(cassette_path, serializer):
        raise CassetteNotFoundError

    @staticmethod
    def save_cassette(cassette_path, cassette_dict, serializer):
        with open(cassette_path, "w") as f:
            f.write(yaml.dump({"interactions": []}))


def test_persister_without_candidate_protocol_falls_back_to_direct_write(tmpdir):
    path = str(tmpdir.join("cassette.yaml"))
    cassette = Cassette.load(
        path=path,
        persister=_DirectOnlyPersister,
        update_strategy=UpdateStrategy.REPLACE_ON_SUCCESS,
    )
    assert not cassette._uses_candidate_updates
    cassette.append(*_interaction())
    cassette._save(exception_was_raised=True)

    # Direct write means the cassette exists even though the scenario failed,
    # and no candidate protocol was attempted.
    assert os.path.exists(path)
    assert _candidate_files(path) == []
