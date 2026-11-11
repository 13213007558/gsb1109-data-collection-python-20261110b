# -*- coding: utf-8 -*-
from __future__ import absolute_import

from decimal import Decimal
from unittest import TestCase

import simplejson as json
from simplejson.decoder import JSONDecoder
from simplejson.errors import JSONDecodeError


class TestIterDecode(TestCase):
    def test_basic_consecutive_values_without_separators(self):
        # Diagnostic files can concatenate short responses with no newline
        # between them; every top-level value kind must still be readable.
        s = '1"two"[3]{"x":4}truefalsenull'
        it = json.iter_decode(s)
        self.assertEqual(next(it), 1)
        self.assertEqual(next(it), 'two')
        self.assertEqual(next(it), [3])
        self.assertEqual(next(it), {'x': 4})
        self.assertEqual(next(it), True)
        self.assertEqual(next(it), False)
        self.assertEqual(next(it), None)
        self.assertRaises(StopIteration, next, it)
        self.assertEqual(it.last_end, len(s))

    def test_whitespace_separated_values(self):
        s = '  1\t\n"two"\r\n  [3] \n '
        self.assertEqual(list(json.iter_decode(s)), [1, 'two', [3]])
        self.assertEqual(json.iter_decode(s).last_end, 0)

    def test_stop_at_third_value_and_resume(self):
        # The headline use case: stop on the third complete value and
        # learn exactly where scanning would resume.
        s = '{"a": 1} "b" [1, 2, 3] "tail"'
        it = json.iter_decode(s)
        seen = []
        for value in it:
            seen.append(value)
            if len(seen) == 3:
                break
        self.assertEqual(seen, [{'a': 1}, 'b', [1, 2, 3]])
        self.assertEqual(s[it.last_end:], ' "tail"')

        # A fresh iterator resumes at the precise recorded position.
        self.assertEqual(list(json.iter_decode(s, it.last_end)), ['tail'])
        # raw_decode agrees on what starts there.
        obj, end = JSONDecoder().raw_decode(s, it.last_end)
        self.assertEqual(obj, 'tail')
        self.assertEqual(end, len(s))

    def test_last_end_tracks_each_value(self):
        s = '1 "two" [3]'
        it = json.iter_decode(s)
        self.assertEqual(it.last_end, 0)
        next(it)
        self.assertEqual(it.last_end, 1)
        next(it)
        self.assertEqual(s[:it.last_end], '1 "two"')
        next(it)
        self.assertEqual(it.last_end, len(s))

    def test_top_level_strings_arrays_objects(self):
        s = '"a\\"b"[]{}[1, {"k": "v"}]'
        self.assertEqual(
            list(json.iter_decode(s)),
            ['a"b', [], {}, [1, {'k': 'v'}]])

    def test_adjacent_digits_are_one_number(self):
        # A run of digits with no separator is one lexical number and
        # must never be cut into several values.
        self.assertEqual(list(json.iter_decode('12345')), [12345])
        self.assertEqual(list(json.iter_decode('1.5e3')), [1500.0])
        self.assertEqual(list(json.iter_decode('-42')), [-42])
        # Separated numbers are of course distinct values.
        self.assertEqual(list(json.iter_decode('12 34')), [12, 34])
        # A number directly followed by another kind of value splits at
        # the lexical boundary.
        self.assertEqual(list(json.iter_decode('12"x"')), [12, 'x'])

    def test_bad_tail_reports_where_it_starts(self):
        s = '1 2 bad'
        it = json.iter_decode(s)
        self.assertEqual(next(it), 1)
        self.assertEqual(next(it), 2)
        try:
            next(it)
        except JSONDecodeError as e:
            self.assertEqual(e.pos, 4)
            self.assertEqual(s[e.pos], 'b')
        else:
            self.fail('expected JSONDecodeError')
        # Values already delivered before the failure stay intact.
        self.assertEqual(it.last_end, 3)

    def test_bad_tail_inside_container_points_at_value_start(self):
        s = '1 [2, 3'
        it = json.iter_decode(s)
        self.assertEqual(next(it), 1)
        try:
            next(it)
        except JSONDecodeError as e:
            self.assertEqual(e.pos, 2)
            self.assertEqual(s[e.pos], '[')
            self.assertIsInstance(e.__cause__, JSONDecodeError)
        else:
            self.fail('expected JSONDecodeError')
        self.assertEqual(it.last_end, 1)

    def test_unterminated_string_position(self):
        s = '1 "abc'
        it = json.iter_decode(s)
        self.assertEqual(next(it), 1)
        try:
            next(it)
        except JSONDecodeError as e:
            self.assertEqual(e.pos, 2)
            self.assertEqual(s[e.pos], '"')
        else:
            self.fail('expected JSONDecodeError')

    def test_second_number_with_lexical_break(self):
        # '1.5' is a complete float; the following '.6' cannot start a
        # value, so the error position is the start of the bad tail.
        s = '1.5.6'
        it = json.iter_decode(s)
        self.assertEqual(next(it), 1.5)
        try:
            next(it)
        except JSONDecodeError as e:
            self.assertEqual(e.pos, 3)
            self.assertEqual(e.doc[e.pos], '.')
        else:
            self.fail('expected JSONDecodeError')

    def test_empty_and_whitespace_only_input(self):
        it = json.iter_decode('')
        self.assertEqual(list(it), [])
        self.assertEqual(it.last_end, 0)
        ws = '   \t\n\r'
        it = json.iter_decode(ws)
        self.assertEqual(list(it), [])
        # No value was produced, so last_end stays at the start index;
        # the unconsumed remainder is whitespace-only.
        self.assertEqual(it.last_end, 0)
        self.assertEqual(ws[it.last_end:].strip(), '')

    def test_trailing_whitespace_remains_in_remainder(self):
        s = '1 2   '
        it = json.iter_decode(s)
        self.assertEqual(list(it), [1, 2])
        # last_end points right after the last value, like raw_decode;
        # resuming from it skips the whitespace and finds nothing left.
        self.assertEqual(s[it.last_end:], '   ')
        self.assertEqual(list(json.iter_decode(s, it.last_end)), [])

    def test_idx_argument(self):
        s = 'xx 1 2'
        self.assertEqual(list(json.iter_decode(s, idx=2)), [1, 2])
        it = json.iter_decode(s, idx=2)
        self.assertEqual(it.last_end, 2)

    def test_negative_idx(self):
        self.assertRaises(
            JSONDecodeError, JSONDecoder().iter_decode, '1', -1)

    def test_bytes_input(self):
        it = json.iter_decode(b'1 "two" [3]')
        self.assertEqual(list(it), [1, 'two', [3]])
        self.assertEqual(it.last_end, len(b'1 "two" [3]'))

    def test_non_text_input(self):
        self.assertRaises(TypeError, json.iter_decode, 123)

    def test_bom_is_skipped(self):
        bom = u'\ufeff'
        self.assertEqual(list(json.iter_decode(bom + '1 2')), [1, 2])
        self.assertEqual(list(json.iter_decode(bom + '  3')), [3])

    def test_decoder_options_are_honored(self):
        it = json.iter_decode('1.5 2.5', parse_float=Decimal)
        self.assertEqual(list(it), [Decimal('1.5'), Decimal('2.5')])

        it = json.iter_decode(
            '{"a": 1} [1, 2]',
            object_pairs_hook=list, array_hook=tuple)
        self.assertEqual(next(it), [('a', 1)])
        self.assertEqual(next(it), (1, 2))

    def test_allow_nan_constants(self):
        it = json.iter_decode('NaN Infinity -Infinity', allow_nan=True)
        values = list(it)
        self.assertTrue(values[0] != values[0])
        self.assertEqual(values[1], float('inf'))
        self.assertEqual(values[2], float('-inf'))

    def test_iterator_protocol(self):
        it = json.iter_decode('1 2')
        self.assertIs(iter(it), it)
        self.assertEqual(next(it), 1)
        self.assertEqual(next(it), 2)
        self.assertRaises(StopIteration, next, it)
        # Stays exhausted on further calls.
        self.assertRaises(StopIteration, next, it)

    def test_method_and_module_function_share_behavior(self):
        s = '1 "x" {'
        for build in (
            lambda: json.iter_decode(s),
            lambda: JSONDecoder().iter_decode(s),
        ):
            it = build()
            self.assertEqual(next(it), 1)
            self.assertEqual(next(it), 'x')
            self.assertRaises(JSONDecodeError, next, it)
