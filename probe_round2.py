# -*- coding: utf-8 -*-
"""Round 2: edge cases for int bitcount, truncated surrogate escapes,
malformed numbers, int-subclass dict keys."""
import simplejson


def run_case(fn):
    try:
        return ('ok', fn())
    except Exception as e:
        return ('err', '%s: %s' % (type(e).__name__, e))


class IntSubclass(int):
    def __str__(self): return 'GARBAGE'
    def __repr__(self): return 'GARBAGE'


CASES = []
def case(name, fn):
    CASES.append((name, fn))

# bitcount parameter edges
case('enc bitcount=True', lambda: simplejson.dumps(3, int_as_string_bitcount=True))
case('enc bitcount=2**70 on 2**100',
     lambda: simplejson.dumps(2**100, int_as_string_bitcount=2**70))
case('enc bitcount=63 on 2**63',
     lambda: simplejson.dumps(2**63, int_as_string_bitcount=63))
case('enc bitcount=63 on -(2**63)',
     lambda: simplejson.dumps(-(2**63), int_as_string_bitcount=63))
case('enc bitcount=63 on -(2**63)-1',
     lambda: simplejson.dumps(-(2**63) - 1, int_as_string_bitcount=63))
case('enc bitcount=64 on -(2**63)-1',
     lambda: simplejson.dumps(-(2**63) - 1, int_as_string_bitcount=64))
case('enc bitcount=100 on 2**100',
     lambda: simplejson.dumps(2**100, int_as_string_bitcount=100))
case('enc bitcount=53 big dict key',
     lambda: simplejson.dumps({2**100: 1}, int_as_string_bitcount=53))
case('enc int subclass key', lambda: simplejson.dumps({IntSubclass(7): 1}))
case('enc int subclass key big', lambda: simplejson.dumps({IntSubclass(2**100): 1}))
case('enc bitcount=53 int subclass',
     lambda: simplejson.dumps(IntSubclass(2**60), int_as_string_bitcount=53))

# truncated / malformed surrogate escapes
for j in ['"\\ud800\\u"', '"\\ud800\\u00"', '"\\ud800\\', '"\\ud800',
          '"\\ud800\\n"', '"\\ud800\\ud"', '"\\udc00\\ud800\\udc00"']:
    case('dec %r' % j, lambda j=j: repr(simplejson.loads(j)))

# malformed numbers
for j in ['01', '1.', '-', '+1', '1e', '0x1', '1e+', '--1', '00', '0', '-0',
          '1e999', '-1e999', '[1,]', "{'a':1}", "'a'", '1 2']:
    case('dec %r' % j, lambda j=j: simplejson.loads(j))

# decode big int as float-looking
case('dec 1e5', lambda: simplejson.loads('1e5'))
case('dec 5000-digit with parse_int=str',
     lambda: simplejson.loads('1' * 5000, parse_int=str))

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
    print('%s%-*s C=%r PY=%r' % ('SAME ' if same else 'DIFF ', width, name, c, py))
print('\n%d/%d cases differ' % (ndiff, len(CASES)))
