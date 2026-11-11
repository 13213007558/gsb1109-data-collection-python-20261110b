"""Offline validation catalog for dict-to-XML field configuration.

``unparse`` turns user-supplied dictionary keys into XML element / attribute
names. These samples decide, before anything is emitted, which configurations
can never become safe XML.

Rejection reason tags (every rejection sample carries exactly one):

* ``NAME_ILLEGAL``: the key cannot be an XML (qualified) name at all.
* ``CONTEXT_ILLEGAL``: the key is only meaningful in another position, or a
  required declaration is missing (single document root, namespace binding,
  attribute prefix in effect).
* ``NOT_REPRESENTABLE``: the dictionary shape has no element/attribute
  equivalent, so data is flattened or dropped even though no exception is
  raised.

Two oracles are used instead of checking whether the output string contains
angle brackets:

* ``unparse`` itself raises ``ValueError`` for names blocked by
  ``_validate_name`` and for document-shape violations.
* ``xml.etree.ElementTree.fromstring`` parses whatever ``unparse`` returns; a
  ``ParseError`` proves the emitted document is not well-formed XML.

The name validator is intentionally a minimal blocklist, so part of this
catalog documents its gaps: entries that ``unparse`` accepts but a real XML
parser rejects. If the validator is ever hardened, those entries move into the
upfront-rejection table.
"""

import xml.etree.ElementTree as ET

import pytest

from xmltodict import parse, unparse


NAME_ILLEGAL = "name-illegal"
CONTEXT_ILLEGAL = "context-illegal"
NOT_REPRESENTABLE = "not-representable"
REJECTION_REASONS = {NAME_ILLEGAL, CONTEXT_ILLEGAL, NOT_REPRESENTABLE}


def _is_well_formed(xml):
    try:
        ET.fromstring(xml)
    except ET.ParseError:
        return False
    return True


def _emit_result(obj, **kwargs):
    """Return ``(status, xml)`` where status classifies the emission attempt."""
    try:
        xml = unparse(obj, full_document=False, **kwargs)
    except ValueError:
        return "rejected", None
    return ("well-formed" if _is_well_formed(xml) else "not-well-formed"), xml


# ---------------------------------------------------------------------------
# NAME_ILLEGAL, caught upfront: _validate_name raises ValueError before output.
# Rows are deliberately nested so the check is exercised below the root, for
# element names, attribute names, and xmlns-prefix names alike.
# ---------------------------------------------------------------------------
UPFRONT_NAME_REJECTIONS = [
    pytest.param(
        NAME_ILLEGAL,
        {"root": {"child": {"bad/name": "x"}}},
        {},
        r"Invalid element name.*\"/\"",
        id="nested-element-with-slash",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"root": {"child": {"a<b": "x"}}},
        {},
        r"Invalid element name.*<",
        id="nested-element-with-angle-bracket",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"root": {"bad name": "x"}},
        {},
        r"Invalid element name.*whitespace",
        id="nested-element-with-whitespace",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"root": {'a"b': "x"}},
        {},
        r"Invalid element name.*quotes",
        id="nested-element-with-quote",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"root": {"a=b": "x"}},
        {},
        r"Invalid element name.*=",
        id="nested-element-with-equals",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"root": {"?pi": "x"}},
        {},
        r"Invalid element name.*cannot start",
        id="nested-element-starting-with-question-mark",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"root": {"!decl": "x"}},
        {},
        r"Invalid element name.*cannot start",
        id="nested-element-starting-with-bang",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@bad/name": "x"}},
        {},
        r"Invalid attribute name.*\"/\"",
        id="attribute-with-slash",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@a<b": "x"}},
        {},
        r"Invalid attribute name.*<",
        id="attribute-with-angle-bracket",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@bad name": "x"}},
        {},
        r"Invalid attribute name.*whitespace",
        id="attribute-with-whitespace",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {'@a"b': "x"}},
        {},
        r"Invalid attribute name.*quotes",
        id="attribute-with-quote",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@?weird": "x"}},
        {},
        r"Invalid attribute name.*cannot start",
        id="attribute-starting-with-question-mark",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@xmlns": {"bad/prefix": "http://e/"}}},
        {},
        r"Invalid attribute name.*\"/\"",
        id="xmlns-prefix-with-slash",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@xmlns": {"bad prefix": "http://e/"}}},
        {},
        r"Invalid attribute name.*whitespace",
        id="xmlns-prefix-with-whitespace",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@xmlns": {"?p": "http://e/"}}},
        {},
        r"Invalid attribute name.*cannot start",
        id="xmlns-prefix-starting-with-question-mark",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@xmlns": {1: "http://e/"}}},
        {},
        r"attribute name must be a string",
        id="xmlns-prefix-non-string",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {1: "x"},
        {},
        r"element name must be a string",
        id="element-key-non-string",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {1: "x"}},
        {},
        r"element name must be a string",
        id="nested-element-key-non-string",
    ),
]


@pytest.mark.parametrize("reason,obj,kwargs,message", UPFRONT_NAME_REJECTIONS)
def test_illegal_names_are_rejected_before_output(reason, obj, kwargs, message):
    assert reason in REJECTION_REASONS
    with pytest.raises(ValueError, match=message):
        unparse(obj, **kwargs)


# ---------------------------------------------------------------------------
# NAME_ILLEGAL, validator gaps: unparse emits a string but a real XML parser
# rejects it. These configurations still cannot be output safely; the parser
# oracle (not a bracket substring check) is what proves it.
# ---------------------------------------------------------------------------
UNCAUGHT_ILLEGAL_NAMES = [
    pytest.param(NAME_ILLEGAL, {"": "x"}, id="empty-element-name"),
    pytest.param(NAME_ILLEGAL, {"a": {"@": "x"}}, id="empty-attribute-name"),
    pytest.param(NAME_ILLEGAL, {"1abc": "x"}, id="element-starts-with-digit"),
    pytest.param(NAME_ILLEGAL, {"-abc": "x"}, id="element-starts-with-hyphen"),
    pytest.param(NAME_ILLEGAL, {".abc": "x"}, id="element-starts-with-dot"),
    pytest.param(NAME_ILLEGAL, {":abc": "x"}, id="element-starts-with-colon"),
    pytest.param(NAME_ILLEGAL, {"a?b": "x"}, id="element-with-question-mark-inside"),
    pytest.param(NAME_ILLEGAL, {"a!b": "x"}, id="element-with-bang-inside"),
    pytest.param(
        NAME_ILLEGAL,
        {"a＜b": "x"},
        id="element-with-fullwidth-angle-bracket",
    ),
    pytest.param(
        NAME_ILLEGAL,
        {"١abc": "x"},
        id="element-starts-with-non-ascii-digit",
    ),
    pytest.param(NAME_ILLEGAL, {"a": {"@1abc": "x"}}, id="attribute-starts-with-digit"),
    pytest.param(NAME_ILLEGAL, {"a": {"@-x": "x"}}, id="attribute-starts-with-hyphen"),
    pytest.param(
        NAME_ILLEGAL,
        {"a": {"@a?b": "x"}},
        id="attribute-with-question-mark-inside",
    ),
]


@pytest.mark.parametrize("reason,obj", UNCAUGHT_ILLEGAL_NAMES)
def test_uncaught_illegal_names_emit_non_well_formed_output(reason, obj):
    assert reason == NAME_ILLEGAL
    status, xml = _emit_result(obj)
    assert status == "not-well-formed"
    assert not _is_well_formed(xml)


# ---------------------------------------------------------------------------
# CONTEXT_ILLEGAL: names themselves may be fine, but the position or the
# surrounding document makes the configuration invalid.
# ---------------------------------------------------------------------------
CONTEXT_VALUE_ERRORS = [
    pytest.param(
        CONTEXT_ILLEGAL,
        {"a": "1", "b": "2"},
        r"Document must have exactly one root",
        id="second-document-root",
    ),
    pytest.param(
        CONTEXT_ILLEGAL,
        {"a": [1, 2]},
        r"document with multiple roots",
        id="repeated-root-list",
    ),
    pytest.param(
        CONTEXT_ILLEGAL,
        {},
        r"Document must have exactly one root",
        id="no-document-root",
    ),
]


@pytest.mark.parametrize("reason,obj,message", CONTEXT_VALUE_ERRORS)
def test_context_violations_raise(reason, obj, message):
    assert reason == CONTEXT_ILLEGAL
    with pytest.raises(ValueError, match=message):
        unparse(obj)


CONTEXT_NON_WELL_FORMED = [
    pytest.param(
        CONTEXT_ILLEGAL,
        {"@id": "1"},
        {},
        id="attribute-marker-at-document-root",
    ),
    pytest.param(
        CONTEXT_ILLEGAL,
        {"#text": "x"},
        {},
        id="text-marker-at-document-root",
    ),
    pytest.param(
        CONTEXT_ILLEGAL,
        {"a": {"@id": "1"}},
        {"attr_prefix": "!"},
        id="default-attribute-key-under-custom-prefix",
    ),
    pytest.param(
        CONTEXT_ILLEGAL,
        {"a:b": "x"},
        {},
        id="qualified-name-without-namespace-binding",
    ),
]


@pytest.mark.parametrize("reason,obj,kwargs", CONTEXT_NON_WELL_FORMED)
def test_context_violations_emit_non_well_formed_output(reason, obj, kwargs):
    assert reason == CONTEXT_ILLEGAL
    status, xml = _emit_result(obj, **kwargs)
    assert status == "not-well-formed"
    assert not _is_well_formed(xml)


# ---------------------------------------------------------------------------
# NOT_REPRESENTABLE: no exception, but the dict shape has no XML counterpart,
# so the emitted document silently loses structure.
# ---------------------------------------------------------------------------
def test_dict_attribute_value_has_no_structured_representation():
    obj = {"a": {"@meta": {"k": "v"}}}
    xml = unparse(obj, full_document=False)
    assert xml == '<a meta="{\'k\': \'v\'}"></a>'
    # The nested dict survives only as its repr string; round-tripping cannot
    # recover the original structure.
    assert parse(xml) == {"a": {"@meta": "{'k': 'v'}"}}


def test_list_attribute_value_has_no_structured_representation():
    obj = {"a": {"@ids": [1, 2]}}
    xml = unparse(obj, full_document=False)
    assert xml == '<a ids="[1, 2]"></a>'
    assert parse(xml) == {"a": {"@ids": "[1, 2]"}}


def test_dict_text_value_has_no_structured_representation():
    obj = {"a": {"#text": {"k": "v"}}}
    xml = unparse(obj, full_document=False)
    assert xml == "<a>{'k': 'v'}</a>"


def test_mixed_content_ordering_is_not_representable():
    doc = parse("<a>before<b/>after</a>")
    xml = unparse(doc, full_document=False)
    # The document stays well-formed, but text/interleaving is collapsed:
    # both text runs merge into one #text entry placed after the child.
    assert _is_well_formed(xml)
    assert xml == "<a><b></b>beforeafter</a>"
    assert parse(xml) == {"a": {"b": None, "#text": "beforeafter"}}


def test_empty_list_has_no_element_representation():
    xml = unparse({"a": {"b": []}}, full_document=False)
    assert xml == "<a></a>"
    # A placeholder entry is the documented way to force the element to exist.
    forced = unparse({"a": {"b": [""]}}, full_document=False)
    assert forced == "<a><b></b></a>"


# ---------------------------------------------------------------------------
# ACCEPTED: legal names, including lookalikes of rejected ones, nested fields,
# custom prefixes, and namespace mapping. Every output must parse cleanly.
# ---------------------------------------------------------------------------
ACCEPTED_CONFIGURATIONS = [
    pytest.param(
        {
            "root": {
                "child": {
                    "item-1": {"_meta": "y", "@id": "7", "#text": "z"}
                }
            }
        },
        {},
        id="nested-legal-element-and-attribute-names",
    ),
    pytest.param({"a-b": "x"}, {}, id="hyphenated-element-name"),
    pytest.param({"_a": "x"}, {}, id="underscore-prefixed-element-name"),
    pytest.param({"a.b": "x"}, {}, id="dotted-element-name"),
    pytest.param({"a1": "x"}, {}, id="digit-suffix-element-name"),
    pytest.param({"abc1": "x"}, {}, id="leading-letter-trailing-digit-name"),
    pytest.param(
        {"a": {"@a-b": "1", "@x1": "2", "@_id": "3"}},
        {},
        id="legal-attribute-name-lookalikes",
    ),
    pytest.param(
        {"a": {"!id": "1"}},
        {"attr_prefix": "!"},
        id="custom-attribute-prefix",
    ),
    pytest.param(
        {"a": {"$text": "x"}},
        {"cdata_key": "$text"},
        id="custom-cdata-key",
    ),
    pytest.param(
        {"café": {"тег": "日本語"}},
        {},
        id="unicode-element-names",
    ),
    pytest.param(
        {"a": {"@xmlns": {"e": "http://e/"}, "e:child": "x"}},
        {},
        id="qualified-name-with-bound-prefix",
    ),
    pytest.param(
        {
            "http://e/:root": {
                "@xmlns": {"e": "http://e/"},
                "@http://e/:id": "7",
                "http://e/:child": "x",
            }
        },
        {"namespaces": {"http://e/": "e"}},
        id="namespace-uri-to-prefix-mapping",
    ),
    pytest.param(
        {"a": {"@xmlns": {"": "http://d/", "p": "http://p/", "_q": "http://q/"}}},
        {},
        id="legal-xmlns-prefixes-including-default",
    ),
]


@pytest.mark.parametrize("obj,kwargs", ACCEPTED_CONFIGURATIONS)
def test_accepted_configurations_produce_well_formed_output(obj, kwargs):
    status, xml = _emit_result(obj, **kwargs)
    assert status == "well-formed"
    assert _is_well_formed(xml)


def test_accepted_nested_configuration_round_trips():
    obj = {
        "root": {
            "child": {
                "item-1": {"_meta": "y", "@id": "7", "#text": "z"}
            }
        }
    }
    assert parse(unparse(obj)) == obj


LOOKALIKE_PAIRS = [
    # (rejected lookalike, accepted legal name) as element names
    pytest.param({"a/b": "x"}, {"a-b": "x"}, id="slash-versus-hyphen-element"),
    pytest.param({"a b": "x"}, {"a_b": "x"}, id="space-versus-underscore-element"),
    pytest.param({"a<b": "x"}, {"a.b": "x"}, id="angle-versus-dot-element"),
    pytest.param({"1abc": "x"}, {"abc1": "x"}, id="leading-digit-versus-trailing-digit"),
    pytest.param({"?x": "x"}, {"_x": "x"}, id="pi-start-versus-underscore-start"),
    # same discrimination for attribute names
    pytest.param(
        {"a": {"@a/b": "x"}},
        {"a": {"@a-b": "x"}},
        id="slash-versus-hyphen-attribute",
    ),
    pytest.param(
        {"a": {"@1x": "x"}},
        {"a": {"@x1": "x"}},
        id="leading-digit-versus-trailing-digit-attribute",
    ),
]


@pytest.mark.parametrize("bad_obj,good_obj", LOOKALIKE_PAIRS)
def test_rejected_and_accepted_lookalikes_are_discriminated(bad_obj, good_obj):
    bad_status, _ = _emit_result(bad_obj)
    good_status, good_xml = _emit_result(good_obj)
    assert bad_status in {"rejected", "not-well-formed"}
    assert good_status == "well-formed"
    assert _is_well_formed(good_xml)
