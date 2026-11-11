from __future__ import absolute_import
import pickle
import sys
from unittest import TestCase

import simplejson as json
from simplejson.compat import StringIO
from simplejson.tests._helpers import skip_if_speedups_missing


def nested(depth, leaf='1'):
    return '[' * depth + leaf + ']' * depth


class TestNestingDepth(TestCase):
    def test_default_decoder_is_unlimited(self):
        decoder = json.JSONDecoder()
        self.assertIsNone(decoder.max_nesting_depth)
        # Ordinary decoding behavior is unchanged when no limit is given.
        self.assertEqual(json.loads(nested(3)), [[[1]]])

    def test_explicit_none_is_unlimited(self):
        decoder = json.JSONDecoder(max_nesting_depth=None)
        self.assertIsNone(decoder.max_nesting_depth)
        self.assertEqual(decoder.decode(nested(3)), [[[1]]])

    def test_depth_at_limit_is_accepted(self):
        self.assertEqual(json.loads(nested(1), max_nesting_depth=1), [1])
        self.assertEqual(
            json.loads(nested(5), max_nesting_depth=5),
            [[[[[1]]]]])

    def test_depth_beyond_limit_is_rejected(self):
        # nested(2) is '[[1]]'; the inner '[' at index 1 is depth 2.
        with self.assertRaises(json.JSONNestedError) as cm:
            json.loads(nested(2), max_nesting_depth=1)
        err = cm.exception
        self.assertEqual(err.depth, 2)
        self.assertEqual(err.limit, 1)
        self.assertEqual(err.pos, 1)
        self.assertEqual(err.doc[err.pos], '[')
        self.assertEqual(err.lineno, 1)
        self.assertEqual(err.colno, 2)
        self.assertEqual(
            err.msg,
            'Nesting level 2 exceeds the maximum allowed nesting depth of 1')
        self.assertIn('line 1 column 2 (char 1)', str(err))

    def test_error_is_value_error_and_picklable(self):
        captured = None
        try:
            json.loads(nested(2), max_nesting_depth=1)
        except json.JSONNestedError as err:
            captured = err
        self.assertIsNotNone(captured)
        self.assertIsInstance(captured, json.JSONDecodeError)
        self.assertIsInstance(captured, ValueError)
        restored = pickle.loads(pickle.dumps(captured))
        self.assertEqual(type(restored), json.JSONNestedError)
        self.assertEqual(restored.msg, captured.msg)
        self.assertEqual(restored.pos, captured.pos)
        self.assertEqual(restored.depth, captured.depth)
        self.assertEqual(restored.limit, captured.limit)

    def test_objects_and_arrays_share_one_counter(self):
        # '[' is depth 1, '{' is depth 2 -> rejected with a limit of 1.
        with self.assertRaises(json.JSONNestedError) as cm:
            json.loads('[{"a": 1}]', max_nesting_depth=1)
        err = cm.exception
        self.assertEqual((err.pos, err.depth, err.limit), (1, 2, 1))
        self.assertEqual(err.doc[err.pos], '{')

        # Depth 2 object nested in a depth 1 object is still allowed at 2.
        self.assertEqual(
            json.loads('{"a": {"b": 1}}', max_nesting_depth=2),
            {"a": {"b": 1}})

        # A third level, regardless of which bracket opens it, is rejected.
        with self.assertRaises(json.JSONNestedError) as cm:
            json.loads('{"a": {"b": {"c": 1}}}', max_nesting_depth=2)
        err = cm.exception
        self.assertEqual((err.pos, err.depth, err.limit), (12, 3, 2))

    def test_brackets_inside_strings_do_not_count(self):
        doc = '["[[[[", {"k": "]}]}"}]'
        self.assertEqual(
            json.loads(doc, max_nesting_depth=2),
            ["[[[[", {"k": "]}]}"}])
        # Escaped quotes must not let a string swallow structural brackets.
        self.assertEqual(
            json.loads('["\\"[[[", {"k": "x"}]', max_nesting_depth=2),
            ['"[[[', {"k": "x"}])

    def test_scalars_and_empty_containers_are_unaffected(self):
        self.assertEqual(json.loads('42', max_nesting_depth=1), 42)
        self.assertEqual(json.loads('"[[["', max_nesting_depth=1), '[[[')
        self.assertEqual(json.loads('true', max_nesting_depth=1), True)
        self.assertEqual(json.loads('null', max_nesting_depth=1), None)
        self.assertEqual(json.loads('[]', max_nesting_depth=1), [])
        self.assertEqual(json.loads('{}', max_nesting_depth=1), {})

    def test_position_points_at_offending_bracket(self):
        with self.assertRaises(json.JSONNestedError) as cm:
            json.loads('  [[]]', max_nesting_depth=1)
        err = cm.exception
        # The outer array (depth 1) is allowed; the inner bracket at
        # index 3 opens the forbidden depth-2 container.
        self.assertEqual((err.pos, err.depth), (3, 2))
        self.assertEqual(err.doc[err.pos], '[')

    def test_multiline_position(self):
        doc = '[\n  [\n    [1]\n  ]\n]'
        with self.assertRaises(json.JSONNestedError) as cm:
            json.loads(doc, max_nesting_depth=2)
        err = cm.exception
        self.assertEqual((err.pos, err.depth), (10, 3))
        self.assertEqual((err.lineno, err.colno), (3, 5))

    def test_invalid_limits_raise_type_error(self):
        for bad in (0, -1, 1.5, '3', False, True):
            with self.assertRaises(TypeError):
                json.JSONDecoder(max_nesting_depth=bad)
            with self.assertRaises(TypeError):
                json.loads(nested(1), max_nesting_depth=bad)

    def test_load_kwarg_and_file_like_object(self):
        with self.assertRaises(json.JSONNestedError) as cm:
            json.load(StringIO(nested(2)), max_nesting_depth=1)
        self.assertEqual((cm.exception.pos, cm.exception.depth), (1, 2))

    def test_raw_decode_reports_nesting_error(self):
        decoder = json.JSONDecoder(max_nesting_depth=1)
        with self.assertRaises(json.JSONNestedError) as cm:
            decoder.raw_decode(' [[]] ')
        self.assertEqual(
            (cm.exception.pos, cm.exception.depth, cm.exception.limit),
            (2, 2, 1))

    def test_depth_state_resets_between_decodes(self):
        decoder = json.JSONDecoder(max_nesting_depth=2)
        for _ in range(10):
            self.assertEqual(decoder.decode('[1]'), [1])
        # A failed decode must not leave the depth counter poisoned either.
        for _ in range(3):
            with self.assertRaises(json.JSONNestedError):
                decoder.decode(nested(3))
        self.assertEqual(decoder.decode('[1]'), [1])

    def test_process_recursion_limit_is_untouched(self):
        before = sys.getrecursionlimit()
        json.loads(nested(5), max_nesting_depth=5)
        json.loads(nested(10), max_nesting_depth=20)
        with self.assertRaises(json.JSONNestedError):
            json.loads(nested(10), max_nesting_depth=3)
        self.assertEqual(sys.getrecursionlimit(), before)

    @skip_if_speedups_missing
    def test_deep_legal_input_through_c_scanner(self):
        # Only the C extension has a stack deep enough for thousands of
        # levels; this verifies the limit does not artificially cap legal
        # input and that a configured limit still trips at the right depth
        # (well before the interpreter's own recursion guard).
        deep = nested(2000)
        self.assertIsNotNone(json.loads(deep))
        with self.assertRaises(json.JSONNestedError) as cm:
            json.loads(deep, max_nesting_depth=1000)
        self.assertEqual(
            (cm.exception.pos, cm.exception.depth, cm.exception.limit),
            (1000, 1001, 1000))
