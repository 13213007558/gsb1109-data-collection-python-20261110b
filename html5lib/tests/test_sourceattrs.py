from __future__ import absolute_import, division, unicode_literals

import difflib
from io import BytesIO, StringIO

import pytest

import html5lib
from html5lib._tokenizer import HTMLTokenizer
from html5lib.sourceattrs import (find_attributes, main, normalize_source,
                                  parsed_attribute_values,
                                  replace_attribute_values)


def slice_value(text, record):
    if record["valueStart"] is None:
        return None
    return text[record["valueStart"]:record["valueEnd"]]


def test_find_attribute_spans():
    doc = '<iframe data-source="source-7" data-id=\'x\' data-bare=ok></iframe>'
    text, records = find_attributes(doc, tags={"iframe"})

    assert [record["name"] for record in records] == [
        "data-source", "data-id", "data-bare"]
    assert [slice_value(text, record) for record in records] == [
        "source-7", "x", "ok"]
    assert all(record["tag"] == "iframe" for record in records)
    assert all(record["used"] for record in records)
    assert [text[record["nameStart"]:record["nameEnd"]]
            for record in records] == ["data-source", "data-id", "data-bare"]
    assert all(record["line"] == 1 for record in records)
    assert [record["column"] for record in records] == [
        record["nameStart"] + 1 for record in records]
    assert records[0]["column"] == 9


def test_filters_are_case_insensitive_and_restrictive():
    doc = ('<iframe DATA-SOURCE="a"></iframe><img data-source="b">'
           '<span data-source="c" data-other="d"></span>')

    _, records = find_attributes(doc, tags={"IFRAME", "span"},
                                 attributes={"data-source"})
    assert [(record["tag"], record["name"]) for record in records] == [
        ("iframe", "data-source"), ("span", "data-source")]

    _, records = find_attributes(doc, attributes={"data-other"})
    assert [(record["tag"], record["name"]) for record in records] == [
        ("span", "data-other")]

    _, records = find_attributes(doc, tags={"img"})
    assert [(record["tag"], record["name"]) for record in records] == [
        ("img", "data-source")]


def test_quotes_and_whitespace_are_preserved():
    doc = ('<iframe data-source="source-7" data-b=\'source-7\' '
           'data-c=source-7 data-d  =  "source-7" ></iframe>')
    new_text, changes = replace_attribute_values(
        doc, {"source-7": "source-42"}, tags={"iframe"})

    assert new_text == (
        '<iframe data-source="source-42" data-b=\'source-42\' '
        'data-c=source-42 data-d  =  "source-42" ></iframe>')
    assert len(changes) == 4
    assert all(change["oldValue"] == "source-7" for change in changes)
    assert all(change["newValue"] == "source-42" for change in changes)


def test_script_style_comments_and_text_are_untouched():
    doc = """<!DOCTYPE html>
<html><head>
<!-- <img data-source="source-7"> comment -->
<script>var s = '<img data-source="source-7">';</script>
<style>.x { background: url('?data-source="source-7"'); }</style>
</head><body>
<iframe data-source="source-7"></iframe>
<textarea><img data-source="source-7"></textarea>
<p>data-source="source-7" as visible text</p>
<img alt='data-source="source-7" inside another attribute'>
</body></html>"""

    new_text, changes = replace_attribute_values(
        doc, {"source-7": "source-42"},
        tags={"iframe"}, attributes={"data-source"})

    assert len(changes) == 1
    assert changes[0]["tag"] == "iframe"
    # Everything except the single real attribute stays byte-for-byte equal.
    start = changes[0]["valueStart"]
    end = changes[0]["valueEnd"]
    assert new_text[:start] == doc[:start]
    assert new_text[start + len("source-42"):] == doc[end:]
    assert new_text.count("source-7") == doc.count("source-7") - 1


def find_element(doc, tag_name):
    return next(element for element in doc.iter()
                if element.tag.endswith(tag_name))


def test_duplicate_attribute_only_used_occurrence_changes():
    doc = '<iframe data-source="source-7" data-source="source-9"></iframe>'

    _, records = find_attributes(doc, attributes={"data-source"})
    assert len(records) == 1
    assert records[0]["used"] is True
    assert records[0]["value"] == "source-7"

    _, all_records = find_attributes(
        doc, attributes={"data-source"}, include_duplicates=True)
    assert [(record["value"], record["used"], record["duplicate"])
            for record in all_records] == [
        ("source-7", True, False), ("source-9", False, True)]

    new_text, changes = replace_attribute_values(
        doc, {"source-7": "source-42", "source-9": "source-43"},
        attributes={"data-source"})
    assert new_text == (
        '<iframe data-source="source-42" data-source="source-9"></iframe>')
    assert len(changes) == 1

    parsed = html5lib.parse(new_text)
    element = find_element(parsed, "iframe")
    assert element.attrib["data-source"] == "source-42"


def test_duplicate_occurrences_can_be_rewritten_explicitly():
    doc = '<iframe data-source="source-7" data-source="source-9"></iframe>'
    new_text, changes = replace_attribute_values(
        doc, {"source-7": "source-42", "source-9": "source-43"},
        attributes={"data-source"}, include_duplicates=True)

    assert new_text == (
        '<iframe data-source="source-42" data-source="source-43"></iframe>')
    assert [change["newValue"] for change in changes] == [
        "source-42", "source-43"]
    # The first occurrence is still the value retained by the parser.
    element = find_element(html5lib.parse(new_text), "iframe")
    assert element.attrib["data-source"] == "source-42"


def test_valueless_attribute_is_reported_but_not_rewritten():
    doc = '<iframe data-source disabled data-empty=""></iframe>'
    text, records = find_attributes(doc, tags={"iframe"})

    by_name = {record["name"]: record for record in records}
    assert by_name["data-source"]["valueStart"] is None
    assert by_name["data-source"]["valueEnd"] is None
    # An empty quoted value still has a (zero-width) value span.
    assert by_name["data-empty"]["valueStart"] == by_name[
        "data-empty"]["valueEnd"]
    assert slice_value(text, by_name["data-empty"]) == ""

    # Valueless attributes cannot be rewritten; only the empty quoted one is.
    new_text, changes = replace_attribute_values(
        doc, lambda record: "never", tags={"iframe"})
    assert new_text == (
        '<iframe data-source disabled data-empty="never"></iframe>')
    assert [change["name"] for change in changes] == ["data-empty"]


def test_callback_can_skip_individual_matches():
    doc = '<iframe data-source="keep"></iframe><img data-source="change">'
    new_text, changes = replace_attribute_values(
        doc,
        lambda record: "changed" if record["tag"] == "img" else None,
        attributes={"data-source"})

    assert new_text == (
        '<iframe data-source="keep"></iframe><img data-source="changed">')
    assert [change["tag"] for change in changes] == ["img"]


def test_reparse_uses_new_source_number():
    doc = '<html><body><iframe data-source="old-1"></iframe></body></html>'
    new_text, changes = replace_attribute_values(
        doc, {"old-1": "new-9000"},
        tags={"iframe"}, attributes={"data-source"})

    assert len(changes) == 1
    parsed = html5lib.parse(new_text)
    element = find_element(parsed, "iframe")
    assert element.attrib["data-source"] == "new-9000"


def test_diff_only_covers_value_spans():
    doc = ("<html>\n"
           "  <body>\n"
           '    <iframe data-source="old" width="3"></iframe>\n'
           '    <img data-source="old">\n'
           "  </body>\n</html>")
    new_text, changes = replace_attribute_values(
        doc, {"old": "new"}, attributes={"data-source"})

    matcher = difflib.SequenceMatcher(None, doc, new_text)
    replace_ops = [opcode for opcode in matcher.get_opcodes()
                   if opcode[0] == "replace"]
    assert len(replace_ops) == len(changes) == 2
    for opcode, change in zip(replace_ops, changes):
        _, i1, i2, j1, j2 = opcode
        assert (i1, i2) == (change["valueStart"], change["valueEnd"])
        assert doc[i1:i2] == "old"
        assert new_text[j1:j2] == "new"


def test_entity_value_span_covers_raw_source():
    doc = '<iframe data-source="a&amp;b"></iframe>'
    text, records = find_attributes(doc, attributes={"data-source"})
    record = records[0]

    assert record["value"] == "a&b"
    assert slice_value(text, record) == "a&amp;b"

    new_text, changes = replace_attribute_values(
        doc, {"a&b": "c&amp;d"}, attributes={"data-source"})
    assert new_text == '<iframe data-source="c&amp;d"></iframe>'
    element = find_element(html5lib.parse(new_text), "iframe")
    assert element.attrib["data-source"] == "c&d"


def test_normalize_source_handles_bom_and_newlines():
    raw = b"\xef\xbb\xbf<iframe\r\n data-source='a'></iframe>"
    text = normalize_source(raw)
    assert text == "<iframe\n data-source='a'></iframe>"

    new_text, changes = replace_attribute_values(
        raw, {"a": "b"}, tags={"iframe"})
    assert new_text == "<iframe\n data-source='b'></iframe>"
    assert changes[0]["oldValue"] == "a"


def test_normalize_source_accepts_files():
    assert normalize_source(StringIO("a\rb")) == "a\nb"
    binary = BytesIO(b"a\r\nb")
    assert normalize_source(binary) == "a\nb"


def test_offsets_hold_across_chunk_boundaries():
    doc = "<p data-source='1234567890' data-other=xy></p>"
    records = []
    tokenizer = HTMLTokenizer(StringIO(doc), sourceAttrs=records)
    tokenizer.stream._defaultChunkSize = 3
    list(tokenizer)

    assert [doc[record["nameStart"]:record["nameEnd"]]
            for record in records] == ["data-source", "data-other"]
    assert [slice_value(doc, record) for record in records] == [
        "1234567890", "xy"]


def test_source_position_tracks_unget():
    tokenizer = HTMLTokenizer(StringIO("abcd"))
    stream = tokenizer.stream
    assert stream.sourcePosition() == 0
    stream.char()
    stream.char()
    assert stream.sourcePosition() == 2
    stream.unget(stream.chunk[stream.chunkOffset - 1])
    assert stream.sourcePosition() == 1
    stream.char()
    assert stream.sourcePosition() == 2


@pytest.mark.parametrize("doc", [
    "",
    "<!DOCTYPE html><html></html>",
    "plain text without tags",
])
def test_no_matches(doc):
    new_text, changes = replace_attribute_values(
        doc, lambda record: "x", attributes={"data-source"})
    assert new_text == doc
    assert changes == []


def test_parsed_attribute_values_filters_elements():
    doc = ('<iframe data-source="a" other="x"></iframe>'
           '<img data-source="b"><p data-source="c">t</p>')
    assert parsed_attribute_values(
        doc, tags={"iframe", "img"}, attributes={"data-source"}) == [
        ("iframe", "data-source", "a"),
        ("img", "data-source", "b"),
    ]


def test_cli_diffs_verifies_and_reports_exit_code(tmp_path, capsys):
    page = tmp_path / "page.html"
    page.write_text(
        '<iframe data-source="old" data-source="keep"></iframe>',
        encoding="utf-8")

    code = main([str(page), "--attr", "data-source", "--tag", "iframe",
                 "--map", "old=new", "--verify"])
    out = capsys.readouterr().out

    assert code == 0
    assert "iframe@data-source 'old' -> 'new'" in out
    assert "-<iframe data-source=\"old\"" in out
    assert "+<iframe data-source=\"new\"" in out
    assert "verified" in out and "data-source='new'" in out
    # Without in-place the file on disk is untouched.
    assert page.read_text(encoding="utf-8") == (
        '<iframe data-source="old" data-source="keep"></iframe>')

    code = main([str(page), "--attr", "data-source", "--map", "zzz=yyy"])
    assert code == 1


def test_cli_in_place_and_duplicates(tmp_path, capsys):
    page = tmp_path / "page.html"
    page.write_text(
        '<iframe data-source="old" data-source="dup"></iframe>',
        encoding="utf-8")

    code = main([str(page), "--attr", "data-source",
                 "--map", "old=new", "--map", "dup=new2",
                 "--include-duplicates", "--in-place"])
    assert code == 0
    assert page.read_text(encoding="utf-8") == (
        '<iframe data-source="new" data-source="new2"></iframe>')
    capsys.readouterr()


def test_cli_rejects_bad_mapping():
    with pytest.raises(ValueError):
        main(["-", "--attr", "data-source", "--map", "missing-equals"])
