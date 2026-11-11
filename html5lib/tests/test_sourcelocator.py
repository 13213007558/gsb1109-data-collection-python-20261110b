from __future__ import absolute_import, division, unicode_literals

import pytest

from . import support  # noqa

from html5lib import HTMLParser
from html5lib._sourcelocator import (
    SOURCE_ORIGIN_IMPLIED, SOURCE_ORIGIN_PRESENT)
from html5lib.constants import namespaces

HTML_NS = namespaces["html"]


BACKENDS = ["etree", "dom"]
try:
    import lxml.etree  # noqa
except ImportError:
    pass
else:
    BACKENDS.append("lxml")


def make_parser(backend):
    return HTMLParser(tree=backend, source_locator=True)


def _root(doc, backend):
    # The lxml builder returns an ElementTree, whose root is fetched separately.
    if hasattr(doc, "getroot"):
        return doc.getroot()
    return doc


def find_local(doc, backend, name):
    if backend == "dom":
        return doc.getElementsByTagName(name)[0]
    root = _root(doc, backend)
    tag = "{%s}%s" % (HTML_NS, name)
    if root.tag == tag:
        return root
    return root.find(".//" + tag)


def find_comments(doc, backend):
    if backend == "dom":
        from xml.dom import minidom
        comments = []

        def walk(node):
            for child in node.childNodes:
                if child.nodeType == child.COMMENT_NODE:
                    comments.append(child)
                walk(child)
        walk(doc)
        return comments
    comments = []
    for element in _root(doc, backend).iter():
        if not isinstance(element.tag, str):
            comments.append(element)
    return comments


@pytest.mark.parametrize("backend", BACKENDS)
def test_locator_disabled_by_default(backend):
    parser = HTMLParser(tree=backend)
    assert parser.source_locator is None
    parser.parse("<p>x</p>")
    assert parser.source_locator is None


@pytest.mark.parametrize("backend", BACKENDS)
def test_present_and_implied_elements(backend):
    parser = make_parser(backend)
    doc = parser.parse("<p>x</p>")
    locator = parser.source_locator

    html = find_local(doc, backend, "html")
    head = find_local(doc, backend, "head")
    body = find_local(doc, backend, "body")
    paragraph = find_local(doc, backend, "p")

    # The structural containers are synthesized by the parser.
    for node in (html, head, body):
        assert locator.sourceOf(node).origin == SOURCE_ORIGIN_IMPLIED
        assert locator.isImplied(node)
        assert locator.locationOf(node) is None

    # The real <p> came from markup and points back at it.
    info = locator.sourceOf(paragraph)
    assert info.origin == SOURCE_ORIGIN_PRESENT
    assert not locator.isImplied(paragraph)
    location = locator.locationOf(paragraph)
    assert location.startOffset == 0
    assert "<p>x</p>"[location.startOffset:location.endOffset] == "<p>"


@pytest.mark.parametrize("backend", BACKENDS)
def test_foster_parented_text_keeps_original_location(backend):
    source = "<table><tr><td>cell</td></tr>REMARK<tr><td>x</td></tr></table>"
    parser = make_parser(backend)
    doc = parser.parse(source)
    locator = parser.source_locator
    body = find_local(doc, backend, "body")

    # The remark is moved out of the table (foster parenting) but its range
    # still points at the remark in the original input.
    ranges = locator.textRangesOf(body)
    assert len(ranges) == 1
    location = ranges[0].location
    assert source[location.startOffset:location.endOffset] == "REMARK"
    assert ranges[0].text == "REMARK"
    assert locator.textLocationOf(body) == location

    # The table itself is present; its implicit tbody is implied.
    table = find_local(doc, backend, "table")
    assert locator.sourceOf(table).origin == SOURCE_ORIGIN_PRESENT
    tbody = find_local(doc, backend, "tbody")
    assert locator.isImplied(tbody)


@pytest.mark.parametrize("backend", BACKENDS)
def test_composite_text_records_each_input_fragment(backend):
    source = "<p>a &amp; b</p>"
    parser = make_parser(backend)
    doc = parser.parse(source)
    paragraph = find_local(doc, backend, "p")
    ranges = parser.source_locator.textRangesOf(paragraph)

    # "a ", "&" (from "&amp;"), " ", "b" are emitted as separate tokens.
    assert "".join(rng.text for rng in ranges) == "a & b"
    # Each range maps back to the exact characters it consumed in the source.
    assert [source[rng.location.startOffset:rng.location.endOffset]
            for rng in ranges] == ["a ", "&amp;", " ", "b"]
    # More than one range proves the text was assembled from multiple inputs.
    assert len(ranges) > 1


@pytest.mark.parametrize("backend", BACKENDS)
def test_newline_normalization_coordinates(backend):
    source = "a\r\nb\rc"
    parser = make_parser(backend)
    doc = parser.parse(source)
    body = find_local(doc, backend, "body")
    ranges = parser.source_locator.textRangesOf(body)

    # Coordinates describe the normalized stream where CR LF and CR each
    # became a single LF, so the text is "a\nb\nc" (5 normalized chars).
    assert ranges[0].text == "a\nb\nc"
    location = ranges[0].location
    assert (location.startOffset, location.endOffset) == (0, 5)
    assert (location.startLine, location.startColumn) == (1, 0)
    # The final "c" is on line 3; the end points one column past it.
    assert (location.endLine, location.endColumn) == (3, 1)


def test_multiline_line_and_column():
    source = "<div>\n  hello\n</div>"
    parser = make_parser("etree")
    doc = parser.parse(source)
    div = find_local(doc, "etree", "div")
    ranges = parser.source_locator.textRangesOf(div)
    # First fragment is the newline plus the two-space indent on line 2.
    first = ranges[0].location
    assert (first.startLine, first.startColumn) == (1, 5)
    assert (first.endLine, first.endColumn) == (2, 2)
    assert ranges[0].text == "\n  "


@pytest.mark.parametrize("backend", BACKENDS)
def test_adoption_agency_clone_is_implied_but_text_is_located(backend):
    source = "<p>1<b>2<i>3</b>4</i>5</p>"
    parser = make_parser(backend)
    doc = parser.parse(source)
    locator = parser.source_locator

    italics = doc.getElementsByTagName("i") if backend == "dom" \
        else list(_root(doc, backend).iter("{%s}i" % HTML_NS))
    origins = {locator.sourceOf(node).origin for node in italics}
    # The original <i> is present, the adoption-agency clone is implied.
    assert SOURCE_ORIGIN_PRESENT in origins
    assert SOURCE_ORIGIN_IMPLIED in origins

    # Even though the clone is parser-generated, its text "4" still points at
    # the original input characters.
    for node in italics:
        for rng in locator.textRangesOf(node):
            if rng.text == "4":
                assert source[rng.location.startOffset:
                              rng.location.endOffset] == "4"


@pytest.mark.parametrize("backend", BACKENDS)
def test_reconstructed_formatting_element_is_implied(backend):
    source = "<b>one<p>two</b>three"
    parser = make_parser(backend)
    doc = parser.parse(source)
    locator = parser.source_locator

    if backend == "dom":
        bolds = doc.getElementsByTagName("b")
    else:
        bolds = list(_root(doc, backend).iter("{%s}b" % HTML_NS))

    origins = sorted(locator.sourceOf(node).origin for node in bolds)
    assert origins == [SOURCE_ORIGIN_IMPLIED, SOURCE_ORIGIN_PRESENT]


@pytest.mark.parametrize("backend", BACKENDS)
def test_comment_present_location(backend):
    source = "<div><!-- hi --></div>"
    parser = make_parser(backend)
    doc = parser.parse(source)
    comments = find_comments(doc, backend)
    assert comments
    comment = comments[0]
    location = parser.source_locator.locationOf(comment)
    assert source[location.startOffset:location.endOffset] == "<!-- hi -->"
    assert parser.source_locator.sourceOf(comment).origin == \
        SOURCE_ORIGIN_PRESENT


def test_doctype_present_location():
    source = "<!doctype html><p>x</p>"
    parser = make_parser("dom")
    doc = parser.parse(source)
    doctype = next(node for node in doc.childNodes
                   if node.nodeType == node.DOCUMENT_TYPE_NODE)
    location = parser.source_locator.locationOf(doctype)
    assert source[location.startOffset:location.endOffset] == "<!doctype html>"


@pytest.mark.parametrize("backend", BACKENDS)
def test_fragment_text_migrates_to_fragment(backend):
    parser = make_parser(backend)
    fragment = parser.parseFragment("<b>bold</b> tail")
    source = "<b>bold</b> tail"
    if backend == "lxml":
        # lxml returns a list of element nodes plus bare text strings; the
        # tail is attached to the last element, which also keeps its own text.
        nodes = [item for item in fragment if hasattr(item, "tag")]
        assert nodes
        target = nodes[-1]
        all_ranges = parser.source_locator.textRangesOf(target)
        # Isolate the migrated tail from the element's own leading text.
        tail_start = source.index(" tail")
        tail_ranges = [rng for rng in all_ranges
                       if rng.location.startOffset >= tail_start]
    else:
        target = fragment
        tail_ranges = parser.source_locator.textRangesOf(target)
    # The tail text was the container's text but is now surfaced from the
    # fragment; its provenance must follow the move.
    assert "".join(rng.text for rng in tail_ranges) == " tail"
    for rng in tail_ranges:
        assert source[rng.location.startOffset:rng.location.endOffset] == rng.text


def test_encoding_reparse_resets_and_still_tracks():
    # A meta charset forces a re-read of the stream under a new encoding.
    data = b"<meta charset=utf-8><p>caf\xc3\xa9</p>"
    parser = make_parser("etree")
    doc = parser.parse(data)
    assert parser.documentEncoding == "utf-8"
    paragraph = find_local(doc, "etree", "p")
    ranges = parser.source_locator.textRangesOf(paragraph)
    assert ranges and ranges[0].text == "café"
    decoded = data.decode("utf-8")
    rng = ranges[0]
    assert decoded[rng.location.startOffset:rng.location.endOffset] == "café"


def test_unknown_node_has_no_source():
    parser = make_parser("etree")
    doc = parser.parse("<p>x</p>")
    # A node created after parsing is unknown to the locator.
    from xml.etree import ElementTree
    foreign = ElementTree.Element("custom")
    assert parser.source_locator.sourceOf(foreign) is None
    assert parser.source_locator.textRangesOf(foreign) == []
    assert parser.source_locator.locationOf(foreign) is None
