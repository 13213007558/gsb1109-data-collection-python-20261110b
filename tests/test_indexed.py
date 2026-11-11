"""
Tests for the record indexing support in the jsonlines library.
"""

import io
import json
import os
from pathlib import Path
from typing import Any, Union

import jsonlines
import pytest


def write_file(tmp_path: Path, name: str, data: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def loads_blank_as_value(line: Union[str, bytes]) -> Any:
    text = line.decode("utf-8") if isinstance(line, bytes) else line
    return json.loads(text) if text.strip() else "blank"


def test_indexed_records_carry_line_and_offset(tmp_path: Path) -> None:
    fname = write_file(
        tmp_path,
        "sample.jsonl",
        b'{"a": 1}\n\nnot json\n{"b": 2}\n{"c": 3}\n',
    )
    with jsonlines.index(fname) as reader:
        assert len(reader) == 3

        first = reader.get(0)
        assert first.record == 0
        assert first.value == {"a": 1}
        assert first.lineno == 1
        assert first.offset == 0

        second = reader.get(1)
        assert second.record == 1
        assert second.value == {"b": 2}
        assert second.lineno == 4

        third = reader.get(2)
        assert third.lineno == 5
        # offset points at the start of the physical line
        with open(fname, "rb") as fp:
            fp.seek(third.offset)
            assert fp.readline() == b'{"c": 3}\n'


def test_indexed_random_access_is_order_independent(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "nums.jsonl", b"1\n2\n3\n4\n5\n")
    with jsonlines.IndexedReader(fname) as reader:
        assert [reader.get(i).value for i in (4, 0, 3, 1, 2)] == [
            5,
            1,
            4,
            2,
            3,
        ]
        assert reader.get(-1).value == 5
        assert reader.get(-5).value == 1


def test_indexed_out_of_range(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "one.jsonl", b"1\n")
    with jsonlines.index(fname) as reader:
        with pytest.raises(IndexError):
            reader.get(1)
        with pytest.raises(IndexError):
            reader.get(-2)


def test_indexed_iter_all(tmp_path: Path) -> None:
    fname = write_file(
        tmp_path,
        "mixed.jsonl",
        b'{"x": 1}\nbroken\n{"x": 2}\n',
    )
    with jsonlines.IndexedReader(fname) as reader:
        records = list(reader.iter_all())
    assert [r.record for r in records] == [0, 1]
    assert [r.value for r in records] == [{"x": 1}, {"x": 2}]
    assert [r.lineno for r in records] == [1, 3]


def test_indexed_skip_empty(tmp_path: Path) -> None:
    fname = write_file(
        tmp_path,
        "gaps.jsonl",
        b"1\n\n   \n2\n",
    )
    # A decoder that treats whitespace-only lines as a valid value makes
    # the difference between the two modes observable.
    with jsonlines.IndexedReader(
        fname, loads=loads_blank_as_value, skip_empty=True
    ) as reader:
        records = list(reader.iter_all())
    assert [r.value for r in records] == [1, 2]
    assert [r.lineno for r in records] == [1, 4]

    with jsonlines.IndexedReader(fname, loads=loads_blank_as_value) as reader:
        records = list(reader.iter_all())
    assert [r.value for r in records] == [1, "blank", "blank", 2]
    assert [r.lineno for r in records] == [1, 2, 3, 4]


def test_indexed_empty_lines_are_not_records_by_default(tmp_path: Path) -> None:
    # An empty line is not valid JSON, so it is simply not indexed.
    fname = write_file(tmp_path, "gaps.jsonl", b"1\n\n2\n")
    with jsonlines.IndexedReader(fname) as reader:
        assert [r.lineno for r in reader.iter_all()] == [1, 3]


def test_indexed_typed_reads(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "typed.jsonl", b'12\ntrue\n"foo"\n')
    with jsonlines.IndexedReader(fname) as reader:
        assert reader.read_at(0, type=int).value == 12
        with pytest.raises(jsonlines.InvalidLineError) as excinfo:
            reader.read_at(1, type=int)
        assert excinfo.value.lineno == 2
        with pytest.raises(ValueError):
            reader.read_at(0, type=str)


def test_indexed_allow_none(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "null.jsonl", b"null\n1\n")
    with jsonlines.IndexedReader(fname) as reader:
        with pytest.raises(jsonlines.InvalidLineError):
            reader.read_at(0)
        assert reader.read_at(0, allow_none=True).value is None


def test_indexed_invalid_utf8_line_is_skipped(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "bad_utf8.jsonl", b"1\n\xff\xff\n2\n")
    with jsonlines.IndexedReader(fname) as reader:
        records = list(reader.iter_all())
    assert [r.value for r in records] == [1, 2]
    assert [r.lineno for r in records] == [1, 3]


def test_indexed_rfc7464_and_bom_prefixes(tmp_path: Path) -> None:
    import codecs

    data = b'\x1e"a"\n' + codecs.BOM_UTF8 + b'"b"\n'
    fname = write_file(tmp_path, "prefixed.jsonl", data)
    with jsonlines.IndexedReader(fname) as reader:
        records = list(reader.iter_all())
    assert [r.value for r in records] == ["a", "b"]
    assert [r.offset for r in records] == [0, len(b'\x1e"a"\n')]


def test_indexed_custom_loads(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "custom.jsonl", b"{}\n")
    with jsonlines.IndexedReader(
        fname, loads=lambda line: "decoded"
    ) as reader:
        assert reader.get(0).value == "decoded"


def test_stale_index_on_truncation(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "trunc.jsonl", b"1\n2\n3\n")
    with jsonlines.index(fname) as reader:
        assert reader.get(2).value == 3
        with open(fname, "wb") as fp:
            fp.write(b"1\n")
        with pytest.raises(jsonlines.StaleIndexError):
            reader.get(0)
        with pytest.raises(jsonlines.StaleIndexError):
            len(reader)


def test_stale_index_on_replacement(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "replaced.jsonl", b"1\n2\n")
    with jsonlines.IndexedReader(fname) as reader:
        assert reader.get(0).value == 1
        os.replace(
            write_file(tmp_path, "other.jsonl", b"9\n8\n7\n"),
            fname,
        )
        with pytest.raises(jsonlines.StaleIndexError):
            reader.get(0)


def test_stale_index_on_same_size_replacement(tmp_path: Path) -> None:
    # A replacement that keeps the file size must still be detected via
    # the inode, rather than only via file size.
    fname = write_file(tmp_path, "swapped.jsonl", b"1\n2\n")
    with jsonlines.IndexedReader(fname) as reader:
        assert reader.get(0).value == 1
        os.replace(
            write_file(tmp_path, "other.jsonl", b"9\n8\n"),
            fname,
        )
        with pytest.raises(jsonlines.StaleIndexError):
            reader.get(0)


def test_rebuild_recovers_from_stale_index(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "rebuild.jsonl", b"1\n2\n")
    with jsonlines.IndexedReader(fname) as reader:
        assert len(reader) == 2
        with open(fname, "ab") as fp:
            fp.write(b"3\n4\n")
        with pytest.raises(jsonlines.StaleIndexError):
            reader.get(0)
        reader.rebuild()
        assert len(reader) == 4
        assert reader.get(3).value == 4


def test_stale_index_when_file_disappears(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "gone.jsonl", b"1\n")
    reader = jsonlines.IndexedReader(fname)
    os.unlink(fname)
    with pytest.raises(jsonlines.StaleIndexError):
        reader.get(0)


def test_index_bound_to_skip_empty_option(tmp_path: Path) -> None:
    # Two readers over the same file with different options get different
    # indexes; neither affects the other.
    fname = write_file(tmp_path, "opts.jsonl", b"1\n\n2\n")
    with jsonlines.IndexedReader(
        fname, loads=loads_blank_as_value
    ) as plain, jsonlines.IndexedReader(
        fname, loads=loads_blank_as_value, skip_empty=True
    ) as skipping:
        assert len(plain) == 3
        assert len(skipping) == 2
        assert plain.get(1).value == "blank"
        assert skipping.get(1).value == 2


def test_indexed_reader_closed_behaviour(tmp_path: Path) -> None:
    fname = write_file(tmp_path, "closed.jsonl", b"1\n2\n")
    reader = jsonlines.IndexedReader(fname)
    reader.close()
    reader.close()  # no-op
    with pytest.raises(RuntimeError):
        reader.get(0)
    reader.rebuild()
    assert reader.get(0).value == 1


def test_index_accepts_pathlike(tmp_path: Path) -> None:
    path = tmp_path / "pathlike.jsonl"
    path.write_bytes(b'{"k": "v"}\n')
    with jsonlines.IndexedReader(Path(path)) as reader:
        record = reader.get(0)
    assert record.value == {"k": "v"}
    assert str(path) in repr(reader)


def test_index_function_rejects_fd() -> None:
    with pytest.raises(ValueError):
        jsonlines.index(1)


def test_indexed_reader_does_not_touch_pipes() -> None:
    # The plain Reader keeps working on a non-seekable stream.
    with jsonlines.Reader(io.StringIO("1\n2\n")) as reader:
        assert list(reader) == [1, 2]


def test_indexed_record_namedtuple_fields() -> None:
    record = jsonlines.IndexedRecord(
        record=3, value={"x": 1}, lineno=7, offset=42
    )
    assert record._fields == ("record", "value", "lineno", "offset")
    assert json.loads(json.dumps(record.value)) == {"x": 1}
