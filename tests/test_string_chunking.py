"""
Tests for the ``chunk_strings`` option, which delivers the decoded contents
of string values as several ``string`` events instead of a single one.
"""

import io

import pytest

import ijson
from ijson import common

from .test_base import SURROGATE_PAIRS_JSON


PYTHON_BACKEND = ijson.get_backend('python')
CHUNKED_DOC = b'''
{
    "plain": "hello world",
    "escaped": "a\\nb\\\\c\\"d\\t",
    "utf8": "\xc3\xb1and\xc3\xba \xe4\xb8\xad\xe6\x96\x87",
    "surrogate": "\xf0\x9f\x92\xa9",
    "empty": "",
    "array": ["first", "second"]
}
'''
CHUNKED_DOC_OBJECT = {
    'plain': 'hello world',
    'escaped': 'a\nb\\c"d\t',
    'utf8': 'ñandú 中文',
    'surrogate': '\U0001F4A9',
    'empty': '',
    'array': ['first', 'second'],
}


def _string_events(events):
    return [value for event, value in events if event == 'string']


def _joined_strings(events):
    return ''.join(_string_events(events))


def _string_end_count(events):
    return sum(1 for event, _ in events if event == 'string_end')


def test_capability_flag(backend):
    """Only the python backend can stream strings without buffering them"""
    expected = backend.backend_name == 'python'
    assert backend.capabilities.string_chunking == expected


def test_unsupported_backends_refuse(adaptor):
    """Backends without the capability must fail instead of faking streaming"""
    if adaptor.backend.capabilities.string_chunking:
        return
    with pytest.raises(ValueError, match="chunks"):
        adaptor.basic_parse(CHUNKED_DOC, chunk_strings=True)
    with pytest.raises(ValueError, match="chunks"):
        adaptor.parse(CHUNKED_DOC, chunk_strings=True)


def test_chunked_basic_parse(adaptor):
    """String values arrive in chunks, everything else as ordinary events"""
    if not adaptor.backend.capabilities.string_chunking:
        pytest.skip("backend does not support string chunking")
    events = adaptor.basic_parse(CHUNKED_DOC, chunk_strings=True,
                                 string_chunk_size=4)
    assert _joined_strings(events) == ''.join((
        CHUNKED_DOC_OBJECT['plain'],
        CHUNKED_DOC_OBJECT['escaped'],
        CHUNKED_DOC_OBJECT['utf8'],
        CHUNKED_DOC_OBJECT['surrogate'],
        *CHUNKED_DOC_OBJECT['array'],
    ))
    # every delivered chunk respects the requested size limit
    assert all(len(value) <= 4 for value in _string_events(events))
    # one boundary marker per string value, empty strings included
    assert _string_end_count(events) == 7
    # map keys are still delivered whole, exactly as without chunking
    keys = [value for event, value in events if event == 'map_key']
    assert keys == ['plain', 'escaped', 'utf8', 'surrogate', 'empty', 'array']
    # non-string events are untouched
    assert ('start_map', None) in events and ('end_map', None) in events
    assert ('start_array', None) in events and ('end_array', None) in events
    assert ('boolean', True) not in events


def test_chunked_parse_carries_prefixes(adaptor):
    """parse() prefixes chunk events and the string_end boundary alike"""
    if not adaptor.backend.capabilities.string_chunking:
        pytest.skip("backend does not support string chunking")
    events = adaptor.parse(CHUNKED_DOC, chunk_strings=True,
                           string_chunk_size=3)
    plain = [(event, value) for prefix, event, value in events
             if prefix == 'plain']
    assert ''.join(value for event, value in plain if event == 'string') == \
        CHUNKED_DOC_OBJECT['plain']
    assert ('string_end', None) in plain
    array_events = [(event, value) for prefix, event, value in events
                    if prefix == 'array.item']
    assert ''.join(value for event, value in array_events
                   if event == 'string') == 'firstsecond'
    assert sum(1 for event, _ in array_events if event == 'string_end') == 2


def test_empty_string_only_emits_boundary(adaptor):
    if not adaptor.backend.capabilities.string_chunking:
        pytest.skip("backend does not support string chunking")
    events = adaptor.basic_parse(b'{"empty": ""}', chunk_strings=True)
    assert ('string', '') not in events
    assert [event for event, _ in events].count('string_end') == 1


def test_surrogate_pairs_are_never_split(adaptor):
    """A high surrogate waits for its low surrogate across chunks"""
    if not adaptor.backend.capabilities.string_chunking:
        pytest.skip("backend does not support string chunking")
    events = adaptor.basic_parse(SURROGATE_PAIRS_JSON, chunk_strings=True)
    assert _string_events(events) == ['\U0001F4A9']
    assert _string_end_count(events) == 1


def test_lone_surrogates_are_preserved():
    """Unpaired surrogates behave like the standard JSON decoder's output"""
    for raw, expected in (
        (b'["\\uD83D"]', ['\ud83d']),
        (b'["\\uDCA9"]', ['\udca9']),
        (b'["\\uD83D\\uD83D"]', ['\ud83d', '\ud83d']),
        (b'["\\uDCA9\\uD83D\\uDCA9"]', ['\udca9', '\U0001F4A9']),
    ):
        events = list(PYTHON_BACKEND.basic_parse(
            io.BytesIO(raw), buf_size=1, chunk_strings=True))
        assert _string_events(events) == expected, raw


@pytest.mark.parametrize("feed_size", range(1, 12))
def test_every_byte_boundary_decodes_correctly(feed_size):
    """Quotes, backslashes, UTF-8 and escapes may all straddle chunks"""
    expected = CHUNKED_DOC_OBJECT
    events = list(PYTHON_BACKEND.basic_parse(
        io.BytesIO(CHUNKED_DOC), buf_size=feed_size,
        chunk_strings=True, string_chunk_size=2))
    assert _joined_strings(events) == ''.join((
        expected['plain'], expected['escaped'], expected['utf8'],
        expected['surrogate'], *expected['array'],
    ))


def test_truncated_escape_is_not_delivered_half_decoded():
    """A broken string never reports a clean string_end boundary"""
    doc = b'{"doc": "abc\\u00"}'
    # skip the empty prefix (not inside a string) and the complete document
    for cut in range(1, len(doc)):
        emitted = []
        with pytest.raises(common.JSONError):
            for event in PYTHON_BACKEND.basic_parse(
                    io.BytesIO(doc[:cut]), buf_size=1, chunk_strings=True):
                emitted.append(event)
        assert not any(event == 'string_end' for event, _ in emitted)


def test_clean_end_and_tail_error_are_distinguishable(adaptor):
    if not adaptor.backend.capabilities.string_chunking:
        pytest.skip("backend does not support string chunking")
    good = b'{"doc": "abc"}'
    events = adaptor.basic_parse(good, chunk_strings=True)
    assert events[-1] == ('end_map', None)
    assert _string_end_count(events) == 1

    malformed = b'{"doc": "abc\\q"}'
    with pytest.raises(common.JSONError):
        adaptor.basic_parse(malformed, chunk_strings=True)

    truncated = b'{"doc": "abc'
    with pytest.raises(common.IncompleteJSONError):
        adaptor.basic_parse(truncated, chunk_strings=True)


def test_invalid_utf8_is_reported(adaptor):
    if not adaptor.backend.capabilities.string_chunking:
        pytest.skip("backend does not support string chunking")
    with pytest.raises(common.IncompleteJSONError):
        adaptor.basic_parse(b'{"doc": "a\xc3"}', chunk_strings=True)


@pytest.mark.parametrize("control", (0x00, 0x0A, 0x09, 0x1F))
def test_raw_control_characters_are_rejected(control):
    """Unescaped control chars are invalid JSON and must not be delivered"""
    raw = b'["a' + bytes([control]) + b'b"]'
    with pytest.raises(common.JSONError):
        list(PYTHON_BACKEND.basic_parse(io.BytesIO(raw), buf_size=1,
                                        chunk_strings=True))
    # the same character expressed as an escape is perfectly valid
    escaped = ''.join(v for e, v in PYTHON_BACKEND.basic_parse(
        io.BytesIO(b'["a\\u%04xb"]' % control), buf_size=1,
        chunk_strings=True) if e == 'string')
    assert escaped == 'a' + chr(control) + 'b'


def test_chunks_are_delivered_before_the_string_ends():
    """The whole point of chunking: output flows while input is still coming"""
    sink = ijson.sendable_list()
    coro = PYTHON_BACKEND.basic_parse_coro(sink, chunk_strings=True,
                                           string_chunk_size=4)
    coro.send(b'{"doc": "abcdefghij')
    delivered = _string_events(sink)
    assert delivered and ''.join(delivered) == 'abcdefghij'
    assert not any(event == 'string_end' for event, _ in sink)
    coro.send(b'klmnop"}')
    coro.close()
    assert _joined_strings(sink) == 'abcdefghijklmnop'
    assert sink[-1] == ('end_map', None)
    assert _string_end_count(sink) == 1


def test_peak_memory_does_not_grow_with_string_length():
    # a 600 KB string streamed in 64-byte reads must not be held whole
    big = b'{"doc": "' + (b'abc' * 200000) + b'"}'
    tracemalloc = pytest.importorskip('tracemalloc')
    tracemalloc.start()
    try:
        total = 0
        for event, value in PYTHON_BACKEND.basic_parse(
                io.BytesIO(big), buf_size=64, chunk_strings=True,
                string_chunk_size=128):
            if event == 'string':
                total += len(value)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert total == 600000
    # peak must stay a small fraction of the 600 KB string, i.e. bounded by
    # the buffer/chunk sizes rather than by the string length
    assert peak < 300_000


def test_multiple_values_are_chunked_independently(adaptor):
    if not adaptor.backend.capabilities.string_chunking:
        pytest.skip("backend does not support string chunking")
    events = adaptor.basic_parse(b'"ab""cd"', chunk_strings=True,
                                 multiple_values=True)
    assert _joined_strings(events) == 'abcd'
    assert _string_end_count(events) == 2


def test_items_and_kvitems_reject_chunking(adaptor):
    """Object-building routines refuse chunked strings on every backend"""
    with pytest.raises(ValueError, match="chunks"):
        adaptor.items(b'{"a": 1}', '', chunk_strings=True)
    with pytest.raises(ValueError, match="chunks"):
        adaptor.kvitems(b'{"a": 1}', '', chunk_strings=True)


@pytest.mark.parametrize("bad_size", (0, -1, 1.5, "10"))
def test_invalid_chunk_size_is_rejected(adaptor, bad_size):
    if not adaptor.backend.capabilities.string_chunking:
        pytest.skip("backend does not support string chunking")
    with pytest.raises(ValueError):
        adaptor.basic_parse(b'{"a": "x"}', chunk_strings=True,
                            string_chunk_size=bad_size)


def test_disabled_chunking_keeps_legacy_events(adaptor):
    """Without the option events must be byte-for-byte the old ones"""
    legacy = adaptor.basic_parse(b'{"a": "xy"}')
    assert ('string', 'xy') in legacy
    assert not any(event == 'string_end' for event, _ in legacy)
