# -*- coding: utf-8 -*-
from __future__ import absolute_import
import collections
import unittest
from decimal import Decimal

import simplejson
from simplejson import dumps_canonical, dump_canonical
from simplejson.compat import PY3, StringIO
from simplejson.raw_json import RawJSON


class TestCanonical(unittest.TestCase):
    def test_scalars(self):
        self.assertEqual(dumps_canonical(None), 'null')
        self.assertEqual(dumps_canonical(True), 'true')
        self.assertEqual(dumps_canonical(False), 'false')
        self.assertEqual(dumps_canonical(123), '123')
        self.assertEqual(dumps_canonical(-2 ** 100), str(-2 ** 100))
        self.assertEqual(dumps_canonical({}), '{}')
        self.assertEqual(dumps_canonical([]), '[]')

    def test_fixed_format(self):
        self.assertEqual(
            dumps_canonical({'a': [1, 2.5, 'x'], 'b': {'c': None}}),
            '{"a":[1,2.5,"x"],"b":{"c":null}}')

    def test_insertion_order_invariant(self):
        obj1 = {'x': 1, 'y': [1, 2], 'z': {'b': 2, 'a': 1}}
        obj2 = {'z': {'a': 1, 'b': 2}, 'y': [1, 2], 'x': 1}
        self.assertEqual(dumps_canonical(obj1), '{"x":1,"y":[1,2],"z":{"a":1,"b":2}}')
        self.assertEqual(dumps_canonical(obj1), dumps_canonical(obj2))

    def test_key_sort_codepoint_order(self):
        self.assertEqual(
            dumps_canonical({'b': 1, 'A': 2, 'a': 3}),
            '{"A":2,"a":3,"b":1}')
        # Sorting follows the key's code points, not its escaped form:
        # 'a' (U+0061) sorts before U+007F even though the escape
        # sequence "\\u007f" would sort before "a".
        self.assertEqual(
            dumps_canonical({'a': 1, u'\x7f': 2}),
            '{"a":1,"\\u007f":2}')

    def test_speedups_toggle_invariant(self):
        obj = {
            'str': u'é\U0001d11e',
            'int': 2 ** 70,
            'float': [1.0, -0.0, 1e16, 1e-5, 0.1],
            'decimal': [Decimal('1.50'), Decimal('1E+2'), Decimal('-0.00')],
            'nested': {'b': (1, 2), 'a': [True, False, None]},
        }
        with_speedups = dumps_canonical(obj)
        simplejson._toggle_speedups(False)
        try:
            without_speedups = dumps_canonical(obj)
        finally:
            simplejson._toggle_speedups(True)
        self.assertEqual(with_speedups, without_speedups)

    def test_float_format(self):
        self.assertEqual(dumps_canonical(1.0), '1.0')
        self.assertEqual(dumps_canonical(0.0), '0.0')
        self.assertEqual(dumps_canonical(-0.0), '-0.0')
        self.assertEqual(dumps_canonical(1.5), '1.5')
        self.assertEqual(dumps_canonical(1e16), '1e+16')
        self.assertEqual(dumps_canonical(1e-5), '1e-05')
        self.assertEqual(dumps_canonical(-1.25e-8), '-1.25e-08')

    def test_nonfinite_float_rejected(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            self.assertRaises(ValueError, dumps_canonical, value)
            self.assertRaises(ValueError, dumps_canonical, [value])
            self.assertRaises(ValueError, dumps_canonical, {'k': value})

    def test_decimal_trailing_zeros(self):
        self.assertEqual(dumps_canonical(Decimal('1.50')), '1.5')
        self.assertEqual(dumps_canonical(Decimal('1.500')), '1.5')
        self.assertEqual(dumps_canonical(Decimal('15E-1')), '1.5')
        self.assertEqual(dumps_canonical(Decimal('1E+2')), '100')
        self.assertEqual(dumps_canonical(Decimal('100')), '100')
        self.assertEqual(dumps_canonical(Decimal('0.0012300')), '0.00123')
        self.assertEqual(dumps_canonical(Decimal('-2.75')), '-2.75')
        self.assertEqual(dumps_canonical(Decimal('0.00')), '0')
        self.assertEqual(dumps_canonical(Decimal('-0.00')), '-0')
        self.assertEqual(dumps_canonical(Decimal('0E-10')), '0')

    def test_decimal_equal_values_same_bytes(self):
        values = [Decimal('1.5'), Decimal('1.50'), Decimal('15E-1'),
                  Decimal('0.15e1'), Decimal('1.50000')]
        self.assertEqual(set(dumps_canonical(v) for v in values), {'1.5'})

    def test_decimal_full_precision(self):
        # More significant digits than the default decimal context
        # precision (28): no rounding may occur.
        self.assertEqual(
            dumps_canonical(Decimal('1.' + '0' * 40 + '1')),
            '1.' + '0' * 40 + '1')
        self.assertEqual(
            dumps_canonical(Decimal('123456789012345678901234567890.5')),
            '123456789012345678901234567890.5')

    def test_nonfinite_decimal_rejected(self):
        for text in ('NaN', 'sNaN', 'Infinity', '-Infinity'):
            self.assertRaises(ValueError, dumps_canonical, Decimal(text))

    def test_string_escaping(self):
        self.assertEqual(dumps_canonical(u'é'), '"\\u00e9"')
        self.assertEqual(dumps_canonical(u'\U0001d11e'), '"\\ud834\\udd1e"')
        self.assertEqual(dumps_canonical('a"b\\c\n'), '"a\\"b\\\\c\\n"')
        self.assertEqual(dumps_canonical('\x01'), '"\\u0001"')
        self.assertEqual(dumps_canonical('\t'), '"\\t"')

    def test_tuple_and_namedtuple_as_array(self):
        self.assertEqual(dumps_canonical((1, 2)), '[1,2]')
        point = collections.namedtuple('Point', 'x y')(1, 2)
        self.assertEqual(dumps_canonical(point), '[1,2]')

    def test_unsupported_types_rejected(self):
        self.assertRaises(TypeError, dumps_canonical, {1: 'x'})
        self.assertRaises(TypeError, dumps_canonical, {None: 'x'})
        self.assertRaises(TypeError, dumps_canonical, set([1]))
        self.assertRaises(TypeError, dumps_canonical, object())
        self.assertRaises(TypeError, dumps_canonical, RawJSON('{}'))

    def test_bytes_rejected(self):
        if PY3:
            self.assertRaises(TypeError, dumps_canonical, b'abc')
            self.assertRaises(TypeError, dumps_canonical, {b'k': 1})

    def test_circular_reference(self):
        dct = {}
        dct['self'] = dct
        self.assertRaises(ValueError, dumps_canonical, dct)
        lst = []
        lst.append(lst)
        self.assertRaises(ValueError, dumps_canonical, lst)

    def test_dump_to_fp(self):
        fp = StringIO()
        dump_canonical({'b': 1.0, 'a': 2}, fp)
        self.assertEqual(fp.getvalue(), '{"a":2,"b":1.0}')

    def test_dumps_unchanged(self):
        # The regular dumps entry point still lets callers choose the
        # format; canonicalization does not leak into it.
        self.assertEqual(simplejson.dumps({'b': 1, 'a': 2}), '{"b": 1, "a": 2}')
        self.assertEqual(simplejson.dumps(Decimal('1.50')), '1.50')


if __name__ == '__main__':
    unittest.main()
