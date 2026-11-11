# -*- coding: utf-8 -*-
r"""Canonical JSON serialization with a fixed, fully specified format.

This module is the canonical output entry point of :mod:`simplejson`.
:func:`dumps` produces byte-identical output for equivalent input so
that two machines can independently compute the same digest of the
same record. The output does not depend on dict insertion order, on
whether the optional C speedups extension is active, or on the host
platform. Unlike :func:`simplejson.dumps`, the format is fixed: there
are no options to choose.

The canonical form is defined as follows.

Supported values (anything else raises :exc:`TypeError`):

* ``None``, ``True``, ``False``
* :class:`int` (any size)
* :class:`float`, finite only
* :class:`decimal.Decimal`, finite only
* :class:`str`
* :class:`list` and :class:`tuple` (including subclasses, encoded as
  JSON arrays)
* :class:`dict` with :class:`str` keys

Out-of-range floats (``nan``, ``inf``, ``-inf``) and non-finite
Decimals raise :exc:`ValueError`.

Output rules:

* Objects are written as ``{"key":value,...}`` with members sorted by
  key in Unicode code point order. Keys must be strings.
* Arrays are written as ``[value,...]``.
* No whitespace appears anywhere in the output.
* Strings are ASCII-only: ``"``, ``\`` and control characters use the
  short escapes ``\"``, ``\\``, ``\b``, ``\f``, ``\n``, ``\r``, ``\t``
  where defined and ``\uXXXX`` (lowercase hex) otherwise; every
  non-ASCII character is escaped as ``\uXXXX``, with characters
  outside the Basic Multilingual Plane written as a surrogate pair.
* Integers are written in decimal with no leading zeros.
* Floats are written exactly as ``repr(float)``: the shortest string
  that round-trips. Integral floats keep a trailing ``.0`` (``1.0``);
  negative zero is written ``-0.0``; magnitudes that need an exponent
  use a lowercase ``e`` with an explicit sign and no leading zeros in
  the exponent (``1e+16``, ``1e-05``).
* Decimals are written in positional notation with no exponent and no
  trailing zeros: the coefficient's trailing zeros are stripped and an
  exponent input form is expanded, so numerically equal values always
  produce the same output (``Decimal('1.50')``, ``Decimal('1.5')`` and
  ``Decimal('15E-1')`` all serialize as ``1.5``). Zero is written
  ``0`` and negative zero ``-0``.

>>> dumps({'b': 1, 'a': 2})
'{"a":2,"b":1}'
>>> dumps({'b': 1, 'a': 2}) == dumps({'a': 2, 'b': 1})
True
>>> dumps([True, None, u'é'])
'[true,null,"\\u00e9"]'
>>> dumps([1.0, -0.0, 1e16, 1e-5])
'[1.0,-0.0,1e+16,1e-05]'
>>> from decimal import Decimal
>>> dumps([Decimal('1.50'), Decimal('15E-1'), Decimal('0.00')])
'[1.5,1.5,0]'
>>> dumps(Decimal('-0.00'))
'-0'
>>> dumps(float('nan'))
Traceback (most recent call last):
...
ValueError: Out of range float values are not JSON compliant: nan
>>> dumps(Decimal('Infinity'))
Traceback (most recent call last):
...
ValueError: Out of range Decimal values are not JSON compliant: Infinity
>>> dumps({1: 'one'})
Traceback (most recent call last):
...
TypeError: canonical JSON object keys must be str, not int
"""
from __future__ import absolute_import

import decimal

from .compat import PY3, string_types
from .encoder import JSONEncoder, _dict_types
from .raw_json import RawJSON

__all__ = ['dumps', 'dump']

# A single shared encoder with the fixed canonical configuration.
# JSONEncoder.iterencode reads the module-level c_make_encoder at call
# time, so this instance transparently uses the C speedups when they
# are enabled and the pure Python path otherwise; both paths produce
# identical bytes for the value set that reaches the encoder here
# (dicts with str keys, lists, str, int, finite floats, RawJSON).
_canonical_encoder = JSONEncoder(
    ensure_ascii=True,
    allow_nan=False,
    check_circular=True,
    sort_keys=True,
    separators=(',', ':'),
    use_decimal=False,
    namedtuple_as_object=False,
    tuple_as_array=True,
)


def _canonical_decimal(value):
    """Return the canonical JSON number string for a finite Decimal.

    Trailing zeros are stripped from the coefficient and the value is
    rendered in positional notation, so numerically equal Decimals
    always produce the same string. The sign of zero is preserved.
    """
    if not value.is_finite():
        raise ValueError(
            "Out of range Decimal values are not JSON compliant: " +
            str(value))
    sign, digits, exponent = value.as_tuple()
    # Strip trailing zero digits from the coefficient, moving their
    # weight into the exponent: 1.50 -> coefficient 15, exponent -1.
    last = len(digits)
    while last > 1 and digits[last - 1] == 0:
        last -= 1
    exponent += len(digits) - last
    digits = digits[:last]
    if digits == (0,):
        # Canonical zero keeps only the sign: 0 or -0.
        return '-0' if sign else '0'
    coefficient = ''.join([str(digit) for digit in digits])
    if exponent >= 0:
        text = coefficient + '0' * exponent
    else:
        point = len(coefficient) + exponent
        if point > 0:
            text = coefficient[:point] + '.' + coefficient[point:]
        else:
            text = '0.' + '0' * (-point) + coefficient
    if sign:
        text = '-' + text
    return text


def _transform(obj, markers):
    """Recursively rewrite *obj* into the plain value set the encoder
    emits canonically: dicts with str keys, lists, and RawJSON wrappers
    holding canonical Decimal strings. Values outside the promised
    domain are rejected here or left for the encoder to reject.
    """
    if isinstance(obj, _dict_types):
        markerid = id(obj)
        if markerid in markers:
            raise ValueError("Circular reference detected")
        markers[markerid] = obj
        try:
            result = {}
            for key, value in obj.items():
                if not isinstance(key, string_types):
                    raise TypeError(
                        'canonical JSON object keys must be str, '
                        'not %s' % (key.__class__.__name__,))
                result[key] = _transform(value, markers)
            return result
        finally:
            del markers[markerid]
    if isinstance(obj, (list, tuple)):
        markerid = id(obj)
        if markerid in markers:
            raise ValueError("Circular reference detected")
        markers[markerid] = obj
        try:
            return [_transform(value, markers) for value in obj]
        finally:
            del markers[markerid]
    if isinstance(obj, decimal.Decimal):
        return RawJSON(_canonical_decimal(obj))
    if isinstance(obj, RawJSON):
        raise TypeError(
            'Object of type RawJSON is not canonical JSON serializable')
    if PY3 and isinstance(obj, bytes):
        raise TypeError(
            'Object of type bytes is not canonical JSON serializable')
    return obj


def dumps(obj):
    """Serialize *obj* to a canonical JSON :class:`str`.

    The output format is fixed and fully specified by this module's
    documentation; there are no options. Equivalent inputs always
    produce byte-identical output, regardless of dict insertion order
    and of whether the C speedups extension is active. Values outside
    the documented domain raise :exc:`TypeError` or :exc:`ValueError`.
    """
    return _canonical_encoder.encode(_transform(obj, {}))


def dump(obj, fp):
    """Serialize *obj* as a canonical JSON stream to *fp* (a
    ``.write()``-supporting file-like object).

    The output is identical to :func:`dumps`; see this module's
    documentation for the format specification.
    """
    for chunk in _canonical_encoder.iterencode(_transform(obj, {})):
        fp.write(chunk)
