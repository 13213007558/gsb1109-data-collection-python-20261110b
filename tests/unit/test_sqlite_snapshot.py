"""Tests for read-only, point-in-time SQLite cache snapshots"""

import os
import sqlite3

import pytest

from requests_cache import CachedSession
from requests_cache.backends.sqlite import SQLiteCache, SQLiteSnapshot, SQLiteSnapshotStorage
from requests_cache.models import CachedRequest, CachedResponse
from tests.conftest import MOCKED_URL, mount_mock_adapter


def make_response(content: bytes, url: str = 'https://example.com/list') -> CachedResponse:
    response = CachedResponse(status_code=200, url=url)
    response.request = CachedRequest(method='GET', url=url)
    response._content = content
    return response


@pytest.fixture
def cache(tmp_path):
    backend = SQLiteCache(tmp_path / 'snapshot_test')
    backend.responses['k_list'] = make_response(b'v1')
    backend.responses['k_detail'] = make_response(b'detail', 'https://example.com/detail')
    backend.redirects['k_redirect'] = 'k_list'
    yield backend
    backend.close()


class TestSQLiteSnapshot:
    def test_frozen_on_refresh_and_delete(self, cache):
        snapshot = cache.snapshot()

        cache.responses['k_list'] = make_response(b'v2')
        cache.responses['k_new'] = make_response(b'new')
        del cache.responses['k_list']
        del cache.redirects['k_redirect']

        # The snapshot keeps the versions visible when it was opened
        assert snapshot.responses['k_list'].content == b'v1'
        assert snapshot.redirects['k_redirect'] == 'k_list'
        assert snapshot.get_response('k_redirect').content == b'v1'

        # Items added after the snapshot are not visible
        assert 'k_new' not in snapshot.responses
        assert len(snapshot.responses) == 2
        assert len(snapshot.redirects) == 1
        assert set(snapshot.responses) == {'k_list', 'k_detail'}

        snapshot.close()

    def test_missing_keys_are_explicit_misses(self, cache):
        snapshot = cache.snapshot()

        with pytest.raises(KeyError):
            snapshot.responses['not_cached']
        assert snapshot.responses.get('not_cached') is None
        assert snapshot.get_response('not_cached') is None
        assert not snapshot.contains(key='not_cached')

        snapshot.close()

    def test_snapshot_is_read_only(self, cache):
        snapshot = cache.snapshot()

        with pytest.raises(TypeError):
            snapshot.responses['x'] = make_response(b'x')
        with pytest.raises(TypeError):
            del snapshot.responses['k_list']
        with pytest.raises(TypeError):
            snapshot.responses.clear()
        with pytest.raises(TypeError):
            snapshot.responses.pop('k_list')
        with pytest.raises(TypeError):
            snapshot.redirects['x'] = 'y'
        with pytest.raises(TypeError):
            snapshot.clear()

        snapshot.close()

    def test_live_writes_continue_while_snapshot_open(self, cache):
        snapshot = cache.snapshot()

        cache.responses['k_written'] = make_response(b'written')
        cache.redirects['k_written_redirect'] = 'k_written'

        assert cache.responses['k_written'].content == b'written'
        assert cache.redirects['k_written_redirect'] == 'k_written'
        # The snapshot itself remains unchanged
        assert 'k_written' not in snapshot.responses

        snapshot.close()

    def test_filter_and_urls_use_frozen_data(self, cache):
        snapshot = cache.snapshot()
        cache.responses.clear()

        responses = list(snapshot.filter())
        assert {r.cache_key for r in responses} == {'k_list', 'k_detail'}
        assert sorted(snapshot.urls()) == [
            'https://example.com/detail',
            'https://example.com/list',
        ]

        snapshot.close()

    def test_context_manager_closes_resources(self, cache):
        with cache.snapshot() as snapshot:
            assert isinstance(snapshot, SQLiteSnapshot)
            assert isinstance(snapshot.responses, SQLiteSnapshotStorage)
            assert snapshot.responses['k_list'].content == b'v1'

        assert snapshot.responses._closed
        assert snapshot.redirects._closed
        assert snapshot._closed
        with pytest.raises(RuntimeError):
            _ = snapshot.responses['k_list']

    def test_close_is_idempotent(self, cache):
        snapshot = cache.snapshot()
        snapshot.close()
        snapshot.close()
        assert snapshot.responses._connection is None

    def test_survives_source_file_deletion(self, tmp_path):
        db_path = tmp_path / 'deletable'
        backend = SQLiteCache(db_path)
        backend.responses['k'] = make_response(b'data')
        snapshot = backend.snapshot()

        os.unlink(backend.db_path)

        assert snapshot.responses['k'].content == b'data'
        snapshot.close()
        backend.close()

    def test_in_memory_source_database(self):
        backend = SQLiteCache(':memory:')
        backend.responses['k'] = make_response(b'mem')
        backend.redirects['r'] = 'k'
        snapshot = backend.snapshot()

        del backend.responses['k']
        del backend.redirects['r']

        assert snapshot.responses['k'].content == b'mem'
        assert snapshot.redirects['r'] == 'k'
        snapshot.close()
        backend.close()

    def test_shared_memory_source_database(self):
        backend = SQLiteCache(use_memory=True)
        backend.responses['k'] = make_response(b'shared_mem')
        backend.redirects['r'] = 'k'
        snapshot = backend.snapshot()

        backend.responses['k'] = make_response(b'overwritten')
        del backend.redirects['r']

        assert snapshot.responses['k'].content == b'shared_mem'
        assert snapshot.redirects['r'] == 'k'

        memory_uri = snapshot.responses.db_path
        snapshot.close()
        # Closing the snapshot drops its private in-memory database
        probe = sqlite3.connect(memory_uri, uri=True)
        assert probe.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        probe.close()
        backend.close()

    @pytest.mark.parametrize('wal', [False, True])
    def test_wal_and_rollback_journal_modes(self, tmp_path, wal):
        backend = SQLiteCache(tmp_path / f'wal_{wal}', wal=wal)
        backend.responses['k'] = make_response(b'v1')
        snapshot = backend.snapshot()

        backend.responses['k'] = make_response(b'v2')

        assert snapshot.responses['k'].content == b'v1'
        snapshot.close()
        backend.close()

    def test_works_with_cached_session(self, tmp_path):
        session = CachedSession(
            cache_name=str(tmp_path / 'session_cache'),
            backend='sqlite',
            allowable_methods=('GET', 'POST'),
        )
        session = mount_mock_adapter(session)

        response = session.get(MOCKED_URL)
        assert response.from_cache is False
        assert session.get(MOCKED_URL).from_cache is True

        snapshot = session.cache.snapshot()
        key = next(iter(session.cache.responses.keys()))
        assert snapshot.get_response(key) is not None

        # Refreshing the live cache does not change the snapshot
        session.cache.delete(key)
        assert session.cache.get_response(key) is None
        assert snapshot.get_response(key) is not None
        with pytest.raises(TypeError):
            snapshot.responses[key] = make_response(b'x')

        snapshot.close()
        session.close()
