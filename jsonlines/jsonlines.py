"""
jsonlines implementation
"""

import builtins
import codecs
import enum
import io
import json
import os
import types
import typing
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    NamedTuple,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

import attr

orjson: Optional[types.ModuleType]
try:
    import orjson
except ImportError:
    orjson = None

ujson: Optional[types.ModuleType]
try:
    import ujson
except ImportError:
    ujson = None


VALID_TYPES = {
    bool,
    dict,
    float,
    int,
    list,
    str,
}

# Characters to skip at the beginning of a line. Note: at most one such
# character is skipped per line.
SKIPPABLE_SINGLE_INITIAL_CHARS = (
    "\x1e",  # RFC7464 text sequence
    codecs.BOM_UTF8.decode(),
)

# Byte-level equivalent of SKIPPABLE_SINGLE_INITIAL_CHARS.
SKIPPABLE_SINGLE_INITIAL_BYTES = (
    b"\x1e",  # RFC7464 text sequence
    codecs.BOM_UTF8,
)


def strip_skippable_initial_bytes(line: bytes) -> bytes:
    """
    Strip a single leading record separator or UTF-8 BOM from a byte line.

    This is the byte-level equivalent of the character stripping in
    :py:class:`Reader`; the UTF-8 BOM is three bytes long.
    """
    for prefix in SKIPPABLE_SINGLE_INITIAL_BYTES:
        if line.startswith(prefix):
            return line[len(prefix) :]
    return line


class DumpsResultConversion(enum.Enum):
    LeaveAsIs = enum.auto()
    EncodeToBytes = enum.auto()
    DecodeToString = enum.auto()


# https://docs.python.org/3/library/functions.html#open
Openable = Union[str, bytes, int, os.PathLike]

LoadsCallable = Callable[[Union[str, bytes]], Any]
DumpsCallable = Callable[[Any], Union[str, bytes]]

# Currently, JSON structures cannot be typed properly:
# - https://github.com/python/typing/issues/182
# - https://github.com/python/mypy/issues/731
JSONCollection = Union[Dict[str, Any], List[Any]]
JSONScalar = Union[bool, float, int, str]
JSONValue = Union[JSONCollection, JSONScalar]
TJSONValue = TypeVar("TJSONValue", bound=JSONValue)

TRW = TypeVar("TRW", bound="ReaderWriterBase")

# Default to using the fastest JSON library for reading, falling back to the
# standard library (always available) if none are installed.
if orjson is not None:
    default_loads = orjson.loads
elif ujson is not None:
    default_loads = ujson.loads
else:
    default_loads = json.loads


# For writing, use the stdlib. Other packages may be faster but their behaviour
# (supported types etc.) and output (whitespace etc.) are not the same as the
# stdlib json module, so this should be opt-in via the ‘dumps=’ arg.
def default_dumps(obj: Any) -> str:
    """
    Fake ``dumps()`` function to use as a default marker.
    """
    raise NotImplementedError  # pragma: no cover


@attr.s(auto_exc=True, auto_attribs=True)
class Error(Exception):
    """
    Base error class.
    """

    message: str


@attr.s(auto_exc=True, auto_attribs=True, init=False)
class InvalidLineError(Error, ValueError):
    """
    Error raised when an invalid line is encountered.

    This happens when the line does not contain valid JSON, or if a
    specific data type has been requested, and the line contained a
    different data type.

    The original line itself is stored on the exception instance as the
    ``.line`` attribute, and the line number as ``.lineno``.

    This class subclasses both ``jsonlines.Error`` and the built-in
    ``ValueError``.
    """

    #: The invalid line
    line: Union[str, bytes]

    #: The line number
    lineno: int

    def __init__(self, message: str, line: Union[str, bytes], lineno: int) -> None:
        self.line = line.rstrip()
        self.lineno = lineno
        super().__init__(f"{message} (line {lineno})")


@attr.s(auto_exc=True, auto_attribs=True)
class StaleIndexError(Error):
    """
    Error raised when a record index no longer matches its file.

    This happens when the indexed file was replaced or truncated after
    the index was built. Rebuild the index with
    :py:meth:`IndexedReader.rebuild` to recover.
    """


@attr.s(auto_attribs=True, repr=False)
class ReaderWriterBase:
    """
    Base class with shared behaviour for both the reader and writer.
    """

    _fp: Union[typing.IO[str], typing.IO[bytes], None] = attr.ib(
        default=None, init=False
    )
    _closed: bool = attr.ib(default=False, init=False)
    _should_close_fp: bool = attr.ib(default=False, init=False)

    def close(self) -> None:
        """
        Close this reader/writer.

        This closes the underlying file if that file has been opened by
        this reader/writer. When an already opened file-like object was
        provided, the caller is responsible for closing it.
        """
        if self._closed:
            return
        self._closed = True
        if self._fp is not None and self._should_close_fp:
            self._fp.close()

    def __repr__(self) -> str:
        cls_name = type(self).__name__
        wrapped = self._repr_for_wrapped()
        return f"<jsonlines.{cls_name} at 0x{id(self):x} wrapping {wrapped}>"

    def _repr_for_wrapped(self) -> str:
        raise NotImplementedError  # pragma: no cover

    def __enter__(self: TRW) -> TRW:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[types.TracebackType],
    ) -> None:
        self.close()


@attr.s(auto_attribs=True, repr=False)
class Reader(ReaderWriterBase):
    """
    Reader for the jsonlines format.

    The first argument must be an iterable that yields JSON encoded
    strings. Usually this will be a readable file-like object, such as
    an open file or an ``io.TextIO`` instance, but it can also be
    something else as long as it yields strings when iterated over.

    Instances are iterable and can be used as a context manager.

    The `loads` argument can be used to replace the standard json
    decoder. If specified, it must be a callable that accepts a
    (unicode) string and returns the decoded object.

    :param file_or_iterable: file-like object or iterable yielding lines as
        strings
    :param loads: custom json decoder callable
    """

    _file_or_iterable: Union[
        typing.IO[str], typing.IO[bytes], Iterable[Union[str, bytes]]
    ]
    _line_iter: Iterator[Tuple[int, Union[bytes, str]]] = attr.ib(init=False)
    _loads: LoadsCallable = attr.ib(default=default_loads, kw_only=True)

    def __attrs_post_init__(self) -> None:
        if isinstance(self._file_or_iterable, io.IOBase):
            self._fp = cast(
                Union[typing.IO[str], typing.IO[bytes]],
                self._file_or_iterable,
            )

        self._line_iter = enumerate(self._file_or_iterable, 1)

    # No type specified, None not allowed
    @overload
    def read(
        self,
        *,
        type: Literal[None] = ...,
        allow_none: Literal[False] = ...,
        skip_empty: bool = ...,
    ) -> JSONValue:
        ...  # pragma: no cover

    # No type specified, None allowed
    @overload
    def read(
        self,
        *,
        type: Literal[None] = ...,
        allow_none: Literal[True],
        skip_empty: bool = ...,
    ) -> Optional[JSONValue]:
        ...  # pragma: no cover

    # Type specified, None not allowed
    @overload
    def read(
        self,
        *,
        type: Type[TJSONValue],
        allow_none: Literal[False] = ...,
        skip_empty: bool = ...,
    ) -> TJSONValue:
        ...  # pragma: no cover

    # Type specified, None allowed
    @overload
    def read(
        self,
        *,
        type: Type[TJSONValue],
        allow_none: Literal[True],
        skip_empty: bool = ...,
    ) -> Optional[TJSONValue]:
        ...  # pragma: no cover

    # Generic definition
    @overload
    def read(
        self,
        *,
        type: Optional[Type[Any]] = ...,
        allow_none: bool = ...,
        skip_empty: bool = ...,
    ) -> Optional[JSONValue]:
        ...  # pragma: no cover

    def read(
        self,
        *,
        type: Optional[Type[Any]] = None,
        allow_none: bool = False,
        skip_empty: bool = False,
    ) -> Optional[JSONValue]:
        """
        Read and decode a line.

        The optional `type` argument specifies the expected data type.
        Supported types are ``dict``, ``list``, ``str``, ``int``,
        ``float``, and ``bool``. When specified, non-conforming lines
        result in :py:exc:`InvalidLineError`.

        By default, input lines containing ``null`` (in JSON) are
        considered invalid, and will cause :py:exc:`InvalidLineError`.
        The `allow_none` argument can be used to change this behaviour,
        in which case ``None`` will be returned instead.

        If `skip_empty` is set to ``True``, empty lines and lines
        containing only whitespace are silently skipped.
        """
        if self._closed:
            raise RuntimeError("reader is closed")
        if type is not None and type not in VALID_TYPES:
            raise ValueError("invalid type specified")

        try:
            lineno, line = next(self._line_iter)
            while skip_empty and not line.rstrip():
                lineno, line = next(self._line_iter)
        except StopIteration:
            raise EOFError from None

        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError as orig_exc:
                exc = InvalidLineError(
                    f"line is not valid utf-8: {orig_exc}", line, lineno
                )
                raise exc from orig_exc

        if line.startswith(SKIPPABLE_SINGLE_INITIAL_CHARS):
            line = line[1:]

        try:
            value: JSONValue = self._loads(line)
        except ValueError as orig_exc:
            exc = InvalidLineError(
                f"line contains invalid json: {orig_exc}", line, lineno
            )
            raise exc from orig_exc

        if value is None:
            if allow_none:
                return None
            raise InvalidLineError("line contains null value", line, lineno)

        if type is not None:
            valid = isinstance(value, type)
            if type is int and isinstance(value, bool):
                # isinstance() is not sufficient, since bool is an int subclass
                valid = False
            if not valid:
                raise InvalidLineError(
                    "line does not match requested type", line, lineno
                )

        return value

    # No type specified, None not allowed
    @overload
    def iter(
        self,
        *,
        type: Literal[None] = ...,
        allow_none: Literal[False] = ...,
        skip_empty: bool = ...,
        skip_invalid: bool = ...,
    ) -> Iterator[JSONValue]:
        ...  # pragma: no cover

    # No type specified, None allowed
    @overload
    def iter(
        self,
        *,
        type: Literal[None] = ...,
        allow_none: Literal[True],
        skip_empty: bool = ...,
        skip_invalid: bool = ...,
    ) -> Iterator[JSONValue]:
        ...  # pragma: no cover

    # Type specified, None not allowed
    @overload
    def iter(
        self,
        *,
        type: Type[TJSONValue],
        allow_none: Literal[False] = ...,
        skip_empty: bool = ...,
        skip_invalid: bool = ...,
    ) -> Iterator[TJSONValue]:
        ...  # pragma: no cover

    # Type specified, None allowed
    @overload
    def iter(
        self,
        *,
        type: Type[TJSONValue],
        allow_none: Literal[True],
        skip_empty: bool = ...,
        skip_invalid: bool = ...,
    ) -> Iterator[Optional[TJSONValue]]:
        ...  # pragma: no cover

    # Generic definition
    @overload
    def iter(
        self,
        *,
        type: Optional[Type[TJSONValue]] = ...,
        allow_none: bool = ...,
        skip_empty: bool = ...,
        skip_invalid: bool = ...,
    ) -> Iterator[Optional[TJSONValue]]:
        ...  # pragma: no cover

    def iter(
        self,
        type: Optional[Type[Any]] = None,
        allow_none: bool = False,
        skip_empty: bool = False,
        skip_invalid: bool = False,
    ) -> Iterator[Optional[JSONValue]]:
        """
        Iterate over all lines.

        This is the iterator equivalent to repeatedly calling
        :py:meth:`~Reader.read()`. If no arguments are specified, this
        is the same as directly iterating over this :py:class:`Reader`
        instance.

        When `skip_invalid` is set to ``True``, invalid lines will be
        silently ignored.

        See :py:meth:`~Reader.read()` for a description of the other
        arguments.
        """
        try:
            while True:
                try:
                    yield self.read(
                        type=type, allow_none=allow_none, skip_empty=skip_empty
                    )
                except InvalidLineError:
                    if not skip_invalid:
                        raise
        except EOFError:
            pass

    def __iter__(self) -> Iterator[Any]:
        """
        See :py:meth:`~Reader.iter()`.
        """
        return self.iter()

    def _repr_for_wrapped(self) -> str:
        if self._fp is not None:
            return repr_for_fp(self._fp)
        class_name = type(self._file_or_iterable).__name__
        return f"<{class_name} at 0x{id(self._file_or_iterable):x}>"


class IndexedRecord(NamedTuple):
    """
    A single valid record together with its position in the source file.

    Instances are returned by :py:class:`IndexedReader`.

    :param record: zero-based record number, counting only valid records
    :param value: decoded JSON value
    :param lineno: one-based physical line number in the file
    :param offset: byte offset of the start of the line in the file
    """

    record: int
    value: JSONValue
    lineno: int
    offset: int


@attr.s(auto_attribs=True, repr=False)
class IndexedReader(ReaderWriterBase):
    """
    Random-access reader that builds a record index for a seekable file.

    Unlike :py:class:`Reader`, which sequentially reads any iterable
    (including pipes), this reader requires a regular file that can be
    opened by path. It scans the file once to build an index mapping
    valid record numbers to their physical line number and byte offset,
    after which individual records can be fetched in arbitrary order.

    Only lines that successfully decode into a JSON value are indexed.
    Empty lines and malformed lines do not get a record number, but they
    do make the physical line number diverge from the record number.

    The index is bound to both the file and the read options used to
    build it. Before each access the file's size, inode and modification
    time are compared against the values recorded while indexing; if the
    file was replaced, truncated, or otherwise changed,
    :py:exc:`StaleIndexError` is raised. Call :py:meth:`rebuild` to
    rescan the file and obtain a fresh index.

    Instances can be used as a context manager.

    :param file: name or ‘path-like object’ of the file to index
    :param loads: custom json decoder callable
    :param skip_empty: whether empty or whitespace-only lines are skipped
    """

    _file: Openable
    _loads: LoadsCallable = attr.ib(default=default_loads, kw_only=True)
    _skip_empty: bool = attr.ib(default=False, kw_only=True)
    _entries: List[Tuple[int, int]] = attr.ib(factory=list, init=False)
    _identity: Tuple[int, int, int, int] = attr.ib(
        default=(0, 0, 0, 0), init=False
    )
    _built: bool = attr.ib(default=False, init=False)

    def __attrs_post_init__(self) -> None:
        self.rebuild()

    def _open_fp(self) -> typing.IO[bytes]:
        return builtins.open(self._file, mode="rb")

    def _check_valid(self) -> None:
        if self._closed:
            raise RuntimeError("reader is closed")
        if not self._built:
            raise StaleIndexError("record index has not been built")
        try:
            stat = os.stat(self._file)
        except OSError as orig_exc:
            raise StaleIndexError(
                f"indexed file is no longer available: {orig_exc}"
            ) from orig_exc
        identity = (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
        )
        if identity != self._identity:
            raise StaleIndexError(
                "indexed file changed since the index was built; "
                "call rebuild()"
            )

    def rebuild(self) -> None:
        """
        (Re)scan the file and rebuild the record index from scratch.
        """
        entries: List[Tuple[int, int]] = []
        with self._open_fp() as fp:
            offset = 0
            for lineno, raw_line in enumerate(fp, 1):
                line_offset = offset
                offset += len(raw_line)

                if self._skip_empty and not raw_line.strip():
                    continue

                raw = strip_skippable_initial_bytes(raw_line)
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue

                try:
                    self._loads(line)
                except ValueError:
                    continue

                entries.append((lineno, line_offset))

        stat = os.stat(self._file)
        self._entries = entries
        self._identity = (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
        )
        self._built = True
        self._closed = False

    def __len__(self) -> int:
        """
        Number of indexed valid records.
        """
        self._check_valid()
        return len(self._entries)

    def _read_entry_at(
        self, fp: typing.IO[bytes], index: int
    ) -> IndexedRecord:
        lineno, offset = self._entries[index]
        fp.seek(offset)
        raw_line = fp.readline()

        line = raw_line.rstrip(b"\r\n")
        line = strip_skippable_initial_bytes(line)
        value: JSONValue = self._loads(line.decode("utf-8"))
        return IndexedRecord(
            record=index, value=value, lineno=lineno, offset=offset
        )

    def _read_entry(self, index: int) -> IndexedRecord:
        with self._open_fp() as fp:
            return self._read_entry_at(fp, index)

    def get(self, record: int) -> IndexedRecord:
        """
        Fetch a single valid record by its zero-based record number.

        :param record: zero-based valid record number
        :raises StaleIndexError: if the indexed file changed
        :raises IndexError: if the record number is out of range
        """
        self._check_valid()
        count = len(self._entries)
        index = record + count if record < 0 else record
        if not 0 <= index < count:
            raise IndexError(
                f"record number out of range: {record} "
                f"(index contains {count} records)"
            )
        return self._read_entry(index)

    def read_at(
        self,
        record: int,
        *,
        type: Optional[Type[Any]] = None,
        allow_none: bool = False,
    ) -> IndexedRecord:
        """
        Fetch a record, optionally validating its decoded value.

        See :py:meth:`Reader.read` for the meaning of the `type` and
        `allow_none` arguments.

        :param record: zero-based valid record number
        :raises StaleIndexError: if the indexed file changed
        :raises IndexError: if the record number is out of range
        :raises InvalidLineError: if the value does not satisfy the
            requested type or null handling
        """
        if type is not None and type not in VALID_TYPES:
            raise ValueError("invalid type specified")

        indexed_record = self.get(record)
        value = indexed_record.value
        if value is None:
            if not allow_none:
                raise InvalidLineError(
                    "line contains null value",
                    repr(value),
                    indexed_record.lineno,
                )
        elif type is not None:
            valid = isinstance(value, type)
            if type is int and isinstance(value, bool):
                valid = False
            if not valid:
                raise InvalidLineError(
                    "line does not match requested type",
                    repr(value),
                    indexed_record.lineno,
                )
        return indexed_record

    def iter_all(self) -> Iterator[IndexedRecord]:
        """
        Yield every indexed record in record-number order.
        """
        self._check_valid()
        with self._open_fp() as fp:
            for index in range(len(self._entries)):
                yield self._read_entry_at(fp, index)

    def _repr_for_wrapped(self) -> str:
        return repr_for_fp_path(self._file)


@attr.s(auto_attribs=True, repr=False)
class Writer(ReaderWriterBase):
    """
    Writer for the jsonlines format.

    Instances can be used as a context manager.

    The `fp` argument must be a file-like object with a ``.write()``
    method accepting either text (unicode) or bytes.

    The `compact` argument can be used to to produce smaller output.

    The `sort_keys` argument can be used to sort keys in json objects,
    and will produce deterministic output.

    For more control, provide a a custom encoder callable using the
    `dumps` argument. The callable must produce (unicode) string output.
    If specified, the `compact` and `sort` arguments will be ignored.

    When the `flush` argument is set to ``True``, the writer will call
    ``fp.flush()`` after each written line.

    :param fp: writable file-like object
    :param compact: whether to use a compact output format
    :param sort_keys: whether to sort object keys
    :param dumps: custom encoder callable
    :param flush: whether to flush the file-like object after writing each line
    """

    _fp: Union[typing.IO[str], typing.IO[bytes]] = attr.ib(default=None)
    _fp_is_binary: bool = attr.ib(default=False, init=False)
    _compact: bool = attr.ib(default=False, kw_only=True)
    _sort_keys: bool = attr.ib(default=False, kw_only=True)
    _flush: bool = attr.ib(default=False, kw_only=True)
    _dumps: DumpsCallable = attr.ib(default=default_dumps, kw_only=True)
    _dumps_result_conversion: DumpsResultConversion = attr.ib(
        default=DumpsResultConversion.LeaveAsIs, init=False
    )

    def __attrs_post_init__(self) -> None:
        if isinstance(self._fp, io.TextIOBase):
            self._fp_is_binary = False
        elif isinstance(self._fp, io.IOBase):
            self._fp_is_binary = True
        else:
            try:
                self._fp.write("")  # type: ignore[call-overload]
            except TypeError:
                self._fp_is_binary = True
            else:
                self._fp_is_binary = False

        if self._dumps is default_dumps:
            self._dumps = json.JSONEncoder(
                ensure_ascii=False,
                separators=(",", ":") if self._compact else (", ", ": "),
                sort_keys=self._sort_keys,
            ).encode

        # Detect if str-to-bytes conversion (or vice versa) is needed for the
        # combination of this file-like object and the used dumps() callable.
        # This avoids checking this for each .write(). Note that this
        # deliberately does not support ‘dynamic’ return types that depend on
        # input and dump options, like simplejson on Python 2 in some cases.
        sample_dumps_result = self._dumps({})
        if isinstance(sample_dumps_result, str) and self._fp_is_binary:
            self._dumps_result_conversion = DumpsResultConversion.EncodeToBytes
        elif isinstance(sample_dumps_result, bytes) and not self._fp_is_binary:
            self._dumps_result_conversion = DumpsResultConversion.DecodeToString

    def write(self, obj: Any) -> int:
        """
        Encode and write a single object.

        :param obj: the object to encode and write
        :return: number of characters or bytes written
        """
        if self._closed:
            raise RuntimeError("writer is closed")

        line = self._dumps(obj)

        # This handles either str or bytes, but the type checker does not know
        # that this code always passes the right type of arguments.
        if self._dumps_result_conversion == DumpsResultConversion.EncodeToBytes:
            line = line.encode()  # type: ignore[union-attr]
        elif self._dumps_result_conversion == DumpsResultConversion.DecodeToString:
            line = line.decode()  # type: ignore[union-attr]

        fp = self._fp
        fp.write(line)  # type: ignore[arg-type]
        fp.write(b"\n" if self._fp_is_binary else "\n")  # type: ignore[call-overload]

        if self._flush:
            fp.flush()

        return len(line) + 1  # including newline

    def write_all(self, iterable: Iterable[Any]) -> int:
        """
        Encode and write multiple objects.

        :param iterable: an iterable of objects
        :return: number of characters or bytes written
        """
        return sum(self.write(obj) for obj in iterable)

    def _repr_for_wrapped(self) -> str:
        return repr_for_fp(self._fp)


@overload
def open(
    file: Openable,
    mode: Literal["r"] = ...,
    *,
    loads: Optional[LoadsCallable] = ...,
) -> Reader:
    ...  # pragma: no cover


@overload
def open(
    file: Openable,
    mode: Literal["w", "a", "x"],
    *,
    dumps: Optional[DumpsCallable] = ...,
    compact: Optional[bool] = ...,
    sort_keys: Optional[bool] = ...,
    flush: Optional[bool] = ...,
) -> Writer:
    ...  # pragma: no cover


@overload
def open(
    file: Openable,
    mode: str = ...,
    *,
    loads: Optional[LoadsCallable] = ...,
    dumps: Optional[DumpsCallable] = ...,
    compact: Optional[bool] = ...,
    sort_keys: Optional[bool] = ...,
    flush: Optional[bool] = ...,
) -> Union[Reader, Writer]:
    ...  # pragma: no cover


def open(
    file: Openable,
    mode: str = "r",
    *,
    loads: Optional[LoadsCallable] = None,
    dumps: Optional[DumpsCallable] = None,
    compact: Optional[bool] = None,
    sort_keys: Optional[bool] = None,
    flush: Optional[bool] = None,
) -> Union[Reader, Writer]:
    """
    Open a jsonlines file for reading or writing.

    This is a convenience function to open a file and wrap it in either a
    :py:class:`Reader` or :py:class:`Writer` instance, depending on the
    specified `mode`.

    Additional keyword arguments will be passed on to the reader and writer;
    see their documentation for available options.

    The resulting reader or writer must be closed after use by the
    caller, which will also close the opened file.  This can be done by
    calling ``.close()``, but the easiest way to ensure proper resource
    finalisation is to use a ``with`` block (context manager), e.g.

    ::

        with jsonlines.open('out.jsonl', mode='w') as writer:
            writer.write(...)

    :param file: name or ‘path-like object’ of the file to open
    :param mode: whether to open the file for reading (``r``),
        writing (``w``), appending (``a``), or exclusive creation (``x``).
    """
    if mode not in {"r", "w", "a", "x"}:
        raise ValueError("'mode' must be either 'r', 'w', 'a', or 'x'")

    cls = Reader if mode == "r" else Writer
    encoding = "utf-8-sig" if mode == "r" else "utf-8"
    fp = builtins.open(file, mode=mode + "t", encoding=encoding)
    kwargs = dict(
        loads=loads,
        dumps=dumps,
        compact=compact,
        sort_keys=sort_keys,
        flush=flush,
    )
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    instance: Union[Reader, Writer] = cls(fp, **kwargs)
    instance._should_close_fp = True
    return instance


def index(
    file: Openable,
    *,
    loads: Optional[LoadsCallable] = None,
    skip_empty: bool = False,
) -> IndexedReader:
    """
    Open a jsonlines file and build a record index for random access.

    This is a convenience wrapper around :py:class:`IndexedReader`. The
    resulting reader must be closed after use by the caller; using a
    ``with`` block is recommended.

    :param file: name or ‘path-like object’ of the file to index
    :param loads: custom json decoder callable
    :param skip_empty: whether empty or whitespace-only lines are skipped
    """
    if isinstance(file, int):
        raise ValueError("an indexed reader requires a file path, not a fd")
    kwargs: Dict[str, Any] = {}
    if loads is not None:
        kwargs["loads"] = loads
    if skip_empty:
        kwargs["skip_empty"] = skip_empty
    return IndexedReader(file, **kwargs)


def repr_for_fp(fp: typing.IO[Any]) -> str:
    """
    Helper to make a useful repr() for a file-like object.
    """
    name = getattr(fp, "name", None)
    if name is not None:
        return repr(name)
    else:
        return repr(fp)


def repr_for_fp_path(file: Openable) -> str:
    """
    Helper to make a useful repr() for a file path.
    """
    if isinstance(file, int):
        return repr(file)
    return repr(os.fspath(file))
