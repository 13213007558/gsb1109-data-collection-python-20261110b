"""Tests for evidence-based format detection."""

import subprocess
import sys
import textwrap
import unittest
from io import BytesIO, StringIO
from unittest.mock import patch

import tablib
from tablib.core import detect_format
from tablib.detection import (
    FormatCandidate,
    detect_formats,
    is_format_available,
    resolve_format,
)
from tablib.exceptions import UnsupportedFormat
from tablib.formats import registry


class DetectionBaseTestCase(unittest.TestCase):
    def setUp(self):
        self.founders = tablib.Dataset(
            headers=('first_name', 'last_name', 'gpa'), title='Founders'
        )
        self.founders.append(('John', 'Adams', 90))
        self.founders.append(('George', 'Washington', 67))

    def export(self, fmt):
        return self.founders.export(fmt)


class DetectFormatsTests(DetectionBaseTestCase):
    def test_every_importable_format_is_detected(self):
        expected = {
            'json': str,
            'yaml': str,
            'csv': str,
            'tsv': str,
            'xls': bytes,
            'xlsx': bytes,
            'ods': bytes,
            'dbf': bytes,
        }
        for fmt, payload_type in expected.items():
            with self.subTest(fmt=fmt):
                payload = self.export(fmt)
                self.assertIsInstance(payload, payload_type)
                candidates = detect_formats(payload)
                titles = [candidate.format for candidate in candidates]
                self.assertIn(fmt, titles)
                candidate = next(c for c in candidates if c.format == fmt)
                self.assertEqual(candidate.confidence, 'strong')
                self.assertTrue(candidate.available)
                self.assertTrue(candidate.evidence)

    def test_candidate_carries_evidence(self):
        candidates = detect_formats(self.export('xlsx'))
        xlsx = next(c for c in candidates if c.format == 'xlsx')
        self.assertTrue(any('xl/workbook.xml' in fact for fact in xlsx.evidence))

        candidates = detect_formats(self.export('xls'))
        xls = next(c for c in candidates if c.format == 'xls')
        self.assertTrue(any('OLE2' in fact for fact in xls.evidence))

        candidates = detect_formats('[{"a": 1}]')
        js = next(c for c in candidates if c.format == 'json')
        self.assertTrue(any('JSON' in fact for fact in js.evidence))

    def test_ambiguous_input_reports_multiple_candidates(self):
        # JSON is also valid YAML and happens to satisfy the CSV sniffer.
        json_text = '[{"last_name": "Adams", "age": 90}]'
        titles = [c.format for c in detect_formats(json_text)]
        self.assertEqual(titles[0], 'json')
        self.assertIn('yaml', titles)
        yaml_candidate = next(c for c in detect_formats(json_text) if c.format == 'yaml')
        self.assertEqual(yaml_candidate.confidence, 'provisional')

        # Flow-style YAML without JSON syntax is also plausible CSV.
        yaml_text = '- {age: 90, first_name: John, last_name: Adams}'
        titles = [c.format for c in detect_formats(yaml_text)]
        self.assertEqual(titles[0], 'yaml')
        self.assertIn('csv', titles)

    def test_unknown_input_returns_no_candidates(self):
        bunk = '¡¡¡¡¡¡---///\n\n\n¡¡£™∞¢£§∞§¶•¶ª∞¶'
        self.assertEqual(detect_formats(bunk), [])
        self.assertEqual(detect_formats(bunk.encode('utf-8')), [])
        self.assertIsNone(detect_format(bunk))

    def test_empty_input_returns_no_candidates(self):
        self.assertEqual(detect_formats(''), [])
        self.assertEqual(detect_formats(b''), [])

    def test_detect_format_matches_historical_results(self):
        self.assertEqual(detect_format(self.export('xls')), 'xls')
        self.assertEqual(detect_format(self.export('xlsx')), 'xlsx')
        self.assertEqual(detect_format(self.export('ods')), 'ods')
        self.assertEqual(detect_format('- {age: 90, first_name: John}'), 'yaml')
        self.assertEqual(detect_format('[{"a": 1}]'), 'json')
        self.assertEqual(detect_format('1,2,3\n4,5,6\n'), 'csv')
        self.assertEqual(detect_format('1\t2\t3\n4\t5\t6\n'), 'tsv')

    def test_resolve_falls_back_to_provisional_candidate(self):
        # Pretty-printed JSON does not satisfy the CSV sniffer, so only the
        # strong JSON match and its provisional YAML twin are reported.
        # Make JSON unavailable: the provisional YAML candidate must be used.
        pretty_json = '[\n  {"a": 1}\n]'
        with patch('tablib.detection.is_format_available',
                   side_effect=lambda title: title != 'json'):
            titles = {c.format: c.confidence for c in detect_formats(pretty_json)}
            self.assertEqual(titles, {'json': 'strong', 'yaml': 'provisional'})
            self.assertEqual(resolve_format(pretty_json), 'yaml')


class StreamHandlingTests(DetectionBaseTestCase):
    def test_seekable_stream_is_rewound(self):
        for fmt in ('xlsx', 'xls', 'ods', 'dbf'):
            stream = BytesIO(self.export(fmt))
            with self.subTest(fmt=fmt):
                detect_formats(stream)
                self.assertEqual(stream.tell(), 0)
                dataset = tablib.Dataset().load(stream)
                # DBF stores field names in upper case.
                self.assertEqual(
                    [header.lower() for header in dataset.headers],
                    ['first_name', 'last_name', 'gpa'],
                )

        text_stream = StringIO(self.export('csv'))
        detect_formats(text_stream)
        self.assertEqual(text_stream.tell(), 0)
        dataset = tablib.Dataset().load(text_stream)
        self.assertEqual(dataset.headers, ['first_name', 'last_name', 'gpa'])

    def test_detection_is_idempotent(self):
        stream = BytesIO(self.export('xlsx'))
        first = [c.format for c in detect_formats(stream)]
        second = [c.format for c in detect_formats(stream)]
        self.assertEqual(first, second)
        self.assertEqual(stream.tell(), 0)

    def test_non_seekable_stream_is_supported(self):
        class OneWayStream:
            def __init__(self, payload):
                self._buffer = BytesIO(payload)

            def read(self, *args):
                return self._buffer.read(*args)

            def seek(self, *args):
                raise OSError('stream is not seekable')

        stream = OneWayStream(self.export('xlsx'))
        candidates = detect_formats(stream)
        self.assertIn('xlsx', [c.format for c in candidates])

    def test_load_reuses_detected_stream(self):
        for fmt in ('xlsx', 'xls', 'ods', 'json', 'csv', 'tsv', 'yaml', 'dbf'):
            payload = self.export(fmt)
            stream = BytesIO(payload) if isinstance(payload, bytes) else StringIO(payload)
            with self.subTest(fmt=fmt):
                candidates = detect_formats(stream)
                self.assertTrue(candidates)
                dataset = tablib.Dataset().load(stream)
                self.assertEqual(dataset.height, 2)


class MissingDependencyTests(DetectionBaseTestCase):
    def test_missing_dependency_is_reported_not_silently_skipped(self):
        payload = self.export('xlsx')
        with patch('tablib.detection.find_spec',
                   side_effect=lambda name: None if name == 'openpyxl' else True):
            candidates = detect_formats(payload)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.format, 'xlsx')
        self.assertFalse(candidate.available)
        self.assertIn('openpyxl', candidate.reason)
        self.assertIn('tablib[xlsx]', candidate.reason)
        self.assertIn('support not installed', str(candidate))

    def test_detect_format_raises_when_only_matching_format_unavailable(self):
        payload = self.export('xlsx')
        with patch('tablib.detection.find_spec',
                   side_effect=lambda name: None if name == 'openpyxl' else True):
            with self.assertRaises(UnsupportedFormat) as ctx:
                detect_format(payload)
        self.assertIn('openpyxl', str(ctx.exception))

        with patch('tablib.detection.find_spec',
                   side_effect=lambda name: None if name == 'openpyxl' else True):
            with self.assertRaises(UnsupportedFormat):
                tablib.Dataset().load(BytesIO(payload))

    def test_other_optional_formats_report_missing_support(self):
        cases = {
            'ods': ('odf', self.export('ods')),
            'xls': ('xlrd', self.export('xls')),
        }
        for fmt, (missing_module, payload) in cases.items():
            with self.subTest(fmt=fmt):
                with patch('tablib.detection.find_spec',
                           side_effect=lambda name, m=missing_module:
                               None if name == m else True):
                    candidates = detect_formats(payload)
                candidate = next(c for c in candidates if c.format == fmt)
                self.assertFalse(candidate.available)
                self.assertIsNotNone(candidate.reason)

    def test_available_format_wins_over_unavailable_alternative(self):
        # Ambiguous YAML/CSV input: with pyyaml missing, CSV remains usable.
        payload = '- {age: 90, first_name: John, last_name: Adams}'
        with patch('tablib.detection.find_spec',
                   side_effect=lambda name: None if name == 'yaml' else True):
            candidates = detect_formats(payload)
            titles = {c.format: c.available for c in candidates}
            self.assertFalse(titles['yaml'])
            self.assertTrue(titles['csv'])
            self.assertEqual(resolve_format(payload), 'csv')

    def test_is_format_available(self):
        self.assertTrue(is_format_available('csv'))
        self.assertTrue(is_format_available('json'))
        with patch('tablib.detection.find_spec', return_value=None):
            self.assertFalse(is_format_available('xlsx'))


class LazyLoadingTests(unittest.TestCase):
    def run_in_fresh_process(self, code):
        result = subprocess.run(
            [sys.executable, '-c', textwrap.dedent(code)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_detection_does_not_import_optional_libraries(self):
        self.run_in_fresh_process('''\
            import sys
            import tablib

            payloads = [
                b'PK\\x03\\x04' + b'\\x00' * 100,
                b'\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1' + b'\\x00' * 100,
                b'\\x03r\\x06\\x03' + b'\\x00' * 100,
                '[{"a": 1}]',
                '- a: 1\\n  b: 2\\n',
                '1,2,3\\n4,5,6\\n',
                '1\\t2\\t3\\n4\\t5\\t6\\n',
            ]
            for payload in payloads:
                tablib.detect_formats(payload)

            for name in (
                'openpyxl', 'xlrd', 'yaml', 'odf', 'pandas', 'tabulate',
                'tablib.formats._xlsx', 'tablib.formats._xls',
                'tablib.formats._yaml', 'tablib.formats._ods',
                'tablib.formats._df', 'tablib.formats._cli',
            ):
                assert name not in sys.modules, name
        ''')


class CustomFormatTests(DetectionBaseTestCase):
    def test_registered_custom_format_participates(self):
        from tablib.formats._csv import CSVFormat

        class CustomFormat(CSVFormat):
            title = 'custom_detection_format'

            @classmethod
            def detect(cls, stream):
                stream.seek(0)
                return stream.read().startswith('custom-magic')

        custom = CustomFormat()
        registry.register('custom_detection_format', custom)
        try:
            candidates = detect_formats(StringIO('custom-magic,1,2'))
            titles = [c.format for c in candidates]
            self.assertIn('custom_detection_format', titles)
            candidate = next(
                c for c in candidates if c.format == 'custom_detection_format'
            )
            self.assertTrue(candidate.evidence)
        finally:
            del registry._formats['custom_detection_format']
            delattr(tablib.Dataset, 'custom_detection_format')
            delattr(tablib.Databook, 'custom_detection_format')
            delattr(tablib.Dataset, 'get_custom_detection_format')
            delattr(tablib.Dataset, 'set_custom_detection_format')


class DataFrameDetectionTests(DetectionBaseTestCase):
    def test_dataframe_input(self):
        try:
            import pandas
        except ImportError:
            self.skipTest('pandas is not installed')

        frame = self.founders.export('df')
        candidates = detect_formats(frame)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].format, 'df')
        self.assertEqual(candidates[0].confidence, 'strong')
        self.assertEqual(resolve_format(frame), 'df')


class FormatCandidateTests(unittest.TestCase):
    def test_str_distinguishes_availability(self):
        available = FormatCandidate(
            format='csv', confidence='strong', available=True, evidence=['fact']
        )
        unavailable = FormatCandidate(
            format='xlsx', confidence='strong', available=False,
            evidence=['fact'], reason='install openpyxl',
        )
        self.assertNotIn('not installed', str(available))
        self.assertIn('xlsx', str(unavailable))
        self.assertIn('support not installed', str(unavailable))
