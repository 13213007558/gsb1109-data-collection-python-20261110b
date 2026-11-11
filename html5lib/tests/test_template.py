"""Tests for ``<template>`` element support.

These exercise the requirement that a template's contents are parsed into a
detached DocumentFragment:

* a plain tree walk / child traversal never sees the unexpanded contents, so a
  selector over already-rendered products cannot pick them up;
* the contents can be fetched explicitly as an independent fragment (including
  nested templates, table rows/cells and ordinary siblings following the
  template);
* a full-page serialization puts the contents back inside the template.

The tests use the standard library's unittest so they can run without the
pytest-only dependencies of the rest of the test suite::

    python3 -m unittest html5lib.tests.test_template
"""
from __future__ import absolute_import, division, unicode_literals

import unittest
from xml.dom import Node

from html5lib import (
    HTMLParser, getTemplateContent, getTreeWalker, parseFragment, serialize,
)
from html5lib.treebuilders import getTreeBuilder

try:
    import lxml.etree as lxml_etree  # noqa: F401
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

HTML_NS = "http://www.w3.org/1999/xhtml"


def _local(tag):
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else tag


class _EtreeMixin(object):
    builder = "etree"
    walker = "etree"

    def get_template(self, doc):
        return next(node for node in doc.iter()
                    if node.tag == "{%s}template" % HTML_NS)

    def content_children(self, content):
        return [_local(node.tag) for node in content]

    def template_visible_children(self, template):
        return len(template)

    def fragment_children(self, fragment):
        return [_local(node.tag) for node in fragment]


class _DomMixin(object):
    builder = "dom"
    walker = "dom"

    def get_template(self, doc):
        return doc.getElementsByTagName("template")[0]

    def content_children(self, content):
        return [node.nodeName for node in content.childNodes]

    def template_visible_children(self, template):
        return template.childNodes.length

    def fragment_children(self, fragment):
        return [node.nodeName for node in fragment.childNodes]


class _LxmlMixin(_EtreeMixin):
    builder = "lxml"
    walker = "lxml"


class TemplateCases(object):
    """Shared behavioural checks, parametrised through the tree mixins."""

    def parse(self, source):
        parser = HTMLParser(tree=getTreeBuilder(self.builder))
        return parser.parse(source)

    def walker_tokens(self, doc, expand_templates=False):
        walker = getTreeWalker(self.walker)(doc)
        walker.expand_templates = expand_templates
        return list(walker)

    def test_content_detached_and_accessible(self):
        doc = self.parse(
            "<body><template><tr><td>x</td></tr></template>"
            "<p>after</p></body>")
        template = self.get_template(doc)
        # The template itself carries no ordinary, visible children.
        self.assertEqual(self.template_visible_children(template), 0)

        content = getTemplateContent(template)
        self.assertIsNotNone(content)
        # Rows and cells are preserved verbatim in the detached fragment.
        self.assertEqual(self.content_children(content), ["tr"])
        self.assertEqual(
            self.content_children(self._first(content)), ["td"])

    def test_plain_walk_excludes_template_contents(self):
        doc = self.parse(
            "<body><template><tr><td>x</td></tr></template>"
            "<p>after</p></body>")
        names = [token.get("name") for token in self.walker_tokens(doc)]
        self.assertNotIn("tr", names)
        self.assertNotIn("td", names)
        # Only already-rendered content is visible.
        self.assertIn("template", names)
        self.assertIn("p", names)

    def test_explicit_walk_of_fragment(self):
        doc = self.parse("<body><template><tr><td>x</td></tr></template></body>")
        content = getTemplateContent(self.get_template(doc))
        tokens = list(getTreeWalker(self.walker)(content))
        names = [token.get("name") for token in tokens
                 if token["type"] == "StartTag"]
        self.assertEqual(names, ["tr", "td"])

    def test_nested_templates(self):
        doc = self.parse(
            "<body><template><template><span>n</span></template>"
            "</template></body>")
        outer = self.get_template(doc)
        outer_content = getTemplateContent(outer)
        self.assertEqual(self.content_children(outer_content), ["template"])
        inner = self._first(outer_content)
        inner_content = getTemplateContent(inner)
        self.assertEqual(self.content_children(inner_content), ["span"])
        self.assertEqual(self.template_visible_children(inner), 0)

    def test_sibling_after_template_is_separate(self):
        doc = self.parse(
            "<body><template><b>hidden</b></template><p>shown</p></body>")
        body = self._body(doc)
        self.assertEqual(self.fragment_children(body), ["template", "p"])

    def test_serialization_round_trip(self):
        source = ("<body><template><tr><td>x</td></tr></template>"
                  "<p>after</p></body>")
        output = serialize(self.parse(source), tree=self.walker,
                           omit_optional_tags=False)
        self.assertIn("<template><tr><td>x</td></tr></template>", output)
        self.assertIn("<p>after</p>", output)

    def test_get_template_content_returns_none_for_other_elements(self):
        doc = self.parse("<body><p>not a template</p></body>")
        body = self._body(doc)
        self.assertIsNone(getTemplateContent(self._first(body)))

    def test_fragment_parsing(self):
        fragment = parseFragment(
            "<template><tr><td>q</td></tr></template><b>z</b>",
            treebuilder=self.builder)
        self.assertEqual(self.fragment_children(fragment),
                         ["template", "b"])
        template = self._first(fragment)
        content = getTemplateContent(template)
        self.assertEqual(self.content_children(content), ["tr"])

    def test_empty_template(self):
        doc = self.parse("<body><template></template></body>")
        template = self.get_template(doc)
        content = getTemplateContent(template)
        self.assertIsNotNone(content)
        self.assertEqual(self.content_children(content), [])
        # Walking an empty fragment yields no tokens and must not raise.
        self.assertEqual(
            list(getTreeWalker(self.walker)(content)), [])
        # Round-tripping an empty template still emits the pair of tags.
        output = serialize(doc, tree=self.walker,
                           omit_optional_tags=False)
        self.assertIn("<template></template>", output)

    def test_text_only_template(self):
        doc = self.parse("<body><template>plain text</template></body>")
        content = getTemplateContent(self.get_template(doc))
        tokens = list(getTreeWalker(self.walker)(content))
        self.assertEqual(
            [token["data"] for token in tokens if token["type"] == "Characters"],
            ["plain text"])

    def test_content_survives_repeated_parse(self):
        parser = HTMLParser(tree=getTreeBuilder(self.builder))
        first = parser.parse("<body><template><b>one</b></template></body>")
        parser.parse("<body><template><i>two</i></template></body>")
        # The first document's binding must not be cleared by the second run.
        first_content = getTemplateContent(self.get_template(first))
        self.assertEqual(self.content_children(first_content), ["b"])

    def _first(self, container):
        if self.builder == "dom":
            return container.childNodes[0]
        return container[0]

    def _body(self, doc):
        if self.builder == "dom":
            return doc.getElementsByTagName("body")[0]
        return next(node for node in doc.iter()
                    if node.tag == "{%s}body" % HTML_NS)


class EtreeTemplateTests(TemplateCases, _EtreeMixin, unittest.TestCase):
    pass


class DomTemplateTests(TemplateCases, _DomMixin, unittest.TestCase):
    def test_content_is_document_fragment_node(self):
        doc = self.parse("<body><template><p>x</p></template></body>")
        content = getTemplateContent(self.get_template(doc))
        self.assertEqual(content.nodeType, Node.DOCUMENT_FRAGMENT_NODE)


@unittest.skipUnless(HAS_LXML, "lxml is not installed")
class LxmlTemplateTests(TemplateCases, _LxmlMixin, unittest.TestCase):
    pass


if __name__ == "__main__":
    unittest.main()
