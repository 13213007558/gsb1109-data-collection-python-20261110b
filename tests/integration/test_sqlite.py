import os
import pickle
import sqlite3
from datetime import timedelta
from os.path import join
from tempfile import NamedTemporaryFile, gettempdir
from threading import Thread
from unittest.mock import MagicMock, patch

import pytest
from platformdirs import user_cache_dir

from requests_cache.backends import BaseCache, SQLiteCache, SQLiteDict
from requests_cache.backends.sqlite import (
    MEMORY_URI,
    DependencyCycleError,
    DependencyStatus,
    DependencyVersionMismatchError,
    MissingDependencyError,
    get_content_version,
)
from requests_cache.models import CachedRequest, CachedResponse
from requests_cache.policy import utcnow
from tests.conftest import skip_pypy
from tests.integration.base_cache_test import BaseCacheTest
from tests.integration.base_storage_test import CACHE_NAME, BaseStorageTest


class TestSQLiteDict(BaseStorageTest):
    storage_class = SQLiteDict
    init_kwargs = {'use_temp': True}

    @classmethod
    def teardown_class(cls):
        try:
            os.unlink(f'{CACHE_NAME}.sqlite')
        except Exception:
            pass

    @patch('requests_cache.backends.sqlite.sqlite3')
    def test_connection_kwargs(self, mock_sqlite):
        """A spot check to make sure optional connection kwargs gets passed to connection;
        except for isolation_level, which is ignored
        """
        cache = self.storage_class(
            'test', use_temp=True, timeout=0.5, isolation_level='DEFERRED', invalid_kwarg='???'
        )
        mock_sqlite.connect.assert_called_with(
            cache.db_path, timeout=0.5, isolation_level=None, check_same_thread=False
        )

    def test_use_cache_dir(self):
        relative_path = self.storage_class(CACHE_NAME).db_path
        cache_dir_path = self.storage_class(CACHE_NAME, use_cache_dir=True).db_path
        assert not str(relative_path).startswith(user_cache_dir())
        assert str(cache_dir_path).startswith(user_cache_dir())

    def test_use_temp(self):
        relative_path = self.storage_class(CACHE_NAME).db_path
        temp_path = self.storage_class(CACHE_NAME, use_temp=True).db_path
        assert not str(relative_path).startswith(gettempdir())
        assert str(temp_path).startswith(gettempdir())

    def test_use_memory(self):
        cache = self.init_cache(use_memory=True)
        assert cache.db_path == MEMORY_URI
        for i in range(20):
            cache[f'key_{i}'] = f'value_{i}'
        for i in range(5):
            del cache[f'key_{i}']

        assert len(cache) == 15
        assert set(cache.keys()) == {f'key_{i}' for i in range(5, 20)}
        assert set(cache.values()) == {f'value_{i}' for i in range(5, 20)}

        cache.clear()
        assert len(cache) == 0

    def test_use_memory__uri(self):
        assert self.init_cache(':memory:').db_path == ':memory:'

    def test_non_dir_parent_exists(self):
        """Expect a custom error message if a parent path already exists but isn't a directory"""
        with NamedTemporaryFile() as tmp:
            with pytest.raises(FileExistsError) as exc_info:
                self.storage_class(join(tmp.name, 'invalid_path'))
                assert 'not a directory' in str(exc_info.value)

    def test_bulk_commit(self):
        cache = self.init_cache()
        with cache.bulk_commit():
            pass

        n_items = 1000
        with cache.bulk_commit():
            for i in range(n_items):
                cache[f'key_{i}'] = f'value_{i}'
        assert set(cache.keys()) == {f'key_{i}' for i in range(n_items)}
        assert set(cache.values()) == {f'value_{i}' for i in range(n_items)}

    def test_bulk_delete__chunked(self):
        """When deleting more items than SQLite can handle in a single statement, it should be
        chunked into multiple smaller statements
        """
        # Populate the cache with more items than can fit in a single delete statement
        cache = self.init_cache()
        with cache.bulk_commit():
            for i in range(2000):
                cache[f'key_{i}'] = f'value_{i}'

        keys = list(cache.keys())

        # First pass to ensure that bulk_delete is split across three statements
        with patch.object(cache, 'connection') as mock_connection:
            con = mock_connection().__enter__.return_value
            cache.bulk_delete(keys)
            main_table_deletes = [
                call
                for call in con.execute.call_args_list
                if f'DELETE FROM {cache.table_name} WHERE' in str(call)
            ]
            assert len(main_table_deletes) == 3

        # Second pass to actually delete keys and make sure it doesn't explode
        cache.bulk_delete(keys)
        assert len(cache) == 0

    def test_bulk_commit__noop(self):
        def do_noop_bulk(cache):
            with cache.bulk_commit():
                pass
            del cache

        cache = self.init_cache()
        thread = Thread(target=do_noop_bulk, args=(cache,))
        thread.start()
        thread.join()

        # make sure connection is not closed by the thread
        cache['key_1'] = 'value_1'
        assert list(cache.keys()) == ['key_1']

    @skip_pypy
    @pytest.mark.parametrize('kwargs', [{'busy_timeout': 5}, {'fast_save': True}, {'wal': True}])
    def test_pragma(self, kwargs):
        """Test settings that make additional PRAGMA statements"""
        cache_1 = self.init_cache('cache_1', **kwargs)
        cache_2 = self.init_cache('cache_2', **kwargs)

        n = 500
        for i in range(n):
            cache_1[f'key_{i}'] = f'value_{i}'
            cache_2[f'key_{i * 2}'] = f'value_{i}'

        assert set(cache_1.keys()) == {f'key_{i}' for i in range(n)}
        assert set(cache_2.values()) == {f'value_{i}' for i in range(n)}

    def test_busy_timeout(self):
        cache = self.init_cache(busy_timeout=5)
        with cache.connection() as con:
            r = con.execute('PRAGMA busy_timeout').fetchone()
            assert r[0] == 5

    def test_wal_sync_mode(self):
        # Should default to 'NORMAL' (1)
        cache = self.init_cache(wal=True)
        with cache.connection() as con:
            r = con.execute('PRAGMA synchronous').fetchone()
            assert r[0] == 1

        # Not recommended, but should still work
        cache = self.init_cache(wal=True, fast_save=True)
        with cache.connection() as con:
            r = con.execute('PRAGMA synchronous').fetchone()
            assert r[0] == 0

    def test_write_acquire_lock(self):
        """Writes to the database acquire the sqlite lock"""
        cache = self.init_cache()
        with patch.object(cache, '_acquire_sqlite_lock') as mock_write:
            cache['key_1'] = 'value_1'
            assert mock_write.call_count == 1

    def test_write_retry_acquire_lock(self):
        """Acquiring the lock should retry BEGIN IMMEDIATE until it succeeds"""
        cache = self.init_cache()
        with patch.object(cache, '_connection') as mock_connection:
            mock_connection.execute.side_effect = [sqlite3.OperationalError] * 10 + [None]
            with cache._acquire_sqlite_lock():
                pass
            begin_calls = [
                c for c in mock_connection.execute.call_args_list if 'BEGIN IMMEDIATE' in str(c)
            ]
            assert len(begin_calls) == 11

    def test_write__error(self):
        """Errors from write operations should propagate"""
        cache = self.init_cache()
        with patch.object(cache, '_write', side_effect=sqlite3.OperationalError('DB is on fire')):
            with pytest.raises(sqlite3.OperationalError):
                cache['key_1'] = 'value_1'
        with patch.object(cache, '_write', side_effect=sqlite3.DatabaseError('HDD also on fire')):
            with pytest.raises(sqlite3.DatabaseError):
                cache['key_1'] = 'value_1'

    def test_vacuum__no_connection(self):
        cache = self.init_cache()
        cache._connection = None
        cache.vacuum()

    def test_vacuum__commits_active_transaction(self):
        cache = self.init_cache()
        mock_con = MagicMock()
        cache._connection = mock_con
        cache._active_transaction = True

        cache.vacuum()

        mock_con.commit.assert_called_once()
        mock_con.execute.assert_called_once_with('VACUUM')
        assert cache._active_transaction is False

    @skip_pypy
    @pytest.mark.parametrize('limit', [None, 50])
    def test_sorted__by_size(self, limit):
        cache = self.init_cache()

        # Insert items with decreasing size
        for i in range(100):
            suffix = 'padding' * (100 - i)
            cache[f'key_{i}'] = f'value_{i}_{suffix}'

        # Sorted items should be in ascending order by size
        items = list(cache.sorted(key='size', limit=limit))
        assert len(items) == (limit or 100)

        prev_item = None
        for item in items:
            assert prev_item is None or len(prev_item) > len(item)

    @skip_pypy
    def test_sorted__reversed(self):
        cache = self.init_cache()

        for i in range(100):
            cache[f'key_{i + 1:03}'] = f'value_{i + 1}'

        items = list(cache.sorted(key='key', reversed=True))
        assert len(items) == 100
        for i, item in enumerate(items):
            assert item == f'value_{100 - i}'

    @skip_pypy
    def test_sorted__invalid_sort_key(self):
        cache = self.init_cache()
        cache['key_1'] = 'value_1'
        with pytest.raises(ValueError):
            list(cache.sorted(key='invalid_key'))

    @skip_pypy
    @pytest.mark.parametrize('limit', [None, 50])
    def test_sorted__by_expires(self, limit):
        cache = self.init_cache()
        now = utcnow()

        # Insert items with decreasing expiration time
        for i in range(100):
            response = CachedResponse(expires=now + timedelta(seconds=101 - i))
            cache[f'key_{i}'] = response

        # Sorted items should be in ascending order by expiration time
        items = list(cache.sorted(key='expires', limit=limit))
        assert len(items) == (limit or 100)

        prev_item = None
        for item in items:
            assert prev_item is None or prev_item.expires < item.expires

    @skip_pypy
    def test_sorted__exclude_expired(self):
        cache = self.init_cache()
        now = utcnow()

        # Make only odd numbered items expired
        for i in range(100):
            delta = 101 - i
            if i % 2 == 1:
                delta -= 101

            response = CachedResponse(status_code=i, expires=now + timedelta(seconds=delta))
            cache[f'key_{i}'] = response

        # Items should only include unexpired (even numbered) items, and still be in sorted order
        items = list(cache.sorted(key='expires', expired=False))
        assert len(items) == 50
        prev_item = None

        for item in items:
            assert prev_item is None or prev_item.expires < item.expires
            assert item.status_code % 2 == 0

    @skip_pypy
    def test_sorted__error(self):
        """sorted() should handle deserialization errors and not return invalid responses"""

        class BadSerializer:
            def loads(self, value):
                response = pickle.loads(value)
                if response.cache_key == 'key_42':
                    raise pickle.PickleError()
                return response

            def dumps(self, value):
                return pickle.dumps(value)

        cache = self.init_cache(serializer=BadSerializer())

        for i in range(100):
            response = CachedResponse(status_code=i)
            response.cache_key = f'key_{i}'
            cache[f'key_{i}'] = response

        # Items should only include unexpired (even numbered) items, and still be in sorted order
        items = list(cache.sorted())
        assert len(items) == 99

    @pytest.mark.parametrize(
        'db_path, use_temp',
        [
            ('filesize_test', True),
            (':memory:', False),
        ],
    )
    def test_size(self, db_path, use_temp):
        """Test approximate expected size of a database, for both file-based and in-memory databases"""
        cache = self.init_cache(db_path, use_temp=use_temp)
        for i in range(100):
            cache[f'key_{i}'] = f'value_{i}'
        assert 10000 < cache.size() < 200000


class TestSQLiteCache(BaseCacheTest):
    backend_class = SQLiteCache
    init_kwargs = {'use_temp': True}

    @classmethod
    def teardown_class(cls):
        try:
            os.unlink(CACHE_NAME)
        except Exception:
            pass

    @patch.object(BaseCache, 'clear', side_effect=IOError)
    @patch('requests_cache.backends.sqlite.unlink', side_effect=os.unlink)
    def test_clear__failure(self, mock_unlink, mock_clear):
        """When a corrupted cache prevents a normal DROP TABLE, clear() should still succeed"""
        session = self.init_session(clear=False)
        session.cache.responses['key_1'] = 'value_1'
        db_path = session.cache.responses.db_path
        session.cache.clear()

        assert len(session.cache.responses) == 0
        mock_unlink.assert_called_once_with(db_path)

    @patch.object(BaseCache, 'clear', side_effect=IOError)
    def test_clear__file_already_deleted(self, mock_clear):
        session = self.init_session(clear=False)
        session.cache.responses['key_1'] = 'value_1'
        os.unlink(session.cache.responses.db_path)
        session.cache.clear()

        assert len(session.cache.responses) == 0

    def test_db_path(self):
        """This is just provided as an alias, since both requests and redirects share the same db
        file
        """
        session = self.init_session()
        assert session.cache.db_path == session.cache.responses.db_path

    def test_count(self):
        """count() should work the same as len(), but with the option to exclude expired responses"""
        session = self.init_session()
        now = utcnow()
        session.cache.responses['key_1'] = CachedResponse(expires=now + timedelta(1))
        session.cache.responses['key_2'] = CachedResponse(expires=now - timedelta(1))

        assert session.cache.count() == 2
        assert session.cache.count(expired=False) == 1

    def test_delete__single_key(self):
        """Vacuum should not be used after delete if there is only a single key"""
        session = self.init_session()
        session.cache.responses['key_1'] = 'value_1'

        with patch.object(SQLiteDict, 'vacuum') as mock_vacuum:
            session.cache.delete('key_1')
            mock_vacuum.assert_not_called()

    def test_delete__skip_vacuum(self):
        """Vacuum should not be used after delete if disabled"""
        session = self.init_session()
        session.cache.responses['key_1'] = 'value_1'
        session.cache.responses['key_2'] = 'value_2'

        with patch.object(SQLiteDict, 'vacuum') as mock_vacuum:
            session.cache.delete('key_1', 'key_2', vacuum=False)
            mock_vacuum.assert_not_called()

    def test_vacuum__frees_disk_space(self):
        """vacuum() should reclaim disk space after bulk deletion (regression test for #1156)"""
        session = self.init_session()
        db_path = session.cache.responses.db_path

        for i in range(200):
            cr = CachedResponse(status_code=200, url=f'https://example.com/{i}')
            cr.request = CachedRequest(method='GET', url=f'https://example.com/{i}')
            cr._content = b'x' * 50_000
            session.cache.save_response(cr, cache_key=f'k{i}')

        size_before = os.path.getsize(db_path)
        session.cache.delete(older_than=timedelta(seconds=-1))
        assert session.cache.responses.count() == 0

        session.cache.responses.vacuum()
        size_after = os.path.getsize(db_path)

        assert size_after < size_before / 10, (
            f'vacuum() did not reclaim disk space: {size_before} -> {size_after} bytes'
        )

    @patch.object(SQLiteDict, 'sorted')
    def test_filter__expired(self, mock_sorted):
        """Filtering by expired should use a more efficient SQL query"""
        session = self.init_session()

        session.cache.filter()
        mock_sorted.assert_called_with(expired=True)

        session.cache.filter(expired=False)
        mock_sorted.assert_called_with(expired=False)

    def test_sorted(self):
        """Test wrapper method for SQLiteDict.sorted(), with all arguments combined"""
        session = self.init_session(clear=False)
        now = utcnow()

        # Insert items with decreasing expiration time
        for i in range(500):
            delta = 1000 - i
            if i > 400:
                delta -= 2000

            response = CachedResponse(status_code=i, expires=now + timedelta(seconds=delta))
            session.cache.responses[f'key_{i}'] = response

        # Sorted items should be in ascending order by expiration time
        items = list(session.cache.sorted(key='expires', expired=False, reversed=True, limit=100))
        assert len(items) == 100

        prev_item = None
        for item in items:
            assert prev_item is None or prev_item.expires < item.expires
            assert item.cache_key
            assert not item.is_expired


class TestSQLiteDependencies:
    """Optional content-versioned dependency declarations for the SQLite backend"""

    def init_cache(self, tmp_path, name='deps'):
        return SQLiteCache(tmp_path / f'{name}.sqlite')

    def make_response(self, body, expires=None):
        response = CachedResponse(url='https://example.com')
        response._content = body
        if expires is not None:
            response.expires = expires
        return response

    def test_no_dependencies_by_default(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['k'] = self.make_response(b'body')
        assert cache.check_dependencies('k') == DependencyStatus.NO_DEPENDENCIES
        assert cache.is_dependency_valid('k')
        # Items without dependencies keep working exactly as before
        assert cache.get_response('k').content == b'body'

    def test_not_cached_status(self, tmp_path):
        cache = self.init_cache(tmp_path)
        assert cache.check_dependencies('missing') == DependencyStatus.NOT_CACHED
        assert cache.get_dependency_aware_response('missing') is None
        assert cache.get_dependency_aware_response('missing', 'default') == 'default'

    def test_content_version_based_on_body(self):
        r1 = self.make_response(b'abc')
        r2 = self.make_response(b'abc')
        r3 = self.make_response(b'xyz')
        assert get_content_version(r1) == get_content_version(r2)
        assert get_content_version(r1) != get_content_version(r3)

    def test_version_unaffected_by_expiration_refresh(self, tmp_path):
        cache = self.init_cache(tmp_path)
        response = self.make_response(b'same-body')
        cache.responses['input'] = response
        version = cache.get_version('input')

        cache.responses['input'] = self.make_response(
            b'same-body', expires=utcnow() + timedelta(hours=2)
        )
        assert cache.get_version('input') == version

    def test_declare_dependencies_captures_versions(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['in2'] = self.make_response(b'b')

        declared = cache.declare_dependencies('derived', ['in1', 'in2'])
        assert declared == {'in1': cache.get_version('in1'), 'in2': cache.get_version('in2')}
        assert cache.get_dependencies('derived') == declared

        # Mapping form allows explicit versions, including None (presence only)
        cache.declare_dependencies('derived', {'in1': None})
        assert cache.get_dependencies('derived') == {'in1': None}

    def test_declare_dependencies_unknown_key(self, tmp_path):
        cache = self.init_cache(tmp_path)
        with pytest.raises(KeyError):
            cache.declare_dependencies('derived', ['nope'])

    def test_valid_dependencies_allow_reuse(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['in2'] = self.make_response(b'b')
        cache.responses['derived'] = self.make_response(b'merged')
        cache.declare_dependencies('derived', ['in1', 'in2'])

        assert cache.check_dependencies('derived') == DependencyStatus.VALID
        assert cache.is_dependency_valid('derived')
        assert cache.get_dependency_aware_response('derived').content == b'merged'

    def test_version_mismatch_when_input_content_changes(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['input'] = self.make_response(b'v1')
        original_version = cache.get_version('input')
        cache.responses['derived'] = self.make_response(b'merged')
        cache.declare_dependencies('derived', ['input'])

        # Same cache key (same URL), different body: the derived result must be regenerated
        cache.responses['input'] = self.make_response(b'v2-different')
        assert cache.get_version('input') != original_version
        assert cache.check_dependencies('derived') == DependencyStatus.VERSION_MISMATCH
        assert not cache.is_dependency_valid('derived')
        assert cache.get_dependency_aware_response('derived') is None

        with pytest.raises(DependencyVersionMismatchError) as exc_info:
            cache.raise_for_dependencies('derived')
        error = exc_info.value
        assert error.dependency_key == 'input'
        assert error.expected_version == original_version
        assert error.actual_version == cache.get_version('input')

    def test_missing_dependency(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['input'] = self.make_response(b'a')
        cache.responses['derived'] = self.make_response(b'merged')
        cache.declare_dependencies('derived', ['input'])
        del cache.responses['input']

        # The dangling reference is retained so the missing input can still be reported
        assert 'input' in cache.get_dependencies('derived')
        assert cache.check_dependencies('derived') == DependencyStatus.MISSING
        with pytest.raises(MissingDependencyError) as exc_info:
            cache.raise_for_dependencies('derived')
        assert exc_info.value.missing_key == 'input'
        assert exc_info.value.dependent_key == 'derived'

    def test_cycle_detection(self, tmp_path):
        cache = self.init_cache(tmp_path)
        for key in ('a', 'b', 'c'):
            cache.responses[key] = self.make_response(key.encode())
        cache.declare_dependencies('a', ['b'])
        cache.declare_dependencies('b', ['c'])
        cache.declare_dependencies('c', ['a'])

        assert cache.check_dependencies('a') == DependencyStatus.CYCLE
        with pytest.raises(DependencyCycleError) as exc_info:
            cache.raise_for_dependencies('a')
        cycle = exc_info.value.cycle
        assert cycle[0] == cycle[-1]
        assert set(cycle) == {'a', 'b', 'c'}

    def test_self_dependency_is_a_cycle(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['a'] = self.make_response(b'a')
        cache.declare_dependencies('a', ['a'])
        assert cache.check_dependencies('a') == DependencyStatus.CYCLE

    def test_transitive_dependencies_are_checked(self, tmp_path):
        cache = self.init_cache(tmp_path)
        for key in ('d', 'e', 'f'):
            cache.responses[key] = self.make_response(key.encode())
        cache.declare_dependencies('e', ['f'])
        cache.declare_dependencies('d', ['e'])
        assert cache.check_dependencies('d') == DependencyStatus.VALID

        f_version = cache.get_version('f')
        cache.responses['f'] = self.make_response(b'f-changed')
        with pytest.raises(DependencyVersionMismatchError) as exc_info:
            cache.raise_for_dependencies('d')
        assert exc_info.value.dependency_key == 'f'
        assert exc_info.value.expected_version == f_version

        cache.responses['f'] = self.make_response(b'f-changed')
        del cache.responses['f']
        assert cache.check_dependencies('d') == DependencyStatus.MISSING

    def test_presence_only_dependency(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['derived'] = self.make_response(b'merged')
        cache.declare_dependencies('derived', {'in1': None})

        cache.responses['in1'] = self.make_response(b'totally-different')
        assert cache.check_dependencies('derived') == DependencyStatus.VALID
        del cache.responses['in1']
        assert cache.check_dependencies('derived') == DependencyStatus.MISSING

    def test_replace_and_merge_declarations(self, tmp_path):
        cache = self.init_cache(tmp_path)
        for key in ('a', 'b', 'c'):
            cache.responses[key] = self.make_response(key.encode())

        cache.declare_dependencies('derived', ['a'])
        cache.declare_dependencies('derived', ['b'], replace=False)
        assert set(cache.get_dependencies('derived')) == {'a', 'b'}

        cache.declare_dependencies('derived', ['c'])
        assert set(cache.get_dependencies('derived')) == {'c'}

    def test_drop_dependencies(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['derived'] = self.make_response(b'merged')
        cache.declare_dependencies('derived', ['in1'])
        cache.drop_dependencies('derived')
        assert cache.get_dependencies('derived') == {}
        assert cache.check_dependencies('derived') == DependencyStatus.NO_DEPENDENCIES

    def test_deleting_dependent_removes_its_declarations(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['derived'] = self.make_response(b'merged')
        cache.declare_dependencies('derived', ['in1'])
        del cache.responses['derived']
        assert cache.get_dependencies('derived') == {}
        assert cache.check_dependencies('in1') == DependencyStatus.NO_DEPENDENCIES

    def test_persistence_across_reopen(self, tmp_path):
        db_path = tmp_path / 'persist.sqlite'
        cache = SQLiteCache(db_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['derived'] = self.make_response(b'merged')
        version = cache.get_version('in1')
        cache.declare_dependencies('derived', ['in1'])
        cache.close()

        reopened = SQLiteCache(db_path)
        assert reopened.get_version('in1') == version
        assert reopened.check_dependencies('derived') == DependencyStatus.VALID

    def test_bulk_delete_removes_owned_declarations(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['derived'] = self.make_response(b'merged')
        cache.declare_dependencies('derived', ['in1'])

        cache.responses.bulk_delete(['derived'])
        assert cache.get_dependencies('derived') == {}
        assert 'in1' in cache.responses

    def test_expired_delete_removes_owned_declarations(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['derived'] = self.make_response(
            b'merged', expires=utcnow() - timedelta(1)
        )
        cache.declare_dependencies('derived', ['in1'])
        cache.delete(expired=True)
        assert cache.get_dependencies('derived') == {}
        assert 'in1' in cache.responses

    def test_clear_removes_dependency_state(self, tmp_path):
        cache = self.init_cache(tmp_path)
        cache.responses['in1'] = self.make_response(b'a')
        cache.responses['derived'] = self.make_response(b'merged')
        cache.declare_dependencies('derived', ['in1'])
        cache.clear()

        cache.responses['fresh'] = self.make_response(b'fresh')
        assert cache.check_dependencies('fresh') == DependencyStatus.NO_DEPENDENCIES

    def test_legacy_database_gets_version_column(self, tmp_path):
        db_path = tmp_path / 'legacy.sqlite'
        # Simulate a database created before dependency support (no version column)
        connection = sqlite3.connect(db_path)
        connection.execute(
            'CREATE TABLE responses (key TEXT PRIMARY KEY, value BLOB, expires INTEGER)'
        )
        connection.execute(
            "INSERT INTO responses (key, value, expires) VALUES ('old', x'00', NULL)"
        )
        connection.commit()
        connection.close()

        cache = SQLiteCache(db_path)
        assert cache.get_version('old') is None
        assert 'old' in cache.responses

        cache.responses['new'] = self.make_response(b'new-body')
        assert cache.get_version('new') == get_content_version(self.make_response(b'new-body'))
