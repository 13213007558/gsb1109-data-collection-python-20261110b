import pickle
import tempfile

from ijson import compat
from ijson.common import ObjectBuilder

try:
    from builtins import aiter, anext
except ImportError:  # Py<3.10
    _MISSING = object()

    def aiter(obj):
        return obj.__aiter__()

    async def anext(ait, default=_MISSING):
        try:
            return await ait.__anext__()
        except StopAsyncIteration:
            if default is _MISSING:
                raise
            return default


def _to_bytes(chunk, warned: bool):
    if isinstance(chunk, bytes):
        return chunk, warned
    if isinstance(chunk, str):
        if not warned:
            compat._warn_and_return(None)
            warned = True
        return chunk.encode("utf-8"), warned
    raise TypeError("from_iter expects an iterable of bytes or str")


class IterReader:
    """File-like object backed by a byte iterator."""

    def __init__(self, byte_iter):
        self._iter = byte_iter
        self._warned = False

    def read(self, n: int) -> bytes:
        if n == 0:
            return b""
        chunk, self._warned = _to_bytes(next(self._iter, b""), self._warned)
        return chunk


class AiterReader:
    """Async file-like object backed by an async byte iterator."""

    def __init__(self, byte_aiter):
        self._aiter = byte_aiter
        self._warned = False

    async def read(self, n: int) -> bytes:
        if n == 0:
            return b""
        chunk, self._warned = _to_bytes(await anext(self._aiter, b""), self._warned)
        return chunk


def from_iter(byte_iter):
    """Convert a byte iterable (sync or async) to a file-like object."""
    if hasattr(byte_iter, "__aiter__"):
        return AiterReader(aiter(byte_iter))
    return IterReader(iter(byte_iter))


class BadEnvelopeError(ValueError):
    """The response envelope is malformed: its status member is missing
    or appears more than once."""


def confirmed_items(source, rows_prefix='rows.item', status_key='status',
                    expected_status='ok', map_type=None, **config):
    """Iterate over the rows of a paged response envelope, but only after the
    envelope's status has been confirmed.

    Some vendors stream the ``rows`` array first and only append the
    envelope ``status`` at the very end, so a page cannot be committed
    while it is being read. This adapter parses ``source`` with
    ``ijson.parse`` in a single streaming pass, spooling every row found
    under ``rows_prefix`` into a temporary file instead of memory, so
    pages much larger than the worker's memory budget are still
    processable. Rows are yielded only once the whole envelope has been
    parsed and its status checked, which makes the result independent of
    whether ``status`` appears before or after ``rows``:

    - If the envelope holds exactly one ``status_key`` member and its
      value equals ``expected_status``, the staged rows are replayed from
      temporary storage and yielded in document order.
    - If the status differs from ``expected_status`` (e.g. ``failed``),
      the envelope is valid but not committable: nothing is yielded.
    - If the status member is missing or repeated, the envelope is
      malformed and ``BadEnvelopeError`` is raised.
    - If the response is truncated or otherwise invalid, the underlying
      ``ijson.JSONError``/``ijson.IncompleteJSONError`` propagates before
      any row is yielded.

    In all exit paths -- confirmation failure, parse errors and the
    consumer closing the generator early -- the temporary staging file is
    closed and deleted, and no unconfirmed row is ever yielded.

    ``map_type`` and any additional keyword arguments are passed through
    to ``ijson.parse`` and the row object builder, mirroring
    ``ijson.items`` semantics.
    """
    import ijson  # deferred: the default backend is bound after this module loads

    staging = tempfile.TemporaryFile(prefix='ijson-confirmed-items-')
    try:
        statuses = []
        row_count = 0
        builder = None
        builder_depth = 0
        builder_is_status = False
        for prefix, event, value in ijson.parse(source, **config):
            if builder is not None:
                builder.event(event, value)
                if event in ('start_map', 'start_array'):
                    builder_depth += 1
                elif event in ('end_map', 'end_array'):
                    builder_depth -= 1
                    if builder_depth == 0:
                        if builder_is_status:
                            statuses.append(builder.value)
                        else:
                            pickle.dump(builder.value, staging,
                                        pickle.HIGHEST_PROTOCOL)
                            row_count += 1
                        builder = None
            elif prefix == rows_prefix or prefix == status_key:
                is_status = prefix == status_key
                if event in ('start_map', 'start_array'):
                    builder = ObjectBuilder(map_type=map_type)
                    builder.event(event, value)
                    builder_depth = 1
                    builder_is_status = is_status
                elif is_status:
                    statuses.append(value)
                else:
                    pickle.dump(value, staging, pickle.HIGHEST_PROTOCOL)
                    row_count += 1
        if len(statuses) != 1:
            raise BadEnvelopeError(
                'expected exactly one %r member in the envelope, found %d'
                % (status_key, len(statuses)))
        if statuses[0] != expected_status:
            return
        staging.seek(0)
        for _ in range(row_count):
            yield pickle.load(staging)
    finally:
        staging.close()
