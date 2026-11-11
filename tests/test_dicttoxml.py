from xmltodict import parse, unparse
import pytest
import re
from io import BytesIO, StringIO
from textwrap import dedent

from xmltodict import (
    IncompleteExportError,
    StreamingUnparser,
    stream_unparse,
)

_HEADER_RE = re.compile(r'^[^\n]*\n')


def _strip(fullxml):
    return _HEADER_RE.sub('', fullxml)


def test_root():
    obj = {'a': None}
    assert obj == parse(unparse(obj))
    assert unparse(obj) == unparse(parse(unparse(obj)))


def test_simple_cdata():
    obj = {'a': 'b'}
    assert obj == parse(unparse(obj))
    assert unparse(obj) == unparse(parse(unparse(obj)))


def test_cdata():
    obj = {'a': {'#text': 'y'}}
    assert obj == parse(unparse(obj), force_cdata=True)
    assert unparse(obj) == unparse(parse(unparse(obj)))


def test_attrib():
    obj = {'a': {'@href': 'x'}}
    assert obj == parse(unparse(obj))
    assert unparse(obj) == unparse(parse(unparse(obj)))


def test_attrib_and_cdata():
    obj = {'a': {'@href': 'x', '#text': 'y'}}
    assert obj == parse(unparse(obj))
    assert unparse(obj) == unparse(parse(unparse(obj)))


def test_list():
    obj = {'a': {'b': ['1', '2', '3']}}
    assert obj == parse(unparse(obj))
    assert unparse(obj) == unparse(parse(unparse(obj)))


def test_list_expand_iter():
    obj = {'a': {'b': [['1', '2'], ['3',]]}}
    #assert obj == parse(unparse(obj, expand_iter="item")))
    exp_xml = dedent('''\
    <?xml version="1.0" encoding="utf-8"?>
    <a><b><item>1</item><item>2</item></b><b><item>3</item></b></a>''')
    assert exp_xml == unparse(obj, expand_iter="item")


def test_generator():
    obj = {'a': {'b': ['1', '2', '3']}}

    def lazy_obj():
        return {'a': {'b': (i for i in ('1', '2', '3'))}}
    assert obj == parse(unparse(lazy_obj()))
    assert unparse(lazy_obj()) == unparse(parse(unparse(lazy_obj())))


def test_no_root():
    with pytest.raises(ValueError):
        unparse({})


def test_multiple_roots():
    with pytest.raises(ValueError):
        unparse({'a': '1', 'b': '2'})
    with pytest.raises(ValueError):
        unparse({'a': ['1', '2', '3']})


def test_no_root_nofulldoc():
    assert unparse({}, full_document=False) == ''


def test_multiple_roots_nofulldoc():
    obj = {"a": 1, "b": 2}
    xml = unparse(obj, full_document=False)
    assert xml == '<a>1</a><b>2</b>'
    obj = {'a': [1, 2]}
    xml = unparse(obj, full_document=False)
    assert xml == '<a>1</a><a>2</a>'


def test_nested():
    obj = {'a': {'b': '1', 'c': '2'}}
    assert obj == parse(unparse(obj))
    assert unparse(obj) == unparse(parse(unparse(obj)))
    obj = {'a': {'b': {'c': {'@a': 'x', '#text': 'y'}}}}
    assert obj == parse(unparse(obj))
    assert unparse(obj) == unparse(parse(unparse(obj)))


def test_semistructured():
    xml = '<a>abc<d/>efg</a>'
    assert _strip(unparse(parse(xml))) == '<a><d></d>abcefg</a>'


def test_preprocessor():
    obj = {"a": {"b:int": [1, 2], "b": "c"}}

    def p(key, value):
        try:
            key, _ = key.split(':')
        except ValueError:
            pass
        return key, value

    assert _strip(unparse(obj, preprocessor=p)) == '<a><b>1</b><b>2</b><b>c</b></a>'


def test_preprocessor_skipkey():
    obj = {'a': {'b': 1, 'c': 2}}

    def p(key, value):
        if key == 'b':
            return None
        return key, value

    assert _strip(unparse(obj, preprocessor=p)) == '<a><c>2</c></a>'


def test_attr_order_roundtrip():
    xml = '<root a="1" b="2" c="3"></root>'
    assert xml == _strip(unparse(parse(xml)))


def test_pretty_print():
    obj = {
        "a": {
            "b": [{"c": [1, 2]}, 3],
            "x": "y",
        }
    }
    newl = '\n'
    indent = '....'
    xml = dedent('''\
    <?xml version="1.0" encoding="utf-8"?>
    <a>
    ....<b>
    ........<c>1</c>
    ........<c>2</c>
    ....</b>
    ....<b>3</b>
    ....<x>y</x>
    </a>''')
    assert xml == unparse(obj, pretty=True, newl=newl, indent=indent)


def test_unparse_with_element_comment():
    obj = {"a": {"#comment": "note", "b": "1"}}
    xml = _strip(unparse(obj, full_document=True))
    assert xml == "<a><!--note--><b>1</b></a>"


def test_unparse_with_multiple_element_comments():
    obj = {"a": {"#comment": ["n1", "n2"], "b": "1"}}
    xml = _strip(unparse(obj, full_document=True))
    assert xml == "<a><!--n1--><!--n2--><b>1</b></a>"


def test_unparse_with_top_level_comment():
    obj = {"#comment": "top", "a": "1"}
    xml = _strip(unparse(obj, full_document=True))
    assert xml == "<!--top--><a>1</a>"


def test_unparse_with_multiple_top_level_comments():
    obj = {"#comment": ["t1", "t2"], "a": "1"}
    xml = _strip(unparse(obj, full_document=True))
    assert xml == "<!--t1--><!--t2--><a>1</a>"


def test_unparse_with_bytes_comment_uses_output_encoding():
    obj = {"#comment": b"caf\xe9", "a": "1"}
    xml = _strip(unparse(obj, full_document=True, encoding="iso-8859-1"))
    assert xml == "<!--caf\xe9--><a>1</a>"


def test_unparse_invalid_bytes_comment_replaced_by_default():
    obj = {"#comment": b"\xff", "a": "1"}
    xml = _strip(unparse(obj, full_document=True, encoding="utf-8"))
    assert xml == "<!--\ufffd--><a>1</a>"


def test_unparse_rejects_invalid_bytes_comment_for_encoding_with_strict():
    obj = {"#comment": b"\xff", "a": "1"}
    with pytest.raises(UnicodeDecodeError, match="'utf-8' codec can't decode byte 0xff"):
        unparse(
            obj,
            full_document=True,
            encoding="utf-8",
            bytes_errors="strict",
        )


def test_unparse_rejects_comment_with_double_hyphen():
    obj = {"#comment": "bad--comment", "a": "1"}
    with pytest.raises(ValueError, match="cannot contain '--'"):
        unparse(obj, full_document=True)


def test_unparse_rejects_comment_ending_with_hyphen():
    obj = {"#comment": "trailing-", "a": "1"}
    with pytest.raises(ValueError, match="cannot end with '-'"):
        unparse(obj, full_document=True)


def test_pretty_print_with_int_indent():
    obj = {
        "a": {
            "b": [{"c": [1, 2]}, 3],
            "x": "y",
        }
    }
    newl = '\n'
    indent = 2
    xml = dedent('''\
    <?xml version="1.0" encoding="utf-8"?>
    <a>
      <b>
        <c>1</c>
        <c>2</c>
      </b>
      <b>3</b>
      <x>y</x>
    </a>''')
    assert xml == unparse(obj, pretty=True, newl=newl, indent=indent)


def test_comment_roundtrip_limited():
    # Input with top-level comments and an element-level comment
    xml = """
    <!--top1--><a><b>1</b><!--e1--></a><!--top2-->
    """
    # Parse with comment processing enabled
    parsed1 = parse(xml, process_comments=True)
    # Unparse and parse again (roundtrip)
    xml2 = unparse(parsed1)
    parsed2 = parse(xml2, process_comments=True)

    # Content preserved
    assert 'a' in parsed2
    assert parsed2['a']['b'] == '1'

    # Element-level comment preserved under '#comment'
    assert parsed2['a']['#comment'] == 'e1'

    # Top-level comments preserved as a list (order not guaranteed)
    top = parsed2.get('#comment')
    assert top is not None
    top_list = top if isinstance(top, list) else [top]
    assert set(top_list) == {'top1', 'top2'}


def test_encoding():
    value = chr(39321)
    obj = {'a': value}
    utf8doc = unparse(obj, encoding='utf-8')
    latin1doc = unparse(obj, encoding='iso-8859-1')
    assert parse(utf8doc) == parse(latin1doc)
    assert parse(utf8doc) == obj


def test_fulldoc():
    xml_declaration_re = re.compile(
        '^' + re.escape('<?xml version="1.0" encoding="utf-8"?>'))
    assert xml_declaration_re.match(unparse({'a': 1}))
    assert not xml_declaration_re.match(unparse({'a': 1}, full_document=False))


def test_non_string_value():
    obj = {'a': 1}
    assert '<a>1</a>' == _strip(unparse(obj))


def test_non_string_attr():
    obj = {'a': {'@attr': 1}}
    assert '<a attr="1"></a>' == _strip(unparse(obj))


def test_short_empty_elements():
    obj = {'a': None}
    assert '<a/>' == _strip(unparse(obj, short_empty_elements=True))


def test_namespace_support():
    obj = {
        "http://defaultns.com/:root": {
            "@xmlns": {
                "": "http://defaultns.com/",
                "a": "http://a.com/",
                "b": "http://b.com/",
            },
            "http://defaultns.com/:x": {
                "@http://a.com/:attr": "val",
                "#text": "1",
            },
            "http://a.com/:y": "2",
            "http://b.com/:z": "3",
        },
    }
    ns = {
        'http://defaultns.com/': '',
        'http://a.com/': 'a',
        'http://b.com/': 'b',
    }

    expected_xml = '''<?xml version="1.0" encoding="utf-8"?>
<root xmlns="http://defaultns.com/" xmlns:a="http://a.com/" \
xmlns:b="http://b.com/"><x a:attr="val">1</x><a:y>2</a:y><b:z>3</b:z></root>'''
    xml = unparse(obj, namespaces=ns)

    assert xml == expected_xml


def test_xmlns_values_use_consistent_boolean_coercion():
    xml = unparse({"root": {"@xmlns": {"a": True}}}, full_document=False)
    assert xml == '<root xmlns:a="true"></root>'


def test_xmlns_dict_honors_custom_attr_prefix():
    # A namespace-declaration dict must be recognized under a custom
    # attr_prefix, like every other attribute — otherwise unparse cannot
    # round-trip what parse(..., attr_prefix="!") produced.
    xml = unparse(
        {"root": {"!xmlns": {"a": "http://a.com/"}, "!id": "7"}},
        attr_prefix="!",
        full_document=False,
    )
    assert xml == '<root xmlns:a="http://a.com/" id="7"></root>'


def test_namespace_roundtrip_honors_custom_attr_prefix():
    obj = parse(
        '<root xmlns:a="http://a.com/" id="7"><a:y>2</a:y></root>',
        process_namespaces=True,
        attr_prefix="!",
    )

    xml = unparse(
        obj,
        attr_prefix="!",
        namespaces={"http://a.com/": "a"},
        full_document=False,
    )

    assert xml == '<root id="7" xmlns:a="http://a.com/"><a:y>2</a:y></root>'


def test_xmlns_values_decode_bytes_with_output_encoding():
    xml = unparse(
        {"root": {"@xmlns": {"a": b"http://ex\xe9.com/"}}},
        full_document=False,
        encoding="iso-8859-1",
    )
    assert xml == '<root xmlns:a="http://ex\xe9.com/"></root>'


def test_xmlns_values_replace_invalid_bytes_by_default():
    xml = unparse(
        {"root": {"@xmlns": {"a": b"\xff"}}},
        full_document=False,
        encoding="utf-8",
    )
    assert xml == '<root xmlns:a="\ufffd"></root>'


def test_xmlns_values_reject_invalid_bytes_with_strict():
    with pytest.raises(UnicodeDecodeError, match="'utf-8' codec can't decode byte 0xff"):
        unparse(
            {"root": {"@xmlns": {"a": b"\xff"}}},
            full_document=False,
            encoding="utf-8",
            bytes_errors="strict",
        )


def test_boolean_unparse():
    expected_xml = '<?xml version="1.0" encoding="utf-8"?>\n<x>true</x>'
    xml = unparse(dict(x=True))
    assert xml == expected_xml

    expected_xml = '<?xml version="1.0" encoding="utf-8"?>\n<x>false</x>'
    xml = unparse(dict(x=False))
    assert xml == expected_xml

    expected_xml = '<?xml version="1.0" encoding="utf-8"?>\n<x attr="true"></x>'
    xml = unparse({'x': {'@attr': True}})
    assert xml == expected_xml

    expected_xml = '<?xml version="1.0" encoding="utf-8"?>\n<x attr="false"></x>'
    xml = unparse({'x': {'@attr': False}})
    assert xml == expected_xml


def test_unparse_bytes_in_attributes_and_cdata_use_output_encoding():
    xml = unparse({"x": {"@attr": b"caf\xe9", "#text": b"caf\xe9"}}, full_document=False, encoding="iso-8859-1")
    assert xml == '<x attr="caf\xe9">caf\xe9</x>'


def test_unparse_bytes_text_node_uses_output_encoding():
    xml = unparse({"x": b"caf\xe9"}, full_document=False, encoding="iso-8859-1")
    assert xml == "<x>caf\xe9</x>"


def test_unparse_bytes_text_node_with_expand_iter_uses_output_encoding():
    xml = unparse({"x": b"caf\xe9"}, full_document=False, encoding="iso-8859-1", expand_iter="item")
    assert xml == "<x>caf\xe9</x>"


def test_unparse_invalid_bytes_in_attributes_and_cdata_replaced_by_default():
    xml = unparse({"x": {"@attr": b"\xff", "#text": b"\xff"}}, full_document=False, encoding="utf-8")
    assert xml == '<x attr="\ufffd">\ufffd</x>'


def test_unparse_invalid_bytes_text_node_replaced_by_default():
    xml = unparse({"x": b"\xff"}, full_document=False, encoding="utf-8")
    assert xml == "<x>\ufffd</x>"


def test_unparse_rejects_invalid_bytes_in_attributes_and_cdata_for_encoding_with_strict():
    with pytest.raises(UnicodeDecodeError, match="'utf-8' codec can't decode byte 0xff"):
        unparse({"x": {"@attr": b"\xff"}}, full_document=False, encoding="utf-8", bytes_errors="strict")
    with pytest.raises(UnicodeDecodeError, match="'utf-8' codec can't decode byte 0xff"):
        unparse({"x": {"#text": b"\xff"}}, full_document=False, encoding="utf-8", bytes_errors="strict")


def test_unparse_rejects_invalid_bytes_text_node_for_encoding_with_strict():
    with pytest.raises(UnicodeDecodeError, match="'utf-8' codec can't decode byte 0xff"):
        unparse({"x": b"\xff"}, full_document=False, encoding="utf-8", bytes_errors="strict")


def test_unparse_rejects_invalid_bytes_errors_handler():
    with pytest.raises(ValueError, match="Invalid bytes_errors handler: nope"):
        unparse({"x": {"@attr": b"\xff"}}, full_document=False, bytes_errors="nope")


def test_rejects_tag_name_with_angle_brackets():
    # Minimal guard: disallow '<' or '>' to prevent breaking tag context
    with pytest.raises(ValueError):
        unparse({"m><tag>content</tag": "unsafe"}, full_document=False)


def test_rejects_attribute_name_with_angle_brackets():
    # Now we expect bad attribute names to be rejected
    with pytest.raises(ValueError):
        unparse(
            {"a": {"@m><tag>content</tag": "unsafe", "#text": "x"}},
            full_document=False,
        )


def test_rejects_malicious_xmlns_prefix():
    # xmlns prefixes go under @xmlns mapping; reject angle brackets in prefix
    with pytest.raises(ValueError):
        unparse(
            {
                "a": {
                    "@xmlns": {"m><bad": "http://example.com/"},
                    "#text": "x",
                }
            },
            full_document=False,
        )


def test_attribute_values_with_angle_brackets_are_escaped():
    # Attribute values should be escaped by XMLGenerator
    xml = unparse({"a": {"@attr": "1<middle>2", "#text": "x"}}, full_document=False)
    # The generated XML should contain escaped '<' and '>' within the attribute value
    assert 'attr="1&lt;middle&gt;2"' in xml


def test_rejects_tag_name_starting_with_question():
    with pytest.raises(ValueError):
        unparse({"?pi": "data"}, full_document=False)


def test_rejects_tag_name_starting_with_bang():
    with pytest.raises(ValueError):
        unparse({"!decl": "data"}, full_document=False)


def test_rejects_attribute_name_starting_with_question():
    with pytest.raises(ValueError):
        unparse({"a": {"@?weird": "x"}}, full_document=False)


def test_rejects_attribute_name_starting_with_bang():
    with pytest.raises(ValueError):
        unparse({"a": {"@!weird": "x"}}, full_document=False)


def test_rejects_xmlns_prefix_starting_with_question_or_bang():
    with pytest.raises(ValueError):
        unparse({"a": {"@xmlns": {"?p": "http://e/"}}}, full_document=False)
    with pytest.raises(ValueError):
        unparse({"a": {"@xmlns": {"!p": "http://e/"}}}, full_document=False)


def test_rejects_non_string_names():
    class Weird:
        def __str__(self):
            return "bad>name"

    # Non-string element key
    with pytest.raises(ValueError):
        unparse({Weird(): "x"}, full_document=False)
    # Non-string attribute key
    with pytest.raises(ValueError):
        unparse({"a": {Weird(): "x"}}, full_document=False)


def test_rejects_tag_name_with_slash():
    with pytest.raises(ValueError):
        unparse({"bad/name": "x"}, full_document=False)


def test_rejects_tag_name_with_whitespace():
    for name in ["bad name", "bad\tname", "bad\nname"]:
        with pytest.raises(ValueError):
            unparse({name: "x"}, full_document=False)


def test_rejects_attribute_name_with_slash():
    with pytest.raises(ValueError):
        unparse({"a": {"@bad/name": "x"}}, full_document=False)


def test_rejects_attribute_name_with_whitespace():
    for name in ["@bad name", "@bad\tname", "@bad\nname"]:
        with pytest.raises(ValueError):
            unparse({"a": {name: "x"}}, full_document=False)


def test_rejects_xmlns_prefix_with_slash_or_whitespace():
    # Slash
    with pytest.raises(ValueError):
        unparse({"a": {"@xmlns": {"bad/prefix": "http://e/"}}}, full_document=False)
    # Whitespace
    with pytest.raises(ValueError):
        unparse({"a": {"@xmlns": {"bad prefix": "http://e/"}}}, full_document=False)


def test_rejects_names_with_quotes_and_equals():
    # Element names
    for name in ['a"b', "a'b", "a=b"]:
        with pytest.raises(ValueError):
            unparse({name: "x"}, full_document=False)
    # Attribute names
    for name in ['@a"b', "@a'b", "@a=b"]:
        with pytest.raises(ValueError):
            unparse({"a": {name: "x"}}, full_document=False)
    # xmlns prefixes
    for prefix in ['a"b', "a'b", "a=b"]:
        with pytest.raises(ValueError):
            unparse({"a": {"@xmlns": {prefix: "http://e/"}}}, full_document=False)


def test_pretty_print_and_short_empty_elements_consistency():
    """Test that pretty and compact modes produce equivalent results when stripped.

    This test covers issue #352: Edge case with pretty_print and short_empty_elements.
    When short_empty_elements=True, empty elements should be written as <tag/>
    regardless of whether pretty printing is enabled.
    """
    # Test case from issue #352: empty list child
    input_dict = {"Foos": {"Foo": []}}

    compact = unparse(
        input_dict, pretty=False, short_empty_elements=True, full_document=False
    )
    pretty = unparse(
        input_dict, pretty=True, short_empty_elements=True, full_document=False
    )
    pretty_compacted = pretty.replace("\n", "").replace("\t", "")

    # They should be equal when pretty formatting is stripped
    assert pretty_compacted == compact
    assert compact == "<Foos/>"
    assert pretty_compacted == "<Foos/>"


def test_empty_list_filtering():
    """Test that empty lists are filtered out and don't create empty child elements."""
    # Test various cases with empty lists
    test_cases = [
        # Case 1: Single empty list child
        ({"Foos": {"Foo": []}}, "<Foos/>"),
        # Case 2: Multiple empty list children
        ({"Foos": {"Foo": [], "Bar": []}}, "<Foos/>"),
        # Case 3: Mixed empty and non-empty children
        ({"Foos": {"Foo": [], "Bar": "value"}}, "<Foos><Bar>value</Bar></Foos>"),
        # Case 4: Nested empty lists
        ({"Foos": {"Foo": {"Bar": []}}}, "<Foos><Foo/></Foos>"),
        # Case 5: Empty list with attributes
        ({"Foos": {"@attr": "value", "Foo": []}}, '<Foos attr="value"/>'),
    ]

    for input_dict, expected_compact in test_cases:
        # Test compact mode
        compact = unparse(
            input_dict,
            pretty=False,
            short_empty_elements=True,
            full_document=False,
        )
        assert compact == expected_compact

        # Test pretty mode
        pretty = unparse(
            input_dict,
            pretty=True,
            short_empty_elements=True,
            full_document=False,
        )
        pretty_compacted = pretty.replace("\n", "").replace("\t", "")
        assert pretty_compacted == expected_compact


def test_empty_list_filtering_with_short_empty_elements_false():
    """Test that empty lists are still filtered when short_empty_elements=False."""
    input_dict = {"Foos": {"Foo": []}}

    # With short_empty_elements=False, empty elements should be <tag></tag>
    compact = unparse(
        input_dict, pretty=False, short_empty_elements=False, full_document=False
    )
    pretty = unparse(
        input_dict, pretty=True, short_empty_elements=False, full_document=False
    )
    pretty_compacted = pretty.replace("\n", "").replace("\t", "")

    # They should be equal when pretty formatting is stripped
    assert pretty_compacted == compact
    assert compact == "<Foos></Foos>"
    assert pretty_compacted == "<Foos></Foos>"


def test_non_empty_lists_are_not_filtered():
    """Test that non-empty lists are not filtered out."""
    # Test with non-empty lists
    input_dict = {"Foos": {"Foo": ["item1", "item2"]}}

    compact = unparse(
        input_dict, pretty=False, short_empty_elements=True, full_document=False
    )
    pretty = unparse(
        input_dict, pretty=True, short_empty_elements=True, full_document=False
    )
    pretty_compacted = pretty.replace("\n", "").replace("\t", "")

    # The lists should be processed normally
    assert pretty_compacted == compact
    assert compact == "<Foos><Foo>item1</Foo><Foo>item2</Foo></Foos>"
    assert (
        pretty_compacted == "<Foos><Foo>item1</Foo><Foo>item2</Foo></Foos>"
    )


def test_empty_dict_vs_empty_list_behavior():
    """Test the difference between empty dicts and empty lists."""
    # Empty dict should create a child element
    input_dict_dict = {"Foos": {"Foo": {}}}
    compact_dict = unparse(
        input_dict_dict,
        pretty=False,
        short_empty_elements=True,
        full_document=False,
    )
    assert compact_dict == "<Foos><Foo/></Foos>"

    # Empty list should be filtered out
    input_dict_list = {"Foos": {"Foo": []}}
    compact_list = unparse(
        input_dict_list,
        pretty=False,
        short_empty_elements=True,
        full_document=False,
    )
    assert compact_list == "<Foos/>"

    # They should be different
    assert compact_dict != compact_list


def test_non_string_text_with_attributes():
    """Test that non-string #text values work when tag has attributes.

    This test covers GitHub issue #366: Tag value (#text) must be a string
    when tag has additional parameters - unparse.

    Also tests that plain values and explicit #text values are treated
    consistently (both go through the same conversion logic).
    """
    # Test cases for explicit #text values with attributes
    assert unparse({"a": {"@param": "test", "#text": 1}}, full_document=False) == '<a param="test">1</a>'

    assert unparse({"a": {"@param": 42, "#text": 3.14}}, full_document=False) == '<a param="42">3.14</a>'

    assert unparse({"a": {"@param": "flag", "#text": True}}, full_document=False) == '<a param="flag">true</a>'

    assert unparse({"a": {"@param": "test", "#text": None}}, full_document=False) == '<a param="test"></a>'

    assert unparse({"a": {"@param": "test", "#text": "string"}}, full_document=False) == '<a param="test">string</a>'

    assert unparse({"a": {"@attr1": "value1", "@attr2": 2, "#text": 100}}, full_document=False) == '<a attr1="value1" attr2="2">100</a>'

    # Test cases for plain values (should be treated the same as #text)
    assert unparse({"a": 1}, full_document=False) == '<a>1</a>'
    assert unparse({"a": 3.14}, full_document=False) == '<a>3.14</a>'
    assert unparse({"a": True}, full_document=False) == '<a>true</a>'
    assert unparse({"a": "hello"}, full_document=False) == '<a>hello</a>'
    assert unparse({"a": None}, full_document=False) == '<a></a>'

    # Consistency tests: plain values should match explicit #text values
    assert unparse({"a": 42}, full_document=False) == unparse({"a": {"#text": 42}}, full_document=False)

    assert unparse({"a": 3.14}, full_document=False) == unparse({"a": {"#text": 3.14}}, full_document=False)

    assert unparse({"a": True}, full_document=False) == unparse({"a": {"#text": True}}, full_document=False)

    assert unparse({"a": "hello"}, full_document=False) == unparse({"a": {"#text": "hello"}}, full_document=False)
    assert unparse({"a": None}, full_document=False) == unparse({"a": {"#text": None}}, full_document=False)


def test_none_text_with_short_empty_elements_and_attributes():
    obj = {"x": {"#text": None, "@pro": None}, "y": None}
    assert unparse(obj, short_empty_elements=True, full_document=False) == '<x pro=""/><y/>'


def test_none_attribute_serializes_as_empty_string():
    assert unparse({"x": {"@pro": None}}, full_document=False) == '<x pro=""></x>'


def test_unparse_skips_none_comment():
    obj = {"a": {"#comment": None, "b": "1"}}
    xml = _strip(unparse(obj, full_document=True))
    assert xml == "<a><b>1</b></a>"


def test_unparse_skips_empty_comment():
    obj = {"a": {"#comment": "", "b": "1"}}
    xml = _strip(unparse(obj, full_document=True))
    assert xml == "<a><b>1</b></a>"


def test_unparse_filters_none_and_empty_comments_in_list():
    obj = {"a": {"#comment": ["n1", None, "", "n2"], "b": "1"}}
    xml = _strip(unparse(obj, full_document=True))
    assert xml == "<a><!--n1--><!--n2--><b>1</b></a>"


def test_pretty_print_element_comments():
    obj = {"a": {"#comment": ["n1", "n2"], "b": "1"}}
    xml = dedent('''\
    <?xml version="1.0" encoding="utf-8"?>
    <a>
    \t<!--n1-->
    \t<!--n2-->
    \t<b>1</b>
    </a>''')
    assert xml == unparse(obj, pretty=True)


def test_pretty_print_top_level_comment_with_int_indent():
    obj = {"#comment": "top", "a": "1"}
    xml = dedent('''\
    <?xml version="1.0" encoding="utf-8"?>
    <!--top-->
    <a>1</a>''')
    assert xml == unparse(obj, pretty=True, indent=4)


# ---------------------------------------------------------------------------
# Streaming export (StreamingUnparser / stream_unparse)
# ---------------------------------------------------------------------------

def _stream(records, root_key="records", **kwargs):
    output = BytesIO()
    with StreamingUnparser(output, root_key, **kwargs) as exporter:
        for record in records:
            exporter.item(record)
    return output.getvalue().decode("utf-8")


def test_streaming_basic_export_and_roundtrip():
    records = [
        {"record": {"id": "1", "name": "a"}},
        {"record": {"id": "2", "name": "b"}},
    ]
    xml = _stream(records)
    assert xml == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<records>'
        '<record><id>1</id><name>a</name></record>'
        '<record><id>2</id><name>b</name></record>'
        '</records>'
    )
    assert parse(xml) == {
        "records": {
            "record": [
                {"id": "1", "name": "a"},
                {"id": "2", "name": "b"},
            ]
        }
    }


def test_streaming_matches_unparse_compact_and_pretty():
    bodies = [
        {"@id": "1", "#text": "first"},
        {"id": "2", "nested": {"k": ["a", "b"]}},
        None,
    ]
    bulk = {"records": {"record": bodies}}

    compact_stream = _stream(
        [{"record": body} for body in bodies], short_empty_elements=True)
    assert compact_stream == unparse(bulk, short_empty_elements=True)

    pretty_stream = _stream(
        [{"record": body} for body in bodies], pretty=True)
    assert pretty_stream == unparse(bulk, pretty=True)


def test_streaming_empty_collection_is_valid_document():
    xml = _stream([])
    assert xml == '<?xml version="1.0" encoding="utf-8"?>\n<records></records>'
    # The empty document must parse cleanly back into an empty root.
    assert parse(xml) == {"records": None}


def test_streaming_empty_collection_short_empty_elements():
    xml = _stream([], short_empty_elements=True)
    assert _strip(xml) == "<records/>"


def test_streaming_generator_source_does_not_buffer_records():
    def records():
        yield {"record": {"id": "1"}}
        yield {"record": {"id": "2"}}

    output = BytesIO()
    stream_unparse(records(), output, "records")
    assert parse(output.getvalue()) == {
        "records": {"record": [{"id": "1"}, {"id": "2"}]}
    }


def test_streaming_writes_each_record_immediately():
    output = BytesIO()
    exporter = StreamingUnparser(output, "records")
    exporter.item({"record": {"id": "1"}})
    # The first record is already on the stream before the second is produced.
    assert b"<record><id>1</id></record>" in output.getvalue()
    exporter.item({"record": {"id": "2"}})
    exporter.close()
    assert output.getvalue().count(b"<record>") == 2


def test_streaming_list_valued_record_emits_siblings():
    xml = _stream([{"item": ["1", "2", "3"]}], root_key="root")
    assert _strip(xml) == "<root><item>1</item><item>2</item><item>3</item></root>"


def test_streaming_root_attributes():
    xml = _stream(
        [{"a": "1"}], root_key="root",
        root_attrs={"version": "2", "flag": True})
    assert _strip(xml) == '<root version="2" flag="true"><a>1</a></root>'


def test_streaming_encoding_is_consistent_in_declaration_and_bytes():
    output = BytesIO()
    with StreamingUnparser(output, "root", encoding="iso-8859-1") as exporter:
        exporter.item({"x": "café"})
    raw = output.getvalue()
    assert raw.startswith(b"<?xml version=\"1.0\" encoding=\"iso-8859-1\"?>")
    assert "caf\xe9".encode("iso-8859-1") in raw
    assert parse(raw) == {"root": {"x": "café"}}


def test_streaming_namespaces_are_consistent_across_document():
    namespaces = {"http://a.com/": "a"}
    output = BytesIO()
    with StreamingUnparser(
        output, "http://a.com/:records",
        root_attrs={"xmlns:a": "http://a.com/"},
        namespaces=namespaces,
    ) as exporter:
        exporter.item({"http://a.com/:record": {"http://a.com/:id": "1"}})
    xml = output.getvalue().decode()
    assert xml == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<a:records xmlns:a="http://a.com/">'
        '<a:record><a:id>1</a:id></a:record>'
        '</a:records>'
    )
    parse(xml)


def test_streaming_comments():
    output = BytesIO()
    with StreamingUnparser(output, "root") as exporter:
        exporter.comment("note")
        exporter.item({"a": "1"})
    assert _strip(output.getvalue().decode()) == "<root><!--note--><a>1</a></root>"


def test_streaming_accepts_text_stream():
    output = StringIO()
    with StreamingUnparser(output, "root") as exporter:
        exporter.item({"a": 1})
    assert output.getvalue() == '<?xml version="1.0" encoding="utf-8"?>\n<root><a>1</a></root>'


def test_streaming_does_not_close_caller_stream():
    output = BytesIO()
    exporter = StreamingUnparser(output, "root")
    exporter.item({"a": 1})
    exporter.close()
    assert not output.closed
    # The caller remains free to keep handling the stream.
    output.write(b"after")


def test_streaming_requires_output_stream():
    with pytest.raises(ValueError, match="output stream is required"):
        StreamingUnparser(None, "root")


def test_streaming_requires_full_document():
    with pytest.raises(ValueError, match="full_document=True"):
        StreamingUnparser(BytesIO(), "root", full_document=False)


def test_streaming_rejects_invalid_bytes_errors():
    with pytest.raises(ValueError, match="Invalid bytes_errors handler"):
        StreamingUnparser(BytesIO(), "root", bytes_errors="nope")


def test_streaming_rejects_non_dict_record():
    exporter = StreamingUnparser(BytesIO(), "root")
    with pytest.raises(ValueError, match="record must be a dict"):
        exporter.item("not-a-dict")
    assert exporter.failed


def test_streaming_unwritable_value_marks_export_incomplete():
    output = BytesIO()
    exporter = StreamingUnparser(output, "root")
    exporter.item({"a": "1"})
    with pytest.raises(ValueError):
        exporter.item({"bad/name": "x"})
    assert exporter.failed
    with pytest.raises(IncompleteExportError):
        exporter.close()
    # The partial stream is missing the closing root tag.
    assert b"</root>" not in output.getvalue()


def test_streaming_context_manager_reports_incomplete_on_source_failure():
    output = BytesIO()
    exporter = None
    with pytest.raises(RuntimeError, match="boom"):
        with StreamingUnparser(output, "root") as exporter:
            exporter.item({"a": "1"})
            raise RuntimeError("boom")
    assert exporter.failed
    assert b"</root>" not in output.getvalue()


def test_stream_unparse_wraps_source_failure_in_incomplete_export():
    def failing_records():
        yield {"a": "1"}
        raise RuntimeError("source exploded")

    output = BytesIO()
    with pytest.raises(IncompleteExportError) as exc_info:
        stream_unparse(failing_records(), output, "root")
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert b"<a>1</a>" in output.getvalue()
    assert b"</root>" not in output.getvalue()


def test_stream_unparse_empty_iterable_is_valid_document():
    output = BytesIO()
    stream_unparse(iter(()), output, "records")
    xml = output.getvalue().decode()
    assert parse(xml) == {"records": None}


def test_streaming_close_is_idempotent():
    output = BytesIO()
    exporter = StreamingUnparser(output, "root")
    exporter.item({"a": 1})
    exporter.close()
    assert exporter.closed
    exporter.close()
    assert output.getvalue().count(b"</root>") == 1


def test_streaming_failed_export_never_emits_closing_root():
    output = BytesIO()
    exporter = StreamingUnparser(output, "root")
    with pytest.raises(ValueError):
        exporter.item({"bad name": "x"})
    with pytest.raises(IncompleteExportError):
        exporter.close()
    # A second close does not change the outcome.
    assert exporter.closed
    assert b"</root>" not in output.getvalue()


def test_streaming_cannot_write_after_close():
    output = BytesIO()
    exporter = StreamingUnparser(output, "root")
    exporter.item({"a": 1})
    exporter.close()
    with pytest.raises(ValueError, match="already closed"):
        exporter.item({"b": 2})
    with pytest.raises(ValueError, match="already closed"):
        exporter.comment("late")
    # The finished document is untouched.
    assert parse(output.getvalue()) == {"root": {"a": "1"}}
