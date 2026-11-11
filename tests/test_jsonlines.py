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

import attr
import jsonlines
import pytest


SAMPLE_BYTES = b'{"a": 1}\n{"b": 2}\n'
SAMPLE_TEXT = SAMPLE_BYTES.decode("utf-8")


def write_jsonl(name: str, lines: str = "") -> str:
    with open(name, "w") as fp:
        fp.write(lines)
    return name


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


def test_checkpoint_initially_zero(tmp_path: pathlib.Path) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n")
    with jsonlines.open(path) as reader:
        progress = reader.checkpoint()
    assert progress.offset == 0
    assert progress.lineno == 0
    assert progress.fingerprint.path == os.path.realpath(path)
    assert progress.fingerprint.inode == os.stat(path).st_ino


def test_checkpoint_unavailable_for_plain_iterable() -> None:
    reader = jsonlines.Reader(["1", "2"])
    with pytest.raises(RuntimeError):
        reader.checkpoint()
    with pytest.raises(RuntimeError):
        reader.acknowledge()


def test_progress_requires_seekable_named_file(tmp_path: pathlib.Path) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n")
    with jsonlines.open(path) as reader:
        progress = reader.checkpoint()
    with pytest.raises(ValueError):
        jsonlines.Reader(io.StringIO("1\n"), progress=progress)


def test_delivered_records_are_not_confirmed(tmp_path: pathlib.Path) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n3\n")
    with jsonlines.open(path) as reader:
        reader.read()
        reader.read()
        assert reader.checkpoint().offset == 0


def test_acknowledge_all_and_resume(tmp_path: pathlib.Path) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), '{"id": 1}\n{"id": 2}\n')
    with jsonlines.open(path) as reader:
        reader.read()
        reader.read()
        progress = reader.acknowledge()
    assert progress.lineno == 2
    with jsonlines.open(path, progress=progress) as reader:
        with pytest.raises(EOFError):
            reader.read()


def test_unacknowledged_records_are_redelivered(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(
        str(tmp_path / "a.jsonl"),
        '{"id": 1}\n{"id": 2}\n{"id": 3}\n{"id": 4}\n',
    )

    # Simulate a crash after reading all four records while only the
    # database transaction for the first two was committed.
    with jsonlines.open(path) as reader:
        for _ in range(4):
            reader.read()
        progress = reader.acknowledge(2)

    assert progress.lineno == 2
    with jsonlines.open(path, progress=progress) as reader:
        assert [obj["id"] for obj in reader] == [3, 4]


def test_acknowledge_by_lineno_keeps_later_records_pending(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n3\n4\n5\n")
    with jsonlines.open(path) as reader:
        reader.read()
        reader.read()
        reader.read()
        reader.acknowledge(2)
        reader.read()
        reader.read()
        progress = reader.checkpoint()
    assert progress.lineno == 2
    with jsonlines.open(path, progress=progress) as reader:
        assert list(reader) == [3, 4, 5]


def test_acknowledge_is_idempotent(tmp_path: pathlib.Path) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n")
    with jsonlines.open(path) as reader:
        reader.read()
        first = reader.acknowledge(1)
        second = reader.acknowledge(1)
        third = reader.acknowledge(0)
    assert first == second == third


def test_acknowledge_unknown_lineno_raises(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n")
    with jsonlines.open(path) as reader:
        reader.read()
        with pytest.raises(ValueError):
            reader.acknowledge(2)


def test_read_progress_token_is_frozen(tmp_path: pathlib.Path) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n")
    with jsonlines.open(path) as reader:
        progress = reader.checkpoint()
    with pytest.raises(attr.exceptions.FrozenInstanceError):
        progress.offset = 123  # type: ignore[misc]
    with pytest.raises(attr.exceptions.FrozenInstanceError):
        progress.fingerprint.inode = 1  # type: ignore[misc]


def test_resume_keeps_working_after_append(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n")
    with jsonlines.open(path) as reader:
        reader.read()
        reader.read()
        progress = reader.acknowledge()

    with open(path, "a") as fp:
        fp.write("3\n4\n")

    with jsonlines.open(path, progress=progress) as reader:
        assert list(reader) == [3, 4]


def test_resume_after_append_without_trailing_newline(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), '{"a": 1}\n{"a": 2}')
    with jsonlines.open(path) as reader:
        reader.read()
        reader.read()
        progress = reader.acknowledge()

    # The previously last line gets completed by the append, followed
    # by a new record. The completed line must not be delivered again.
    with open(path, "a") as fp:
        fp.write('-suffix\n{"a": 3}\n')

    with jsonlines.open(path, progress=progress) as reader:
        assert list(reader) == [{"a": 3}]


def test_resume_with_crlf_line_endings(
    tmp_path: pathlib.Path,
) -> None:
    path = str(tmp_path / "a.jsonl")
    with open(path, "wb") as fp:
        fp.write(b"1\r\n2\r\n3\r\n")
    with jsonlines.open(path) as reader:
        reader.read()
        reader.read()
        progress = reader.acknowledge()
    with jsonlines.open(path, progress=progress) as reader:
        assert list(reader) == [3]


def test_progress_rejected_for_different_file_with_same_name(
    tmp_path: pathlib.Path,
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_path = write_jsonl(str(first_dir / "data.jsonl"), "1\n")
    second_path = write_jsonl(str(second_dir / "data.jsonl"), "1\n")

    with jsonlines.open(first_path) as reader:
        progress = reader.acknowledge()

    with pytest.raises(jsonlines.ReadProgressMismatchError) as excinfo:
        jsonlines.open(second_path, progress=progress)
    assert excinfo.value.progress is progress
    assert excinfo.value.fingerprint.path == os.path.realpath(second_path)


def test_progress_rejected_when_file_was_replaced(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n")
    with jsonlines.open(path) as reader:
        reader.read()
        progress = reader.acknowledge()

    os.unlink(path)
    write_jsonl(path, "99\n")

    with pytest.raises(jsonlines.ReadProgressMismatchError) as excinfo:
        jsonlines.open(path, progress=progress)
    assert "device/inode" in str(excinfo.value)


def test_acknowledge_skipped_line_then_resume(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\nbroken\n3\n")
    with jsonlines.open(path) as reader:
        assert reader.read() == 1
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            reader.read()
        assert excinfo.value.lineno == 2
        progress = reader.acknowledge_skipped_line(excinfo.value.lineno)

    with jsonlines.open(path, progress=progress) as reader:
        assert list(reader) == [3]


def test_acknowledge_skipped_line_also_confirms_prior_records(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\nbroken\n3\n")
    with jsonlines.open(path) as reader:
        reader.read()
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            reader.read()
        progress = reader.acknowledge_skipped_line(excinfo.value.lineno)
    assert progress.lineno == 2
    with jsonlines.open(path, progress=progress) as reader:
        assert list(reader) == [3]


def test_acknowledge_skipped_line_rejects_other_lineno(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\nbroken\n")
    with jsonlines.open(path) as reader:
        reader.read()
        with pytest.raises(jsonlines.InvalidLineError):
            reader.read()
        with pytest.raises(ValueError):
            reader.acknowledge_skipped_line(1)


def test_unacknowledged_invalid_line_is_hit_again_on_resume(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\nbroken\n3\n")
    with jsonlines.open(path) as reader:
        reader.read()
        with pytest.raises(jsonlines.InvalidLineError):
            reader.read()
        progress = reader.checkpoint()
    with jsonlines.open(path, progress=progress) as reader:
        assert reader.read() == 1
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            reader.read()
        assert excinfo.value.lineno == 2


def test_iter_skip_invalid_acknowledges_invalid_lines(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\nbad\n2\n")
    with jsonlines.open(path) as reader:
        assert list(reader.iter(skip_invalid=True)) == [1, 2]
        progress = reader.checkpoint()
    # The invalid line (and everything before it) is acknowledged; the
    # valid records were never explicitly acknowledged by the caller.
    assert progress.lineno == 2
    with jsonlines.open(path, progress=progress) as reader:
        assert list(reader) == [2]


def test_skip_empty_keeps_physical_lineno_and_resumes(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n\n2\n\n3\n")
    with jsonlines.open(path) as reader:
        assert reader.read(skip_empty=True) == 1
        assert reader.read(skip_empty=True) == 2
        assert reader.lineno == 3
        progress = reader.acknowledge()
    with jsonlines.open(path, progress=progress) as reader:
        assert list(reader.iter(skip_empty=True)) == [3]


def test_progress_tracking_with_binary_reader(
    tmp_path: pathlib.Path,
) -> None:
    path = str(tmp_path / "a.jsonl")
    with open(path, "wb") as fp:
        fp.write(b'{"x": 1}\n{"x": 2}\n')
    fp = open(path, "rb")
    with fp, jsonlines.Reader(fp) as reader:
        assert reader.read() == {"x": 1}
        progress = reader.acknowledge()
    fp = open(path, "rb")
    with fp, jsonlines.Reader(fp, progress=progress) as reader:
        assert list(reader) == [{"x": 2}]


def test_read_progress_survives_serialization_roundtrip(
    tmp_path: pathlib.Path,
) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n3\n")
    with jsonlines.open(path) as reader:
        reader.read()
        reader.read()
        progress = reader.acknowledge()

    payload = attr.asdict(progress)
    restored = jsonlines.ReadProgress(
        offset=payload["offset"],
        lineno=payload["lineno"],
        fingerprint=jsonlines.FileFingerprint(**payload["fingerprint"]),
    )
    with jsonlines.open(path, progress=restored) as reader:
        assert list(reader) == [3]


def test_lineno_reflects_physical_lines(tmp_path: pathlib.Path) -> None:
    path = write_jsonl(str(tmp_path / "a.jsonl"), "1\n2\n")
    with jsonlines.open(path) as reader:
        assert reader.lineno == 0
        reader.read()
        assert reader.lineno == 1
        reader.read()
        assert reader.lineno == 2
