# -*- coding: utf-8 -*-
"""Dual-path probe: run identical inputs through C speedups and pure-Python.

Usage (C extension is built for cp312 in this workspace):
    /Users/guo/.local/bin/python3.12 probe_dual_path.py
"""
import simplejson


def run_case(fn):
    try:
        return ('ok', fn())
    except Exception as e:
        return ('err', '%s: %s' % (type(e).__name__, e))


class IntSubclass(int):
    def __str__(self): return 'GARBAGE'
    def __repr__(self): return 'GARBAGE'


class WithForJson(object):
    def for_json(self): return {'via': 'for_json'}


def _circ():
    x = []
    x.append(x)
    return x


BIG = [2**63 - 1, 2**63, 2**64, -(2**63), -(2**63) - 1, 2**100]

CASES = []
def case(name, fn):
    CASES.append((name, fn))

# --- A. huge ints: encode ---
for v in BIG:
    case('enc int %s' % v, lambda v=v: simplejson.dumps(v))
for v in [2**53, -(2**53), 2**53 - 1]:
    case('enc bitcount=53 %s' % v,
         lambda v=v: simplejson.dumps(v, int_as_string_bitcount=53))
case('enc bigint_as_string 2**53',
     lambda: simplejson.dumps(2**53, bigint_as_string=True))
case('enc int subclass', lambda: simplejson.dumps(IntSubclass(7)))
case('enc int subclass big', lambda: simplejson.dumps(IntSubclass(2**100)))
case('enc bitcount=0', lambda: simplejson.dumps(1, int_as_string_bitcount=0))
case('enc bitcount=-1', lambda: simplejson.dumps(1, int_as_string_bitcount=-1))
case('enc bitcount="53"',
     lambda: simplejson.dumps(1, int_as_string_bitcount='53'))

# --- B. huge ints: decode ---
case('dec 30-digit int', lambda: simplejson.loads('123456789012345678901234567890'))
case('dec 5000-digit int', lambda: simplejson.loads('1' * 5000))
case('dec 5000-digit parse_int=float',
     lambda: simplejson.loads('1' * 5000, parse_int=float))
case('dec neg 5000-digit', lambda: simplejson.loads('-' + '1' * 5000))

# --- C. lone surrogates ---
for s in ['𐀀', '\ud800', '\udfff', 'a\ud800b']:
    case('enc ascii %r' % s, lambda s=s: simplejson.dumps(s))
    case('enc noascii %r' % s, lambda s=s: repr(simplejson.dumps(s, ensure_ascii=False)))
for j in ['"\\ud800"', '"\\udc00"', '"\\ud800\\udc00"', '"\\udc00\\ud800"',
          '"\\ud800\\ud800"', '"\\ud800A"', '"\\ud800\\u0041"', '"\\ud800\\udc0x"']:
    case('dec %s' % j, lambda j=j: repr(simplejson.loads(j)))
case('roundtrip noascii 𐀀',
     lambda: repr(simplejson.loads(simplejson.dumps('𐀀', ensure_ascii=False))))
case('roundtrip noascii lone \\ud800',
     lambda: repr(simplejson.loads(simplejson.dumps('\ud800', ensure_ascii=False))))

# --- D. default-triggering objects ---
case('enc object()', lambda: simplejson.dumps(object()))
case('enc set', lambda: simplejson.dumps({1, 2}))
case('enc {1:2} int key', lambda: simplejson.dumps({1: 2}))
case('enc obj key', lambda: simplejson.dumps({object(): 1}))
case('enc obj key skipkeys', lambda: simplejson.dumps({object(): 1}, skipkeys=True))
case('enc circular', lambda: simplejson.dumps(_circ()))
case('enc default=str object()', lambda: simplejson.dumps(object(), default=str))
case('enc default returns obj',
     lambda: simplejson.dumps(object(), default=lambda o: o))
case('enc for_json=False', lambda: simplejson.dumps(WithForJson()))
case('enc for_json=True', lambda: simplejson.dumps(WithForJson(), for_json=True))

results = {}
for enabled in (True, False):
    simplejson._toggle_speedups(enabled)
    tag = 'C ' if enabled else 'PY'
    assert (simplejson.encoder.c_make_encoder is not None) == enabled
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
    print('%s%-*s C=%r PY=%r' % ('SAME ' if same else 'DIFF ', width, name, c, py))
print('\n%d/%d cases differ' % (ndiff, len(CASES)))
