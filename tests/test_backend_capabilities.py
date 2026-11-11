"""
Backend capability matrix tests.

A feed that parses fine on a worker with a fully equipped ijson installation
(e.g. with the yajl C backends available) does not necessarily parse on a
worker that only has the pure-python backend. Each backend therefore
advertises a ``capabilities`` object describing the non-standard JSON
features it supports.

The cases below form a small, fixed capability matrix covering:

* C-style comments,
* multiple top-level values,
* numbers with very large exponents (float overflow vs. arbitrary precision),
* invalid UTF-8 byte sequences,
* truncated input (both structural truncation and incomplete tokens).

For every case the tests check that the actual behavior matches the backend's
declared capabilities: a declared capability produces the documented result,
an undeclared one is refused explicitly instead of being silently accepted,
and malformed data is never silently accepted regardless of declarations.

They also compare the outcome of reading the whole payload at once against
feeding it in small chunks, since a backend can behave differently across its
internal buffering boundaries. Not every backend has to support every
capability; what matters is that declarations and behavior agree, and that the
whole-read and chunked-read event sequences (or error categories) match.
"""

import collections
import io
from decimal import Decimal

import ijson
from ijson import common


WHOLE = "whole"
CHUNKED = "chunked"
CHUNK_SIZES = (1, 2, 5)

# Outcome kinds used to compare results without coupling to exact messages
EVENTS = "events"
ERROR = "error"


# A capability matrix case.
#
# ``expect`` maps the backend's capabilities to either ``('events', list)``
# for a successful parse, ``('raises', exception_class)`` for a mandatory
# error, or ``None`` when no behavior is promised for backends lacking the
# capability (in which case only whole/chunked equivalence is checked).
MatrixCase = collections.namedtuple(
    "MatrixCase", ("name", "data", "kwargs", "expect"))


COMMENT_JSON = b'{"a": 2 /* comment */}'
COMMENT_EVENTS = [
    ('start_map', None),
    ('map_key', 'a'),
    ('number', 2),
    ('end_map', None),
]
MULTI_VALUE_JSON = b'1 2'
MULTI_VALUE_EVENTS = [('number', 1), ('number', 2)]
HUGE_EXPONENT = b'1e100000'
HUGE_EXPONENT_DECIMAL = Decimal('1E+100000')
INVALID_UTF8_JSON = b'"\xa8"'
TRUNCATED_JSON = b'{"key": "value"'
INCOMPLETE_TOKEN_JSON = b'nul'


def _expect_comments(caps):
    if caps.c_comments:
        return (EVENTS, COMMENT_EVENTS)
    return ('raises', ValueError)


def _expect_comments_without_flag(caps):
    # Comments must never be silently accepted when allow_comments is off,
    # regardless of whether the backend supports them at all.
    return ('raises', common.JSONError)


def _expect_multiple_values(caps):
    if caps.multiple_values:
        return (EVENTS, MULTI_VALUE_EVENTS)
    return ('raises', ValueError)


def _expect_multiple_values_without_flag(caps):
    # Extra top-level values must be rejected unless explicitly opted in.
    return ('raises', common.JSONError)


def _expect_huge_exponent_decimal(caps):
    # Decimal mode keeps arbitrary precision; no backend may overflow or lose
    # the exponent here.
    return (EVENTS, [('number', HUGE_EXPONENT_DECIMAL)])


def _expect_huge_exponent_float(caps):
    # float mode cannot represent the value; backends report it through
    # different JSONError subclasses, but must never hand back inf silently.
    return ('raises', common.JSONError)


def _expect_invalid_utf8(caps):
    # Invalid UTF-8 must always be rejected rather than decoded into garbage.
    return ('raises', common.JSONError)


def _expect_truncated_document(caps):
    # Structural truncation is detectable by every backend.
    return ('raises', common.IncompleteJSONError)


def _expect_incomplete_token(caps):
    if not caps.incomplete_json_tokens_detection:
        # No promise is made for backends that don't detect these; the
        # whole/chunked equivalence test still applies.
        return None
    return ('raises', common.IncompleteJSONError)


MATRIX_CASES = [
    MatrixCase("comments", COMMENT_JSON,
               {"allow_comments": True}, _expect_comments),
    MatrixCase("comments_without_flag", COMMENT_JSON,
               {}, _expect_comments_without_flag),
    MatrixCase("multiple_values", MULTI_VALUE_JSON,
               {"multiple_values": True}, _expect_multiple_values),
    MatrixCase("multiple_values_without_flag", MULTI_VALUE_JSON,
               {}, _expect_multiple_values_without_flag),
    MatrixCase("huge_exponent_decimal", HUGE_EXPONENT,
               {"use_float": False}, _expect_huge_exponent_decimal),
    MatrixCase("huge_exponent_float", HUGE_EXPONENT,
               {"use_float": True}, _expect_huge_exponent_float),
    MatrixCase("invalid_utf8", INVALID_UTF8_JSON,
               {}, _expect_invalid_utf8),
    MatrixCase("truncated_document", TRUNCATED_JSON,
               {}, _expect_truncated_document),
    MatrixCase("incomplete_token", INCOMPLETE_TOKEN_JSON,
               {}, _expect_incomplete_token),
]


def _byte_chunks(data, chunk_size):
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]


def _run(backend, case, style, chunk_size=1):
    """Run a case either on the whole payload or via a chunked reader."""
    if style == WHOLE:
        reader = io.BytesIO(case.data)
    else:
        reader = ijson.from_iter(_byte_chunks(case.data, chunk_size))
    try:
        events = list(backend.basic_parse_gen(reader, **case.kwargs))
    except Exception as exc:
        return (ERROR, type(exc), exc)
    return (EVENTS, events, None)


def _error_category(exc_type):
    if issubclass(exc_type, common.IncompleteJSONError):
        return 'incomplete_json_error'
    if issubclass(exc_type, common.JSONError):
        return 'json_error'
    if issubclass(exc_type, ValueError):
        return 'value_error'
    return exc_type.__name__


def _outcome_category(outcome):
    kind = outcome[0]
    if kind == EVENTS:
        return (EVENTS, outcome[1])
    return (ERROR, _error_category(outcome[1]))


def test_capability_matrix(backend):
    """Declared capabilities match actual behavior for every matrix case."""
    for case in MATRIX_CASES:
        expected = case.expect(backend.capabilities)
        if expected is None:
            continue
        for style in (WHOLE, CHUNKED):
            outcome = _run(backend, case, style)
            if expected[0] == EVENTS:
                assert outcome[0] == EVENTS, (case.name, style, outcome)
                assert outcome[1] == expected[1], (case.name, style)
            else:
                assert outcome[0] == ERROR, (case.name, style, outcome)
                assert isinstance(outcome[2], expected[1]), (
                    case.name, style, outcome[2])


def test_whole_and_chunked_sequences_agree(backend):
    """Whole-read and chunked reads yield the same events or error category."""
    for case in MATRIX_CASES:
        whole = _run(backend, case, WHOLE)
        for chunk_size in CHUNK_SIZES:
            chunked = _run(backend, case, CHUNKED, chunk_size=chunk_size)
            assert _outcome_category(chunked) == _outcome_category(whole), (
                case.name, chunk_size, whole, chunked
            )
            if whole[0] == EVENTS:
                assert chunked[1] == whole[1], (case.name, chunk_size)
