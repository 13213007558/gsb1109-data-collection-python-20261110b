"""Offline record/replay of truncated and never-started responses.

These use httpx's in-memory ``MockTransport`` (which VCR patches), so nothing
ever touches a real address. A first pass records the interaction, including
its failure, into a cassette file; a second pass plays that file back.
"""

import asyncio

import pytest

import vcr

httpx = pytest.importorskip("httpx")


class _TruncatedStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"partial body"
        raise httpx.ReadError("server stopped sending")

    def close(self):
        pass


class _TruncatedAsyncStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"partial body"
        raise httpx.ReadError("server stopped sending")

    async def aclose(self):
        pass


def _read_streamed_body(client):
    chunks = []
    with client.stream("GET", "http://example.invalid/") as response:
        for chunk in response.iter_raw():
            chunks.append(chunk)
    return b"".join(chunks)


def test_during_body_failure_is_recorded_and_replayed_sync(tmp_path):
    cassette_path = tmp_path / "truncated.yaml"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-length": "100"}, stream=_TruncatedStream()),
    )

    with (
        httpx.Client(transport=transport) as client,
        vcr.use_cassette(str(cassette_path)) as cassette,
    ):
        with pytest.raises(httpx.ReadError, match="server stopped sending"):
            _read_streamed_body(client)
        # Checked before the cassette is saved: saving converts body bytes to
        # YAML text in place, as it does for ordinary responses.
        recorded = cassette.responses[0]
        assert recorded["body"]["string"] == b"partial body"
        assert recorded["failure"] == {
            "type": "httpx.ReadError",
            "message": "server stopped sending",
            "phase": "during_body",
        }
    # The failure is ordinary cassette data, readable in the saved YAML.
    on_disk = cassette_path.read_text()
    assert "failure:" in on_disk
    assert "during_body" in on_disk

    # Replay: no MockTransport handler is even consulted, and the same bytes are
    # delivered before the same error.
    replay_transport = httpx.MockTransport(lambda request: pytest.fail("replay must not reach a transport"))
    with (
        httpx.Client(transport=replay_transport) as client,
        vcr.use_cassette(str(cassette_path)),
    ):
        with pytest.raises(httpx.ReadError, match="server stopped sending"):
            _read_streamed_body(client)


def test_before_response_failure_is_distinct_and_replayed_sync(tmp_path):
    cassette_path = tmp_path / "never_started.yaml"
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused")))

    with (
        httpx.Client(transport=transport) as client,
        vcr.use_cassette(str(cassette_path)) as cassette,
    ):
        with pytest.raises(httpx.ConnectError, match="refused"):
            client.get("http://example.invalid/")
        recorded = cassette.responses[0]
        # A never-started response carries no body and is distinct from a
        # truncated one by its phase.
        assert recorded["body"]["string"] == b""
        assert recorded["failure"]["phase"] == "before_response"
        assert recorded["failure"]["type"] == "httpx.ConnectError"

    replay_transport = httpx.MockTransport(lambda request: pytest.fail("replay must not reach a transport"))
    with (
        httpx.Client(transport=replay_transport) as client,
        vcr.use_cassette(str(cassette_path)),
    ):
        with pytest.raises(httpx.ConnectError, match="refused"):
            client.get("http://example.invalid/")


async def _collect_async_body(client):
    chunks = []
    async with client.stream("GET", "http://example.invalid/") as response:
        async for chunk in response.aiter_raw():
            chunks.append(chunk)
    return b"".join(chunks)


def test_failures_record_and_replay_async(tmp_path):
    cassette_path = tmp_path / "truncated_async.yaml"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=_TruncatedAsyncStream()),
    )

    async def record():
        async with httpx.AsyncClient(transport=transport) as client:
            with vcr.use_cassette(str(cassette_path)):
                with pytest.raises(httpx.ReadError, match="server stopped sending"):
                    await _collect_async_body(client)

    asyncio.run(record())

    replay_transport = httpx.MockTransport(lambda request: pytest.fail("replay must not reach a transport"))

    async def replay():
        async with httpx.AsyncClient(transport=replay_transport) as client:
            with vcr.use_cassette(str(cassette_path)):
                with pytest.raises(httpx.ReadError, match="server stopped sending"):
                    await _collect_async_body(client)

    asyncio.run(replay())


def test_successful_interaction_still_records_and_replays(tmp_path):
    # Regression guard: ordinary complete responses keep working end to end.
    cassette_path = tmp_path / "ok.yaml"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"all good"))

    with (
        httpx.Client(transport=transport) as client,
        vcr.use_cassette(str(cassette_path)),
    ):
        assert client.get("http://example.invalid/").read() == b"all good"

    replay_transport = httpx.MockTransport(lambda request: pytest.fail("replay must not reach a transport"))
    with (
        httpx.Client(transport=replay_transport) as client,
        vcr.use_cassette(str(cassette_path)),
    ):
        assert client.get("http://example.invalid/").read() == b"all good"
