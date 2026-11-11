import unittest

try:
    from html.parser import HTMLParser
except ImportError:
    # Python 2.7
    from HTMLParser import HTMLParser

import simplejson as json

class TestEncodeForHTML(unittest.TestCase):

    def setUp(self):
        self.decoder = json.JSONDecoder()
        self.encoder = json.JSONEncoderForHTML()
        self.non_ascii_encoder = json.JSONEncoderForHTML(ensure_ascii=False)

    def test_basic_encode(self):
        self.assertEqual(r'"\u0026"', self.encoder.encode('&'))
        self.assertEqual(r'"\u003c"', self.encoder.encode('<'))
        self.assertEqual(r'"\u003e"', self.encoder.encode('>'))
        self.assertEqual(r'"\u2028"', self.encoder.encode(u'\u2028'))

    def test_non_ascii_basic_encode(self):
        self.assertEqual(r'"\u0026"', self.non_ascii_encoder.encode('&'))
        self.assertEqual(r'"\u003c"', self.non_ascii_encoder.encode('<'))
        self.assertEqual(r'"\u003e"', self.non_ascii_encoder.encode('>'))
        self.assertEqual(r'"\u2028"', self.non_ascii_encoder.encode(u'\u2028'))

    def test_basic_roundtrip(self):
        for char in '&<>':
            self.assertEqual(
                char, self.decoder.decode(
                    self.encoder.encode(char)))

    def test_prevent_script_breakout(self):
        bad_string = '</script><script>alert("gotcha")</script>'
        self.assertEqual(
            r'"\u003c/script\u003e\u003cscript\u003e'
            r'alert(\"gotcha\")\u003c/script\u003e"',
            self.encoder.encode(bad_string))
        self.assertEqual(
            bad_string, self.decoder.decode(
                self.encoder.encode(bad_string)))


class ScriptNodeCollector(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.start_tags = []
        self.data = []

    def handle_starttag(self, tag, attrs):
        self.start_tags.append(tag)

    def handle_data(self, data):
        self.data.append(data)


class TestEncodeForHTMLStrict(unittest.TestCase):

    def setUp(self):
        self.encoder = json.JSONEncoderForHTML(strict=True)

    def assert_script_roundtrip(self, value):
        encoded = self.encoder.encode(value)
        document = (
            '<!DOCTYPE html><html><body>'
            '<script type="application/json">' + encoded + '</script>'
            '</body></html>')
        collector = ScriptNodeCollector()
        collector.feed(document)
        collector.close()
        # Re-parsing the HTML must yield exactly the original data
        # node: one script element, no injected markup.
        self.assertEqual(['html', 'body', 'script'], collector.start_tags)
        extracted = ''.join(collector.data)
        self.assertEqual(encoded, extracted)
        # The extracted text must be valid JSON equivalent to the input.
        self.assertEqual(value, json.loads(extracted))

    def test_strict_roundtrip_script_breakout(self):
        self.assert_script_roundtrip({
            'record': '</script><script>alert("gotcha")</script>',
            'comment': '<!--<script>var x = 1;</script>-->',
            'markup': '<b>&amp;</b>',
            'separators': u'\u2028\u2029',
            'count': 42,
        })

    def test_strict_roundtrip_ensure_ascii_false(self):
        self.encoder = json.JSONEncoderForHTML(
            strict=True, ensure_ascii=False)
        self.assert_script_roundtrip(
            {'record': '</script>', 'snowman': u'\u2603', 'sep': u'\u2028'})

    def test_strict_output_has_no_unsafe_chars(self):
        encoded = self.encoder.encode(
            {'record': '</script><!--<script>&'})
        for char in '<>&':
            self.assertNotIn(char, encoded)

    def test_strict_raw_json_safe_fragment_verbatim(self):
        self.assertEqual(
            '{"v": {"a": [1, 2]}}',
            self.encoder.encode({'v': json.RawJSON('{"a": [1, 2]}')}))
        self.assertEqual('[[1, 2]]',
                         self.encoder.encode([json.RawJSON('[1, 2]')]))
        self.assertEqual('{"a": 1}',
                         self.encoder.encode(json.RawJSON('{"a": 1}')))

    def test_strict_raw_json_unsafe_chars_rejected(self):
        for fragment in ('"</script>"', '"<b>"', '"&"', u'"\u2028"', u'"\u2029"'):
            self.assertRaises(
                ValueError,
                self.encoder.encode, {'v': json.RawJSON(fragment)})

    def test_strict_raw_json_invalid_json_rejected(self):
        for fragment in ('', '{', '{"a": 1', 'not json', '{"a": 1} x',
                         'NaN', 'Infinity', "'single quotes'"):
            self.assertRaises(
                ValueError,
                self.encoder.encode, {'v': json.RawJSON(fragment)})

    def test_strict_raw_json_nested_rejected(self):
        self.assertRaises(
            ValueError,
            self.encoder.encode, [json.RawJSON('"</script>"')])
        self.assertRaises(
            ValueError,
            self.encoder.encode, {'a': {'b': json.RawJSON('"</script>"')}})

    def test_strict_raw_json_from_default_rejected(self):
        class Wrapped(object):
            pass
        encoder = json.JSONEncoderForHTML(
            strict=True,
            default=lambda o: json.RawJSON('"</script>"'))
        self.assertRaises(ValueError, encoder.encode, Wrapped())

    def test_strict_allow_nan_rejected(self):
        self.assertRaises(
            ValueError, json.JSONEncoderForHTML, strict=True, allow_nan=True)
        self.assertRaises(
            ValueError, json.JSONEncoderForHTML, strict=True, ignore_nan=True)

    def test_strict_nan_raises(self):
        self.assertRaises(ValueError, self.encoder.encode, float('nan'))

    def test_non_strict_raw_json_still_escaped(self):
        # Outside strict mode RawJSON keeps the historical behavior:
        # embedded with HTML-unsafe characters escaped.
        self.assertEqual(
            '{"v": "\\u003c/script\\u003e"}',
            json.JSONEncoderForHTML().encode(
                {'v': json.RawJSON('"</script>"')}))
