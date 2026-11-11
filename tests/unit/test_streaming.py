"""Tests for deferred caching of streamed responses (``lazy_streams``)"""

import gzip
import gc

import pytest
from requests import Response
from urllib3.exceptions import DecodeError

from requests_cache.streaming import DeferredStreamCache, defer_cache_write
from tests.conftest import MOCKED_URL


class FakeRawResponse:
    """Minimal stand-in for urllib3.HTTPResponse, with optional mid-stream failure"""

    def __init__(self, chunks, error=None):
        self._chunks = list(chunks)
        self._error = error
        self.headers = {}
        self.closed = False

    def stream(self, amt=None, decode_content=None):
        for chunk in self._chunks:
            yield chunk
        if self._error:
            raise self._error

    def read(self, amt=None, decode_content=None, **kwargs):
        if not self._chunks:
            if self._error:
                raise self._error
            return b''
        if amt is None:
            data = b''.join(self._chunks)
            self._chunks = []
            return data
        return self._chunks.pop(0)

    def close(self):
        self.closed = True


class FakeCache:
    def __init__(self):
        self.saved = {}

    def save_response(self, response, cache_key, expires):
        self.saved[cache_key] = response._content


def make_deferred_stream(chunks, error=None, max_size=None):
    """Build a Response whose raw stream is wrapped for deferred caching"""
    response = Response()
    response._content = False
    response._content_consumed = False
    response.raw = FakeRawResponse(chunks, error=error)
    cache = FakeCache()
    defer_cache_write(response, cache, 'cache_key', max_size=max_size)
    return response, cache


def test_defer_cache_write__wraps_raw():
    response, _ = make_deferred_stream([b'data'])
    assert isinstance(response.raw, DeferredStreamCache)


def test_deferred_stream__full_stream_is_cached():
    response, cache = make_deferred_stream([b'chunk1', b'chunk2'])
    assert b''.join(response.raw.stream()) == b'chunk1chunk2'
    assert cache.saved == {'cache_key': b'chunk1chunk2'}


def test_deferred_stream__full_read_is_cached():
    response, cache = make_deferred_stream([b'chunk1', b'chunk2'])
    assert response.raw.read() == b'chunk1chunk2'
    assert cache.saved == {'cache_key': b'chunk1chunk2'}


def test_deferred_stream__partial_read_then_close_is_not_cached():
    response, cache = make_deferred_stream([b'chunk1', b'chunk2'])
    assert response.raw.read(5) == b'chunk1'
    response.raw.close()
    assert cache.saved == {}


def test_deferred_stream__abandoned_stream_is_not_cached():
    response, cache = make_deferred_stream([b'chunk1', b'chunk2'])
    stream = response.raw.stream()
    assert next(stream) == b'chunk1'
    stream.close()
    assert cache.saved == {}


def test_deferred_stream__stream_error_is_not_cached():
    response, cache = make_deferred_stream([b'chunk1'], error=DecodeError('invalid'))
    with pytest.raises(DecodeError):
        b''.join(response.raw.stream())
    assert cache.saved == {}


def test_deferred_stream__read_error_is_not_cached():
    response, cache = make_deferred_stream([b'chunk1'], error=ConnectionError('dropped'))
    assert response.raw.read(5) == b'chunk1'
    with pytest.raises(ConnectionError):
        response.raw.read()
    assert cache.saved == {}


def test_deferred_stream__max_size_does_not_interrupt_stream():
    response, cache = make_deferred_stream([b'chunk1', b'chunk2'], max_size=8)
    assert b''.join(response.raw.stream()) == b'chunk1chunk2'
    assert cache.saved == {}


def test_lazy_streams__not_cached_until_fully_consumed(mock_session):
    mock_session.settings.lazy_streams = True
    response = mock_session.get(MOCKED_URL, stream=True)
    assert response._content_consumed is False
    assert not mock_session.cache.contains(url=MOCKED_URL)

    chunks = list(response.iter_content(chunk_size=4))
    assert b''.join(chunks) == b'mock response'
    assert mock_session.cache.contains(url=MOCKED_URL)

    # A cache hit is read the same way as the original streamed response
    cached_response = mock_session.get(MOCKED_URL, stream=True)
    assert cached_response.from_cache is True
    assert list(cached_response.iter_content(chunk_size=4)) == chunks


def test_lazy_streams__content_property(mock_session):
    mock_session.settings.lazy_streams = True
    response = mock_session.get(MOCKED_URL, stream=True)
    assert response.content == b'mock response'
    assert mock_session.cache.contains(url=MOCKED_URL)
    assert mock_session.get(MOCKED_URL).from_cache is True


def test_lazy_streams__partial_read_is_not_cached(mock_session):
    mock_session.settings.lazy_streams = True
    response = mock_session.get(MOCKED_URL, stream=True)
    assert next(response.iter_content(chunk_size=4)) == b'mock'
    response.close()

    assert not mock_session.cache.contains(url=MOCKED_URL)
    assert mock_session.get(MOCKED_URL, stream=True).from_cache is False


def test_lazy_streams__abandoned_stream_is_not_cached(mock_session):
    mock_session.settings.lazy_streams = True
    response = mock_session.get(MOCKED_URL, stream=True)
    for _ in response.iter_content(chunk_size=4):
        break
    gc.collect()

    assert not mock_session.cache.contains(url=MOCKED_URL)


def test_lazy_streams__max_cacheable_size(mock_session):
    mock_session.settings.lazy_streams = True
    mock_session.settings.max_cacheable_size = 4
    response = mock_session.get(MOCKED_URL, stream=True)

    # Exceeding the size limit skips caching, but doesn't interrupt the download
    assert b''.join(response.iter_content(chunk_size=4)) == b'mock response'
    assert not mock_session.cache.contains(url=MOCKED_URL)
    assert mock_session.get(MOCKED_URL, stream=True).from_cache is False


def test_lazy_streams__encoded_content(mock_session):
    body = b'mock gzip response' * 10
    url = f'{MOCKED_URL}/gzip'
    mock_session.mock_adapter.register_uri(
        'GET', url, content=gzip.compress(body), headers={'Content-Encoding': 'gzip'}
    )
    mock_session.settings.lazy_streams = True

    response = mock_session.get(url, stream=True)
    assert response.content == body
    assert mock_session.cache.contains(url=url)

    cached_response = mock_session.get(url, stream=True)
    assert cached_response.from_cache is True
    assert cached_response.content == body


def test_lazy_streams__disabled_by_default(mock_session):
    """Without lazy_streams, streamed responses are buffered and cached up front"""
    response = mock_session.get(MOCKED_URL, stream=True)
    assert mock_session.cache.contains(url=MOCKED_URL)
    assert b''.join(response.iter_content(chunk_size=4)) == b'mock response'
