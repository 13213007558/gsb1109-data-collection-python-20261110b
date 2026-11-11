"""BaseCache tests that use mocked responses only"""

import pickle
from datetime import timedelta
from logging import getLogger
from pickle import PickleError
from time import time
from unittest.mock import patch

import pytest
from requests import Request

from requests_cache.backends import BaseCache, SQLiteDict
from requests_cache.cache_keys import create_key
from requests_cache.models import CachedRequest, CachedResponse
from requests_cache.policy import utcnow
from requests_cache.session import CachedSession
from tests.conftest import (
    MOCKED_URL,
    MOCKED_URL_ETAG,
    MOCKED_URL_HTTPS,
    MOCKED_URL_JSON,
    MOCKED_URL_REDIRECT,
    START_DT,
    ignore_deprecation,
    mount_mock_adapter,
    patch_normalize_url,
    skip_pypy,
    time_travel,
)

logger = getLogger(__name__)


class InvalidResponse:
    """Class that will raise an error when unpickled"""

    def __init__(self):
        self.foo = 'bar'

    def __setstate__(self, value):
        raise ValueError('Invalid response!')


def test_contains__key(mock_session):
    mock_session.get(MOCKED_URL, params={'foo': 'bar'})
    key = list(mock_session.cache.responses.keys())[0]
    assert mock_session.cache.contains(key)
    assert not mock_session.cache.contains(f'{key}_b')


def test_contains__request(mock_session):
    mock_session.get(MOCKED_URL, params={'foo': 'bar'})
    request = Request('GET', MOCKED_URL, params={'foo': 'bar'})
    assert mock_session.cache.contains(request=request)
    request.params = None
    assert not mock_session.cache.contains(request=request)


def test_contains__multipart_request(mock_session):
    files = [('file', ('test.txt', b'content'))]
    mock_session.settings.allowable_methods = ('GET', 'POST')
    mock_session.post(MOCKED_URL, files=files)
    request = Request('POST', MOCKED_URL, files=files)
    assert mock_session.cache.contains(request=request)
    request.files = None
    assert not mock_session.cache.contains(request=request)


@pytest.mark.parametrize('verify', [True, False])
def test_contains__url(verify, mock_session):
    mock_session.get(MOCKED_URL, verify=verify)
    assert mock_session.cache.contains(url=MOCKED_URL, verify=verify)
    assert not mock_session.cache.contains(url=f'{MOCKED_URL}?foo=bar', verify=verify)


def test_delete__keys(mock_session):
    r = mock_session.get(MOCKED_URL)
    mock_session.cache.delete(r.cache_key, 'nonexistent_key')
    mock_session.cache.delete('nonexistent_key')
    assert not mock_session.cache.contains(url=MOCKED_URL)


@skip_pypy  # time-machine doesn't work on PyPy
@patch_normalize_url
def test_delete__expired(mock_normalize_url, mock_session):
    unexpired_url = f'{MOCKED_URL}?x=1'
    mock_session.mock_adapter.register_uri(
        'GET', unexpired_url, status_code=200, text='mock response'
    )
    mock_session.settings.expire_after = 1

    with time_travel(START_DT):
        mock_session.get(MOCKED_URL)
        mock_session.get(MOCKED_URL_JSON)

    mock_session.settings.expire_after = 2
    with time_travel(START_DT + timedelta(seconds=1.1)):
        mock_session.get(unexpired_url)

    # At this point we should have 1 unexpired response and 2 expired responses
    assert len(mock_session.cache.responses) == 3

    # Use the generic BaseCache implementation, not the SQLite-specific one
    with time_travel(START_DT + timedelta(seconds=2)):
        BaseCache.delete(mock_session.cache, expired=True)
    assert len(mock_session.cache.responses) == 1

    cached_response = list(mock_session.cache.responses.values())[0]
    assert cached_response.url == unexpired_url

    # Now the last response should be expired as well
    with time_travel(START_DT + timedelta(seconds=4)):
        BaseCache.delete(mock_session.cache, expired=True)
    assert len(mock_session.cache.responses) == 0


@skip_pypy
def test_delete__expired__per_request(mock_session):
    second_url = f'{MOCKED_URL}/endpoint_2'
    third_url = f'{MOCKED_URL}/endpoint_3'
    mock_session.mock_adapter.register_uri('GET', second_url, status_code=200)
    mock_session.mock_adapter.register_uri('GET', third_url, status_code=200)

    # Cache 3 responses with different expiration times
    with time_travel(START_DT):
        mock_session.get(MOCKED_URL)
        mock_session.get(second_url, expire_after=5)
        mock_session.get(third_url, expire_after=10)

        # All 3 responses should still be cached
        mock_session.cache.delete(expired=True)
        for response in mock_session.cache.responses.values():
            logger.info(f'Expires in {response.expires_delta} seconds')
        assert len(mock_session.cache.responses) == 3

    # One should be expired after 5s, and another should be expired after 10s
    with time_travel(START_DT + timedelta(seconds=6)):
        mock_session.cache.delete(expired=True)
        assert len(mock_session.cache.responses) == 2
    with time_travel(START_DT + timedelta(seconds=11)):
        mock_session.cache.delete(expired=True)
        assert len(mock_session.cache.responses) == 1


def test_delete__invalid(tempfile_path):
    class BadSerialzier:
        def dumps(self, value):
            return pickle.dumps(value)

        def loads(self, value):
            response = pickle.loads(value)
            if response.url.endswith('/json'):
                raise PickleError
            return response

    mock_session = CachedSession(
        cache_name=tempfile_path, backend='sqlite', serializer=BadSerialzier()
    )
    mock_session = mount_mock_adapter(mock_session)

    # Start with two cached responses, one of which will raise an error
    mock_session.get(MOCKED_URL)
    mock_session.get(MOCKED_URL_JSON)

    # Use the generic BaseCache implementation, not the SQLite-specific one
    BaseCache.delete(mock_session.cache, expired=True, invalid=True)

    assert len(mock_session.cache.responses) == 1
    assert mock_session.get(MOCKED_URL).from_cache is True
    assert mock_session.get(MOCKED_URL_JSON).from_cache is False


@skip_pypy
def test_delete__older_than(mock_session):
    # Cache 4 responses with different creation times
    with time_travel(START_DT):
        request = CachedRequest(method='GET', url='https://test.com/test_0')
        mock_session.cache.save_response(CachedResponse(request=request))
    with time_travel(START_DT - timedelta(seconds=1.1)):
        request = CachedRequest(method='GET', url='https://test.com/test_1')
        mock_session.cache.save_response(CachedResponse(request=request))
    with time_travel(START_DT - timedelta(seconds=2.1)):
        request = CachedRequest(method='GET', url='https://test.com/test_2')
        mock_session.cache.save_response(CachedResponse(request=request))
    with time_travel(START_DT - timedelta(seconds=3.1)):
        request = CachedRequest(method='GET', url='https://test.com/test_3')
        mock_session.cache.save_response(CachedResponse(request=request))

    # Incrementally remove responses older than 3, 2, and 1 seconds
    with time_travel(START_DT):
        assert len(mock_session.cache.responses) == 4
        mock_session.cache.delete(older_than=timedelta(seconds=3))
        assert len(mock_session.cache.responses) == 3
        mock_session.cache.delete(older_than=timedelta(seconds=2))
        assert len(mock_session.cache.responses) == 2
        mock_session.cache.delete(older_than=timedelta(seconds=1))
        assert len(mock_session.cache.responses) == 1

    # Remove the last response after it's 1 second old
    with time_travel(START_DT + timedelta(seconds=1.1)):
        mock_session.cache.delete(older_than=timedelta(seconds=1))
    assert len(mock_session.cache.responses) == 0


def test_delete__urls(mock_session):
    urls = [MOCKED_URL, MOCKED_URL_JSON, MOCKED_URL_REDIRECT]
    for url in urls:
        mock_session.get(url)

    mock_session.cache.delete(urls=urls)

    for url in urls:
        assert not mock_session.cache.contains(url=url)


@pytest.mark.parametrize('verify', [True, False])
def test_delete__requests(verify, mock_session):
    urls = [MOCKED_URL, MOCKED_URL_JSON, MOCKED_URL_REDIRECT]
    for url in urls:
        mock_session.get(url, verify=verify)

    requests = [Request('GET', url).prepare() for url in urls]
    mock_session.cache.delete(requests=requests, verify=verify)

    for request in requests:
        assert not mock_session.cache.contains(request=request)


def test_cache_tags__save_and_get(mock_session):
    response = mock_session.get(MOCKED_URL, cache_tags=['product:1', 'catalog'])
    assert mock_session.cache.get_tags(response.cache_key) == ['catalog', 'product:1']
    assert mock_session.cache.get_keys_for_tags(['product:1']) == [response.cache_key]
    assert mock_session.cache.get_keys_for_tags(['catalog', 'unknown']) == [response.cache_key]
    assert mock_session.cache.get_keys_for_tags(['unknown']) == []


def test_cache_tags__single_tag_string(mock_session):
    response = mock_session.get(MOCKED_URL, cache_tags='product:1')
    assert mock_session.cache.get_tags(response.cache_key) == ['product:1']


def test_cache_tags__replace_on_overwrite(mock_session):
    response = mock_session.get(MOCKED_URL, cache_tags=['product:1'])
    mock_session.get(MOCKED_URL, cache_tags=['product:2'], force_refresh=True)
    assert mock_session.cache.get_tags(response.cache_key) == ['product:2']
    assert mock_session.cache.get_keys_for_tags(['product:1']) == []


def test_delete__tags(mock_session):
    """Responses with any invalidated tag should be deleted; unrelated responses should not"""
    mock_session.get(MOCKED_URL, cache_tags=['product:1', 'catalog'])
    mock_session.get(MOCKED_URL_JSON, cache_tags=['product:1'])
    mock_session.get(MOCKED_URL_HTTPS, cache_tags=['product:2'])

    mock_session.cache.delete(tags=['product:1'])

    # Both responses tagged 'product:1' are gone, including the multi-tagged one
    assert not mock_session.cache.contains(url=MOCKED_URL)
    assert not mock_session.cache.contains(url=MOCKED_URL_JSON)
    # The unrelated response is untouched
    assert mock_session.cache.contains(url=MOCKED_URL_HTTPS)
    # Tag associations for deleted responses are cleaned up
    assert mock_session.cache.get_keys_for_tags(['catalog']) == []
    assert mock_session.cache.get_keys_for_tags(['product:2']) != []


def test_delete__tags__unknown_tag(mock_session):
    mock_session.get(MOCKED_URL, cache_tags=['product:1'])
    mock_session.cache.delete(tags=['unknown'])
    assert mock_session.cache.contains(url=MOCKED_URL)


def test_cache_tags__persist_across_sessions(mock_session):
    """Tag associations and invalidation records should survive reopening the cache"""
    response = mock_session.get(MOCKED_URL, cache_tags=['product:1'])
    db_path = mock_session.cache.db_path
    mock_session.close()

    session_2 = CachedSession(cache_name=db_path, backend='sqlite')
    assert session_2.cache.get_keys_for_tags(['product:1']) == [response.cache_key]

    session_2.cache.delete(tags=['product:1'])
    assert not session_2.cache.contains(url=MOCKED_URL)
    session_2.close()

    # The invalidation record also persists, and blocks writes from older in-flight requests
    session_3 = CachedSession(cache_name=db_path, backend='sqlite')
    assert session_3.cache.tags_invalidated_since(['product:1'], since=0) is True
    session_3.close()


def test_save_response__invalidated_tag_not_rewritten(mock_session):
    """A response for a request started before a tag invalidation must not be written back"""
    response = mock_session.get(MOCKED_URL, cache_tags=['product:1'])
    request_started_at = time()
    mock_session.cache.delete(tags=['product:1'])

    mock_session.cache.save_response(
        response,
        cache_tags=['product:1'],
        request_started_at=request_started_at,
    )
    assert not mock_session.cache.contains(url=MOCKED_URL)

    # A request started after the invalidation is cached normally
    mock_session.cache.save_response(
        response,
        cache_tags=['product:1'],
        request_started_at=time(),
    )
    assert mock_session.cache.contains(url=MOCKED_URL)


def test_clear__removes_cache_tags(mock_session):
    response = mock_session.get(MOCKED_URL, cache_tags=['product:1'])
    mock_session.cache.clear()
    assert mock_session.cache.get_keys_for_tags(['product:1']) == []
    assert mock_session.cache.get_tags(response.cache_key) == []


def test_recreate_keys(mock_session):
    # Cache some initial responses with default key function
    urls = [MOCKED_URL, MOCKED_URL_JSON, MOCKED_URL_ETAG]
    for url in urls:
        mock_session.get(url)
    old_cache_keys = set(mock_session.cache.responses.keys())

    # Switch to a new key function and recreate keys
    def new_key_fn(*args, **kwargs):
        return create_key(*args, **kwargs) + '_suffix'

    # Check that responses are saved with new keys
    mock_session.settings.key_fn = new_key_fn
    mock_session.cache.recreate_keys()
    new_cache_keys = set(mock_session.cache.responses.keys())
    assert len(old_cache_keys) == len(new_cache_keys) == len(urls)
    assert old_cache_keys != new_cache_keys

    # Check that responses are returned from the cache correctly using the new key function
    for url in urls:
        assert mock_session.get(url).from_cache is True


def test_recreate_keys__same_key_fn(mock_session):
    urls = [MOCKED_URL, MOCKED_URL_JSON, MOCKED_URL_ETAG]
    for url in urls:
        mock_session.get(url)
    old_cache_keys = set(mock_session.cache.responses.keys())

    mock_session.cache.recreate_keys()
    new_cache_keys = set(mock_session.cache.responses.keys())
    assert old_cache_keys == new_cache_keys

    # Check that responses are returned from the cache correctly using the new key function
    for url in urls:
        assert mock_session.get(url).from_cache is True


def test_recreate_keys__empty_response_body(mock_session):
    # Cache some initial responses with default key function, and modify request body (pre-1.0)
    urls = [MOCKED_URL, MOCKED_URL_JSON, MOCKED_URL_ETAG]
    for url in urls:
        r = mock_session.get(url)
        r = CachedResponse.from_response(r)
        r.request.body = b'None'
        mock_session.cache.responses[r.cache_key] = r

    # Switch to a new key function and recreate keys
    def new_key_fn(*args, **kwargs):
        return create_key(*args, **kwargs) + '_suffix'

    mock_session.settings.key_fn = new_key_fn
    mock_session.cache.recreate_keys()

    # Check that responses are returned from the cache correctly using the new key function
    for url in urls:
        assert mock_session.get(url).from_cache is True


def test_reset_expiration__extend_expiration(mock_session):
    # Start with an expired response
    mock_session.settings.expire_after = utcnow() - timedelta(seconds=1)
    mock_session.get(MOCKED_URL)

    # Set expiration in the future
    mock_session.cache.reset_expiration(utcnow() + timedelta(seconds=1))
    assert len(mock_session.cache.responses) == 1
    response = mock_session.get(MOCKED_URL)
    assert response.is_expired is False and response.from_cache is True


def test_reset_expiration__shorten_expiration(mock_session):
    # Start with a non-expired response
    mock_session.settings.expire_after = utcnow() + timedelta(seconds=1)
    mock_session.get(MOCKED_URL)

    # Set expiration in the past
    mock_session.cache.reset_expiration(utcnow() - timedelta(seconds=1))
    response = mock_session.get(MOCKED_URL)
    assert response.is_expired is False and response.from_cache is False


def test_clear(mock_session):
    mock_session.get(MOCKED_URL)
    mock_session.get(MOCKED_URL_REDIRECT)
    mock_session.cache.clear()
    assert not mock_session.cache.contains(url=MOCKED_URL)
    assert not mock_session.cache.contains(url=MOCKED_URL_REDIRECT)


def test_save_response__manual(mock_session):
    response = mock_session.get(MOCKED_URL)
    mock_session.cache.clear()
    mock_session.cache.save_response(response)


def test_update(mock_session):
    src_cache = BaseCache()
    for i in range(20):
        src_cache.responses[f'key_{i}'] = f'value_{i}'
        src_cache.redirects[f'key_{i}'] = f'value_{i}'

    mock_session.cache.update(src_cache)
    assert len(mock_session.cache.responses) == 20
    assert len(mock_session.cache.redirects) == 20


@patch_normalize_url
def test_urls(mock_normalize_url, mock_session):
    for url in [MOCKED_URL, MOCKED_URL, MOCKED_URL_JSON, MOCKED_URL_HTTPS]:
        mock_session.get(url)

    expected_urls = [MOCKED_URL_JSON, MOCKED_URL, MOCKED_URL_HTTPS]
    assert mock_session.cache.urls() == expected_urls


def test_urls__error(mock_session):
    responses = [mock_session.get(url) for url in [MOCKED_URL, MOCKED_URL_JSON, MOCKED_URL_HTTPS]]
    responses[2] = None
    with patch.object(SQLiteDict, 'deserialize', side_effect=responses):
        expected_urls = [MOCKED_URL_JSON, MOCKED_URL]
        assert mock_session.cache.urls() == expected_urls

    # The invalid response should be skipped, but remain in the cache
    assert len(mock_session.cache.responses.keys()) == 3


# Deprecated methods
# --------------------


def test_remove_expired_responses(mock_session):
    with ignore_deprecation(), patch.object(
        mock_session.cache, 'delete'
    ) as mock_delete, patch.object(mock_session.cache, 'reset_expiration') as mock_reset:
        mock_session.cache.remove_expired_responses(expire_after=1)
        mock_delete.assert_called_once_with(expired=True, invalid=True)
        mock_reset.assert_called_once_with(1)

        mock_session.cache.remove_expired_responses()
        assert mock_delete.call_count == 2 and mock_reset.call_count == 1
