import asyncio
import contextlib
import io

import pytest

import ijson
from ijson import asyncio as ijson_asyncio

from .test_base import JSON, JSON_EVENTS


ARRAY_JSON = b'[{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}]'
ARRAY_ITEMS = [{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}]


class ControllableAsyncReader:
    """An async reader whose read() calls can be made to block on demand."""

    def __init__(self, data):
        self.data = io.BytesIO(data)
        self.blocking = False
        self.cancelled_reads = 0
        self.block_started = asyncio.Event()

    async def read(self, n=-1):
        if self.blocking:
            self.block_started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled_reads += 1
                raise
        return self.data.read(n)


def run(coro):
    return asyncio.run(coro)


def make_items_reader(backend, reader, **kwargs):
    return backend.items_cancellable_async(
        reader, 'item', buf_size=4, **kwargs
    )


async def _cancel_while_blocked(reader, awaitable):
    reader.block_started.clear()
    task = asyncio.ensure_future(awaitable)
    await reader.block_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class TestCancellableAsyncIterable:

    def test_is_a_plain_async_iterable(self, backend):
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = []
            async for item in make_items_reader(backend, reader):
                records.append(item)
            return records
        assert run(main()) == ARRAY_ITEMS

    def test_next_raises_stop_async_iteration_at_eof(self, backend):
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = make_items_reader(backend, reader)
            collected = []
            while True:
                try:
                    collected.append(await records.next())
                except StopAsyncIteration:
                    break
            return collected
        assert run(main()) == ARRAY_ITEMS

    def test_reuse_across_attempt_boundaries(self, backend):
        """Ending an attempt without a read() in flight keeps the reader."""
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = make_items_reader(backend, reader)
            collected = [await records.next()]

            # The crawl attempt ends while no read() is in flight; the reader
            # is simply picked up by the next attempt.
            idle = asyncio.ensure_future(asyncio.sleep(10))
            idle.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await idle

            for _ in range(3):
                collected.append(await records.next())
            with pytest.raises(StopAsyncIteration):
                await records.next()
            return collected
        assert run(main()) == ARRAY_ITEMS

    def test_cancel_between_records_resumes_without_duplicates(self, backend):
        """A cancelled next() resumes on the next not-yet-returned record."""
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = make_items_reader(
                backend, reader, recoverable_reads=True
            )
            collected = [await records.next()]
            reader.blocking = True
            await _cancel_while_blocked(reader, records.next())
            assert reader.cancelled_reads == 1
            reader.blocking = False
            while True:
                try:
                    collected.append(await records.next())
                except StopAsyncIteration:
                    break
            return collected
        assert run(main()) == ARRAY_ITEMS

    def test_cancel_mid_record_keeps_the_half_built_record(self, backend):
        """A halfway-interrupted record is finished on later calls."""
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = make_items_reader(
                backend, reader, recoverable_reads=True
            )
            collected = [await records.next()]
            # Several timeouts while the second record is being assembled.
            for _ in range(3):
                reader.blocking = True
                await _cancel_while_blocked(reader, records.next())
                reader.blocking = False
            # Everything yielded so far is a complete, expected record.
            assert collected == ARRAY_ITEMS[:1]
            while True:
                try:
                    collected.append(await records.next())
                except StopAsyncIteration:
                    break
            return collected
        assert run(main()) == ARRAY_ITEMS

    def test_events_not_lost_or_duplicated_after_cancellation(self, backend):
        async def main():
            blocking_reader = ControllableAsyncReader(JSON)
            cancellable = backend.basic_parse_cancellable_async(
                blocking_reader, buf_size=3, recoverable_reads=True
            )
            events = []
            with contextlib.suppress(StopAsyncIteration):
                while len(events) < len(JSON_EVENTS) // 2:
                    events.append(await cancellable.next())
            for _ in range(4):
                blocking_reader.blocking = True
                await _cancel_while_blocked(
                    blocking_reader, cancellable.next()
                )
                blocking_reader.blocking = False
            with contextlib.suppress(StopAsyncIteration):
                while True:
                    events.append(await cancellable.next())
            return events
        assert run(main()) == JSON_EVENTS

    def test_interrupted_read_marks_stream_unrecoverable(self, backend):
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = make_items_reader(backend, reader)
            assert records.recoverable
            await records.next()
            reader.blocking = True
            await _cancel_while_blocked(reader, records.next())
            assert reader.cancelled_reads == 1
            assert not records.recoverable
            reader.blocking = False
            for _ in range(2):
                with pytest.raises(ijson_asyncio.UnrecoverableStreamError):
                    await records.next()
            assert not records.recoverable
        run(main())

    def test_wait_for_timeout_is_still_raised_to_caller(self, backend):
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = make_items_reader(backend, reader)
            reader.blocking = True
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(records.next(), timeout=0.01)
            assert not records.recoverable
            with pytest.raises(ijson_asyncio.UnrecoverableStreamError):
                await records.next()
        run(main())

    def test_recoverable_reads_allows_full_resumption(self, backend):
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = make_items_reader(
                backend, reader, recoverable_reads=True
            )
            collected = []
            for expected in ARRAY_ITEMS:
                reader.blocking = True
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(records.next(), timeout=0.01)
                assert records.recoverable
                reader.blocking = False
                collected.append(await records.next())
                assert collected[-1] == expected
            with pytest.raises(StopAsyncIteration):
                await records.next()
            return collected
        assert run(main()) == ARRAY_ITEMS

    def test_cancellation_during_initial_read_probe(self, backend):
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            reader.blocking = True
            records = make_items_reader(backend, reader)
            await _cancel_while_blocked(reader, records.next())
            assert not records.recoverable
            reader.blocking = False
            with pytest.raises(ijson_asyncio.UnrecoverableStreamError):
                await records.next()
        run(main())

    def test_parse_and_kvitems_pipelines(self, backend):
        async def main():
            reader = ControllableAsyncReader(JSON)
            parse_records = backend.parse_cancellable_async(
                reader, buf_size=8, recoverable_reads=True
            )
            events = []
            with contextlib.suppress(StopAsyncIteration):
                while True:
                    events.append(await parse_records.next())
            assert events == list(ijson.parse(io.BytesIO(JSON)))

            kv_reader = ControllableAsyncReader(JSON)
            kv_records = backend.kvitems_cancellable_async(
                kv_reader, 'docs.item', buf_size=8, recoverable_reads=True
            )
            pairs = []
            with contextlib.suppress(StopAsyncIteration):
                while True:
                    pairs.append(await kv_records.next())
            assert pairs == list(ijson.kvitems(io.BytesIO(JSON), 'docs.item'))
        run(main())

    def test_top_level_factories_exist_on_every_backend(self, backend):
        for name in (
            'basic_parse_cancellable_async',
            'parse_cancellable_async',
            'items_cancellable_async',
            'kvitems_cancellable_async',
        ):
            assert callable(getattr(backend, name))
            assert callable(getattr(ijson, name))
        if backend.backend_name == ijson.backend_name:
            for name in (
                'basic_parse_cancellable_async',
                'parse_cancellable_async',
                'items_cancellable_async',
                'kvitems_cancellable_async',
            ):
                assert getattr(ijson, name) is getattr(backend, name)

    def test_regular_async_iteration_still_works(self, backend):
        """The one-shot async iteration interface is unchanged in behavior."""
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            regular = backend.items_async(reader, 'item', buf_size=4)
            return [item async for item in regular]
        assert run(main()) == ARRAY_ITEMS

    def test_aclose_ends_iteration_and_releases_the_pipeline(self, backend):
        async def main():
            reader = ControllableAsyncReader(ARRAY_JSON)
            records = make_items_reader(backend, reader)
            first = await records.next()
            await records.aclose()
            with pytest.raises(StopAsyncIteration):
                await records.next()
            leftovers = [item async for item in records]
            assert leftovers == []
            return first
        assert run(main()) == ARRAY_ITEMS[0]
