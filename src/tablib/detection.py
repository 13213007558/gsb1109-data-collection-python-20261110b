"""Tablib - format detection.

The detection phase reports *all* plausible formats together with the evidence
behind each guess instead of silently picking a winner. Structural probes run
against a buffered copy of the payload, so:

* optional parsing libraries (openpyxl, xlrd, pyyaml, odfpy, ...) are not
  imported just to sniff a file;
* the caller's stream is rewound after being read and is never handed back
  empty when it is seekable.

A format whose signature matches but whose optional dependency is missing is
reported as an unavailable candidate with installation instructions rather
than being dropped in favour of another format.
"""

from __future__ import annotations

__lazy_modules__ = {
    "csv",
    "dataclasses",
    "importlib.util",
    "io",
    "json",
    "re",
    "tablib.exceptions",
    "tablib.formats",
    "tablib.utils",
    "zipfile",
}

import csv
import json
import re
import zipfile
from dataclasses import dataclass, field
from importlib.util import find_spec
from io import BytesIO

from .exceptions import UnsupportedFormat
from .formats import uninstalled_format_messages
from .utils import normalize_input

# Modules that must be importable for an optional format to be usable.
OPTIONAL_FORMAT_DEPENDENCIES = {
    'xlsx': ('openpyxl',),
    'xls': ('xlrd', 'xlwt'),
    'yaml': ('yaml',),
    'ods': ('odf',),
    'df': ('pandas',),
    'cli': ('tabulate',),
}

# Every format shipped with Tablib. Anything registered beyond this set is a
# third-party format and falls back to its own ``detect()`` implementation.
BUILTIN_FORMATS = {
    'json', 'xlsx', 'xls', 'yaml', 'csv', 'tsv', 'ods', 'dbf', 'html',
    'jira', 'latex', 'rst', 'sql', 'df', 'cli',
}

# Order mirrors ``Registry.register_builtins()`` so tie-breaking keeps the
# historical autodetection behaviour.
PROBE_ORDER = ('json', 'xlsx', 'xls', 'yaml', 'csv', 'tsv', 'ods', 'dbf')

OLE2_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
ZIP_SIGNATURE = b'PK\x03\x04'

# dBASE version bytes used by the various DBF dialects.
DBF_VERSION_BYTES = {
    0x02, 0x03, 0x04, 0x05, 0x30, 0x31, 0x43, 0x7B,
    0x83, 0x8B, 0x8E, 0xCB, 0xF5, 0xFB,
}

_YAML_BLOCK_SEQUENCE = re.compile(r'(?m)^\s*-\s+\S')
_YAML_MAPPING = re.compile(r'(?m)^\s*[\w&*"\'\[\{][^:\n]*:\s')
_YAML_DOCUMENT = re.compile(r'(?m)^---\s*$')


@dataclass
class FormatCandidate:
    """A single format guess produced by :func:`detect_formats`.

    :param format: registered format title (e.g. ``'xlsx'``).
    :param confidence: ``'strong'`` for a direct signature/parse match,
        ``'provisional'`` when the payload also fits another format.
    :param available: whether the format can actually be imported on this
        machine.
    :param evidence: human-readable facts backing the guess.
    :param reason: when unavailable, explains how to enable the format.
    """

    format: str
    confidence: str
    available: bool
    evidence: list[str] = field(default_factory=list)
    reason: str | None = None

    def __str__(self):
        status = self.format if self.available else f'{self.format} (support not installed)'
        return f'{status} [{self.confidence}]'


def is_format_available(title):
    """Return whether an optional format's dependencies are importable."""
    dependencies = OPTIONAL_FORMAT_DEPENDENCIES.get(title)
    if dependencies is None:
        return True
    return all(find_spec(module) is not None for module in dependencies)


def _unavailable_reason(title):
    info = uninstalled_format_messages.get(title)
    if info is None:
        return f"The '{title}' format is not available."
    return (
        "The '{key}' format is not available. You may want to install the "
        "{package_name} (or `pip install \"tablib[{extras_name}]\"`).".format(
            **info, key=title
        )
    )


def _buffer_payload(stream):
    """Read the stream once, rewind it, and return the bytes plus text view."""
    if hasattr(stream, 'read'):
        payload = stream.read()
        if hasattr(stream, 'seek'):
            try:
                stream.seek(0)
            except (OSError, ValueError):
                # Non-seekable input (e.g. a pipe): nothing we can do, but the
                # buffered payload below is still used for every probe.
                pass
    elif isinstance(stream, (str, bytes, bytearray, memoryview)):
        payload = stream
    else:
        raise TypeError(
            'Expected a file-like object, string, or bytestring; got '
            f'{type(stream).__name__}'
        )

    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    elif isinstance(payload, (bytearray, memoryview)):
        payload = bytes(payload)
    if not isinstance(payload, bytes):
        raise TypeError(
            f'Could not buffer readable payload of type {type(payload).__name__}'
        )

    try:
        text = payload.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = None
    return payload, text


def _zip_listing(sample):
    if not sample.startswith(ZIP_SIGNATURE):
        return None
    try:
        with zipfile.ZipFile(BytesIO(sample)) as archive:
            return archive.namelist(), _read_zip_member(archive, 'mimetype')
    except (zipfile.BadZipFile, OSError):
        return None


def _read_zip_member(archive, name):
    try:
        return archive.read(name)
    except KeyError:
        return None


def _probe_xlsx(sample, listing):
    if listing is None:
        return None
    names, _ = listing
    if 'xl/workbook.xml' in names:
        return 'strong', ["ZIP archive containing 'xl/workbook.xml' (OOXML spreadsheet)"]
    if '[Content_Types].xml' in names:
        return 'provisional', ["ZIP archive containing '[Content_Types].xml' (OOXML container)"]
    return None


def _probe_ods(sample, listing):
    if listing is None:
        return None
    names, mimetype = listing
    if mimetype is not None and mimetype.startswith(
        b'application/vnd.oasis.opendocument.spreadsheet'
    ):
        return 'strong', ["ZIP archive 'mimetype' member declares an OpenDocument spreadsheet"]
    if 'content.xml' in names and 'META-INF/manifest.xml' in names:
        return 'provisional', ["ZIP archive contains ODF 'content.xml' and manifest"]
    return None


def _probe_xls(sample, listing):
    if sample.startswith(OLE2_SIGNATURE):
        return 'strong', ['OLE2 compound document signature (legacy Excel .xls)']
    return None


def _probe_dbf(sample, listing):
    # dBASE files open with a dialect version byte followed by 3 reserved
    # zero bytes and a 16-byte reserved region; require all of them.
    if (
        len(sample) >= 32
        and sample[0] in DBF_VERSION_BYTES
        and sample[12:32] == b'\x00' * 20
    ):
        return 'strong', [f'dBASE header version byte 0x{sample[0]:02X}']
    return None


def _probe_json(text):
    if text is None or not text.strip():
        return None
    try:
        json.loads(text)
    except (TypeError, ValueError):
        return None
    return 'strong', ['payload parses as valid JSON']


def _probe_yaml(text):
    if text is None or not text.strip():
        return None
    matched = []
    if _YAML_DOCUMENT.search(text):
        matched.append("YAML document marker '---'")
    if _YAML_BLOCK_SEQUENCE.search(text):
        matched.append('block sequence entry ("- ...")')
    if _YAML_MAPPING.search(text):
        matched.append('block/flow mapping ("key: value")')
    if not matched:
        return None
    try:
        json.loads(text)
    except (TypeError, ValueError):
        return 'strong', ['YAML structure detected: ' + ', '.join(matched)]
    return 'provisional', [
        'YAML structure detected (' + ', '.join(matched) + '); YAML is a JSON superset'
    ]


def _probe_delimited(text, delimiter):
    if text is None or not text.strip():
        return None
    try:
        csv.Sniffer().sniff(text[:2048], delimiters=delimiter)
    except csv.Error:
        return None
    return 'strong', [f"csv.Sniffer accepted delimiter {delimiter!r}"]


def _probe_dataframe(stream):
    # Identify a pandas DataFrame without importing pandas: the registry only
    # enables the 'df' format when pandas is installed.
    if type(stream).__name__ != 'DataFrame':
        return None
    module = type(stream).__module__
    if not module.startswith('pandas'):
        return None
    return 'strong', [f'{module}.DataFrame instance']


def _make_candidate(title, confidence, evidence):
    available = is_format_available(title)
    reason = None if available else _unavailable_reason(title)
    return FormatCandidate(
        format=title,
        confidence=confidence,
        available=available,
        evidence=list(evidence),
        reason=reason,
    )


def _structural_candidates(sample, text):
    listing = _zip_listing(sample)
    probes = (
        ('xlsx', lambda: _probe_xlsx(sample, listing)),
        ('ods', lambda: _probe_ods(sample, listing)),
        ('xls', lambda: _probe_xls(sample, listing)),
        ('dbf', lambda: _probe_dbf(sample, listing)),
        ('json', lambda: _probe_json(text)),
        ('yaml', lambda: _probe_yaml(text)),
        ('csv', lambda: _probe_delimited(text, ',')),
        ('tsv', lambda: _probe_delimited(text, '\t')),
    )
    results = {}
    for title, probe in probes:
        outcome = probe()
        if outcome is not None:
            confidence, evidence = outcome
            results[title] = _make_candidate(title, confidence, evidence)
    return results


def _third_party_candidates(stream, handled_titles):
    """Let registered custom formats weigh in via their own ``detect()``.

    Only non-builtin entries are resolved: builtin optional libraries must not
    be imported just to iterate the registry during detection.
    """
    from .formats import registry

    registry._ensure_builtins_registered()
    candidates = []
    for title in registry._formats:
        if title in BUILTIN_FORMATS or title in handled_titles:
            continue
        fmt = registry.get_format(title)
        detect = getattr(fmt, 'detect', None)
        if detect is None:
            continue
        try:
            matched = detect(stream)
        except Exception:
            matched = False
        finally:
            if hasattr(stream, 'seek'):
                try:
                    stream.seek(0)
                except (OSError, ValueError):
                    pass
        if matched:
            candidates.append(_make_candidate(title, 'strong', [
                f"{type(fmt).__name__}.detect() accepted the stream"
            ]))
    return candidates


def detect_formats(stream):
    """Return every plausible format for ``stream`` with supporting evidence.

    ``stream`` may be a file-like object, string, or bytestring. It is buffered
    internally and rewound when seekable, so the same object can be passed
    straight to :meth:`~tablib.core.Dataset.load` afterwards.

    :rtype: list[FormatCandidate], ordered by autodetection priority.
    """
    normalized = normalize_input(stream)

    dataframe_outcome = _probe_dataframe(stream)
    if dataframe_outcome is not None:
        confidence, evidence = dataframe_outcome
        return [_make_candidate('df', confidence, evidence)]

    sample, text = _buffer_payload(normalized)
    candidates_by_title = _structural_candidates(sample, text)

    candidates = [
        candidates_by_title[title]
        for title in PROBE_ORDER
        if title in candidates_by_title
    ]
    candidates.extend(_third_party_candidates(normalized, set(candidates_by_title)))
    return candidates


def resolve_format(stream):
    """Pick an importable, strongly matching format or raise a clear error.

    Mirrors the historical return of :func:`detect_format` (``None`` when
    nothing matches), but when the only matches are formats missing optional
    support, raises :class:`~tablib.exceptions.UnsupportedFormat` with the
    installation instructions instead of silently falling back to another
    format.
    """
    candidates = detect_formats(stream)
    for required_confidence in ('strong', 'provisional'):
        for candidate in candidates:
            if candidate.available and candidate.confidence == required_confidence:
                return candidate.format

    unavailable = [candidate for candidate in candidates if not candidate.available]
    if unavailable:
        details = '; '.join(
            f"{candidate.format}: {candidate.reason}" for candidate in unavailable
        )
        raise UnsupportedFormat(
            'Input matches format(s) whose optional support is not installed on '
            f'this machine: {details}'
        )
    return None
