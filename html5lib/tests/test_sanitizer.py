from __future__ import absolute_import, division, unicode_literals

import warnings

import pytest

from six.moves import urllib_parse as urlparse

from html5lib import constants, parseFragment, serialize, getTreeWalker

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from html5lib.filters import sanitizer


def sanitize_html(stream):
    parsed = parseFragment(stream)
    with pytest.deprecated_call():
        serialized = serialize(parsed,
                               sanitize=True,
                               omit_optional_tags=False,
                               use_trailing_solidus=True,
                               space_before_trailing_solidus=False,
                               quote_attr_values="always",
                               quote_char='"',
                               alphabetical_attributes=True)
    return serialized


PAGE_URL = "https://announce.example.gov.cn/notice/2026/001.html"


def sanitize_html_with_policy(stream, link_policy=PAGE_URL):
    parsed = parseFragment(stream)
    with pytest.deprecated_call():
        serialized = serialize(parsed,
                               sanitize=True,
                               link_policy=link_policy,
                               omit_optional_tags=False,
                               use_trailing_solidus=True,
                               space_before_trailing_solidus=False,
                               quote_attr_values="always",
                               quote_char='"',
                               alphabetical_attributes=True)
    return serialized


def test_should_handle_astral_plane_characters():
    sanitized = sanitize_html("<p>&#x1d4b5; &#x1d538;</p>")
    expected = '<p>\U0001d4b5 \U0001d538</p>'
    assert expected == sanitized


def test_should_allow_relative_uris():
    sanitized = sanitize_html('<p><a href="/example.com"></a></p>')
    expected = '<p><a href="/example.com"></a></p>'
    assert expected == sanitized


def test_invalid_data_uri():
    sanitized = sanitize_html('<audio controls="" src="data:foobar"></audio>')
    expected = '<audio controls></audio>'
    assert expected == sanitized


def test_invalid_ipv6_url():
    sanitized = sanitize_html('<a href="h://]">')
    expected = "<a></a>"
    assert expected == sanitized


def test_data_uri_disallowed_type():
    sanitized = sanitize_html('<audio controls="" src="data:text/html,<html>"></audio>')
    expected = "<audio controls></audio>"
    assert expected == sanitized


def test_wbr_allowed():
    sanitized = sanitize_html('<wbr>')
    expected = '<wbr/>'
    assert expected == sanitized


def param_sanitizer():
    for ns, tag_name in sanitizer.allowed_elements:
        if ns != constants.namespaces["html"]:
            continue
        if tag_name in ['caption', 'col', 'colgroup', 'optgroup', 'option', 'table', 'tbody', 'td',
                        'tfoot', 'th', 'thead', 'tr', 'select']:
            continue  # TODO
        if tag_name == 'image':
            yield ("test_should_allow_%s_tag" % tag_name,
                   "<img title=\"1\"/>foo &lt;bad&gt;bar&lt;/bad&gt; baz",
                   "<%s title='1'>foo <bad>bar</bad> baz</%s>" % (tag_name, tag_name))
        elif tag_name == 'br':
            yield ("test_should_allow_%s_tag" % tag_name,
                   "<br title=\"1\"/>foo &lt;bad&gt;bar&lt;/bad&gt; baz<br/>",
                   "<%s title='1'>foo <bad>bar</bad> baz</%s>" % (tag_name, tag_name))
        elif tag_name in constants.voidElements:
            yield ("test_should_allow_%s_tag" % tag_name,
                   "<%s title=\"1\"/>foo &lt;bad&gt;bar&lt;/bad&gt; baz" % tag_name,
                   "<%s title='1'>foo <bad>bar</bad> baz</%s>" % (tag_name, tag_name))
        else:
            yield ("test_should_allow_%s_tag" % tag_name,
                   "<%s title=\"1\">foo &lt;bad&gt;bar&lt;/bad&gt; baz</%s>" % (tag_name, tag_name),
                   "<%s title='1'>foo <bad>bar</bad> baz</%s>" % (tag_name, tag_name))

    for ns, attribute_name in sanitizer.allowed_attributes:
        if ns is not None:
            continue
        if attribute_name != attribute_name.lower():
            continue  # TODO
        if attribute_name == 'style':
            continue
        attribute_value = 'foo'
        if attribute_name in sanitizer.attr_val_is_uri:
            attribute_value = '%s://sub.domain.tld/path/object.ext' % sanitizer.allowed_protocols[0]
        yield ("test_should_allow_%s_attribute" % attribute_name,
               "<p %s=\"%s\">foo &lt;bad&gt;bar&lt;/bad&gt; baz</p>" % (attribute_name, attribute_value),
               "<p %s='%s'>foo <bad>bar</bad> baz</p>" % (attribute_name, attribute_value))

    for protocol in sanitizer.allowed_protocols:
        rest_of_uri = '//sub.domain.tld/path/object.ext'
        if protocol == 'data':
            rest_of_uri = 'image/png;base64,aGVsbG8gd29ybGQ='
        yield ("test_should_allow_uppercase_%s_uris" % protocol,
               "<img src=\"%s:%s\">foo</a>" % (protocol, rest_of_uri),
               """<img src="%s:%s">foo</a>""" % (protocol, rest_of_uri))

    for protocol in sanitizer.allowed_protocols:
        rest_of_uri = '//sub.domain.tld/path/object.ext'
        if protocol == 'data':
            rest_of_uri = 'image/png;base64,aGVsbG8gd29ybGQ='
        protocol = protocol.upper()
        yield ("test_should_allow_uppercase_%s_uris" % protocol,
               "<img src=\"%s:%s\">foo</a>" % (protocol, rest_of_uri),
               """<img src="%s:%s">foo</a>""" % (protocol, rest_of_uri))


def test_details_open_allowed():
    sanitized = sanitize_html("<details open>.</details>")
    expected = '<details open>.</details>'
    assert expected == sanitized


def test_details_summary_allowed():
    sanitized = sanitize_html("<details><summary>.</summary><p>...</p></details>")
    expected = '<details><summary>.</summary><p>...</p></details>'
    assert expected == sanitized


@pytest.mark.parametrize("expected, input",
                         (pytest.param(expected, input, id=id)
                          for id, expected, input in param_sanitizer()))
def test_sanitizer(expected, input):
    parsed = parseFragment(expected)
    expected = serialize(parsed,
                         omit_optional_tags=False,
                         use_trailing_solidus=True,
                         space_before_trailing_solidus=False,
                         quote_attr_values="always",
                         quote_char='"',
                         alphabetical_attributes=True)
    assert expected == sanitize_html(input)


def test_lowercase_color_codes_in_style():
    sanitized = sanitize_html("<p style=\"border: 1px solid #a2a2a2;\"></p>")
    expected = '<p style=\"border: 1px solid #a2a2a2;\"></p>'
    assert expected == sanitized


def test_uppercase_color_codes_in_style():
    sanitized = sanitize_html("<p style=\"border: 1px solid #A2A2A2;\"></p>")
    expected = '<p style=\"border: 1px solid #A2A2A2;\"></p>'
    assert expected == sanitized


def test_ol_start_allowed():
    sanitized = sanitize_html("<ol start=2><li>.</ol>")
    expected = '<ol start="2"><li>.</li></ol>'
    assert expected == sanitized


def test_ol_type_allowed():
    sanitized = sanitize_html("<ol type=I><li>.</ol>")
    expected = '<ol type="I"><li>.</li></ol>'
    assert expected == sanitized


def test_ol_reversed_allowed():
    sanitized = sanitize_html("<ol reversed><li>.</ol>")
    expected = '<ol reversed><li>.</li></ol>'
    assert expected == sanitized


def test_policy_keeps_relative_same_site_links():
    sanitized = sanitize_html_with_policy(
        '<p><a href="/notice/2026/002.html">root relative</a>'
        '<a href="003.html">relative</a>'
        '<a href="?page=2">query</a>'
        '<a href="#section">fragment</a></p>')
    expected = ('<p><a href="/notice/2026/002.html">root relative</a>'
                '<a href="003.html">relative</a>'
                '<a href="?page=2">query</a>'
                '<a href="#section">fragment</a></p>')
    assert expected == sanitized


def test_policy_keeps_same_site_absolute_link():
    sanitized = sanitize_html_with_policy(
        '<a href="https://announce.example.gov.cn/notice/2026/004.html">same</a>')
    assert sanitized == (
        '<a href="https://announce.example.gov.cn/notice/2026/004.html">same</a>')


def test_policy_normalizes_default_port_and_host_case():
    policy = sanitizer.SameOriginLinkPolicy(PAGE_URL)
    assert policy.allows("https://ANNOUNCE.example.gov.cn:443/notice/2026/005.html")
    assert not policy.allows("http://announce.example.gov.cn/notice/2026/006.html")


def test_external_link_keeps_only_visible_text():
    sanitized = sanitize_html_with_policy(
        '<a href="https://evil.example/x">点此查看 <strong>详情</strong></a>')
    assert sanitized == '<a>点此查看 <strong>详情</strong></a>'


def test_protocol_relative_external_link_is_stripped():
    sanitized = sanitize_html_with_policy('<a href="//evil.example/y">x</a>')
    assert sanitized == '<a>x</a>'


def test_protocol_relative_same_site_link_is_kept():
    sanitized = sanitize_html_with_policy(
        '<a href="//announce.example.gov.cn/notice/2026/007.html">x</a>')
    assert sanitized == (
        '<a href="//announce.example.gov.cn/notice/2026/007.html">x</a>')


def test_entity_encoded_external_link_is_stripped():
    for href in ("https&#58;//evil.example/z", "https&colon;//evil.example/z",
                 "&#104;ttps://evil.example/z"):
        sanitized = sanitize_html_with_policy('<a href="%s">x</a>' % href)
        assert sanitized == '<a>x</a>'


def test_backslash_and_scheme_slash_bypasses_are_stripped():
    for href in ("https:\\evil.example\\a", "https:/evil.example",
                 "https:\\/evil.example/x", "HTTPS:\\EVIL.EXAMPLE",
                 "https://evil.example\t/x"):
        sanitized = sanitize_html_with_policy('<a href="%s">x</a>' % href)
        assert sanitized == '<a>x</a>'


def test_empty_and_unparseable_targets_are_stripped():
    sanitized = sanitize_html_with_policy(
        '<a href="">empty</a><a href="https://[bad">junk</a>')
    assert sanitized == '<a>empty</a><a>junk</a>'


def test_policy_covers_svg_xlink_href():
    sanitized = sanitize_html_with_policy(
        '<svg><a xlink:href="/notice/2026/008.html">same</a>'
        '<a xlink:href="https://evil.example/g">external</a></svg>')
    assert sanitized == ('<svg><a href="/notice/2026/008.html">same</a>'
                         '<a>external</a></svg>')


def test_in_document_base_cannot_restore_external_link():
    outputs = [
        sanitize_html_with_policy(
            '<base href="https://evil.example/">'
            '<a href="//evil.example/base">base</a>'),
        sanitize_html_with_policy(
            '<div xml:base="https://evil.example/">'
            '<a href="//evil.example/xb">xml base</a></div>'),
    ]
    assert outputs[0] == '&lt;base href="https://evil.example/"&gt;<a>base</a>'
    assert outputs[1] == '<div><a>xml base</a></div>'

    for serialized in outputs:
        reparsed = parseFragment(serialized)
        for token in getTreeWalker("etree")(reparsed):
            if token["type"] in ("StartTag", "EmptyTag") and token["name"] == "a":
                assert (None, "href") not in token["data"]


def test_reparsed_relative_link_resolves_to_announcement():
    serialized = sanitize_html_with_policy(
        '<p><a href="/notice/2026/009.html">公告九</a></p>')
    reparsed = parseFragment(serialized)
    hrefs = [token["data"][(None, "href")]
             for token in getTreeWalker("etree")(reparsed)
             if token["type"] == "StartTag" and token["name"] == "a"]
    assert hrefs == ["/notice/2026/009.html"]
    assert urlparse.urljoin(PAGE_URL, hrefs[0]) == \
        "https://announce.example.gov.cn/notice/2026/009.html"


def test_reparsed_external_link_is_not_clickable():
    serialized = sanitize_html_with_policy(
        '<a href="https://evil.example/x">external</a>')
    reparsed = parseFragment(serialized)
    for token in getTreeWalker("etree")(reparsed):
        if token["type"] in ("StartTag", "EmptyTag"):
            assert (None, "href") not in token["data"]


class _AllowAllPolicy(object):
    def allows(self, value):
        return True


def test_custom_policy_object_is_used():
    sanitized = sanitize_html_with_policy(
        '<a href="https://evil.example/x">x</a>',
        link_policy=_AllowAllPolicy())
    assert sanitized == '<a href="https://evil.example/x">x</a>'


def test_explicit_policy_instance_is_used():
    sanitized = sanitize_html_with_policy(
        '<a href="https://evil.example/x">x</a>',
        link_policy=sanitizer.SameOriginLinkPolicy(PAGE_URL))
    assert sanitized == '<a>x</a>'


def test_no_policy_keeps_legacy_behavior():
    sanitized = sanitize_html('<a href="https://evil.example/x">x</a>')
    assert sanitized == '<a href="https://evil.example/x">x</a>'
    sanitized = sanitize_html('<a href="/example.com"></a>')
    assert sanitized == '<a href="/example.com"></a>'
