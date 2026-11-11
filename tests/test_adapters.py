import asyncio
import io
import tempfile

import ijson
import pytest


def test_from_iter_read0_does_not_consume():
    chunks = [b'{"key":', b'"value"}']
    file_obj = ijson.from_iter(iter(chunks))
    assert file_obj.read(0) == b""
    assert file_obj.read(1) == b'{"key":'
    assert file_obj.read(1) == b'"value"}'
    assert file_obj.read(1) == b""


@pytest.mark.parametrize(
    "chunks_factory",
    [list, tuple, iter, lambda chunks: (chunk for chunk in chunks)],
    ids=["list", "tuple", "iter", "generator"],
)
def test_from_iter_accepts_iterable(chunks_factory):
    chunks = chunks_factory([b'{"key":', b'"value"}'])
    file_obj = ijson.from_iter(chunks)
    assert file_obj.read(1) == b'{"key":'
    assert file_obj.read(1) == b'"value"}'
    assert file_obj.read(1) == b""


@pytest.mark.parametrize(
    "chunks_factory",
    [list, tuple, iter, lambda chunks: (chunk for chunk in chunks)],
    ids=["list", "tuple", "iter", "generator"],
)
def test_from_iter_accepts_string_and_warns(chunks_factory):
    chunks = chunks_factory(['{"key":', '"value"}'])
    file_obj = ijson.from_iter(chunks)
    with pytest.deprecated_call():
        assert file_obj.read(1) == b'{"key":'
    assert file_obj.read(1) == b'"value"}'
    assert file_obj.read(1) == b""


def test_from_iter_accepts_aiterable():
    async def chunks():
        yield b'{"key":'
        yield b'"value"}'

    async def main():
        file_obj = ijson.from_iter(chunks())
        assert await file_obj.read(0) == b""
        assert await file_obj.read(1) == b'{"key":'
        assert await file_obj.read(1) == b'"value"}'
        assert await file_obj.read(1) == b""

    asyncio.run(main())


def test_from_iter_accepts_async_string_chunks_and_warns():
    async def chunks():
        yield '{"key":'
        yield '"value"}'

    async def main():
        file_obj = ijson.from_iter(chunks())
        with pytest.deprecated_call():
            assert await file_obj.read(1) == b'{"key":'
        assert await file_obj.read(1) == b'"value"}'
        assert await file_obj.read(1) == b""

    asyncio.run(main())


ENVELOPE_ROWS = b'"rows": [{"id": 1, "tags": ["a", "b"]}, {"id": 2}, 3, null]'
EXPECTED_ROWS = [{"id": 1, "tags": ["a", "b"]}, {"id": 2}, 3, None]


def test_confirmed_items_status_after_rows():
    doc = b'{%s, "status": "ok"}' % ENVELOPE_ROWS
    assert list(ijson.confirmed_items(doc)) == EXPECTED_ROWS


def test_confirmed_items_status_before_rows():
    doc = b'{"status": "ok", %s}' % ENVELOPE_ROWS
    assert list(ijson.confirmed_items(doc)) == EXPECTED_ROWS


def test_confirmed_items_status_position_gives_same_result():
    after = list(ijson.confirmed_items(b'{%s, "status": "ok"}' % ENVELOPE_ROWS))
    before = list(ijson.confirmed_items(b'{"status": "ok", %s}' % ENVELOPE_ROWS))
    assert after == before


def test_confirmed_items_accepts_file_object():
    doc = io.BytesIO(b'{%s, "status": "ok"}' % ENVELOPE_ROWS)
    assert list(ijson.confirmed_items(doc)) == EXPECTED_ROWS


def test_confirmed_items_matches_items_output():
    doc = b'{"rows": [{"v": 1.5}, {"v": 100000000000000000000}], "status": "ok"}'
    assert list(ijson.confirmed_items(doc)) == list(ijson.items(doc, 'rows.item'))


def test_confirmed_items_failed_status_yields_nothing():
    doc = b'{%s, "status": "failed"}' % ENVELOPE_ROWS
    assert list(ijson.confirmed_items(doc)) == []


def test_confirmed_items_empty_rows():
    assert list(ijson.confirmed_items(b'{"rows": [], "status": "ok"}')) == []


def test_confirmed_items_missing_status_is_bad_envelope():
    with pytest.raises(ijson.BadEnvelopeError):
        list(ijson.confirmed_items(b'{%s}' % ENVELOPE_ROWS))


def test_confirmed_items_duplicate_status_is_bad_envelope():
    doc = b'{%s, "status": "ok", "status": "ok"}' % ENVELOPE_ROWS
    with pytest.raises(ijson.BadEnvelopeError):
        list(ijson.confirmed_items(doc))


def test_confirmed_items_truncated_response_yields_nothing():
    gen = ijson.confirmed_items(b'{%s, "status": "ok"' % ENVELOPE_ROWS)
    with pytest.raises(ijson.JSONError):
        next(gen)


def test_confirmed_items_custom_envelope_shape():
    doc = b'{"data": [{"id": 1}], "code": "DONE"}'
    gen = ijson.confirmed_items(
        doc, rows_prefix='data.item', status_key='code', expected_status='DONE')
    assert list(gen) == [{"id": 1}]


def test_confirmed_items_many_rows():
    doc = b'{"rows": [%s], "status": "ok"}' % b','.join(
        b'{"id": %d}' % i for i in range(10000))
    rows = list(ijson.confirmed_items(doc))
    assert rows == [{"id": i} for i in range(10000)]


@pytest.fixture
def tracked_temporary_files(monkeypatch):
    created = []
    real_temporary_file = tempfile.TemporaryFile

    def tracking_temporary_file(*args, **kwargs):
        f = real_temporary_file(*args, **kwargs)
        created.append(f)
        return f

    monkeypatch.setattr(tempfile, 'TemporaryFile', tracking_temporary_file)
    return created


def test_confirmed_items_early_close_releases_staging(tracked_temporary_files):
    gen = ijson.confirmed_items(b'{%s, "status": "ok"}' % ENVELOPE_ROWS)
    assert next(gen) == EXPECTED_ROWS[0]
    gen.close()
    assert tracked_temporary_files
    assert all(f.closed for f in tracked_temporary_files)


def test_confirmed_items_failed_status_releases_staging(tracked_temporary_files):
    gen = ijson.confirmed_items(b'{%s, "status": "failed"}' % ENVELOPE_ROWS)
    assert list(gen) == []
    assert tracked_temporary_files
    assert all(f.closed for f in tracked_temporary_files)


def test_confirmed_items_parse_error_releases_staging(tracked_temporary_files):
    gen = ijson.confirmed_items(b'{%s, "status": "ok"' % ENVELOPE_ROWS)
    with pytest.raises(ijson.JSONError):
        next(gen)
    assert tracked_temporary_files
    assert all(f.closed for f in tracked_temporary_files)


def test_confirmed_items_bad_envelope_releases_staging(tracked_temporary_files):
    gen = ijson.confirmed_items(b'{%s}' % ENVELOPE_ROWS)
    with pytest.raises(ijson.BadEnvelopeError):
        next(gen)
    assert tracked_temporary_files
    assert all(f.closed for f in tracked_temporary_files)
