"""Offline tests for the aiohttp stubs' failure recording and replay."""

import asyncio

import pytest

from vcr.cassette import Cassette
from vcr.request import Request

aiohttp = pytest.importorskip("aiohttp")

from vcr.stubs.aiohttp_stubs import (  # noqa: E402
    MockStream,
    _FailingMockStream,
    build_response,
    play_responses,
    record_response,
)

FAILURE = {
    "type": "aiohttp.ClientConnectionError",
    "message": "connection reset",
    "phase": "during_body",
}
REQUEST = Request("GET", "http://example.invalid/", None, {})


def _truncated_response(body=b"0123456789"):
    return build_response(
        REQUEST,
        {
            "status": {"code": 200, "message": "OK"},
            "headers": {},
            "body": {"string": body},
            "failure": FAILURE,
        },
        [],
    )


def test_truncated_stream_fails_at_a_fixed_body_position():
    async def go():
        stream = _truncated_response().content
        assert isinstance(stream, _FailingMockStream)
        received = b""
        # Different read sizes must all break after the same bytes.
        with pytest.raises(aiohttp.ClientConnectionError, match="connection reset"):
            for size in (4, 3, 10, 1):
                received += await stream.read(size)
        assert received == b"0123456789"
        # The failure is persistent on a dead stream.
        with pytest.raises(aiohttp.ClientConnectionError):
            await stream.read(1)

    asyncio.run(go())


def test_truncated_iter_any_delivers_every_byte_then_fails():
    async def go():
        stream = _truncated_response(b"chunk-data").content
        received = b""
        with pytest.raises(aiohttp.ClientConnectionError):
            async for chunk in stream.iter_any():
                received += chunk
        assert received == b"chunk-data"

    asyncio.run(go())


def test_response_read_raises_on_truncated_body():
    async def go():
        response = _truncated_response(b"xyz")
        with pytest.raises(aiohttp.ClientConnectionError):
            await response.read()

    asyncio.run(go())


def _in_flight_response(body_stream):
    # A response still being received: no cached body yet, like a real
    # ClientResponse while the cassette records it.
    response = build_response(
        REQUEST,
        {"status": {"code": 200, "message": "OK"}, "headers": {}, "body": {"string": b""}},
        [],
    )
    response._body = None
    response.content = body_stream
    return response


def test_record_response_captures_the_partial_body_and_failure():
    async def go():
        cassette = Cassette("test")
        response = _in_flight_response(_FailingMockStream(b"partial", FAILURE))
        with pytest.raises(aiohttp.ClientConnectionError):
            await record_response(cassette, REQUEST, response)
        assert len(cassette) == 1
        recorded = cassette.responses[0]
        assert recorded["body"]["string"] == b"partial"
        assert recorded["failure"]["phase"] == "during_body"
        # make_failure records the exception's canonical module path.
        assert recorded["failure"]["type"] == "aiohttp.client_exceptions.ClientConnectionError"
        assert recorded["failure"]["message"] == "connection reset"

    asyncio.run(go())


def test_record_response_records_a_complete_streaming_body():
    async def go():
        cassette = Cassette("test")
        response = _in_flight_response(MockStream(b"complete"))
        await record_response(cassette, REQUEST, response)
        recorded = cassette.responses[0]
        assert recorded["body"]["string"] == b"complete"
        assert "failure" not in recorded
        # The drained stream is replaced with a fresh, fully readable one.
        assert isinstance(response.content, MockStream)

    asyncio.run(go())


def test_record_response_uses_a_cached_redirect_body():
    async def go():
        # Followed redirects cache the body in ``_body`` and drain the stream.
        cassette = Cassette("test")
        response = build_response(
            REQUEST,
            {"status": {"code": 200, "message": "OK"}, "headers": {}, "body": {"string": b"cached"}},
            [],
        )
        assert response._body == b"cached"
        await record_response(cassette, REQUEST, response)
        assert cassette.responses[0]["body"]["string"] == b"cached"
        assert "failure" not in cassette.responses[0]

    asyncio.run(go())


def test_play_responses_raises_for_a_before_response_failure():
    class _Cassette:
        def play_response(self, request):
            return {
                "status": {"code": None, "message": None},
                "headers": {},
                "body": {"string": b""},
                "failure": {
                    "type": "aiohttp.ClientConnectionError",
                    "message": "never connected",
                    "phase": "before_response",
                },
            }

    with pytest.raises(aiohttp.ClientConnectionError, match="never connected"):
        play_responses(_Cassette(), REQUEST, {})


def test_play_responses_builds_a_normal_response():
    class _Cassette:
        def play_response(self, request):
            return {
                "status": {"code": 200, "message": "OK"},
                "headers": {},
                "body": {"string": b"plain"},
            }

    async def go():
        response = play_responses(_Cassette(), REQUEST, {})
        assert response.status == 200
        assert await response.read() == b"plain"

    asyncio.run(go())
