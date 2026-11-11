"""
Tests for the jsonlines library.
"""

import codecs
import collections
import io
import json
import os
import pathlib
import tempfile

import jsonlines
import pytest

from typing import List


SAMPLE_BYTES = b'{"a": 1}\n{"b": 2}\n'
SAMPLE_TEXT = SAMPLE_BYTES.decode("utf-8")


def is_json_decode_error(exc: object) -> bool:
    if type(exc).__module__ == "ujson":
        # The ujson package has its own ujson.JSONDecodeError; because of the
        # line above this function also works if it's not installed.
        import ujson

        return isinstance(exc, ujson.JSONDecodeError)
    else:
        # Otherwise, this should be a stdlib json.JSONDecodeError, which also
        # works for orjson since orjson.JSONDecodeError inherits from it.
        return isinstance(exc, json.JSONDecodeError)


def test_reader() -> None:
    fp = io.BytesIO(SAMPLE_BYTES)
    with jsonlines.Reader(fp) as reader:
        it = iter(reader)
        assert next(it) == {"a": 1}
        assert next(it) == {"b": 2}
        with pytest.raises(StopIteration):
            next(it)
        with pytest.raises(EOFError):
            reader.read()


def test_reading_from_iterable() -> None:
    with jsonlines.Reader(["1", b"{}"]) as reader:
        assert list(reader) == [1, {}]
    assert "wrapping <list at " in repr(reader)


def test_reader_rfc7464_text_sequences() -> None:
    fp = io.BytesIO(b'\x1e"a"\x0a\x1e"b"\x0a')
    with jsonlines.Reader(fp) as reader:
        assert list(reader) == ["a", "b"]


def test_reader_utf8_bom_bytes() -> None:
    """
    UTF-8 BOM is ignored, even if it occurs in the middle of a stream.
    """
    chunks = [
        codecs.BOM_UTF8,
        b"1\n",
        codecs.BOM_UTF8,
        b"2\n",
    ]
    fp = io.BytesIO(b"".join(chunks))
    with jsonlines.Reader(fp) as reader:
        assert list(reader) == [1, 2]


def test_reader_utf8_bom_text() -> None:
    """
    Text version of ``test_reader_utf8_bom_bytes()``.
    """
    chunks = [
        "1\n",
        codecs.BOM_UTF8.decode(),
        "2\n",
    ]
    fp = io.StringIO("".join(chunks))
    with jsonlines.Reader(fp) as reader:
        assert list(reader) == [1, 2]


def test_reader_utf8_bom_bom_bom() -> None:
    """
    Too many UTF-8 BOM BOM BOM chars cause BOOM 💥 BOOM.
    """
    reader = jsonlines.Reader([codecs.BOM_UTF8.decode() * 3 + "1\n"])
    with pytest.raises(jsonlines.InvalidLineError) as excinfo:
        reader.read()

    exc = excinfo.value
    assert "invalid json" in str(exc)
    assert is_json_decode_error(exc.__cause__)


def test_writer_text() -> None:
    fp = io.StringIO()
    with jsonlines.Writer(fp) as writer:
        writer.write({"a": 1})
        writer.write({"b": 2})
    assert fp.getvalue() == SAMPLE_TEXT


def test_writer_binary() -> None:
    fp = io.BytesIO()
    with jsonlines.Writer(fp) as writer:
        writer.write_all(
            [
                {"a": 1},
                {"b": 2},
            ]
        )
    assert fp.getvalue() == SAMPLE_BYTES


def test_closing() -> None:
    reader = jsonlines.Reader([])
    reader.close()
    with pytest.raises(RuntimeError):
        reader.read()
    writer = jsonlines.Writer(io.BytesIO())
    writer.close()
    writer.close()  # no-op
    with pytest.raises(RuntimeError):
        writer.write(123)


def test_invalid_lines() -> None:
    data = "[1, 2"
    with jsonlines.Reader(io.StringIO(data)) as reader:
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            reader.read()
        exc = excinfo.value
        assert "invalid json" in str(exc)
        assert exc.line == data
        assert is_json_decode_error(exc.__cause__)


def test_skip_invalid() -> None:
    fp = io.StringIO("12\ninvalid\n34")
    reader = jsonlines.Reader(fp)
    it = reader.iter(skip_invalid=True)
    assert next(it) == 12
    assert next(it) == 34


def test_empty_strings_in_iterable() -> None:
    input = ["123", "", "456"]
    it = iter(jsonlines.Reader(input))
    assert next(it) == 123
    with pytest.raises(jsonlines.InvalidLineError):
        next(it)
    with pytest.raises(StopIteration):
        next(it)
    it = jsonlines.Reader(input).iter(skip_empty=True)
    assert list(it) == [123, 456]


def test_invalid_utf8() -> None:
    with jsonlines.Reader([b"\xff\xff"]) as reader:
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            reader.read()
        assert "line is not valid utf-8" in str(excinfo.value)


def test_empty_lines() -> None:
    data_with_empty_line = b"1\n\n2\n"
    with jsonlines.Reader(io.BytesIO(data_with_empty_line)) as reader:
        assert reader.read()
        with pytest.raises(jsonlines.InvalidLineError):
            reader.read()
        assert reader.read() == 2
        with pytest.raises(EOFError):
            reader.read()
    with jsonlines.Reader(io.BytesIO(data_with_empty_line)) as reader:
        assert list(reader.iter(skip_empty=True)) == [1, 2]


def test_typed_reads() -> None:
    with jsonlines.Reader(io.StringIO('12\ntrue\n"foo"\n')) as reader:
        assert reader.read(type=int) == 12

        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            reader.read(type=int)
        exc = excinfo.value
        assert "does not match requested type" in str(exc)
        assert exc.line == "true"

        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            reader.read(type=float)
        exc = excinfo.value
        assert "does not match requested type" in str(exc)
        assert exc.line == '"foo"'


def test_typed_read_invalid_type() -> None:
    reader = jsonlines.Reader([])
    with pytest.raises(ValueError) as excinfo:
        reader.read(type="nope")  # type: ignore[call-overload]
    exc = excinfo.value
    assert str(exc) == "invalid type specified"


def test_typed_iteration() -> None:
    fp = io.StringIO("1\n2\n")
    with jsonlines.Reader(fp) as reader:
        actual = list(reader.iter(type=int))
        assert actual == [1, 2]

    fp = io.StringIO("1\n2\n")
    with jsonlines.Reader(fp) as reader:
        it = reader.iter(type=str)
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            next(it)
        exc = excinfo.value
        assert "does not match requested type" in str(exc)


def test_writer_flags() -> None:
    fp = io.BytesIO()
    with jsonlines.Writer(fp, compact=True, sort_keys=True) as writer:
        writer.write(
            collections.OrderedDict(
                [
                    ("b", 2),
                    ("a", 1),
                ]
            )
        )
    assert fp.getvalue() == b'{"a":1,"b":2}\n'


def test_custom_dumps() -> None:
    fp = io.BytesIO()
    writer = jsonlines.Writer(fp, dumps=lambda obj: "oh hai")
    with writer:
        nbytes = writer.write({})
        assert nbytes == len(b"oh hai\n")

    assert fp.getvalue() == b"oh hai\n"


def test_custom_dumps_bytes() -> None:
    """
    A custom dump function that returns bytes (e.g. ‘orjson’) should work.
    """

    fp = io.BytesIO()
    writer = jsonlines.Writer(fp, dumps=lambda obj: b"some bytes")
    with writer:
        writer.write(123)

    assert fp.getvalue() == b"some bytes\n"


def test_custom_loads() -> None:
    fp = io.BytesIO(b"{}\n")
    with jsonlines.Reader(fp, loads=lambda s: "uh what") as reader:
        assert reader.read() == "uh what"


def test_open_reading() -> None:
    with tempfile.NamedTemporaryFile("wb") as fp:
        fp.write(b"123\n")
        fp.flush()
        with jsonlines.open(fp.name) as reader:
            assert list(reader) == [123]


def test_open_reading_with_utf8_bom() -> None:
    """
    The ``.open()`` helper ignores a UTF-8 BOM.
    """
    with tempfile.NamedTemporaryFile("wb") as fp:
        fp.write(codecs.BOM_UTF8)
        fp.write(b"123\n")
        fp.flush()
        with jsonlines.open(fp.name) as reader:
            assert list(reader) == [123]


def test_open_writing() -> None:
    with tempfile.NamedTemporaryFile("w+b") as fp:
        with jsonlines.open(fp.name, mode="w") as writer:
            writer.write(123)
        assert fp.read() == b"123\n"
    assert fp.name in repr(writer)


def test_open_and_append_writing() -> None:
    with tempfile.NamedTemporaryFile("w+b") as fp:
        with jsonlines.open(fp.name, mode="w") as writer:
            nbytes = writer.write(123)
            assert nbytes == len(str(123)) + 1
        with jsonlines.open(fp.name, mode="a") as writer:
            nbytes = writer.write(456)
            assert nbytes == len(str(456)) + 1
        assert fp.read() == b"123\n456\n"


def test_open_invalid_mode() -> None:
    with pytest.raises(ValueError) as excinfo:
        jsonlines.open("foo", mode="foo")
    assert "mode" in str(excinfo.value)


def test_single_char_stripping() -> None:
    """ "
    Sanity check that a helper constant actually contains single-char strings.
    """
    assert all(len(s) == 1 for s in jsonlines.jsonlines.SKIPPABLE_SINGLE_INITIAL_CHARS)


def test_write_shards_splits_on_byte_limit(tmp_path: pathlib.Path) -> None:
    # Each record encodes to '{"n": 0}' etc: 8 bytes + newline = 9 bytes.
    records = [{"n": i} for i in range(10)]
    pattern = str(tmp_path / "part-{shard}.jsonl")
    manifest = jsonlines.write_shards(records, pattern, max_bytes=27)

    assert [(m.first_index, m.last_index) for m in manifest] == [
        (0, 2),
        (3, 5),
        (6, 8),
        (9, 9),
    ]
    assert [m.record_count for m in manifest] == [3, 3, 3, 1]
    assert [m.shard for m in manifest] == [0, 1, 2, 3]
    for info in manifest:
        assert info.size_bytes == os.path.getsize(info.path)
        assert info.size_bytes <= 27
        with jsonlines.open(info.path) as reader:
            assert list(reader) == records[info.first_index : info.last_index + 1]


def test_write_shards_exact_fit(tmp_path: pathlib.Path) -> None:
    # A record that exactly fills the shard stays in it; the next one
    # starts a new shard. '"ab"' is 4 bytes + newline = 5 bytes.
    manifest = jsonlines.write_shards(
        ["ab", "cd"], str(tmp_path / "s-{shard}.jsonl"), max_bytes=5
    )
    assert [(m.first_index, m.last_index, m.size_bytes) for m in manifest] == [
        (0, 0, 5),
        (1, 1, 5),
    ]


def test_write_shards_measures_encoded_bytes(tmp_path: pathlib.Path) -> None:
    # Sizes are measured on the encoded bytes, not the text: 'é' takes
    # two bytes in UTF-8, so '"é"' plus newline is 5 bytes.
    manifest = jsonlines.write_shards(
        ["é", "é"], str(tmp_path / "s-{shard}.jsonl"), max_bytes=5
    )
    assert [m.size_bytes for m in manifest] == [5, 5]
    with jsonlines.open(manifest[0].path) as reader:
        assert list(reader) == ["é"]


def test_write_shards_custom_dumps_bytes(tmp_path: pathlib.Path) -> None:
    # A custom dumps callable returning bytes (e.g. orjson) is measured
    # as-is, with the newline counted on top.
    manifest = jsonlines.write_shards(
        [{"a": 1}, {"a": 2}],
        str(tmp_path / "s-{shard}.jsonl"),
        max_bytes=9,
        dumps=lambda obj: json.dumps(obj).encode("utf-8"),
    )
    assert [m.size_bytes for m in manifest] == [9, 9]
    with jsonlines.open(manifest[0].path) as reader:
        assert list(reader) == [{"a": 1}]


def test_write_shards_oversized_reject(tmp_path: pathlib.Path) -> None:
    records = ["ab", "cd", "ef", "x" * 100]
    pattern = str(tmp_path / "s-{shard}.jsonl")
    with pytest.raises(jsonlines.OversizedRecordError) as excinfo:
        jsonlines.write_shards(records, pattern, max_bytes=10)
    exc = excinfo.value
    assert exc.index == 3
    assert exc.size == 103  # 100 chars + 2 quotes + newline
    assert exc.max_bytes == 10
    # The shard completed before the oversized record is deliverable.
    with jsonlines.open(str(tmp_path / "s-0.jsonl")) as reader:
        assert list(reader) == ["ab", "cd"]
    # The incomplete shard was cleaned up.
    assert not (tmp_path / "s-1.jsonl").exists()
    assert not (tmp_path / "s-1.jsonl.part").exists()


def test_write_shards_oversized_separate(tmp_path: pathlib.Path) -> None:
    records = ["ab", "x" * 100, "cd"]
    manifest = jsonlines.write_shards(
        records,
        str(tmp_path / "s-{shard}.jsonl"),
        max_bytes=10,
        on_oversized="separate",
    )
    assert [(m.first_index, m.last_index) for m in manifest] == [(0, 0), (1, 1), (2, 2)]
    assert manifest[1].size_bytes == 103  # exceeds the limit, as documented
    for info in manifest:
        with jsonlines.open(info.path) as reader:
            assert list(reader) == [records[info.first_index]]


def test_write_shards_failed_shard_keeps_completed(tmp_path: pathlib.Path) -> None:
    records = [{"n": i} for i in range(10)]
    completed: List[jsonlines.ShardInfo] = []

    def make_path(shard: int) -> str:
        if shard == 1:
            return str(tmp_path / "nonexistent-dir" / "s-1.jsonl")
        return str(tmp_path / f"s-{shard}.jsonl")

    with pytest.raises(OSError):
        jsonlines.write_shards(
            records,
            make_path,
            max_bytes=27,
            on_shard_complete=completed.append,
        )
    # The first shard was completed before the failure and is deliverable.
    assert len(completed) == 1
    assert completed[0].first_index == 0
    assert completed[0].last_index == 2
    with jsonlines.open(completed[0].path) as reader:
        assert list(reader) == [{"n": 0}, {"n": 1}, {"n": 2}]


def test_write_shards_empty_input(tmp_path: pathlib.Path) -> None:
    manifest = jsonlines.write_shards(
        [], str(tmp_path / "s-{shard}.jsonl"), max_bytes=10
    )
    assert manifest == []
    assert list(tmp_path.iterdir()) == []


def test_write_shards_path_without_placeholder(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError) as excinfo:
        jsonlines.write_shards([1], str(tmp_path / "out.jsonl"), max_bytes=10)
    assert "{shard}" in str(excinfo.value)


def test_write_shards_path_format_spec(tmp_path: pathlib.Path) -> None:
    manifest = jsonlines.write_shards(
        [1, 2], str(tmp_path / "part-{shard:03d}.jsonl"), max_bytes=2
    )
    assert [os.path.basename(m.path) for m in manifest] == [
        "part-000.jsonl",
        "part-001.jsonl",
    ]
