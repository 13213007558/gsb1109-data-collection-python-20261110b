"""
Round-trip tests between readers, writers, and (custom) JSON backends.

These tests pin down the responsibility boundaries between the
:py:class:`jsonlines.Writer`, the :py:class:`jsonlines.Reader`, and the
``dumps``/``loads`` callables plugged into them:

- The writer asks the ``dumps`` callable for a ``str`` or ``bytes``
  representation of each object, converts it to match the file-like
  object if needed, and terminates every record with a newline.
- The reader hands each line to the ``loads`` callable as a decoded
  (unicode) string, regardless of whether the underlying file yields
  bytes or text.
- The jsonlines format itself is backend-agnostic, so an archive
  written using one JSON backend must be readable using another.

Values that JSON cannot represent faithfully (tuples, non-string dict
keys, non-finite floats, ...) are covered separately in
:py:class:`TestBackendCapabilityDifferences`.
"""

import io
import json
import math
from typing import Any, Callable, Dict, List, Optional, Union

import pytest

import jsonlines

DumpsCallable = Callable[[Any], Union[str, bytes]]
LoadsCallable = Callable[[Union[str, bytes]], Any]


def bytes_dumps(obj: Any) -> bytes:
    """
    Custom ``dumps`` callable mimicking e.g. orjson: returns UTF-8 bytes.
    """
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def str_or_bytes_loads(data: Union[str, bytes]) -> Any:
    """
    Custom ``loads`` callable mimicking e.g. orjson: accepts str and bytes.
    """
    return json.loads(data)


RECORDS: List[Dict[str, Any]] = [
    {"name": "plain ascii", "value": 1},
    {"name": "unicode ☃ 中文 🐍", "value": 2.5},
    {"name": "nested", "value": {"flags": [True, False], "count": 0}},
]

UNICODE_STRINGS = [
    "héllo wörld",
    "中文测试",
    "🐍💥",
    "combining é",  # "e" + U+0301 combining acute accent
    "snowman ☃",
    '"quoted"',
    "control ",
    "line\nbreak",
    "tab\there",
]


def test_roundtrip_default_backend_text_fp() -> None:
    fp = io.StringIO()
    with jsonlines.Writer(fp) as writer:
        writer.write_all(RECORDS)
    with jsonlines.Reader(io.StringIO(fp.getvalue())) as reader:
        assert list(reader) == RECORDS


def test_roundtrip_default_backend_binary_fp() -> None:
    fp = io.BytesIO()
    with jsonlines.Writer(fp) as writer:
        writer.write_all(RECORDS)
    with jsonlines.Reader(io.BytesIO(fp.getvalue())) as reader:
        assert list(reader) == RECORDS


@pytest.mark.parametrize(
    "dumps,loads",
    [
        (None, None),
        (bytes_dumps, None),
        (None, str_or_bytes_loads),
        (bytes_dumps, str_or_bytes_loads),
    ],
    ids=[
        "stdlib-writer-stdlib-reader",
        "bytes-writer-stdlib-reader",
        "stdlib-writer-custom-reader",
        "custom-writer-custom-reader",
    ],
)
def test_roundtrip_across_backends(
    dumps: Optional[DumpsCallable],
    loads: Optional[LoadsCallable],
) -> None:
    """
    An archive written by backend A must be readable by backend B.
    """
    fp = io.BytesIO()
    writer_kwargs: Dict[str, Any] = {"dumps": dumps} if dumps else {}
    with jsonlines.Writer(fp, **writer_kwargs) as writer:
        writer.write_all(RECORDS)

    reader_kwargs: Dict[str, Any] = {"loads": loads} if loads else {}
    with jsonlines.Reader(io.BytesIO(fp.getvalue()), **reader_kwargs) as reader:
        assert list(reader) == RECORDS


def test_custom_bytes_dumps_on_text_fp() -> None:
    """
    A bytes-returning ``dumps`` (orjson-style) also works on a text file:
    the writer decodes the result before writing.
    """
    fp = io.StringIO()
    with jsonlines.Writer(fp, dumps=bytes_dumps) as writer:
        writer.write_all(RECORDS)
    with jsonlines.Reader(io.StringIO(fp.getvalue())) as reader:
        assert list(reader) == RECORDS


def test_custom_str_dumps_on_binary_fp() -> None:
    """
    A str-returning ``dumps`` (stdlib-style) also works on a binary file:
    the writer encodes the result before writing.
    """
    fp = io.BytesIO()
    with jsonlines.Writer(fp, dumps=json.dumps) as writer:
        writer.write_all(RECORDS)
    with jsonlines.Reader(io.BytesIO(fp.getvalue())) as reader:
        assert list(reader) == RECORDS


def test_roundtrip_unicode_strings() -> None:
    fp = io.BytesIO()
    with jsonlines.Writer(fp) as writer:
        writer.write_all(UNICODE_STRINGS)

    data = fp.getvalue()
    # The default writer emits literal UTF-8, not \uXXXX escape sequences.
    assert "中文".encode("utf-8") in data
    # Embedded newlines inside strings must be escaped, so that every
    # record still occupies exactly one line.
    assert len(data.splitlines()) == len(UNICODE_STRINGS)

    with jsonlines.Reader(io.BytesIO(data)) as reader:
        assert list(reader) == UNICODE_STRINGS


def test_reader_accepts_archive_without_trailing_newline() -> None:
    """
    Archives produced by other tools may not end the last line with a
    newline character; the reader must still yield the final record.
    """
    fp = io.BytesIO(b'{"a": 1}\n{"b": 2}')
    with jsonlines.Reader(fp) as reader:
        assert list(reader) == [{"a": 1}, {"b": 2}]

    fp = io.BytesIO(b'{"a": 1}')
    with jsonlines.Reader(fp) as reader:
        assert list(reader) == [{"a": 1}]

    fp = io.StringIO('{"a": 1}\n{"b": 2}')
    with jsonlines.Reader(fp) as reader:
        assert list(reader) == [{"a": 1}, {"b": 2}]


def test_writer_always_terminates_records_with_newline() -> None:
    """
    Archives produced by the writer itself always end with a newline, so
    appending more records later cannot corrupt the last written record.
    """
    fp = io.BytesIO()
    with jsonlines.Writer(fp) as writer:
        writer.write({"a": 1})
    assert fp.getvalue() == b'{"a": 1}\n'


def test_reader_decodes_bytes_lines_before_calling_loads() -> None:
    """
    The ``loads`` callable always receives a decoded (unicode) string,
    even when the underlying file yields bytes.
    """
    seen_types: List[type] = []

    def recording_loads(line: Union[str, bytes]) -> Any:
        seen_types.append(type(line))
        return json.loads(line)

    fp = io.BytesIO(b'{"a": 1}\n{"b": 2}\n')
    with jsonlines.Reader(fp, loads=recording_loads) as reader:
        assert list(reader) == [{"a": 1}, {"b": 2}]
    assert seen_types == [str, str]


def test_writer_passes_objects_through_to_dumps() -> None:
    """
    The writer hands each object to the ``dumps`` callable as-is.

    Note: the writer probes the ``dumps`` callable once with ``{}`` to
    detect whether it returns ``str`` or ``bytes``; the actual records
    follow after that probe.
    """
    seen: List[Any] = []

    def recording_dumps(obj: Any) -> str:
        seen.append(obj)
        return json.dumps(obj)

    records: List[Any] = [{"a": 1}, ["b", 2], "c"]
    fp = io.StringIO()
    with jsonlines.Writer(fp, dumps=recording_dumps) as writer:
        writer.write_all(records)
    assert seen[0] == {}  # return-type probe
    assert seen[1:] == records


class TestBackendCapabilityDifferences:
    """
    Values that cannot round-trip faithfully through JSON.

    These are not bugs in the reader or writer, but inherent differences
    between Python objects and what JSON (and specific JSON backends)
    can represent. Code exchanging archives with other parties should
    not rely on these values surviving a round-trip.
    """

    def test_tuples_come_back_as_lists(self) -> None:
        fp = io.BytesIO()
        with jsonlines.Writer(fp) as writer:
            writer.write({"point": (1, 2)})
        with jsonlines.Reader(io.BytesIO(fp.getvalue())) as reader:
            assert list(reader) == [{"point": [1, 2]}]

    def test_non_string_dict_keys_come_back_as_strings(self) -> None:
        fp = io.BytesIO()
        with jsonlines.Writer(fp) as writer:
            writer.write({1: "a"})
        assert fp.getvalue() == b'{"1": "a"}\n'
        with jsonlines.Reader(io.BytesIO(fp.getvalue())) as reader:
            assert list(reader) == [{"1": "a"}]

    def test_non_finite_floats_are_non_standard_json(self) -> None:
        """
        The stdlib backend emits ``NaN``/``Infinity``/``-Infinity``,
        which are not valid JSON. Stricter backends (e.g. orjson)
        refuse to serialize such values, so archives containing them
        are not portable across backends.
        """
        fp = io.BytesIO()
        with jsonlines.Writer(fp) as writer:
            writer.write_all([float("nan"), float("inf"), float("-inf")])
        assert fp.getvalue() == b"NaN\nInfinity\n-Infinity\n"
        with jsonlines.Reader(io.BytesIO(fp.getvalue())) as reader:
            values = list(reader)
        assert math.isnan(values[0])
        assert values[1:] == [float("inf"), float("-inf")]

    def test_null_records_need_allow_none(self) -> None:
        """
        ``null`` is valid JSON, but the reader rejects it by default;
        reading such archives back requires ``allow_none=True``.
        """
        fp = io.BytesIO()
        with jsonlines.Writer(fp) as writer:
            writer.write(None)
        assert fp.getvalue() == b"null\n"

        with jsonlines.Reader(io.BytesIO(fp.getvalue())) as reader:
            with pytest.raises(jsonlines.InvalidLineError):
                reader.read()

        with jsonlines.Reader(io.BytesIO(fp.getvalue())) as reader:
            assert list(reader.iter(allow_none=True)) == [None]
