# -*- coding: utf-8 -*-
from unittest import TestCase

import simplejson as json
import simplejson.decoder
from simplejson.compat import StringIO

LONE_HIGH = u'"\\ud800"'
LONE_LOW = u'"\\udc00"'
VALID_PAIR = u'"\\ud834\\udd20"'
COMBINED = u'\U0001d120'
REPLACEMENT = u'\ufffd'


class TestLoneSurrogates(TestCase):
    def test_default_passes_lone_surrogates_through(self):
        # Historical behavior is unchanged when the option is not used.
        self.assertEqual(json.loads(LONE_HIGH), u'\ud800')
        self.assertEqual(json.loads(LONE_LOW), u'\udc00')
        self.assertEqual(json.loads(VALID_PAIR), COMBINED)
        self.assertEqual(json.loads(u'{"\\ud800": 1}'), {u'\ud800': 1})

    def test_explicit_pass_mode(self):
        self.assertEqual(
            json.loads(LONE_HIGH, lone_surrogates='pass'), u'\ud800')

    def test_reject_value(self):
        for doc in (LONE_HIGH, LONE_LOW):
            with self.assertRaises(json.JSONDecodeError) as cm:
                json.loads(doc, lone_surrogates='reject')
            self.assertEqual(cm.exception.pos, 1)
            self.assertIn('Lone surrogate', str(cm.exception))

    def test_reject_key(self):
        with self.assertRaises(json.JSONDecodeError) as cm:
            json.loads(u'{"\\ud800": 1}', lone_surrogates='reject')
        self.assertEqual(cm.exception.pos, 2)

    def test_reject_nested_position(self):
        with self.assertRaises(json.JSONDecodeError) as cm:
            json.loads(u'{"a": [1, "\\udc00"]}', lone_surrogates='reject')
        self.assertEqual(cm.exception.pos, 11)

    def test_reject_allows_valid_pairs(self):
        self.assertEqual(
            json.loads(VALID_PAIR, lone_surrogates='reject'), COMBINED)

    def test_reject_lone_high_before_valid_pair(self):
        # The first escape is a lone high surrogate even though a valid
        # pair follows it.
        with self.assertRaises(json.JSONDecodeError) as cm:
            json.loads(u'"\\ud834\\ud834\\udd20"', lone_surrogates='reject')
        self.assertEqual(cm.exception.pos, 1)

    def test_replace_value(self):
        self.assertEqual(
            json.loads(LONE_HIGH, lone_surrogates='replace'), REPLACEMENT)
        self.assertEqual(
            json.loads(u'"a\\ud800b"', lone_surrogates='replace'),
            u'a\ufffdb')
        self.assertEqual(
            json.loads(u'"\\udc00\\udc01\\udfff"', lone_surrogates='replace'),
            u'\ufffd\ufffd\ufffd')

    def test_replace_preserves_valid_pairs(self):
        self.assertEqual(
            json.loads(VALID_PAIR, lone_surrogates='replace'), COMBINED)
        # Lone high surrogate followed by a valid pair: only the first
        # escape is repaired.
        self.assertEqual(
            json.loads(u'"\\ud800\\ud834\\udd20"', lone_surrogates='replace'),
            u'\ufffd' + COMBINED)

    def test_replace_hook_reports_positions(self):
        repairs = []
        doc = u'["a\\ud800b", "\\udc00"]'
        result = json.loads(
            doc, lone_surrogates='replace',
            lone_surrogate_hook=lambda pos, cp: repairs.append((pos, cp)))
        self.assertEqual(result, [u'a\ufffdb', u'\ufffd'])
        # Positions point at the backslash of each \uXXXX escape in doc.
        self.assertEqual(repairs, [(3, 0xd800), (14, 0xdc00)])
        self.assertEqual(doc[3], u'\\')
        self.assertEqual(doc[14], u'\\')

    def test_literal_replacement_char_is_not_a_repair(self):
        repairs = []
        doc = u'"\\ufffd\ufffd\\ud834\\udd20"'
        result = json.loads(
            doc, lone_surrogates='replace',
            lone_surrogate_hook=lambda pos, cp: repairs.append((pos, cp)))
        self.assertEqual(result, u'\ufffd\ufffd' + COMBINED)
        self.assertEqual(repairs, [])

    def test_replace_keys(self):
        self.assertEqual(
            json.loads(u'{"\\ud800": 1}', lone_surrogates='replace'),
            {u'\ufffd': 1})

    def test_replace_key_collision_raises(self):
        # Two distinct lone surrogate escapes repair to the same key.
        with self.assertRaises(json.JSONDecodeError) as cm:
            json.loads(u'{"\\ud800": 1, "\\udc00": 2}',
                       lone_surrogates='replace')
        self.assertIn('Duplicate object key', str(cm.exception))

    def test_replace_key_collision_with_literal_replacement_char(self):
        # A literal U+FFFD key is not itself a repair, but a repaired key
        # colliding with it is still a conflict, in either order.
        for doc in (u'{"\\ufffd": 1, "\\ud800": 2}',
                    u'{"\\ud800": 1, "\\ufffd": 2}'):
            with self.assertRaises(json.JSONDecodeError) as cm:
                json.loads(doc, lone_surrogates='replace')
            self.assertIn('Duplicate object key', str(cm.exception))

    def test_replace_natural_duplicate_keys_still_last_wins(self):
        # No repair involved: historical last-wins behavior is kept.
        self.assertEqual(
            json.loads(u'{"a": 1, "a": 2}', lone_surrogates='replace'),
            {u'a': 2})

    def test_replace_collision_check_scopes_per_object(self):
        # The same repaired key in different objects is not a conflict.
        result = json.loads(
            u'{"\\ud800": {"\\udc00": 1}, "b": {"\\ud800": 2}}',
            lone_surrogates='replace')
        self.assertEqual(result, {u'\ufffd': {u'\ufffd': 1}, u'b': {u'\ufffd': 2}})

    def test_pairs_hook_bypasses_collision_check(self):
        # With object_pairs_hook the pairs are delivered as-is.
        result = json.loads(
            u'{"\\ud800": 1, "\\udc00": 2}',
            lone_surrogates='replace',
            object_pairs_hook=lambda pairs: pairs)
        self.assertEqual(result, [(u'\ufffd', 1), (u'\ufffd', 2)])

    def test_object_hook_still_checks_collisions(self):
        seen = []
        def hook(dct):
            seen.append(dct)
            return dct
        with self.assertRaises(json.JSONDecodeError):
            json.loads(u'{"\\ud800": 1, "\\udc00": 2}',
                       lone_surrogates='replace', object_hook=hook)
        self.assertEqual(seen, [])

    def test_hook_not_called_in_other_modes(self):
        calls = []
        json.loads(LONE_HIGH, lone_surrogates='pass',
                   lone_surrogate_hook=lambda p, c: calls.append(p))
        self.assertEqual(calls, [])
        with self.assertRaises(json.JSONDecodeError):
            json.loads(LONE_HIGH, lone_surrogates='reject',
                       lone_surrogate_hook=lambda p, c: calls.append(p))
        self.assertEqual(calls, [])

    def test_invalid_mode_rejected(self):
        for mode in ('bogus', '', 0, True):
            with self.assertRaises(ValueError):
                json.JSONDecoder(lone_surrogates=mode)
            with self.assertRaises(ValueError):
                json.loads(LONE_HIGH, lone_surrogates=mode)

    def test_invalid_hook_rejected(self):
        with self.assertRaises(TypeError):
            json.JSONDecoder(lone_surrogate_hook='not callable')

    def test_decoder_instance_reusable(self):
        dec = json.JSONDecoder(lone_surrogates='replace')
        self.assertEqual(dec.lone_surrogates, 'replace')
        self.assertEqual(dec.decode(LONE_HIGH), REPLACEMENT)
        self.assertEqual(dec.decode(u'"ok"'), u'ok')
        self.assertEqual(json.JSONDecoder().lone_surrogates, 'pass')

    def test_load_file_like(self):
        self.assertEqual(
            json.load(StringIO(LONE_HIGH), lone_surrogates='replace'),
            REPLACEMENT)

    def test_scanstring_backends(self):
        # py_scanstring and c_scanstring accept the same optional
        # lone_surrogates / lone_surrogate_hook arguments.
        scanstrings = [simplejson.decoder.py_scanstring]
        if simplejson.decoder.c_scanstring is not None:
            scanstrings.append(simplejson.decoder.c_scanstring)
        for scanstring in scanstrings:
            self.assertEqual(
                scanstring(LONE_HIGH, 1, None, True),
                (u'\ud800', 8))
            self.assertEqual(
                scanstring(LONE_HIGH, 1, None, True, 'pass'),
                (u'\ud800', 8))
            self.assertEqual(
                scanstring(LONE_HIGH, 1, None, True, 'replace'),
                (REPLACEMENT, 8))
            self.assertEqual(
                scanstring(VALID_PAIR, 1, None, True, 'replace'),
                (COMBINED, 14))
            repairs = []
            self.assertEqual(
                scanstring(LONE_LOW, 1, None, True, 'replace',
                           lambda p, c: repairs.append((p, c))),
                (REPLACEMENT, 8))
            self.assertEqual(repairs, [(1, 0xdc00)])
            with self.assertRaises(json.JSONDecodeError) as cm:
                scanstring(LONE_HIGH, 1, None, True, 'reject')
            self.assertEqual(cm.exception.pos, 1)
            with self.assertRaises(ValueError):
                scanstring(LONE_HIGH, 1, None, True, 'bogus')

    def test_old_call_signature_still_works(self):
        # Positional callers predating the option are unaffected.
        self.assertEqual(
            simplejson.decoder.py_scanstring(LONE_HIGH, 1, None, True),
            (u'\ud800', 8))
        if simplejson.decoder.c_scanstring is not None:
            self.assertEqual(
                simplejson.decoder.c_scanstring(LONE_HIGH, 1, None, True),
                (u'\ud800', 8))
