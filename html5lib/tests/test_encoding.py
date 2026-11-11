from __future__ import absolute_import, division, unicode_literals

import os
from io import BytesIO

import pytest

from .support import get_data_files, test_dir, errorMessage, TestData as _TestData
from html5lib import HTMLParser, _inputstream


class ReadOnlyStream(object):
    """A byte stream that can only be read: seeking is not supported.

    Mirrors a crawling pipeline whose bytes are consumed as they arrive.
    """

    def __init__(self, data):
        self._buffer = BytesIO(data)
        self.seek_calls = 0

    def read(self, numBytes=-1):
        return self._buffer.read(numBytes)

    def seek(self, *args):
        self.seek_calls += 1
        raise AssertionError("non-seekable stream must never be rewound")


def _late_conflict_data():
    # The meta element is pushed beyond the 1024-byte prescan window, so the
    # initial encoding is the tentative default rather than the meta one.
    data = b"<!--" + b"a" * 1020 + b"--><meta charset='utf-8'>"
    data += "café".encode("utf-8")
    return data


def test_basic_prescan_length():
    data = "<title>Caf\u00E9</title><!--a--><meta charset='utf-8'>".encode('utf-8')
    pad = 1024 - len(data) + 1
    data = data.replace(b"-a-", b"-" + (b"a" * pad) + b"-")
    assert len(data) == 1024  # Sanity
    stream = _inputstream.HTMLBinaryInputStream(data, useChardet=False)
    assert 'utf-8' == stream.charEncoding[0].name


def test_parser_reparse():
    data = "<title>Caf\u00E9</title><!--a--><meta charset='utf-8'>".encode('utf-8')
    pad = 10240 - len(data) + 1
    data = data.replace(b"-a-", b"-" + (b"a" * pad) + b"-")
    assert len(data) == 10240  # Sanity
    stream = _inputstream.HTMLBinaryInputStream(data, useChardet=False)
    assert 'windows-1252' == stream.charEncoding[0].name
    p = HTMLParser(namespaceHTMLElements=False)
    doc = p.parse(data, useChardet=False)
    assert 'utf-8' == p.documentEncoding
    assert doc.find(".//title").text == "Caf\u00E9"


@pytest.mark.parametrize("expected,data,kwargs", [
    ("utf-16le", b"\xFF\xFE", {"override_encoding": "iso-8859-2"}),
    ("utf-16be", b"\xFE\xFF", {"override_encoding": "iso-8859-2"}),
    ("utf-8", b"\xEF\xBB\xBF", {"override_encoding": "iso-8859-2"}),
    ("iso-8859-2", b"", {"override_encoding": "iso-8859-2", "transport_encoding": "iso-8859-3"}),
    ("iso-8859-2", b"<meta charset=iso-8859-3>", {"transport_encoding": "iso-8859-2"}),
    ("iso-8859-2", b"<meta charset=iso-8859-2>", {"same_origin_parent_encoding": "iso-8859-3"}),
    ("iso-8859-2", b"", {"same_origin_parent_encoding": "iso-8859-2", "likely_encoding": "iso-8859-3"}),
    ("iso-8859-2", b"", {"same_origin_parent_encoding": "utf-16", "likely_encoding": "iso-8859-2"}),
    ("iso-8859-2", b"", {"same_origin_parent_encoding": "utf-16be", "likely_encoding": "iso-8859-2"}),
    ("iso-8859-2", b"", {"same_origin_parent_encoding": "utf-16le", "likely_encoding": "iso-8859-2"}),
    ("iso-8859-2", b"", {"likely_encoding": "iso-8859-2", "default_encoding": "iso-8859-3"}),
    ("iso-8859-2", b"", {"default_encoding": "iso-8859-2"}),
    ("windows-1252", b"", {"default_encoding": "totally-bogus-string"}),
    ("windows-1252", b"", {}),
])
def test_parser_args(expected, data, kwargs):
    stream = _inputstream.HTMLBinaryInputStream(data, useChardet=False, **kwargs)
    assert expected == stream.charEncoding[0].name
    p = HTMLParser()
    p.parse(data, useChardet=False, **kwargs)
    assert expected == p.documentEncoding


@pytest.mark.parametrize("kwargs", [
    {"override_encoding": "iso-8859-2"},
    {"override_encoding": None},
    {"transport_encoding": "iso-8859-2"},
    {"transport_encoding": None},
    {"same_origin_parent_encoding": "iso-8859-2"},
    {"same_origin_parent_encoding": None},
    {"likely_encoding": "iso-8859-2"},
    {"likely_encoding": None},
    {"default_encoding": "iso-8859-2"},
    {"default_encoding": None},
    {"foo_encoding": "iso-8859-2"},
    {"foo_encoding": None},
])
def test_parser_args_raises(kwargs):
    with pytest.raises(TypeError) as exc_info:
        p = HTMLParser()
        p.parse("", useChardet=False, **kwargs)
    assert exc_info.value.args[0].startswith("Cannot set an encoding with a unicode input")


def param_encoding():
    for filename in get_data_files("encoding"):
        tests = _TestData(filename, b"data", encoding=None)
        for test in tests:
            yield test[b'data'], test[b'encoding']


@pytest.mark.parametrize("data, encoding", param_encoding())
def test_parser_encoding(data, encoding):
    p = HTMLParser()
    assert p.documentEncoding is None
    p.parse(data, useChardet=False)
    encoding = encoding.lower().decode("ascii")

    assert encoding == p.documentEncoding, errorMessage(data, encoding, p.documentEncoding)


@pytest.mark.parametrize("data, encoding", param_encoding())
def test_prescan_encoding(data, encoding):
    stream = _inputstream.HTMLBinaryInputStream(data, useChardet=False)
    encoding = encoding.lower().decode("ascii")

    # Very crude way to ignore irrelevant tests
    if len(data) > stream.numBytesMeta:
        return

    assert encoding == stream.charEncoding[0].name, errorMessage(data, encoding, stream.charEncoding[0].name)


# pylint:disable=wrong-import-position
try:
    import chardet  # noqa
except ImportError:
    print("chardet not found, skipping chardet tests")
else:
    def test_chardet():
        with open(os.path.join(test_dir, "encoding", "chardet", "test_big5.txt"), "rb") as fp:
            encoding = _inputstream.HTMLInputStream(fp.read()).charEncoding
            assert encoding[0].name == "big5"
# pylint:enable=wrong-import-position


def test_fixed_encoding_stream_settles_immediately():
    source = ReadOnlyStream(_late_conflict_data())
    stream = _inputstream.HTMLBinaryInputStream(source, useChardet=False,
                                                allowEncodingChange=False)
    # The encoding is settled as soon as the decoder starts consuming bytes.
    assert stream.charEncoding[1] == "certain"
    assert stream.charEncoding[0].name == "windows-1252"
    assert stream.encodingChangeResult == _inputstream.ENCODING_UNDETERMINED
    assert stream.encodingConflicts == []
    # Nothing ever rewound the underlying read-only byte stream.
    assert source.seek_calls == 0


def test_fixed_encoding_ignores_late_conflict():
    source = ReadOnlyStream(_late_conflict_data())
    parser = HTMLParser(namespaceHTMLElements=False)
    parser.parse(source, useChardet=False, allowEncodingChange=False)

    assert parser.documentEncoding == "windows-1252"
    assert parser.encodingChangeResult == _inputstream.ENCODING_IGNORED
    assert parser.encodingConflicts == [("utf-8", "windows-1252")]
    assert source.seek_calls == 0


def test_fixed_encoding_confirms_matching_declaration():
    data = "<html><head><meta charset='utf-8'></head><body>ok</body></html>".encode("utf-8")
    parser = HTMLParser()
    parser.parse(BytesIO(data), useChardet=False, allowEncodingChange=False)

    assert parser.documentEncoding == "utf-8"
    assert parser.encodingChangeResult == _inputstream.ENCODING_CONFIRMED
    assert parser.encodingConflicts == []


def test_fixed_encoding_records_every_conflict():
    data = (b"<!--" + b"a" * 1020 +
            b"--><meta charset='utf-8'><meta charset='iso-8859-2'>")
    parser = HTMLParser()
    parser.parse(ReadOnlyStream(data), useChardet=False,
                 allowEncodingChange=False)

    assert parser.documentEncoding == "windows-1252"
    assert parser.encodingChangeResult == _inputstream.ENCODING_IGNORED
    assert parser.encodingConflicts == [
        ("utf-8", "windows-1252"),
        ("iso-8859-2", "windows-1252"),
    ]


def test_fixed_encoding_tree_matches_one_shot_parse():
    import xml.etree.ElementTree as ElementTree

    data = _late_conflict_data()

    fixed_parser = HTMLParser(namespaceHTMLElements=False)
    fixed_tree = fixed_parser.parse(ReadOnlyStream(data), useChardet=False,
                                    allowEncodingChange=False)

    one_shot_parser = HTMLParser(namespaceHTMLElements=False)
    one_shot_tree = one_shot_parser.parse(
        BytesIO(data), useChardet=False, override_encoding="windows-1252")

    serialize = lambda tree: ElementTree.tostring(tree, encoding="unicode")
    assert serialize(fixed_tree) == serialize(one_shot_tree)


def test_fixed_encoding_without_conflict_matches_default_parse():
    import xml.etree.ElementTree as ElementTree

    data = ("<html><head><meta charset='utf-8'></head>"
            "<body><p>café</p></body></html>").encode("utf-8")

    default_tree = HTMLParser().parse(BytesIO(data), useChardet=False)
    fixed_tree = HTMLParser().parse(BytesIO(data), useChardet=False,
                                    allowEncodingChange=False)

    serialize = lambda tree: ElementTree.tostring(tree, encoding="unicode")
    assert serialize(fixed_tree) == serialize(default_tree)


def test_default_mode_still_reparses_conflict():
    parser = HTMLParser()
    parser.parse(BytesIO(_late_conflict_data()), useChardet=False)

    assert parser.documentEncoding == "utf-8"
    assert parser.encodingChangeResult == _inputstream.ENCODING_REPARSED
    assert parser.encodingConflicts == []


def test_fixed_encoding_stream_change_encoding_dispositions():
    stream = _inputstream.HTMLBinaryInputStream(
        ReadOnlyStream(_late_conflict_data()), useChardet=False,
        allowEncodingChange=False)

    # A UTF-16 declaration is mapped to UTF-8 by the encoding rules, and the
    # conflict with windows-1252 is recorded rather than applied.
    stream.changeEncoding("utf-16le")
    assert stream.charEncoding[0].name == "windows-1252"
    assert stream.encodingChangeResult == _inputstream.ENCODING_IGNORED
    assert stream.encodingConflicts == [("utf-8", "windows-1252")]

    # An unparseable declaration does not change the disposition at all.
    stream.changeEncoding("totally-bogus")
    assert stream.encodingChangeResult == _inputstream.ENCODING_IGNORED
    assert stream.encodingConflicts == [("utf-8", "windows-1252")]

    # A matching declaration settles (confirms) the current encoding.
    stream.changeEncoding("windows-1252")
    assert stream.charEncoding[1] == "certain"
    assert stream.encodingChangeResult == _inputstream.ENCODING_CONFIRMED


def test_fixed_encoding_tolerated_for_unicode_input():
    parser = HTMLParser()
    parser.parse("<meta charset='utf-8'><p>ok", allowEncodingChange=False)
    assert parser.encodingChangeResult is None
    assert parser.encodingConflicts == []

