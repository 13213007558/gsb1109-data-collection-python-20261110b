'''
Asynchronous helpers for resumable record reading.

The :class:`cancellable_async_iterable` class wraps one of ijson's parsing
pipelines and adds cancellation-and-resume semantics on top of the usual
asynchronous iteration protocol. This is useful when a single read is subject
to a timeout (e.g. a crawl service that limits the time spent waiting on a
response stream) but the same stream is reused by the next attempt.
'''
import asyncio

from ijson import utils
from ijson.utils35 import _get_read, sendable_deque


class UnrecoverableStreamError(Exception):
    '''
    Raised when the underlying stream can no longer be consumed safely.

    This happens when a cancellation interrupted a ``read()`` call whose
    outcome cannot be determined: the read might have consumed bytes from the
    stream without returning them, so any further data would start at an
    unknown offset and records could be silently corrupted. The stream must be
    abandoned (e.g. by reopening the connection) instead of being read again.
    '''
    pass


class cancellable_async_iterable:
    '''
    An asynchronous record reader that survives cancellations.

    Works like the regular ``*_async`` iterables (it supports ``async for``),
    but records can also be pulled one at a time with :meth:`next` and the
    coroutine awaiting it can be cancelled (e.g. with ``asyncio.wait_for`` and
    a timeout). The reader is then left in a consistent state:

    * Records already handed to the caller are never produced again.
    * A cancellation that lands between two records simply pauses parsing;
      the following :meth:`next` call continues with the next record.
    * A cancellation that lands while a record is only half read retains the
      partially built record; subsequent :meth:`next` calls finish it using
      the newly read data. No already-consumed stream content is lost.

    The only unrecoverable situation is a cancellation that interrupts the
    underlying ``read()`` coroutine itself. In that case it is impossible to
    know whether the stream consumed bytes before being cancelled, so the
    reader marks itself as broken: the cancellation is re-raised to the
    caller as usual, and every later use raises
    :class:`UnrecoverableStreamError` instead of risking returning records
    assembled from the wrong byte offset. Readers whose ``read()`` guarantees
    that a cancelled call consumed nothing (and that the stream position is
    therefore unchanged, as is the case of ``asyncio.StreamReader``) can pass
    ``recoverable_reads=True`` to opt out of this protection.

    Instances are normally created through the ``<routine>_cancellable_async``
    helpers available on each backend, which take the same arguments as the
    corresponding ``<routine>_async`` functions.
    '''

    def __init__(self, f, buf_size, *coro_pipeline, recoverable_reads=False):
        self.events = sendable_deque()
        self.coro = utils.chain(self.events, *coro_pipeline)
        self.coro_finished = False
        self.f = f
        self.buf_size = buf_size
        self.read = None
        self.recoverable_reads = recoverable_reads
        self._broken = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.next()

    async def next(self):
        '''
        Return the next record, or raise ``StopAsyncIteration`` at EOF.

        Awaiting this coroutine can be cancelled. Unless the underlying
        ``read()`` was interrupted and its byte consumption cannot be
        accounted for, a later call resumes parsing exactly where this one
        stopped, without re-emitting records already returned.
        '''
        if self._broken:
            raise UnrecoverableStreamError(
                'the stream cannot be read anymore: a previous cancellation '
                'interrupted a read() call that might have consumed bytes'
            )
        if self.read is None:
            try:
                self.read = await _get_read(self.f)
            except asyncio.CancelledError:
                self._note_interrupted_read()
                raise
        while True:
            if self.events:
                return self.events.popleft()
            if self.coro_finished:
                raise StopAsyncIteration
            try:
                data = await self.read(self.buf_size)
            except asyncio.CancelledError:
                self._note_interrupted_read()
                raise
            try:
                self.coro.send(data)
            except StopIteration:
                self.coro_finished = True
                if self.events:
                    return self.events.popleft()
                raise StopAsyncIteration

    def _note_interrupted_read(self):
        if not self.recoverable_reads:
            self._broken = True
            self._close_pipeline()

    def _close_pipeline(self):
        # Closing a pipeline halfway through a document can surface a parse
        # error (e.g. IncompleteJSONError); like the synchronous generators do,
        # we swallow it since the reader is being deliberately torn down.
        if not self.coro_finished:
            try:
                self.coro.close()
            except Exception:
                pass
        self.coro_finished = True
        self.events.clear()

    async def aclose(self):
        '''
        Release the internal parsing pipeline.

        After this call the reader cannot be iterated anymore. Cancelling an
        in-flight :meth:`next` is not needed; this is meant for readers that
        are abandoned before reaching the end of the stream.
        '''
        self._close_pipeline()

    @property
    def recoverable(self):
        '''
        Whether this reader can still be used.

        ``False`` after a cancellation interrupted an underlying ``read()``
        on a reader that does not guarantee safe read cancellation.
        '''
        return not self._broken


def _make_basic_parse_cancellable_async(backend):
    def basic_parse_cancellable_async(f, buf_size=64 * 1024,
                                      recoverable_reads=False, **config):
        from ijson import common
        return cancellable_async_iterable(
            f, buf_size, recoverable_reads=recoverable_reads,
            *common._basic_parse_pipeline(backend, config)
        )
    return basic_parse_cancellable_async


def _make_parse_cancellable_async(backend):
    def parse_cancellable_async(f, buf_size=64 * 1024,
                                recoverable_reads=False, **config):
        from ijson import common
        return cancellable_async_iterable(
            f, buf_size, recoverable_reads=recoverable_reads,
            *common._parse_pipeline(backend, config)
        )
    return parse_cancellable_async


def _make_items_cancellable_async(backend):
    def items_cancellable_async(f, prefix, map_type=None, buf_size=64 * 1024,
                                recoverable_reads=False, **config):
        from ijson import common
        return cancellable_async_iterable(
            f, buf_size, recoverable_reads=recoverable_reads,
            *common._items_pipeline(backend, prefix, map_type, config)
        )
    return items_cancellable_async


def _make_kvitems_cancellable_async(backend):
    def kvitems_cancellable_async(f, prefix, map_type=None, buf_size=64 * 1024,
                                  recoverable_reads=False, **config):
        from ijson import common
        return cancellable_async_iterable(
            f, buf_size, recoverable_reads=recoverable_reads,
            *common._kvitems_pipeline(backend, prefix, map_type, config)
        )
    return kvitems_cancellable_async
