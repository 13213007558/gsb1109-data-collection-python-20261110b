# -*- coding: utf-8 -*-
"""Round 3: default()/for_json interactions, error notes, bytes input,
check_circular, namedtuple, iterable_as_array."""
import sys
import collections
import simplejson


def run_case(fn):
    try:
        return ('ok', fn())
    except Exception as e:
        notes = getattr(e, '__notes__', None)
        return ('err', '%s: %s notes=%r' % (type(e).__name__, e, notes))


class WithForJson(object):
    def for_json(self): return {'via': 'for_json'}

class BadForJson(object):
    def for_json(self): raise RuntimeError('for_json boom')

def bad_default(o): raise RuntimeError('default boom')

NT = collections.namedtuple('NT', ['x'])

CASES = []
def case(name, fn):
    CASES.append((name, fn))

case('default raises', lambda: simplejson.dumps(object(), default=bad_default))
case('default raises in list',
     lambda: simplejson.dumps([1, object()], default=bad_default))
case('default raises in dict val',
     lambda: simplejson.dumps({'k': object()}, default=bad_default))
case('for_json raises', lambda: simplejson.dumps(BadForJson(), for_json=True))
case('for_json wins over default',
     lambda: simplejson.dumps(WithForJson(), for_json=True, default=bad_default))
case('default used when for_json=False',
     lambda: simplejson.dumps(WithForJson(), for_json=False, default=lambda o: 'D'))
case('default returns dict w/ circular',
     lambda: simplejson.dumps(object(), default=lambda o: _circd()))
def _circd():
    d = {}
    d['self'] = d
    return d
case('check_circular=False circular',
     lambda: simplejson.dumps(_circl(), check_circular=False))
def _circl():
    x = []
    x.append(x)
    return x
case('namedtuple default', lambda: simplejson.dumps(NT(1)))
case('namedtuple as object', lambda: simplejson.dumps(NT(1), namedtuple_as_object=True))
case('namedtuple off', lambda: simplejson.dumps(NT(1), namedtuple_as_object=False))
case('iterable_as_array gen',
     lambda: simplejson.dumps(iter([1, 2]), iterable_as_array=True))
case('iterable_as_array off gen', lambda: simplejson.dumps(iter([1, 2])))
case('bytes input ascii', lambda: simplejson.loads(b'[1, 2]'))
case('bytes input utf8 𐀀', lambda: repr(simplejson.loads('"𐀀"'.encode('utf-8'))))
case('bytes input raw surrogate utf8',
     lambda: simplejson.loads(b'"\xed\xa0\x80"'))
case('bytes input bad utf8', lambda: simplejson.loads(b'"\xff"'))
case('BOM utf8', lambda: simplejson.loads(b'\xef\xbb\xbf"1"'))
case('dec strict ctrl', lambda: simplejson.loads('"a\x01b"'))
case('dec strict=False ctrl', lambda: repr(simplejson.loads('"a\x01b"', strict=False)))
case('enc tuple', lambda: simplejson.dumps((1, 2)))
case('enc tuple_as_array=False',
     lambda: simplejson.dumps((1, 2), tuple_as_array=False))

results = {}
for enabled in (True, False):
    simplejson._toggle_speedups(enabled)
    tag = 'C ' if enabled else 'PY'
    for name, fn in CASES:
        results.setdefault(name, {})[tag] = run_case(fn)

simplejson._toggle_speedups(True)
width = max(len(n) for n, _ in CASES)
ndiff = 0
for name, fn in CASES:
    c, py = results[name]['C '], results[name]['PY']
    same = c == py
    if not same:
        ndiff += 1
    print('%s%-*s\n  C =%r\n  PY=%r' % ('SAME ' if same else 'DIFF ', width, name, c, py))
print('\n%d/%d cases differ' % (ndiff, len(CASES)))
