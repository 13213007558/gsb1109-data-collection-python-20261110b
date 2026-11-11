from __future__ import absolute_import
import unittest
from collections import OrderedDict

import simplejson as json
from simplejson import RawJSON

DOCUMENT = (
    '{"id": 7, '
    '"receipt": { "b": 2,  "a" : [1, 2, {"z" : 3}] }, '
    '"nested": {"receipt": {"x": 1}}, '
    '"items": ["keep", {"raw": true}], '
    '"note": "receipt"}'
)

RECEIPT_RAW = '{ "b": 2,  "a" : [1, 2, {"z" : 3}] }'


class TestPreserveRaw(unittest.TestCase):

    def test_root_field_preserved_verbatim(self):
        result = json.loads(DOCUMENT, preserve_raw=[('receipt',)])
        receipt = result['receipt']
        self.assertIsInstance(receipt, RawJSON)
        self.assertEqual(receipt.encoded_json, RECEIPT_RAW)

    def test_other_fields_decode_normally(self):
        result = json.loads(DOCUMENT, preserve_raw=[('receipt',)])
        self.assertEqual(result['id'], 7)
        self.assertEqual(result['note'], 'receipt')
        self.assertEqual(result['nested'], {'receipt': {'x': 1}})
        self.assertEqual(result['items'], ['keep', {'raw': True}])

    def test_same_key_elsewhere_not_preserved(self):
        result = json.loads(DOCUMENT, preserve_raw=[('receipt',)])
        nested = result['nested']['receipt']
        self.assertNotIsInstance(nested, RawJSON)
        self.assertEqual(nested, {'x': 1})

    def test_nested_path(self):
        result = json.loads(DOCUMENT, preserve_raw=[('nested', 'receipt')])
        receipt = result['nested']['receipt']
        self.assertIsInstance(receipt, RawJSON)
        self.assertEqual(receipt.encoded_json, '{"x": 1}')
        self.assertNotIsInstance(result['receipt'], RawJSON)
        self.assertEqual(result['receipt'], {'b': 2, 'a': [1, 2, {'z': 3}]})

    def test_array_index_path(self):
        result = json.loads(DOCUMENT, preserve_raw=[('items', 1)])
        self.assertEqual(result['items'][0], 'keep')
        raw = result['items'][1]
        self.assertIsInstance(raw, RawJSON)
        self.assertEqual(raw.encoded_json, '{"raw": true}')

    def test_scalar_paths(self):
        result = json.loads(
            '{"s": "text", "n": 42, "f": 1.5, "b": true, "x": null}',
            preserve_raw=[('s',), ('n',), ('f',), ('b',), ('x',)])
        self.assertEqual(result['s'].encoded_json, '"text"')
        self.assertEqual(result['n'].encoded_json, '42')
        self.assertEqual(result['f'].encoded_json, '1.5')
        self.assertEqual(result['b'].encoded_json, 'true')
        self.assertEqual(result['x'].encoded_json, 'null')
        for value in result.values():
            self.assertIsInstance(value, RawJSON)

    def test_distinguishable_from_str(self):
        result = json.loads(DOCUMENT, preserve_raw=[('receipt',)])
        self.assertIsInstance(result['note'], str)
        self.assertNotIsInstance(result['receipt'], str)
        self.assertIsInstance(result['receipt'], RawJSON)

    def test_raw_fragment_must_be_valid_json(self):
        self.assertRaises(
            json.JSONDecodeError,
            json.loads, '{"receipt": {invalid}}',
            preserve_raw=[('receipt',)])

    def test_invalid_document_still_raises(self):
        self.assertRaises(
            json.JSONDecodeError,
            json.loads, '{"receipt": 1,}',
            preserve_raw=[('receipt',)])

    def test_extra_data_still_raises(self):
        self.assertRaises(
            json.JSONDecodeError,
            json.loads, '{"receipt": 1} extra',
            preserve_raw=[('receipt',)])

    def test_round_trip_through_dumps(self):
        result = json.loads(DOCUMENT, preserve_raw=[('receipt',)])
        self.assertIn(RECEIPT_RAW, json.dumps(result))

    def test_preserved_value_decodes_to_same_object(self):
        result = json.loads(DOCUMENT, preserve_raw=[('receipt',)])
        self.assertEqual(
            json.loads(result['receipt'].encoded_json),
            json.loads(DOCUMENT)['receipt'])

    def test_whole_document(self):
        result = json.loads(DOCUMENT, preserve_raw=[()])
        self.assertIsInstance(result, RawJSON)
        self.assertEqual(result.encoded_json, DOCUMENT)

    def test_empty_preserve_raw_matches_default(self):
        self.assertEqual(
            json.loads(DOCUMENT, preserve_raw=[]),
            json.loads(DOCUMENT))

    def test_path_below_preserved_path_is_ignored(self):
        result = json.loads(
            DOCUMENT, preserve_raw=[('receipt',), ('receipt', 'a')])
        self.assertIsInstance(result['receipt'], RawJSON)
        self.assertEqual(result['receipt'].encoded_json, RECEIPT_RAW)

    def test_path_through_scalar_is_graceful(self):
        result = json.loads('{"a": 1}', preserve_raw=[('a', 'b')])
        self.assertEqual(result, {'a': 1})

    def test_missing_path_is_graceful(self):
        result = json.loads('{"a": 1}', preserve_raw=[('absent',)])
        self.assertEqual(result, {'a': 1})

    def test_object_pairs_hook_still_applies(self):
        result = json.loads(
            DOCUMENT,
            object_pairs_hook=OrderedDict,
            preserve_raw=[('receipt',)])
        self.assertIsInstance(result, OrderedDict)
        self.assertIsInstance(result['nested'], OrderedDict)
        self.assertIsInstance(result['receipt'], RawJSON)

    def test_object_hook_still_applies(self):
        seen = []

        def hook(dct):
            seen.append(dct)
            return dct

        result = json.loads(
            DOCUMENT, object_hook=hook, preserve_raw=[('receipt',)])
        self.assertIsInstance(result['receipt'], RawJSON)
        self.assertIn({'receipt': {'x': 1}}, seen)

    def test_parse_int_still_applies_outside_preserved(self):
        result = json.loads(
            DOCUMENT, parse_int=str, preserve_raw=[('receipt',)])
        self.assertEqual(result['id'], '7')
        self.assertEqual(result['receipt'].encoded_json, RECEIPT_RAW)

    def test_invalid_path_element(self):
        self.assertRaises(
            TypeError,
            json.JSONDecoder, preserve_raw=[(1.5,)])
        self.assertRaises(
            TypeError,
            json.JSONDecoder, preserve_raw=[(True,)])

    def test_repr(self):
        result = json.loads(DOCUMENT, preserve_raw=[('receipt',)])
        self.assertEqual(
            repr(result['receipt']),
            'RawJSON(%r)' % (RECEIPT_RAW,))

    def test_decoder_class_directly(self):
        decoder = json.JSONDecoder(preserve_raw=[('receipt',)])
        result = decoder.decode(DOCUMENT)
        self.assertIsInstance(result['receipt'], RawJSON)

    def test_load_from_file_like(self):
        from simplejson.compat import StringIO
        result = json.load(StringIO(DOCUMENT), preserve_raw=[('receipt',)])
        self.assertIsInstance(result['receipt'], RawJSON)
        self.assertEqual(result['receipt'].encoded_json, RECEIPT_RAW)

    def test_bytes_input(self):
        result = json.loads(
            DOCUMENT.encode('utf-8'), preserve_raw=[('receipt',)])
        self.assertEqual(result['receipt'].encoded_json, RECEIPT_RAW)
