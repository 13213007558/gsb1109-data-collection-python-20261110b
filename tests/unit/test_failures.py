import http.client

import pytest

from vcr.failures import (
    BEFORE_RESPONSE,
    DURING_BODY,
    PlaybackError,
    is_before_response_failure,
    make_failure,
    normalize_failure,
    playback_exception,
    register_playback_exception_factory,
    unregister_playback_exception_factory,
)


def test_make_failure_records_dotted_type_and_phase():
    exc = http.client.RemoteDisconnected("connection closed without response")
    failure = make_failure(exc, BEFORE_RESPONSE)
    assert failure == {
        "type": "http.client.RemoteDisconnected",
        "message": "connection closed without response",
        "phase": "before_response",
    }


def test_make_failure_rejects_unknown_phase():
    with pytest.raises(ValueError):
        make_failure(ValueError("x"), "halfway")


def test_normalize_failure_defaults_to_during_body():
    failure = normalize_failure({"type": "http.client.IncompleteRead", "message": "cut"})
    assert failure["phase"] == DURING_BODY
    assert failure["message"] == "cut"


@pytest.mark.parametrize(
    "bad",
    [
        {"message": "no type", "phase": DURING_BODY},
        {"type": "x.Thing", "phase": "nope"},
        "not a dict",
    ],
)
def test_normalize_failure_rejects_bad_payloads(bad):
    with pytest.raises((TypeError, ValueError)):
        normalize_failure(bad)


def test_playback_exception_rebuilds_stdlib_class():
    failure = make_failure(http.client.RemoteDisconnected("closed without response"), DURING_BODY)
    exc = playback_exception(failure)
    assert isinstance(exc, http.client.RemoteDisconnected)
    # The recorded message is what's reconstructed; no exception is pickled.
    assert str(exc) == "closed without response"
    assert isinstance(playback_exception(make_failure(http.client.IncompleteRead(b"x"), DURING_BODY)),
                      http.client.IncompleteRead)


def test_playback_exception_falls_back_for_unknown_type():
    exc = playback_exception(
        {"type": "some.uninstalled.thing.Error", "message": "boom", "phase": BEFORE_RESPONSE},
    )
    assert isinstance(exc, PlaybackError)
    assert exc.failure["type"] == "some.uninstalled.thing.Error"
    assert "boom" in str(exc)


def test_registered_factory_rebuilds_non_standard_exception():
    type_path = "tests.custom.PlaceholderError"

    def factory(failure):
        return RuntimeError(f"rebuilt: {failure['message']}")

    register_playback_exception_factory(type_path, factory)
    try:
        exc = playback_exception({"type": type_path, "message": "hi", "phase": DURING_BODY})
        assert isinstance(exc, RuntimeError)
        assert str(exc) == "rebuilt: hi"
    finally:
        unregister_playback_exception_factory(type_path)
    assert isinstance(
        playback_exception({"type": type_path, "message": "hi", "phase": DURING_BODY}),
        PlaybackError,
    )


def test_register_factory_validates_arguments():
    with pytest.raises(TypeError):
        register_playback_exception_factory(123, lambda f: None)
    with pytest.raises(TypeError):
        register_playback_exception_factory("x.Thing", "not callable")


def test_is_before_response_failure_distinguishes_phases():
    assert is_before_response_failure(
        {
            "failure": {"type": "http.client.RemoteDisconnected", "message": "", "phase": BEFORE_RESPONSE},
        },
    )
    assert not is_before_response_failure(
        {
            "failure": {"type": "http.client.IncompleteRead", "message": "", "phase": DURING_BODY},
        },
    )
    assert not is_before_response_failure({"body": {"string": b"ok"}})
    assert not is_before_response_failure({"failure": "not a dict"})
