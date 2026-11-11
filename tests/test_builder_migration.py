"""
Cross-builder migration tests for the crawl worker.

The crawl worker is swapping its Beautiful Soup tree builder. These tests
run the selectors the worker relies on -- adjacent sibling (`+`),
`:nth-child`, and namespace-qualified selectors -- against every available
HTML builder (`html.parser`, `lxml`, `html5lib`), including pages with
unclosed table tags, and compare the results.

Two kinds of outcome are kept apart:

1. Differences explained by tree building (`TestUnclosedTableTags`,
   `TestNamespaceQualifiedSelectors`): each expected change is backed by an
   explicit assertion on the parsed tree (the "evidence"), so a result
   difference is never accepted without a proven structural cause.
2. Same tree, different match (`TestSameTreeDifferentMatch`): builders that
   produce identical trees must produce identical matches. No such
   discrepancies are known today; they are isolated here so a future one
   blocks the migration decision instead of slipping through.
"""
import unittest
import textwrap

import bs4

import soupsieve as sv
from . import util

HTML_BUILDERS = ('html.parser', 'lxml', 'html5lib')

NS = {
    'svg': 'http://www.w3.org/2000/svg',
    'xhtml': 'http://www.w3.org/1999/xhtml'
}

BROKEN_TABLE = """
<div id="root">
<table id="t">
<tr id="r1"><td id="a">1<td id="b">2
<tr id="r2"><td id="c">3<td id="d">4
</table>
<p id="after">x</p>
</div>
"""

SVG_FRAGMENT = """
<div id="root">
<svg id="s1" viewBox="0 0 10 10"><circle id="c1" r="5"/></svg>
</div>
"""


def available_html_builders():
    """Return the HTML builders that are installed."""

    return list(util.available_parsers(*HTML_BUILDERS))


def parse_all(markup):
    """Parse the same markup with every available HTML builder."""

    return {
        parser: bs4.BeautifulSoup(textwrap.dedent(markup), parser)
        for parser in available_html_builders()
    }


def tree_signature(tag, normalize_xhtml=False):
    """Structural signature: nested `(name, namespace, children)` tuples.

    With `normalize_xhtml`, the XHTML namespace is treated as no namespace:
    Soup Sieve matches HTML documents as if all tags were in the XHTML
    namespace, so the distinction is not observable through selectors.
    """

    namespace = tag.namespace
    if normalize_xhtml and namespace == NS['xhtml']:
        namespace = None
    return (
        tag.name,
        namespace,
        tuple(
            tree_signature(child, normalize_xhtml)
            for child in tag.children
            if isinstance(child, bs4.element.Tag)
        )
    )


def select_ids(soup, selector, namespaces=None):
    """Run a selector and return the sorted ids of matched elements."""

    return sorted(
        el.get('id') for el in sv.select(selector, soup, namespaces=namespaces)
    )


class TestUnclosedTableTags(unittest.TestCase):
    """
    Unclosed `<td>`/`<tr>` across builders.

    `html.parser` nests everything, `lxml` and `html5lib` auto-close. Every
    selector result asserted here is tied to a structural fact proven in
    `test_tree_shape_evidence`.
    """

    @classmethod
    def setUpClass(cls):
        """Parse the broken table once per builder."""

        cls.soups = parse_all(BROKEN_TABLE)

    def table(self, parser):
        """Return the `table#t` element for a builder."""

        return self.soups[parser].find('table')

    def test_tree_shape_evidence(self):
        """Prove the builders really do build different trees."""

        for parser in available_html_builders():
            table = self.table(parser)
            rows = table.find_all('tr')
            cells_a = table.find('td', id='a')

            if parser == 'html.parser':
                # No auto-closing: the second cell nests inside the first,
                # and the second row nests inside the first row.
                self.assertIsNotNone(cells_a.find('td'))
                self.assertEqual(len(rows[0].find_all('tr')), 1)
                self.assertIsNone(table.find('tbody'))
            elif parser == 'lxml':
                # Cells and rows are auto-closed into siblings;
                # no `tbody` is synthesized.
                self.assertIsNone(cells_a.find('td'))
                self.assertEqual(
                    len(rows[0].find_all('td', recursive=False)), 2
                )
                self.assertIsNone(table.find('tbody'))
            else:
                # `html5lib` auto-closes like `lxml` and additionally
                # inserts `tbody`.
                self.assertIsNone(cells_a.find('td'))
                self.assertEqual(
                    len(rows[0].find_all('td', recursive=False)), 2
                )
                self.assertIsNotNone(table.find('tbody'))

        # The signatures are pairwise different: no two builders build
        # the same tree.
        signatures = [
            tree_signature(self.table(p)) for p in available_html_builders()
        ]
        self.assertEqual(len(signatures), len(set(signatures)))

    def test_adjacent_sibling_cells(self):
        """`td + td` only matches once cells are siblings."""

        expected = {
            'html.parser': [],
            'lxml': ['b', 'd'],
            'html5lib': ['b', 'd']
        }
        for parser, soup in self.soups.items():
            self.assertEqual(
                select_ids(soup, 'td + td'), expected[parser], parser
            )

    def test_adjacent_sibling_rows(self):
        """`tr + tr` only matches once rows are siblings."""

        expected = {'html.parser': [], 'lxml': ['r2'], 'html5lib': ['r2']}
        for parser, soup in self.soups.items():
            self.assertEqual(
                select_ids(soup, 'tr + tr'), expected[parser], parser
            )

    def test_nth_child(self):
        """`:nth-child` positions shift with the nesting of unclosed cells."""

        for parser, soup in self.soups.items():
            if parser == 'html.parser':
                # Every cell/row is an only child in the nested tree.
                self.assertEqual(
                    select_ids(soup, 'td:nth-child(2)'), [], parser
                )
                self.assertEqual(
                    select_ids(soup, 'tr:nth-child(2)'), [], parser
                )
            else:
                self.assertEqual(
                    select_ids(soup, 'td:nth-child(2)'), ['b', 'd'], parser
                )
                self.assertEqual(
                    select_ids(soup, 'tr:nth-child(2)'), ['r2'], parser
                )

    def test_table_child_combinator(self):
        """`table > tr` differs under all three builders.

        `html.parser` nests the second row, `lxml` flattens rows into the
        table, `html5lib` inserts a `tbody` in between.
        """

        expected = {
            'html.parser': ['r1'],
            'lxml': ['r1', 'r2'],
            'html5lib': []
        }
        for parser, soup in self.soups.items():
            self.assertEqual(
                select_ids(soup, 'table > tr'), expected[parser], parser
            )

    def test_stable_selectors(self):
        """Selectors whose results do not change across builders."""

        for parser, soup in self.soups.items():
            self.assertEqual(
                select_ids(soup, 'table + p'), ['after'], parser
            )
            self.assertEqual(
                select_ids(soup, 'table td'), ['a', 'b', 'c', 'd'], parser
            )
            self.assertEqual(
                select_ids(soup, '#t tr'), ['r1', 'r2'], parser
            )


@util.requires_html5lib
class TestNamespaceQualifiedSelectors(unittest.TestCase):
    """
    Inline SVG: only `html5lib` assigns real namespaces.

    `html.parser`/`lxml` report `None` for every tag. Soup Sieve treats tags
    in HTML documents as belonging to the XHTML namespace, which is why
    `xhtml|*` selectors match under `html.parser` and `lxml` but not under
    `html5lib` for foreign (SVG) content.
    """

    @classmethod
    def setUpClass(cls):
        """Parse the SVG fragment once per builder."""

        cls.soups = parse_all(SVG_FRAGMENT)

    def test_namespace_evidence(self):
        """Prove the namespace assignment itself differs between builders."""

        for parser, soup in self.soups.items():
            circle = soup.find('circle')
            if parser == 'html5lib':
                self.assertEqual(circle.namespace, NS['svg'])
                self.assertEqual(soup.find('svg').namespace, NS['svg'])
            else:
                self.assertIsNone(circle.namespace)
                self.assertIsNone(soup.find('svg').namespace)

    def test_prefixed_selector(self):
        """`svg|circle` only matches where the namespace is assigned."""

        for parser, soup in self.soups.items():
            expected = ['c1'] if parser == 'html5lib' else []
            self.assertEqual(
                select_ids(soup, 'svg|circle', NS), expected, parser
            )

    def test_xhtml_prefix_on_foreign_content(self):
        """`xhtml|circle` matches only under the implied-namespace builders."""

        for parser, soup in self.soups.items():
            expected = [] if parser == 'html5lib' else ['c1']
            self.assertEqual(
                select_ids(soup, 'xhtml|circle', NS), expected, parser
            )

    def test_stable_selectors(self):
        """Namespace-insensitive selectors do not change across builders."""

        for parser, soup in self.soups.items():
            self.assertEqual(
                select_ids(soup, '*|circle', NS), ['c1'], parser
            )
            self.assertEqual(
                select_ids(soup, 'circle', NS), ['c1'], parser
            )
            self.assertEqual(
                select_ids(soup, 'div > svg > circle', NS), ['c1'], parser
            )


#: Cases rechecked below for "same tree, different match" discrepancies.
#: Each entry is `(name, markup, selectors, namespaces)`; content is rooted
#: at `#root` so the comparison is not dominated by `html`/`body` wrappers.
SAME_TREE_CASES = [
    (
        'unclosed table',
        BROKEN_TABLE,
        [
            'td + td', 'tr + tr', 'table + p', 'td:nth-child(2)',
            'tr:nth-child(2)', 'table > tr', 'table td', '#t tr'
        ],
        None
    ),
    (
        'unclosed list items',
        '<div id="root"><ul id="u"><li id="l1">a<li id="l2">b'
        '<li id="l3">c</ul><span id="s">s</span></div>',
        ['li + li', 'li:nth-child(2)', 'li:last-child', 'ul + span'],
        None
    ),
    (
        'unclosed paragraphs',
        '<div id="root"><p id="p1">a<p id="p2">b</div>',
        ['p + p', 'p:nth-child(2)', 'div > p'],
        None
    ),
    (
        'inline svg namespaces',
        SVG_FRAGMENT,
        [
            'svg|circle', 'xhtml|circle', '*|circle', 'circle',
            'div > svg > circle'
        ],
        NS
    ),
    (
        'well-formed table',
        '<div id="root"><table id="t">'
        '<tr id="r1"><td id="a">1</td><td id="b">2</td></tr>'
        '<tr id="r2"><td id="c">3</td><td id="d">4</td></tr>'
        '</table></div>',
        ['td + td', 'tr + tr', 'td:nth-child(2)', 'table > tr'],
        None
    ),
]


class TestSameTreeDifferentMatch(unittest.TestCase):
    """
    Builders that produce identical trees must produce identical matches.

    Cases are grouped by the structural signature of their `#root` subtree
    (with the XHTML namespace normalized away, since Soup Sieve does not
    distinguish it from no namespace in HTML documents); within a group
    every selector must return the same ids. No discrepancies are known
    today -- if this test ever fails, the mismatch has no tree-building
    explanation and the migration decision needs revisiting.
    """

    def test_same_tree_same_match(self):
        """Compare match results within each group of identical trees."""

        discrepancies = []
        for name, markup, selectors, namespaces in SAME_TREE_CASES:
            soups = parse_all(markup)
            groups = {}
            for parser, soup in soups.items():
                signature = tree_signature(
                    soup.find(id='root'), normalize_xhtml=True
                )
                results = {
                    selector: select_ids(soup, selector, namespaces)
                    for selector in selectors
                }
                groups.setdefault(signature, {})[parser] = results

            for group in groups.values():
                if len(group) < 2:
                    continue
                reference_parser, reference = next(iter(group.items()))
                for parser, results in group.items():
                    for selector in selectors:
                        if results[selector] != reference[selector]:
                            discrepancies.append(
                                f'{name}: {selector!r} differs for '
                                f'identical trees: '
                                f'{reference_parser} -> '
                                f'{reference[selector]}, '
                                f'{parser} -> {results[selector]}'
                            )

        self.assertEqual(discrepancies, [])
