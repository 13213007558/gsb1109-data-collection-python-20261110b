# -*- coding: utf-8 -*-
import sys
import unittest
from unittest import TestCase

import simplejson as json
from simplejson.compat import StringIO

skipif_pre_311 = unittest.skipIf(
    sys.version_info < (3, 11), 'exception notes require Python 3.11+')


class DateObj(object):
    """A non-JSON-native value that serializes differently depending on
    the field it is stored under: ``display`` wants the original text,
    ``normalized`` wants the ISO date."""

    def __init__(self, text, iso):
        self.text = text
        self.iso = iso


def date_transform(obj, path):
    if isinstance(obj, DateObj):
        field = path[-1] if path else None
        if field == 'display':
            return obj.text
        if field == 'normalized':
            return obj.iso
        return {'display': obj.text, 'normalized': obj.iso}
    raise TypeError(
        'Object of type %s is not JSON serializable' %
        type(obj).__name__)


class TestTransform(TestCase):
    def test_root_and_nested_paths(self):
        seen = []

        def transform(obj, path):
            seen.append((type(obj).__name__, path))
            return None

        json.dumps({'a': [1, DateObj('t', 'i')], 'b': DateObj('t', 'i')},
                   transform=transform)
        self.assertIn(('DateObj', ('a', 1)), seen)
        self.assertIn(('DateObj', ('b',)), seen)

    def test_root_path_is_empty_tuple(self):
        seen = []
        json.dumps(DateObj('t', 'i'),
                   transform=lambda o, p: (seen.append(p), 1)[1])
        self.assertEqual(seen, [()])

    def test_display_vs_normalized_use_case(self):
        record = {
            'display': DateObj('2026年11月11日', '2026-11-11'),
            'normalized': DateObj('2026年11月11日', '2026-11-11'),
        }
        self.assertEqual(
            json.dumps(record, transform=date_transform, sort_keys=True),
            '{"display": "2026\\u5e7411\\u670811\\u65e5", '
            '"normalized": "2026-11-11"}')

    def test_same_object_different_paths_not_memoized(self):
        shared = DateObj('text-x', 'iso-x')
        doc = {'a': {'display': shared}, 'b': {'normalized': shared}}
        self.assertEqual(
            json.dumps(doc, transform=date_transform, sort_keys=True),
            '{"a": {"display": "text-x"}, '
            '"b": {"normalized": "iso-x"}}')

    def test_transform_result_can_need_transform(self):
        def transform(obj, path):
            if isinstance(obj, DateObj):
                # Stop after one level of wrapping so the chain terminates.
                if len(path) >= 2:
                    return obj.iso
                return ['nested', DateObj(obj.text, obj.iso)]
            raise TypeError('not serializable')

        self.assertEqual(
            json.dumps([DateObj('t', 'i')], transform=transform),
            '[["nested", "i"]]')

    def test_transform_replaces_default(self):
        calls = []

        def default(obj):
            calls.append(('default', obj))
            return 'default'

        def transform(obj, path):
            calls.append(('transform', path))
            return 'transform'

        out = json.dumps(DateObj('t', 'i'), default=default,
                         transform=transform)
        self.assertEqual(out, '"transform"')
        self.assertEqual(calls, [('transform', ())])

    def test_default_signature_unchanged_without_transform(self):
        def default(obj):
            self.assertIsInstance(obj, DateObj)
            return obj.iso

        self.assertEqual(
            json.dumps(DateObj('t', 'i'), default=default), '"i"')

    def test_transform_must_be_callable(self):
        self.assertRaises(TypeError, json.JSONEncoder, transform=42)
        self.assertRaises(TypeError, json.dumps, DateObj('t', 'i'),
                          transform=42)

    def test_transform_propagates_typeerror(self):
        def transform(obj, path):
            raise TypeError('boom')

        self.assertRaises(TypeError, json.dumps, DateObj('t', 'i'),
                          transform=transform)

    def test_namedtuple_path_threading(self):
        from collections import namedtuple
        Point = namedtuple('Point', ['when'])
        seen = []

        def transform(obj, path):
            if isinstance(obj, DateObj):
                seen.append(path)
                return obj.iso
            raise TypeError('not serializable')

        json.dumps(Point(DateObj('t', 'i')), transform=transform)
        self.assertEqual(seen, [('when',)])

    def test_int_key_path_uses_stringified_key(self):
        seen = []
        json.dumps({1: DateObj('t', 'i')},
                   transform=lambda o, p: (seen.append(p), None)[1])
        # Both the C and pure-Python encoders hand over the stringified key.
        self.assertEqual(seen, [('1',)])

    def test_dump_accepts_transform(self):
        fp = StringIO()
        json.dump(DateObj('t', 'i'), fp, transform=date_transform)
        self.assertEqual(fp.getvalue(),
                         '{"display": "t", "normalized": "i"}')

    def test_encoder_exposes_transform(self):
        transform = lambda o, p: None
        enc = json.JSONEncoder(transform=transform)
        self.assertIs(enc.transform, transform)

    def test_subclass_transform_method(self):
        # A subclass can override transform() like .default(); the
        # class-level default is None so the base encode still works.
        class MyEncoder(json.JSONEncoder):
            def transform(self, obj, path):
                if isinstance(obj, DateObj):
                    return obj.iso if path and path[-1] == 'normalized' \
                        else obj.text
                raise TypeError('not serializable')

        record = {'display': DateObj('t', 'i'),
                  'normalized': DateObj('t', 'i')}
        self.assertEqual(
            MyEncoder().encode(record),
            '{"display": "t", "normalized": "i"}')
        self.assertIsNone(json.JSONEncoder().transform)

    @skipif_pre_311
    def test_cycle_through_transform_is_located(self):
        d = {}

        def transform(obj, path):
            if isinstance(obj, DateObj):
                # Pointing the replacement back at a container already on
                # the markers stack must raise, not loop forever.
                return d
            raise TypeError('not serializable')

        d['k'] = [1, DateObj('t', 'i')]
        with self.assertRaises(ValueError) as cm:
            json.dumps(d, transform=transform)
        self.assertEqual(str(cm.exception), 'Circular reference detected')
        notes = cm.exception.__notes__
        location_notes = [n for n in notes if n.startswith(
            'Circular reference detected at path ')]
        self.assertEqual(len(location_notes), 1)
        self.assertIn('.k[1]', location_notes[0])

    @skipif_pre_311
    def test_container_cycle_with_transform_is_located(self):
        cyc = {}
        cyc['a'] = []
        cyc['a'].append(cyc)
        with self.assertRaises(ValueError) as cm:
            json.dumps(cyc, transform=date_transform)
        self.assertEqual(str(cm.exception), 'Circular reference detected')
        notes = cm.exception.__notes__
        location_notes = [n for n in notes if n.startswith(
            'Circular reference detected at path ')]
        self.assertEqual(len(location_notes), 1)
        self.assertIn('.a[0]', location_notes[0])

    def test_cycle_without_transform_keeps_bare_message(self):
        cyc = {}
        cyc['a'] = cyc
        with self.assertRaises(ValueError) as cm:
            json.dumps(cyc)
        self.assertEqual(str(cm.exception), 'Circular reference detected')
        if sys.version_info >= (3, 11):
            self.assertFalse(
                any('at path' in n for n in cm.exception.__notes__))

