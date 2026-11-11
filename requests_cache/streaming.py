"""Deferred caching for streamed responses.

With the ``lazy_streams`` setting enabled, a streamed response (``stream=True``) is not written
to the cache up front. Instead, its raw response is wrapped so that response data is buffered as
the caller consumes it, and the response is only cached once the body has been read to completion.
This guarantees that the cache never contains a partially downloaded response.
"""

from logging import getLogger
from typing import TYPE_CHECKING, Optional

from requests import Response

from .models.raw_response import _reset_fp

if TYPE_CHECKING:
    from .backends import BaseCache

logger = getLogger(__name__)


def defer_cache_write(
    response: Response,
    cache: 'BaseCache',
    cache_key: str,
    expires=None,
    max_size: Optional[int] = None,
):
    """Wrap a streamed response so it is only written to the cache after its body has been
    fully consumed by the caller. See :py:class:`.DeferredStreamCache` for details.
    """
    response.raw = DeferredStreamCache(  # type: ignore[assignment]
        response.raw,
        response=response,
        cache=cache,
        cache_key=cache_key,
        expires=expires,
        max_size=max_size,
    )


class DeferredStreamCache:
    """A proxy for :py:class:`urllib3.response.HTTPResponse` that buffers response data as the
    caller streams it, and writes the complete response to the cache once the stream is
    exhausted.

    The response will **not** be cached if:

    * The caller closes the response (or abandons the stream) before fully reading it
    * An error occurs mid-stream (e.g., a decode error or dropped connection)
    * The response body exceeds ``max_size`` (if set); in that case buffering stops, but
      streaming to the caller continues uninterrupted
    """

    def __init__(
        self,
        raw,
        *,
        response: Response,
        cache: 'BaseCache',
        cache_key: str,
        expires=None,
        max_size: Optional[int] = None,
    ):
        self._raw = raw
        self._response = response
        self._cache = cache
        self._cache_key = cache_key
        self._expires = expires
        self._max_size = max_size
        self._chunks = []
        self._size = 0
        self._completed = False
        self._aborted = False

    def __getattr__(self, name):
        """Delegate all other attributes to the wrapped raw response"""
        return getattr(self._raw, name)

    @property
    def _finished(self) -> bool:
        return self._completed or self._aborted

    def read(self, amt=None, decode_content=None, **kwargs) -> bytes:
        try:
            data = self._raw.read(amt=amt, decode_content=decode_content, **kwargs)
        except Exception:
            self._abort('read error')
            raise
        self._buffer(data)
        # A full read, or an empty read (EOF), means the stream is exhausted
        if amt is None or (not data and amt != 0):
            self._complete()
        return data

    def stream(self, amt=None, decode_content=None, **kwargs):
        kwargs['decode_content'] = decode_content
        if amt is not None:
            kwargs['amt'] = amt
        try:
            for chunk in self._raw.stream(**kwargs):
                self._buffer(chunk)
                yield chunk
        except GeneratorExit:
            self._abort('stream abandoned')
            raise
        except Exception:
            self._abort('stream error')
            raise
        else:
            self._complete()

    def close(self):
        try:
            self._raw.close()
        finally:
            if not self._completed:
                self._abort('closed before fully consumed')

    def _buffer(self, data: bytes):
        if self._finished or not data:
            return
        self._size += len(data)
        if self._max_size is not None and self._size > self._max_size:
            self._abort(f'exceeds max_cacheable_size ({self._max_size} bytes)')
            return
        self._chunks.append(data)

    def _abort(self, reason: str):
        """Discard buffered data; the response will not be cached"""
        if self._finished:
            return
        self._aborted = True
        self._chunks = []
        logger.debug(f'Not caching streamed response ({reason}): {self._cache_key}')

    def _complete(self):
        """Write the fully consumed response to the cache"""
        if self._finished:
            return
        self._completed = True
        body = b''.join(self._chunks)
        self._chunks = []

        response = self._response
        raw = self._raw
        # Unwrap so the normal cache-write path (and any further reads) see the original raw
        # response, with its file pointer reset to the buffered body
        response.raw = raw
        _reset_fp(raw, body)

        if getattr(raw, '_has_decoded_content', False) or 'content-encoding' not in raw.headers:
            # Buffered chunks are already decoded
            response._content = body
            response._content_consumed = True
        else:
            # Buffered chunks are still content-encoded; decode them via Response.content
            _ = response.content
            _reset_fp(raw, response.content)

        logger.debug(f'Streamed response fully consumed; writing to cache: {self._cache_key}')
        self._cache.save_response(response, self._cache_key, self._expires)
