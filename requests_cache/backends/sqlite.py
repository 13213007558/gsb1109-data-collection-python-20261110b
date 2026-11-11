"""SQLite cache backend. For usage details, see :ref:`Backends: SQLite <sqlite>`.

.. automodsumm:: requests_cache.backends.sqlite
   :classes-only:
   :nosignatures:
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from logging import getLogger
from os import unlink
from os.path import getsize, isfile
from pathlib import Path
from tempfile import gettempdir
from time import sleep, time
from typing import Any, Collection, Dict, Iterable, Iterator, List, Optional, Tuple, Type

from attrs import define, field
from platformdirs import user_cache_dir

from .._utils import chunkify, get_valid_kwargs
from ..models.response import CachedResponse
from ..policy import ExpirationTime
from ..serializers import SerializerType, pickle_serializer
from . import BaseCache, BaseStorage, StrOrPath
from .base import DESERIALIZE_ERRORS, VT

MEMORY_URI = 'file::memory:?cache=shared'
SQLITE_MAX_VARIABLE_NUMBER = 999
QUARANTINE_TABLE = 'quarantine'

logger = getLogger(__name__)


@define
class CorruptCacheEntry:
    """A cache entry whose serialized value cannot be deserialized.

    Args:
        cache_key: Cache key of the corrupted response
        error: Deserialization error message
        raw_value: Original serialized bytes, exactly as stored in the database
        redirect_keys: Cache keys in the redirects table that point to this response
        quarantined_at: Unix timestamp of when the entry was moved to the quarantine table;
            only set for entries read back from quarantine
    """

    cache_key: str
    error: str
    raw_value: bytes
    redirect_keys: List[str] = field(factory=list)
    quarantined_at: Optional[int] = None


class SQLiteCache(BaseCache):
    """SQLite cache backend.

    Args:
        db_path: Database file path
        use_cache_dir: Store database in a user cache directory (e.g., `~/.cache/http_cache.sqlite`)
        use_temp: Store database in a temp directory (e.g., ``/tmp/http_cache.sqlite``)
        use_memory: Store database in memory instead of in a file
        busy_timeout: Timeout in milliseconds for SQLite to wait if a table is locked.
            See `pragma: busy_timeout <https://www.sqlite.org/pragma.html#pragma_busy_timeout>`_
            for details.
        fast_save: Significantly increases cache write performance, but with the possibility of data
            loss. See `pragma: synchronous <https://www.sqlite.org/pragma.html#pragma_synchronous>`_
            for details.
        wal: Use `Write Ahead Logging <https://sqlite.org/wal.html>`_, so readers do not block writers.
        kwargs: Additional keyword arguments for :py:func:`sqlite3.connect`
    """

    def __init__(
        self,
        db_path: StrOrPath = 'http_cache',
        serializer: Optional[SerializerType] = None,
        **kwargs,
    ):
        super().__init__(cache_name=str(db_path), **kwargs)
        # Only override serializer if a non-None value is specified
        skwargs = {'serializer': serializer, **kwargs} if serializer else kwargs
        self.responses: SQLiteDict = SQLiteDict(db_path, table_name='responses', **skwargs)
        self.redirects: SQLiteDict = SQLiteDict(
            db_path,
            table_name='redirects',
            lock=self.responses._lock,
            serializer=None,
            **kwargs,
        )

    @property
    def db_path(self) -> StrOrPath:
        return self.responses.db_path

    def clear(self):
        """Delete all items from the cache. If this fails due to a corrupted cache or other I/O
        error, this will  attempt to delete the cache file and re-initialize.
        """
        try:
            super().clear()
        except Exception:
            logger.exception('Failed to clear cache')
            if isfile(self.responses.db_path):
                unlink(self.responses.db_path)
            self.responses.init_db()
            self.redirects.init_db()

    # A more efficient SQLite implementation of BaseCache.delete
    def delete(
        self,
        *keys: str,
        expired: bool = False,
        vacuum: bool = True,
        **kwargs,
    ):
        """Remove responses from the cache according one or more conditions.

        Args:
            keys: Remove responses with these cache keys
            expired: Remove all expired responses
            vacuum: Vacuum the database after deleting responses to free up disk space
            kwargs: Additional keyword arguments for :py:meth:`BaseCache.delete`
        """
        # If deleting a single key, skip bulk delete + vacuum and ignore any KeyErrors
        if len(keys) == 1:
            try:
                del self.responses[keys[0]]
            except KeyError:
                pass
        # Bulk delete multiple keys and/or all expired responses in SQL
        elif keys:
            self.responses.bulk_delete(keys)
        if expired:
            self._delete_expired()

        # For any remaining conditions, use base implementation
        if kwargs:
            with self.responses._lock:
                return super().delete(**kwargs)
        else:
            self._prune_redirects()

        # Skip vacuuming if only one key was deleted, or if explicitly disabled
        if vacuum and (expired or kwargs or len(keys) > 1):
            self.responses.vacuum()

    def _delete_expired(self):
        """A more efficient implementation of deleting expired responses in SQL"""
        with self.responses._lock, self.responses.connection(commit=True) as con:
            con.execute(
                f'DELETE FROM {self.responses.table_name} WHERE expires <= ?',
                (round(time()),),
            )

    def _prune_redirects(self):
        """A more efficient implementation of removing invalid redirects in SQL"""
        with self.redirects.connection(commit=True) as conn:
            t1 = self.redirects.table_name
            t2 = self.responses.table_name
            conn.execute(
                f'DELETE FROM {t1} WHERE key IN ('
                f'    SELECT {t1}.key FROM {t1}'
                f'    LEFT JOIN {t2} ON {t2}.key = {t1}.value'
                f'    WHERE {t2}.key IS NULL'
                ')'
            )

    def count(self, expired: bool = True) -> int:
        """Count number of responses, optionally excluding expired

        Args:
            expired: Set to ``False`` to count only unexpired responses
        """
        return self.responses.count(expired=expired)

    # A more efficient implementation of BaseCache.filter to make use of indexes
    def filter(
        self,
        valid: bool = True,
        expired: bool = True,
        invalid: bool = False,
        older_than: ExpirationTime = None,
    ) -> Iterator[CachedResponse]:
        if valid and not invalid:
            return self.responses.sorted(expired=expired)
        else:
            return super().filter(
                valid=valid, expired=expired, invalid=invalid, older_than=older_than
            )

    # A more efficient implementation of BaseCache.recreate_keys
    def recreate_keys(self):
        with self.responses.bulk_commit():
            super().recreate_keys()

    def sorted(
        self,
        key: str = 'expires',
        reversed: bool = False,
        limit: Optional[int] = None,
        expired: bool = True,
    ) -> Iterator[CachedResponse]:
        """Get cached responses, with sorting and other query options.

        Args:
            key: Key to sort by; either 'expires', 'size', or 'key'
            reversed: Sort in descending order
            limit: Maximum number of responses to return
            expired: Set to ``False`` to exclude expired responses
        """
        return self.responses.sorted(key, reversed, limit, expired)

    def scan_corrupt_entries(self) -> List[CorruptCacheEntry]:
        """Scan the cache for responses that cannot be deserialized, without modifying anything.

        Every entry in the ``responses`` table is deserialized using the current serializer.
        Entries that fail are returned together with their original serialized bytes and the keys
        of any redirects that point to them, so the result can be inspected before calling
        :py:meth:`quarantine_corrupt_entries`.

        Returns:
            Corrupted entries found in the cache
        """
        redirects_by_value: Dict[str, List[str]] = {}
        with self.redirects.connection() as con:
            for redirect_key, target_key in con.execute(
                f'SELECT key, value FROM {self.redirects.table_name}'
            ):
                redirects_by_value.setdefault(target_key, []).append(redirect_key)

        corrupt_entries: List[CorruptCacheEntry] = []
        serializer = self.responses.serializer
        if not serializer:
            return corrupt_entries
        with self.responses.connection() as con:
            for cache_key, raw_value in con.execute(
                f'SELECT key, value FROM {self.responses.table_name}'
            ):
                try:
                    serializer.loads(raw_value)
                except DESERIALIZE_ERRORS as e:
                    logger.error(f'Unable to deserialize response with key {cache_key}: {e}')
                    corrupt_entries.append(
                        CorruptCacheEntry(
                            cache_key=cache_key,
                            error=str(e),
                            raw_value=_to_bytes(raw_value),
                            redirect_keys=redirects_by_value.get(cache_key, []),
                        )
                    )
        return corrupt_entries

    def quarantine_corrupt_entries(
        self,
        entries: Optional[Iterable[CorruptCacheEntry]] = None,
        vacuum: bool = False,
    ) -> List[CorruptCacheEntry]:
        """Move corrupted responses into a ``quarantine`` table instead of deleting them.

        This is a maintenance operation intended to be run after reviewing the results of
        :py:meth:`scan_corrupt_entries`. Each quarantined entry keeps its original serialized
        bytes, along with the deserialization error and the keys of the redirects associated with
        it, so it can be inspected or repaired separately. The corrupted rows and their redirect
        entries are removed from the active cache tables, while all other URLs keep working.

        Args:
            entries: Corrupted entries to quarantine. If not provided, a scan is run first.
            vacuum: Vacuum the database afterwards to free up disk space
        Returns:
            The entries that were quarantined
        """
        if entries is None:
            entries = self.scan_corrupt_entries()
        entries = list(entries)
        if not entries:
            return []

        responses = self.responses
        redirects = self.redirects
        quarantined: List[CorruptCacheEntry] = []
        with responses.connection(commit=True) as con:
            table_created = False
            now = round(time())
            for entry in entries:
                row = con.execute(
                    f'SELECT value FROM {responses.table_name} WHERE key=?',
                    (entry.cache_key,),
                ).fetchone()
                # Skip entries that were removed between the preview and this operation
                if row is None:
                    logger.debug(
                        f'Skipping quarantine for missing cache key: {entry.cache_key}'
                    )
                    continue
                if not table_created:
                    con.execute(
                        f'CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} ('
                        '    key TEXT PRIMARY KEY,'
                        '    value BLOB,'
                        '    error TEXT,'
                        '    redirect_keys TEXT,'
                        '    quarantined_at INTEGER'
                        ')'
                    )
                    table_created = True
                con.execute(
                    f'INSERT OR REPLACE INTO {QUARANTINE_TABLE}'
                    ' (key, value, error, redirect_keys, quarantined_at) VALUES (?,?,?,?,?)',
                    (
                        entry.cache_key,
                        row[0],
                        entry.error,
                        json.dumps(entry.redirect_keys),
                        now,
                    ),
                )
                con.execute(
                    f'DELETE FROM {responses.table_name} WHERE key=?',
                    (entry.cache_key,),
                )
                if entry.redirect_keys:
                    marks, args = _format_sequence(entry.redirect_keys)
                    con.execute(
                        f'DELETE FROM {redirects.table_name} WHERE key IN ({marks})',
                        args,
                    )
                quarantined.append(entry)

        if quarantined:
            logger.info(f'Quarantined {len(quarantined)} corrupted response(s)')
        if vacuum and quarantined:
            responses.vacuum()
        return quarantined

    def get_quarantined_entries(self) -> List[CorruptCacheEntry]:
        """Read corrupted entries previously moved to the ``quarantine`` table.

        The original serialized bytes are included, so entries can be inspected or repaired
        separately without affecting the active cache tables.

        Returns:
            Quarantined entries, in the order they were stored
        """
        entries: List[CorruptCacheEntry] = []
        with self.responses.connection() as con:
            try:
                rows = con.execute(
                    f'SELECT key, value, error, redirect_keys, quarantined_at'
                    f' FROM {QUARANTINE_TABLE} ORDER BY quarantined_at'
                ).fetchall()
            except sqlite3.OperationalError:
                return entries

        for cache_key, raw_value, error, redirect_keys, quarantined_at in rows:
            entries.append(
                CorruptCacheEntry(
                    cache_key=cache_key,
                    error=error,
                    raw_value=_to_bytes(raw_value),
                    redirect_keys=json.loads(redirect_keys) if redirect_keys else [],
                    quarantined_at=quarantined_at,
                )
            )
        return entries


class SQLiteDict(BaseStorage):
    """A dictionary-like interface for SQLite"""

    def __init__(
        self,
        db_path: StrOrPath,
        table_name: str = 'http_cache',
        busy_timeout: Optional[int] = None,
        fast_save: bool = False,
        serializer: Optional[SerializerType] = pickle_serializer,
        use_cache_dir: bool = False,
        use_memory: bool = False,
        use_temp: bool = False,
        wal: bool = False,
        **kwargs,
    ):
        super().__init__(serializer=serializer, **kwargs)
        self._active_transaction = False
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = kwargs.pop('lock', threading.RLock())
        if kwargs.pop('isolation_level', None):
            logger.warning(
                'SQLite backend requires exclusive transactions; isolation_level is ignored'
            )
        self.connection_kwargs = get_valid_kwargs(sqlite_template, kwargs)
        self.connection_kwargs.setdefault('check_same_thread', False)
        if use_memory:
            self.connection_kwargs['uri'] = True
        self.db_path = _get_sqlite_cache_path(db_path, use_cache_dir, use_temp, use_memory)
        self.busy_timeout = busy_timeout
        self.fast_save = fast_save
        self.table_name = table_name
        self.wal = wal
        self.init_db()

    def init_db(self):
        """Initialize the database, if it hasn't already been"""
        self.close()
        with self.connection(commit=True) as con:
            # Add new column to tables created before 1.0
            try:
                con.execute(f'ALTER TABLE {self.table_name} ADD COLUMN expires INTEGER')
            except sqlite3.OperationalError:
                pass

            con.execute(
                f'CREATE TABLE IF NOT EXISTS {self.table_name} ('
                '    key TEXT PRIMARY KEY,'
                '    value BLOB, '
                '    expires INTEGER'
                ')'
            )
            con.execute(f'CREATE INDEX IF NOT EXISTS expires_idx ON {self.table_name}(expires)')

    @contextmanager
    def connection(self, commit=False) -> Iterator[sqlite3.Connection]:
        """Get a thread-local database connection"""
        with self._lock:
            if not self._connection:
                logger.debug(f'Opening connection to {self.db_path}:{self.table_name}')
                self._connection = sqlite3.connect(
                    self.db_path, isolation_level=None, **self.connection_kwargs
                )
                # Note: DBAPI doesn't support integer placeholders
                if self.busy_timeout is not None:
                    self._connection.execute(f'PRAGMA busy_timeout={self.busy_timeout}')
                if self.fast_save:
                    self._connection.execute('PRAGMA synchronous=OFF')
                if self.wal:
                    self._connection.execute('PRAGMA journal_mode=WAL')
                # In WAL mode, default to normal sync mode (best balance between safety/performance)
                if self.wal and not self.fast_save:
                    self._connection.execute('PRAGMA synchronous=NORMAL')

        # Multithreaded write operations must be run in serial
        if commit and not self._active_transaction:
            with self._acquire_sqlite_lock():
                yield self._connection
        # Read operations can be run in parallel (no lock or COMMIT)
        else:
            yield self._connection

    def close(self):
        """Close any active connections"""
        with self._lock:
            if self._connection:
                self._connection.close()
                self._connection = None

    @contextmanager
    def bulk_commit(self):
        """Insert a large number of records within a single transaction

        Example:

            >>> d1 = SQLiteDict('test')
            >>> with d1.bulk_commit():
            ...     for i in range(1000):
            ...         d1[i] = i * 2

        """
        with self._acquire_sqlite_lock():
            yield

    @contextmanager
    def _acquire_sqlite_lock(self):
        with self._lock:
            # Wait until we can acquire a write lock
            while True:
                try:
                    self._connection.execute('BEGIN IMMEDIATE')
                    self._active_transaction = True
                    break
                except sqlite3.OperationalError:
                    # note that time.sleep can take 50us+:
                    # https://github.com/python/cpython/issues/125997.
                    sleep(1e-4)
                    continue
            try:
                yield
                self._connection.commit()
            except sqlite3.OperationalError:
                self._connection.rollback()
            finally:
                self._active_transaction = False

    def __del__(self):
        self.close()

    def __delitem__(self, key):
        with self.connection(commit=True) as con:
            cur = con.execute(f'DELETE FROM {self.table_name} WHERE key=?', (key,))
        if not cur.rowcount:
            raise KeyError

    def __getitem__(self, key):
        with self.connection() as con:
            # Using placeholders here with python 3.12+ and concurrency results in the error:
            # sqlite3.InterfaceError: bad parameter or other API misuse
            cur = con.execute(f"SELECT value FROM {self.table_name} WHERE key='{key}'")
            row = cur.fetchone()
            cur.close()
            if not row:
                raise KeyError(key)

            return self.deserialize(key, row[0])

    def __setitem__(self, key, value):
        self._write(key, value)

    def _write(self, key, value):
        # If available, set expiration as a timestamp in unix format
        expires = getattr(value, 'expires_unix', None)
        value = self.serialize(value)
        with self.connection(commit=True) as con:
            con.execute(
                f'INSERT OR REPLACE INTO {self.table_name} (key,value,expires) VALUES (?,?,?)',
                (key, value, expires),
            )

    def __iter__(self):
        with self.connection() as con:
            for row in con.execute(f'SELECT key FROM {self.table_name}'):
                yield row[0]

    def __len__(self):
        return self.count()

    def bulk_delete(self, keys=None, values=None):
        """Delete multiple items from the cache, without raising errors for any missing items.
        Supports deleting by either key or by value.
        """
        if not keys and not values:
            return

        column = 'key' if keys else 'value'
        with self._lock, self.connection(commit=True) as con:
            # Split into small enough chunks for SQLite to handle
            for chunk in chunkify(keys or values, max_size=SQLITE_MAX_VARIABLE_NUMBER):
                marks, args = _format_sequence(chunk)
                statement = f'DELETE FROM {self.table_name} WHERE {column} IN ({marks})'
                con.execute(statement, args)

    def clear(self):
        with self._lock:
            with self.connection(commit=True) as con:
                con.execute(f'DROP TABLE IF EXISTS {self.table_name}')
            self.init_db()
            self.vacuum()

    def count(self, expired: bool = True) -> int:
        """Count number of responses, optionally excluding expired"""
        filter_expr = ''
        params: Tuple = ()
        if not expired:
            filter_expr = 'WHERE expires is null or expires > ?'
            params = (time(),)
        query = f'SELECT COUNT(key) FROM {self.table_name} {filter_expr}'

        with self.connection() as con:
            return con.execute(query, params).fetchone()[0]

    def serialize(self, value: VT):
        value = super().serialize(value)
        return sqlite3.Binary(value) if isinstance(value, bytes) else value

    def size(self) -> int:
        """Return the size of the database, in bytes. For an in-memory database, this will be an
        estimate based on page size.
        """
        try:
            return getsize(self.db_path)
        except IOError:
            return self._estimate_size()

    def _estimate_size(self) -> int:
        """Estimate the current size of the database based on page count * size"""
        with self.connection() as conn:
            page_count = conn.execute('PRAGMA page_count').fetchone()[0]
            page_size = conn.execute('PRAGMA page_size').fetchone()[0]
            return page_count * page_size

    def sorted(
        self,
        key: str = 'expires',
        reversed: bool = False,
        limit: Optional[int] = None,
        expired: bool = True,
    ) -> Iterator[CachedResponse]:
        """Get cache values in sorted order; see :py:meth:`.SQLiteCache.sorted` for usage details"""
        # Get sort key, direction, and limit
        if key not in ['expires', 'size', 'key']:
            raise ValueError(f'Invalid sort key: {key}')
        if key == 'size':
            key = 'LENGTH(value)'
        direction = 'DESC' if reversed else 'ASC'
        limit_expr = f'LIMIT {limit}' if limit else ''

        # Filter out expired items, if specified
        filter_expr = ''
        params: Tuple = ()
        if not expired:
            filter_expr = 'WHERE expires is null or expires > ?'
            params = (time(),)

        with self.connection() as con:
            for row in con.execute(
                f'SELECT key, value FROM {self.table_name} {filter_expr}'
                f'  ORDER BY {key} {direction} {limit_expr}',
                params,
            ):
                result = self.deserialize(row[0], row[1])
                # Omit any results that can't be deserialized
                if result:
                    yield result

    def vacuum(self):
        # VACUUM cannot run inside a transaction; acquire the lock and run it directly
        with self._lock:
            if self._connection:
                if self._active_transaction:
                    self._connection.commit()
                    self._active_transaction = False
                self._connection.execute('VACUUM')


def _format_sequence(values: Collection) -> Tuple[str, List]:
    """Get SQL parameter marks for a sequence-based query"""
    return ','.join(['?'] * len(values)), list(values)


def _to_bytes(value: Any) -> bytes:
    """Return a stored BLOB value as plain bytes, regardless of the sqlite3 row factory/type"""
    return bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else value


def _get_sqlite_cache_path(
    db_path: StrOrPath, use_cache_dir: bool, use_temp: bool, use_memory: bool = False
) -> StrOrPath:
    """Get a resolved path for a SQLite database file (or memory URI)"""
    # Use an in-memory database, if specified
    db_path = str(db_path)
    if use_memory:
        return MEMORY_URI
    elif ':memory:' in db_path or 'mode=memory' in db_path:
        return db_path

    # Add file extension if not specified
    if not Path(db_path).suffix:
        db_path += '.sqlite'
    return get_cache_path(db_path, use_cache_dir, use_temp)


def get_cache_path(db_path: StrOrPath, use_cache_dir: bool = False, use_temp: bool = False) -> Path:
    """Get a resolved cache path"""
    db_path = Path(db_path)

    # Save to platform-specific temp or user cache directory, if specified
    if use_cache_dir and not db_path.is_absolute():
        db_path = Path(user_cache_dir()) / db_path
    elif use_temp and not db_path.is_absolute():
        db_path = Path(gettempdir()) / db_path

    # Expand relative and user paths (~), make parent dir(s), and better error if parent is a file
    db_path = db_path.expanduser().absolute()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        raise FileExistsError(
            f'Parent path exists and is not a directory: {db_path.parent}.'
            'Please either delete the file or choose a different path.'
        ) from None
    return db_path


def sqlite_template(
    timeout: float = 5.0,
    detect_types: int = 0,
    isolation_level: Optional[str] = None,
    check_same_thread: bool = True,
    factory: Optional[Type] = None,
    cached_statements: int = 100,
    uri: bool = False,
):
    """Template function to get an accurate signature for the builtin :py:func:`sqlite3.connect`"""
