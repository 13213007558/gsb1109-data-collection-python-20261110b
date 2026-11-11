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


@attr.s(frozen=True, auto_attribs=True)
class FileFingerprint:
    """
    Immutable identity description of the file a :py:class:`ReadProgress`
    belongs to.

    The resolved file path together with the device and inode numbers
    uniquely identify a file, so that a progress token can never be
    applied to a different file that happens to have the same name.
    The recorded size is informational only and not part of the file
    identity, which allows resuming files that were appended to.
    """

    #: Resolved file path (``str`` or ``bytes``, depending on how the
    #: file was opened)
    path: Union[str, bytes]

    #: Device on which the file resides (``st_dev``)
    device: int

    #: Inode number of the file (``st_ino``)
    inode: int

    #: File size in bytes when the fingerprint was taken
    size: int


def _same_file(left: FileFingerprint, right: FileFingerprint) -> bool:
    """
    Return whether two fingerprints describe the same file.

    The file size is deliberately ignored, since appending to a file
    changes its size without changing its identity.
    """
    return (
        left.path == right.path
        and left.device == right.device
        and left.inode == right.inode
    )


@attr.s(frozen=True, auto_attribs=True)
class ReadProgress:
    """
    Immutable read progress token.

    A token describes the input span that has been confirmed by the
    caller: every physical line that ends at or before ``offset`` has
    been delivered, and ``lineno`` is the number of physical lines that
    ``offset`` corresponds to. Reopening the same file with this token
    makes the reader resume at ``offset``, meaning any records that were
    delivered but not confirmed will be delivered again.

    Tokens are bound to a specific file through their
    :py:class:`FileFingerprint`; passing a token for another file results
    in :py:class:`ReadProgressMismatchError`.
    """

    #: Byte offset to resume reading from
    offset: int

    #: Number of physical lines fully consumed up to ``offset``
    lineno: int

    #: Identity of the file this progress belongs to
    fingerprint: FileFingerprint


@attr.s(auto_exc=True, auto_attribs=True, init=False)
class ReadProgressMismatchError(Error):
    """
    Error raised when a :py:class:`ReadProgress` does not belong to the
    file being opened.

    The supplied token is available as the ``.progress`` attribute, and
    the fingerprint of the opened file as ``.fingerprint``.
    """

    #: The progress token that did not match
    progress: ReadProgress

    #: The fingerprint of the file that was opened instead
    fingerprint: FileFingerprint

    def __init__(
        self, progress: ReadProgress, fingerprint: FileFingerprint
    ) -> None:
        self.progress = progress
        self.fingerprint = fingerprint
        if progress.fingerprint.path != fingerprint.path:
            message = (
                "read progress belongs to "
                f"{progress.fingerprint.path!r}, but {fingerprint.path!r} was opened"
            )
        else:
            message = (
                f"read progress belongs to a different file at "
                f"{fingerprint.path!r} (device/inode no longer matches)"
            )
        super().__init__(message)


@attr.s(auto_attribs=True)
class _ReadProgressTracker:
    """
    Internal helper that tracks confirmed reading progress using byte
    offsets on a seekable binary stream.

    Reading and confirmation are separate steps. Every consumed
    physical line extends the delivered span, while the confirmed span
    only advances when the caller explicitly acknowledges something.
    Both spans are prefixes of the file, described by a byte offset and
    the corresponding physical line number.
    """

    _buffer: typing.BinaryIO
    _fingerprint: FileFingerprint
    _lineno: int
    _line_end_offset: int
    _confirmed_offset: int
    _confirmed_lineno: int
    #: Delivered but not yet acknowledged lines as
    #: ``(lineno, end_offset)`` pairs, in ascending order
    _pending: List[Tuple[int, int]] = attr.ib(factory=list, init=False)

    @classmethod
    def start(
        cls,
        buffer: typing.BinaryIO,
        fingerprint: FileFingerprint,
        progress: ReadProgress,
    ) -> "_ReadProgressTracker":
        offset = progress.offset
        buffer.seek(offset)
        if offset > 0:
            # When the confirmed offset is not positioned directly after
            # a newline, it points at the end of a line without a line
            # terminator. If more data was appended in the meantime,
            # skip the remainder of that already delivered line.
            buffer.seek(offset - 1)
            previous_byte = buffer.read(1)
            buffer.seek(offset)
            if previous_byte != b"\n":
                buffer.readline()
        return cls(
            buffer=buffer,
            fingerprint=fingerprint,
            lineno=progress.lineno,
            line_end_offset=offset,
            confirmed_offset=offset,
            confirmed_lineno=progress.lineno,
        )

    def read_line(self, *, skip_empty: bool) -> Optional[Tuple[int, bytes]]:
        while True:
            line = self._buffer.readline()
            if not line:
                return None
            self._lineno += 1
            self._line_end_offset = self._buffer.tell()
            if skip_empty and not line.rstrip():
                # Empty lines are never delivered to the caller. They
                # are simply skipped here; on resume they may be read
                # (and skipped) again, which is harmless.
                continue
            return self._lineno, line

    @property
    def lineno(self) -> int:
        """
        Line number of the most recently read physical line.
        """
        return self._lineno

    def record_delivered(self) -> None:
        """
        Record the most recently read line as a delivered record that
        still needs explicit acknowledgement.
        """
        self._pending.append((self._lineno, self._line_end_offset))

    def acknowledge(self, lineno: Optional[int] = None) -> ReadProgress:
        """
        Acknowledge delivered records, either all of them or through the
        given physical line number.
        """
        if lineno is None:
            if self._pending:
                self._confirmed_lineno, self._confirmed_offset = self._pending[-1]
                self._pending.clear()
        elif lineno <= self._confirmed_lineno:
            pass
        else:
            self._require_pending_lineno(lineno)
            self._confirm_through(lineno)
        return self.checkpoint()

    def acknowledge_skipped(self, lineno: int) -> ReadProgress:
        """
        Acknowledge a line that was consumed but not delivered (e.g. an
        invalid line) by moving the confirmed span past it.
        """
        if lineno <= self._confirmed_lineno:
            return self.checkpoint()
        if lineno != self._lineno:
            raise ValueError(
                f"cannot acknowledge line {lineno}; the most recently "
                f"read line is {self._lineno}"
            )
        # Confirming a skipped line necessarily confirms every line
        # before it, since progress is always a prefix of the input.
        self._pending.append((lineno, self._line_end_offset))
        self._confirm_through(lineno)
        return self.checkpoint()

    def _require_pending_lineno(self, lineno: int) -> None:
        if not any(pending_lineno == lineno for pending_lineno, _ in self._pending):
            raise ValueError(
                f"line {lineno} has not been delivered and cannot be "
                "acknowledged"
            )

    def _confirm_through(self, lineno: int) -> None:
        remaining: List[Tuple[int, int]] = []
        for pending_lineno, end_offset in self._pending:
            if pending_lineno <= lineno:
                self._confirmed_lineno = pending_lineno
                self._confirmed_offset = end_offset
            else:
                remaining.append((pending_lineno, end_offset))
        self._pending = remaining

    def checkpoint(self) -> ReadProgress:
        return ReadProgress(
            offset=self._confirmed_offset,
            lineno=self._confirmed_lineno,
            fingerprint=self._fingerprint,
        )


def _binary_progress_buffer(
    fp: Optional[typing.IO[Any]],
) -> Optional[typing.BinaryIO]:
    """
    Return a seekable binary stream suitable for offset tracking, or
    ``None`` when the given file-like object does not support this.
    """
    if isinstance(fp, io.TextIOBase):
        buffer = getattr(fp, "buffer", None)
    elif isinstance(fp, io.IOBase):
        buffer = fp
    else:
        return None
    if isinstance(buffer, (io.BufferedIOBase, io.RawIOBase)) and buffer.seekable():
        return buffer
    return None


def _make_file_fingerprint(buffer: typing.BinaryIO) -> FileFingerprint:
    name = getattr(buffer, "name", None)
    if not isinstance(name, (str, bytes, os.PathLike)):
        raise ValueError(
            "read progress tracking requires a file opened from a file name"
        )
    stat_result = os.stat(name)
    path: Union[str, bytes] = os.path.realpath(name)
    return FileFingerprint(
        path=path,
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
    )


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

    Read progress tracking is available for readers wrapping a
    seekable file opened from a file name. Reading and confirmation
    are separate steps:

    - :py:meth:`read()` merely *delivers* records;
    - :py:meth:`acknowledge()` marks delivered records as durably
      processed (e.g. after a database transaction has been
      committed);
    - :py:meth:`checkpoint()` returns an immutable
      :py:class:`ReadProgress` token for the acknowledged input span.

    Pass a previous token as the `progress` argument when reopening
    the same file to resume after the last acknowledged record.
    Records delivered but not acknowledged are delivered again on
    reopen. The token is bound to a specific file (path, device and
    inode) and cannot be used with a different file, even if it has
    the same name. Files that were only appended to can be resumed
    without issues.

    :param file_or_iterable: file-like object or iterable yielding lines as
        strings
    :param loads: custom json decoder callable
    :param progress: previously obtained :py:class:`ReadProgress` to
        resume from; the file must be the same one the token was
        created for
    """

    _file_or_iterable: Union[
        typing.IO[str], typing.IO[bytes], Iterable[Union[str, bytes]]
    ]
    _line_iter: Iterator[Tuple[int, Union[bytes, str]]] = attr.ib(init=False)
    _last_lineno: int = attr.ib(default=0, init=False)
    _loads: LoadsCallable = attr.ib(default=default_loads, kw_only=True)
    _progress: Optional[ReadProgress] = attr.ib(default=None, kw_only=True)
    _progress_tracker: Optional[_ReadProgressTracker] = attr.ib(
        default=None, init=False
    )

    def __attrs_post_init__(self) -> None:
        if isinstance(self._file_or_iterable, io.IOBase):
            self._fp = cast(
                Union[typing.IO[str], typing.IO[bytes]],
                self._file_or_iterable,
            )

        self._line_iter = enumerate(self._file_or_iterable, 1)

        buffer = _binary_progress_buffer(self._fp)
        buffer_name = getattr(buffer, "name", None) if buffer is not None else None
        if buffer is not None and isinstance(
            buffer_name, (str, bytes, os.PathLike)
        ):
            fingerprint = _make_file_fingerprint(buffer)
            if self._progress is not None:
                if not _same_file(self._progress.fingerprint, fingerprint):
                    raise ReadProgressMismatchError(self._progress, fingerprint)
            else:
                self._progress = ReadProgress(
                    offset=0, lineno=0, fingerprint=fingerprint
                )
            self._progress_tracker = _ReadProgressTracker.start(
                buffer=buffer,
                fingerprint=fingerprint,
                progress=self._progress,
            )
        elif self._progress is not None:
            raise ValueError(
                "resuming from read progress requires a seekable file "
                "opened from a file name"
            )

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

        tracker = self._progress_tracker
        try:
            if tracker is None:
                lineno, line = next(self._line_iter)
                while skip_empty and not line.rstrip():
                    lineno, line = next(self._line_iter)
            else:
                result = tracker.read_line(skip_empty=skip_empty)
                if result is None:
                    raise EOFError
                lineno, line = result
        except StopIteration:
            raise EOFError from None

        self._last_lineno = lineno

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
                if tracker is not None:
                    tracker.record_delivered()
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

        if tracker is not None:
            tracker.record_delivered()
        return value

    @property
    def lineno(self) -> int:
        """
        Physical line number of the most recently read line.

        This is the line number of the last line consumed by
        :py:meth:`read()`, including lines that failed to decode.
        """
        tracker = self._progress_tracker
        if tracker is not None:
            return tracker.lineno
        return self._last_lineno

    def checkpoint(self) -> "ReadProgress":
        """
        Return a :py:class:`ReadProgress` token for the confirmed input
        span.

        The token only covers records acknowledged via
        :py:meth:`acknowledge()` or
        :py:meth:`acknowledge_skipped_line()` (including invalid lines
        skipped by :py:meth:`iter()` with ``skip_invalid=True``).
        Records that have been delivered but not yet acknowledged are
        not included; reopening the file with this token causes them to
        be delivered again.

        Only available for readers wrapping a seekable file opened
        from a file name.
        """
        tracker = self._progress_tracker
        if tracker is None:
            raise RuntimeError(
                "read progress tracking is unavailable for this reader"
            )
        return tracker.checkpoint()

    def acknowledge(self, lineno: Optional[int] = None) -> "ReadProgress":
        """
        Acknowledge delivered records as durably processed.

        Call this after the records returned by :py:meth:`read()` have
        been committed elsewhere (e.g. inserted into a database and
        committed). Without an argument, every record delivered so far
        is acknowledged. With a `lineno` argument, only records up to
        and including that physical line are acknowledged; later
        delivered records remain unconfirmed.

        :param lineno: physical line number to acknowledge through
        :return: an updated :py:class:`ReadProgress` token
        """
        tracker = self._progress_tracker
        if tracker is None:
            raise RuntimeError(
                "read progress tracking is unavailable for this reader"
            )
        return tracker.acknowledge(lineno)

    def acknowledge_skipped_line(self, lineno: int) -> "ReadProgress":
        """
        Acknowledge a line that could not be decoded as processed.

        Call this after catching :py:class:`InvalidLineError`, passing
        the exception's :py:attr:`~InvalidLineError.lineno`, to include
        the offending input span in the confirmed progress. Without
        this, the line remains unconfirmed and will be encountered
        again when reading is resumed. This also confirms every record
        delivered before the skipped line.

        :param lineno: :py:attr:`InvalidLineError.lineno` of the
            skipped line
        :return: an updated :py:class:`ReadProgress` token
        """
        tracker = self._progress_tracker
        if tracker is None:
            raise RuntimeError(
                "read progress tracking is unavailable for this reader"
            )
        return tracker.acknowledge_skipped(lineno)

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
                except InvalidLineError as exc:
                    if not skip_invalid:
                        raise
                    tracker = self._progress_tracker
                    if tracker is not None:
                        tracker.acknowledge_skipped(exc.lineno)
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
    progress: Optional["ReadProgress"] = ...,
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
    progress: Optional["ReadProgress"] = ...,
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
    progress: Optional["ReadProgress"] = None,
) -> Union[Reader, Writer]:
    """
    Open a jsonlines file for reading or writing.

    This is a convenience function to open a file and wrap it in either a
    :py:class:`Reader` or :py:class:`Writer` instance, depending on the
    specified `mode`.

    When reading, a :py:class:`ReadProgress` token obtained from a
    previous run can be passed as `progress` to resume reading after
    the last confirmed record. Records that were delivered but not
    confirmed will be delivered again. The token is bound to a specific
    file and cannot be used with a different file, even if it has the
    same name.

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
    :param progress: read progress token to resume from (reading only)
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
        progress=progress,
    )
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    try:
        instance: Union[Reader, Writer] = cls(fp, **kwargs)
    except BaseException:
        fp.close()
        raise
    instance._should_close_fp = True
    return instance


def repr_for_fp(fp: typing.IO[Any]) -> str:
    """
    Helper to make a useful repr() for a file-like object.
    """
    name = getattr(fp, "name", None)
    if name is not None:
        return repr(name)
    else:
        return repr(fp)
