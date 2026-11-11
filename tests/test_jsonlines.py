"""
Tests for the jsonlines library.
"""

import codecs
import collections
import io
import json
import tempfile
from typing import List

import jsonlines
import pytest


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


def test_on_invalid_invalid_json() -> None:
    fp = io.StringIO("12\ninvalid\n34\n")
    rejections: List[jsonlines.Rejection] = []
    with jsonlines.Reader(fp) as reader:
        values = list(
            reader.iter(skip_invalid=True, on_invalid=rejections.append)
        )
    assert values == [12, 34]
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.lineno == 2
    assert rejection.line == "invalid\n"
    assert rejection.reason is jsonlines.RejectionReason.INVALID_JSON


def test_on_invalid_invalid_utf8() -> None:
    fp = io.BytesIO(b"12\n\xff\xff\n34\n")
    rejections: List[jsonlines.Rejection] = []
    with jsonlines.Reader(fp) as reader:
        values = list(
            reader.iter(skip_invalid=True, on_invalid=rejections.append)
        )
    assert values == [12, 34]
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.lineno == 2
    assert rejection.line == b"\xff\xff\n"
    assert rejection.reason is jsonlines.RejectionReason.INVALID_UTF8


def test_on_invalid_type_mismatch() -> None:
    fp = io.StringIO("12\ntrue\n34\n")
    rejections: List[jsonlines.Rejection] = []
    with jsonlines.Reader(fp) as reader:
        values = list(
            reader.iter(
                type=int,
                skip_invalid=True,
                on_invalid=rejections.append,
            )
        )
    assert values == [12, 34]
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.lineno == 2
    assert rejection.line == "true\n"
    assert rejection.reason is jsonlines.RejectionReason.TYPE_MISMATCH


def test_on_invalid_receives_unmodified_line() -> None:
    """
    The rejected line is passed verbatim, including a BOM prefix that is
    otherwise stripped and trailing whitespace that is otherwise removed.
    """
    original_line = codecs.BOM_UTF8.decode() + "invalid  "
    rejections: List[jsonlines.Rejection] = []
    with jsonlines.Reader([original_line]) as reader:
        list(reader.iter(skip_invalid=True, on_invalid=rejections.append))
    assert len(rejections) == 1
    assert rejections[0].lineno == 1
    assert rejections[0].line == original_line


def test_on_invalid_empty_lines_follow_skip_empty() -> None:
    data = "12\n\n  \n34\n"

    # With skip_empty=True, empty and whitespace-only lines are skipped
    # without triggering the rejection callback.
    rejections: List[jsonlines.Rejection] = []
    with jsonlines.Reader(io.StringIO(data)) as reader:
        values = list(
            reader.iter(
                skip_empty=True,
                skip_invalid=True,
                on_invalid=rejections.append,
            )
        )
    assert values == [12, 34]
    assert rejections == []

    # With skip_empty=False, those same lines are invalid lines and get
    # reported for quarantine.
    quarantined: List[jsonlines.Rejection] = []
    with jsonlines.Reader(io.StringIO(data)) as reader:
        values = list(
            reader.iter(skip_invalid=True, on_invalid=quarantined.append)
        )
    assert values == [12, 34]
    assert [rejection.lineno for rejection in quarantined] == [2, 3]
    assert [rejection.line for rejection in quarantined] == ["\n", "  \n"]
    assert all(
        rejection.reason is jsonlines.RejectionReason.INVALID_JSON
        for rejection in quarantined
    )


def test_on_invalid_without_skip_invalid_is_ignored() -> None:
    rejections: List[jsonlines.Rejection] = []
    with jsonlines.Reader(io.StringIO("invalid\n")) as reader:
        iterator = reader.iter(on_invalid=rejections.append)
        with pytest.raises(jsonlines.InvalidLineError):
            next(iterator)
    assert rejections == []


def test_on_invalid_callback_error_stops_iteration() -> None:
    lines = iter(["12", "invalid", "34"])
    reader = jsonlines.Reader(lines)

    def fail(rejection: jsonlines.Rejection) -> None:
        raise OSError("quarantine storage unavailable")

    iterator = reader.iter(skip_invalid=True, on_invalid=fail)
    assert next(iterator) == 12
    with pytest.raises(OSError, match="quarantine"):
        next(iterator)

    # The generator must not silently continue, and the following line
    # must not have been consumed from the underlying iterable.
    assert next(lines) == "34"
    with pytest.raises(StopIteration):
        next(iterator)


def test_on_invalid_callback_eoferror_is_not_swallowed() -> None:
    def fail(rejection: jsonlines.Rejection) -> None:
        raise EOFError("quarantine stream ended")

    reader = jsonlines.Reader(io.StringIO("12\ninvalid\n34\n"))
    iterator = reader.iter(skip_invalid=True, on_invalid=fail)
    assert next(iterator) == 12
    with pytest.raises(EOFError, match="quarantine"):
        next(iterator)


def test_invalid_line_error_reason_and_raw_line() -> None:
    reader = jsonlines.Reader(io.StringIO("true"))
    with pytest.raises(jsonlines.InvalidLineError) as excinfo:
        reader.read(type=int)
    exc = excinfo.value
    assert exc.reason is jsonlines.RejectionReason.TYPE_MISMATCH
    assert exc.raw_line == "true"
    assert exc.line == "true"

    original_line = codecs.BOM_UTF8.decode() + "invalid  "
    with jsonlines.Reader([original_line]) as failing_reader:
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            failing_reader.read()
    exc = excinfo.value
    assert exc.reason is jsonlines.RejectionReason.INVALID_JSON
    assert exc.raw_line == original_line
    assert exc.line == original_line.rstrip()

    with jsonlines.Reader([b"\xff\xff"]) as failing_reader:
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            failing_reader.read()
    exc = excinfo.value
    assert exc.reason is jsonlines.RejectionReason.INVALID_UTF8
    assert exc.raw_line == b"\xff\xff"


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
