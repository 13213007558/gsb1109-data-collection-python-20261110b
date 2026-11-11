"""Tests for the ijson.items_at_path method"""

import collections

import ijson
from ijson import ARRAY_ITEM


def test_dotted_key_vs_nested_path(adaptor):
    json = b'{"a.b": 1, "a": {"b": 2}}'
    assert [1] == adaptor.items_at_path(json, ('a.b',))
    assert [2] == adaptor.items_at_path(json, ('a', 'b'))


def test_array_item_vs_item_key(adaptor):
    json = b'{"map": {"item": 1}, "arr": [2, 3]}'
    assert [1] == adaptor.items_at_path(json, ('map', 'item'))
    assert [2, 3] == adaptor.items_at_path(json, ('arr', ARRAY_ITEM))


def test_empty_keys_are_kept(adaptor):
    json = b'{"": {"": 1}, "a": 2}'
    assert [1] == adaptor.items_at_path(json, ('', ''))
    assert [{'': 1}] == adaptor.items_at_path(json, ('',))


def test_nested_arrays(adaptor):
    json = b'[[1, 2], [3]]'
    assert [1, 2, 3] == adaptor.items_at_path(json, (ARRAY_ITEM, ARRAY_ITEM))


def test_container_match(adaptor):
    json = b'{"a": [{"x": 1}, {"x": 2}]}'
    assert [[{'x': 1}, {'x': 2}]] == adaptor.items_at_path(json, ('a',))


def test_empty_path_matches_root(adaptor):
    assert [{'a': 1}] == adaptor.items_at_path(b'{"a": 1}', ())
    assert [5] == adaptor.items_at_path(b'5', ())


def test_map_type(adaptor):
    json = b'{"a": {"b": 1}}'
    obj = adaptor.items_at_path(json, ('a',), map_type=collections.OrderedDict)[0]
    assert isinstance(obj, collections.OrderedDict)


def test_top_level_function():
    json = b'{"a.b": 1, "a": {"b": 2}}'
    assert [2] == list(ijson.items_at_path(json, ('a', 'b')))
    assert [1] == list(ijson.items_at_path(json, ('a.b',)))


def test_streaming_delivery(backend):
    # matched values are delivered as soon as they are complete,
    # without consuming the rest of the event stream
    events = iter([
        ('start_array', None),
        ('number', 1),
        ('number', 2),
        ('end_array', None),
    ])
    result = backend.items_at_path(events, (ARRAY_ITEM,))
    assert 1 == next(result)
    assert ('number', 2) == next(events)
