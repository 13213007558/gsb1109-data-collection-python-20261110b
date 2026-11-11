"""Concurrent, per-task cassette isolation with ``use_contextvar``.

Two cassettes whose lifetimes overlap inside one event loop can't rely on the
process-wide class patch: with the default behavior the cassette that entered
last answers every request. ``use_contextvar=True`` binds each cassette to the
task that entered it via a ``contextvars`` variable, so requests are routed to
the cassette of the task that makes them.

Everything here replays pre-written cassettes in ``record_mode=NONE``, so no
socket is ever opened, except where a task deliberately has no cassette and is
asserted to keep the real network behavior.
"""

import asyncio

import pytest

pytest.importorskip("asyncio")
aiohttp = pytest.importorskip("aiohttp")

import vcr  # noqa: E402
from vcr.context import current_cassette  # noqa: E402
from vcr.persisters.filesystem import FilesystemPersister  # noqa: E402
from vcr.request import Request  # noqa: E402
from vcr.serializers import yamlserializer  # noqa: E402

URL_A = "http://vcr-isolation.example/a"
URL_B = "http://vcr-isolation.example/b"
URL_OUTER = "http://vcr-isolation.example/outer"
URL_INNER = "http://vcr-isolation.example/inner"


def _write_cassette(cassette_path, uri, body):
    request = Request("GET", uri, None, {})
    response = {
        "status": {"code": 200, "message": "OK"},
        "headers": {"Content-Type": ["text/plain"]},
        "body": {"string": body.encode()},
    }
    FilesystemPersister.save_cassette(
        str(cassette_path),
        {"requests": [request], "responses": [response]},
        yamlserializer,
    )


async def _get(url):
    async with aiohttp.ClientSession() as session:
        response = await session.get(url)
        return await response.text()


def test_overlapping_cassettes_are_isolated_per_task(tmp_path):
    path_a = tmp_path / "a.yaml"
    path_b = tmp_path / "b.yaml"
    _write_cassette(path_a, URL_A, "alpha")
    _write_cassette(path_b, URL_B, "bravo")
    my_vcr = vcr.VCR(use_contextvar=True)

    async def run():
        barrier = asyncio.Barrier(2)
        played = []

        async def worker(cassette_path, url, expected):
            with my_vcr.use_cassette(str(cassette_path), record_mode=vcr.mode.NONE) as cassette:
                # Both cassettes are installed before either request is made.
                await barrier.wait()
                body = await _get(url)
                assert body == expected
                played.append(cassette)

        await asyncio.gather(
            worker(path_a, URL_A, "alpha"),
            worker(path_b, URL_B, "bravo"),
        )
        return played

    cassettes = asyncio.run(run())
    assert sorted(cassette.play_count for cassette in cassettes) == [1, 1]


def test_default_patching_lets_the_last_cassette_capture_everything(tmp_path):
    # Documents why isolation has to be opted into: without use_contextvar the
    # process-wide patch means only the last-entered cassette is reachable.
    path_a = tmp_path / "a.yaml"
    path_b = tmp_path / "b.yaml"
    _write_cassette(path_a, URL_A, "alpha")
    _write_cassette(path_b, URL_B, "bravo")
    my_vcr = vcr.VCR()

    async def run():
        barrier = asyncio.Barrier(2)

        async def worker_a():
            with my_vcr.use_cassette(str(path_a), record_mode=vcr.mode.NONE):
                await barrier.wait()
                await _get(URL_A)

        async def worker_b():
            with my_vcr.use_cassette(str(path_b), record_mode=vcr.mode.NONE):
                await barrier.wait()
                await _get(URL_B)

        await asyncio.gather(worker_a(), worker_b())

    with pytest.raises(vcr.errors.CannotOverwriteExistingCassetteException):
        asyncio.run(run())


def test_nested_cassette_restores_the_outer_one(tmp_path):
    outer_path = tmp_path / "outer.yaml"
    inner_path = tmp_path / "inner.yaml"
    _write_cassette(outer_path, URL_OUTER, "outer-response")
    _write_cassette(inner_path, URL_INNER, "inner-response")
    my_vcr = vcr.VCR(use_contextvar=True)

    async def run():
        with my_vcr.use_cassette(
            str(outer_path),
            record_mode=vcr.mode.NONE,
            allow_playback_repeats=True,
        ) as outer:
            assert current_cassette.get() is outer
            assert await _get(URL_OUTER) == "outer-response"

            with my_vcr.use_cassette(str(inner_path), record_mode=vcr.mode.NONE) as inner:
                assert current_cassette.get() is inner
                assert await _get(URL_INNER) == "inner-response"
                assert inner.play_count == 1

            # The inner cassette exited: the caller's outer cassette is back.
            assert current_cassette.get() is outer
            assert await _get(URL_OUTER) == "outer-response"
            assert outer.play_count == 2

        assert current_cassette.get() is None

    asyncio.run(run())


def test_cancelling_one_task_keeps_the_other_cassette_working(tmp_path):
    path_a = tmp_path / "a.yaml"
    path_b = tmp_path / "b.yaml"
    _write_cassette(path_a, URL_A, "alpha")
    _write_cassette(path_b, URL_B, "bravo")
    my_vcr = vcr.VCR(use_contextvar=True)

    async def run():
        barrier = asyncio.Barrier(2)

        async def hold_cassette_a():
            with my_vcr.use_cassette(str(path_a), record_mode=vcr.mode.NONE):
                await barrier.wait()
                await asyncio.sleep(30)

        async def play_cassette_b():
            with my_vcr.use_cassette(
                str(path_b),
                record_mode=vcr.mode.NONE,
                allow_playback_repeats=True,
            ) as cassette_b:
                await barrier.wait()
                assert await _get(URL_B) == "bravo"
                await asyncio.sleep(0)
                assert await _get(URL_B) == "bravo"
                return cassette_b

        task_a = asyncio.create_task(hold_cassette_a())
        task_b = asyncio.create_task(play_cassette_b())
        cassette_b = await task_b
        task_a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_a
        return cassette_b

    cassette_b = asyncio.run(run())
    assert cassette_b.play_count == 2


def test_task_without_a_cassette_keeps_real_network_behavior(tmp_path):
    path_a = tmp_path / "a.yaml"
    _write_cassette(path_a, URL_A, "alpha")
    my_vcr = vcr.VCR(use_contextvar=True)

    async def run():
        # Created before any cassette is entered, this task copies a context
        # with no cassette bound. While it waits to run, the current task
        # installs a cassette (and the process-wide aiohttp patch); its request
        # must still take the real network path instead of being replayed.
        live_task = asyncio.create_task(_get("http://127.0.0.1:1/live"))

        with my_vcr.use_cassette(str(path_a), record_mode=vcr.mode.NONE) as cassette:
            with pytest.raises(aiohttp.ClientConnectorError):
                await live_task
            assert cassette.play_count == 0

    asyncio.run(run())
