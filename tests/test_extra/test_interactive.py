"""Test interactive pseudo classes (`:hover`, `:focus`, `:focus-visible`, and `:focus-within`)."""
import copy
import pickle

import bs4

import soupsieve as sv
from .. import util


class TestInteractive(util.TestCase):
    """Test interactive pseudo classes with explicitly supplied focus and hover state."""

    MARKUP = """
    <html id="html">
    <body id="body">
    <form id="form">
    <div id="wrap"><input id="in1" type="text"><input id="in2" type="text"></div>
    <a id="link" href="#">link</a>
    </form>
    </body>
    </html>
    """

    def setUp(self):
        """Parse the markup and collect the elements used as state."""

        super().setUp()
        self.soup = self.soup(self.MARKUP, 'html.parser')
        self.html = self.soup.find(id='html')
        self.body = self.soup.find(id='body')
        self.form = self.soup.find(id='form')
        self.wrap = self.soup.find(id='wrap')
        self.in1 = self.soup.find(id='in1')
        self.in2 = self.soup.find(id='in2')
        self.link = self.soup.find(id='link')

    def state(self, **kwargs):
        """Build a state object, defaulting to no focus and an empty hover path."""

        kwargs.setdefault('focus', None)
        kwargs.setdefault('hover', [])
        return sv.InteractiveState(**kwargs)

    @staticmethod
    def ids(elements):
        """Get the IDs of the elements."""

        return [el['id'] for el in elements]

    def test_no_state_matches_nothing(self):
        """Without state, the interactive pseudo classes match nothing, including inside `:not()`."""

        for pattern in (':hover', ':focus', ':focus-visible', ':focus-within'):
            self.assertEqual(sv.select(pattern, self.soup), [])
            self.assertFalse(sv.match(pattern, self.in1))
        # `:not(:focus)` therefore matches every element, as it did before state could be supplied.
        self.assertEqual(sv.select('input:not(:focus)', self.soup), [self.in1, self.in2])

    def test_focus(self):
        """Test `:focus` against the explicitly focused element."""

        state = self.state(focus=self.in1)
        self.assertEqual(sv.select(':focus', self.soup, state=state), [self.in1])
        self.assertTrue(sv.match(':focus', self.in1, state=state))
        self.assertFalse(sv.match(':focus', self.in2, state=state))
        self.assertEqual(sv.select('input:focus', self.soup, state=state), [self.in1])

    def test_no_focus(self):
        """Test that nothing matches `:focus` when no element has focus."""

        state = self.state(focus=None)
        self.assertEqual(sv.select(':focus', self.soup, state=state), [])
        self.assertEqual(sv.select(':focus-within', self.soup, state=state), [])

    def test_hover_path(self):
        """Test `:hover` against the element under the pointer and its ancestors on the hover path."""

        state = self.state(hover=[self.link, self.body, self.html])
        self.assertEqual(sv.select(':hover', self.soup, state=state), [self.html, self.body, self.link])
        self.assertTrue(sv.match('a:hover', self.link, state=state))
        self.assertFalse(sv.match('a:hover', self.in1, state=state))

    def test_focus_visible(self):
        """Test `:focus-visible` with a tag, an iterable, and the default of matching nothing."""

        tag_state = self.state(focus=self.in1, focus_visible=self.in1)
        self.assertEqual(sv.select(':focus-visible', self.soup, state=tag_state), [self.in1])

        iterable_state = self.state(focus=self.in1, focus_visible=[self.in2])
        self.assertEqual(sv.select(':focus-visible', self.soup, state=iterable_state), [self.in2])

        default_state = self.state(focus=self.in1)
        self.assertEqual(sv.select(':focus-visible', self.soup, state=default_state), [])
        # `:focus` is independent of the `:focus-visible` information.
        self.assertEqual(sv.select(':focus', self.soup, state=default_state), [self.in1])

    def test_focus_within(self):
        """Test `:focus-within` matches the focused element and every ancestor."""

        state = self.state(focus=self.in1)
        self.assertEqual(
            sorted(self.ids(sv.select(':focus-within', self.soup, state=state))),
            ['body', 'form', 'html', 'in1', 'wrap']
        )
        self.assertTrue(sv.match('form:focus-within', self.form, state=state))
        self.assertFalse(sv.match('a:focus-within', self.link, state=state))

    def test_compound_conditions(self):
        """Test interactive pseudo classes combined in a single compound selector."""

        state = self.state(focus=self.in1, hover=[self.in1, self.wrap, self.form, self.body, self.html])
        self.assertTrue(sv.match('input:focus:hover', self.in1, state=state))
        self.assertFalse(sv.match('input:focus:hover', self.in2, state=state))
        self.assertEqual(sv.select('input:focus:hover', self.soup, state=state), [self.in1])
        # Both states must hold, so a focused but unhovered element does not match.
        self.assertFalse(sv.match('input:hover', self.in2, state=self.state(focus=self.in1, hover=[])))

    def test_combination_conditions(self):
        """Test interactive state queried from selector lists, relationships, and `nth ... of`."""

        state = self.state(focus=self.in1, focus_visible=self.in1, hover=[self.link, self.body, self.html])

        self.assertEqual(
            sv.select(':is(a:hover, input:focus)', self.soup, state=state),
            [self.in1, self.link]
        )
        self.assertEqual(sv.select('input:not(:focus)', self.soup, state=state), [self.in2])
        self.assertEqual(sv.select('form:has(input:focus)', self.soup, state=state), [self.form])
        self.assertEqual(sv.select('div:has(> input:focus)', self.soup, state=state), [self.wrap])
        self.assertEqual(sv.select('form input:focus', self.soup, state=state), [self.in1])
        self.assertEqual(sv.select(':nth-child(1 of input:focus)', self.soup, state=state), [self.in1])
        # A double negation still reads the supplied state and only matches the focused input.
        self.assertEqual(sv.select('input:not(:not(:focus))', self.soup, state=state), [self.in1])

    def test_compiled_methods(self):
        """Test every method of a compiled selector carries the per-call state."""

        rule = sv.compile('input:focus')
        state = self.state(focus=self.in1)
        self.assertTrue(rule.match(self.in1, state=state))
        self.assertFalse(rule.match(self.in2, state=state))
        self.assertEqual(rule.select(self.soup, state=state), [self.in1])
        self.assertEqual(rule.select_one(self.soup, state=state), self.in1)
        self.assertEqual(list(rule.iselect(self.soup, state=state)), [self.in1])
        self.assertEqual(rule.closest(self.in1, state=state), self.in1)
        self.assertEqual(rule.filter([self.in1, self.in2], state=state), [self.in1])
        self.assertEqual(rule.filter(self.wrap, state=state), [self.in1])
        # Without state, the same compiled object still matches nothing.
        self.assertEqual(rule.select(self.soup), [])

    def test_module_functions(self):
        """Test the module level functions forward the state."""

        state = self.state(focus=self.in1)
        self.assertEqual(sv.select_one(':focus', self.soup, state=state), self.in1)
        self.assertEqual(list(sv.iselect(':focus', self.soup, state=state)), [self.in1])
        self.assertTrue(sv.match(':focus', self.in1, state=state))
        self.assertEqual(sv.closest(':focus-within', self.in2, state=state), self.wrap)
        self.assertEqual(sv.filter(':focus', [self.in1, self.in2], state=state), [self.in1])

    def test_shared_rule_separate_state(self):
        """Two sessions share one compiled rule but each supplies its own tags and state."""

        rule = sv.compile('input:focus')
        # The compiled rule is shared through the cache and never carries the per-call state.
        self.assertIs(sv.compile('input:focus'), rule)

        other = bs4.BeautifulSoup('<input id="other">', 'html.parser')
        other_input = other.find(id='other')

        first_state = self.state(focus=self.in1)
        second_state = sv.InteractiveState(focus=other_input, hover=[])

        self.assertEqual(rule.select(self.soup, state=first_state), [self.in1])
        self.assertEqual(rule.select(other, state=second_state), [other_input])
        # The state of one session is never visible to the other.
        self.assertEqual(rule.select(other, state=first_state), [])
        self.assertEqual(rule.select(self.soup, state=second_state), [])
        # Old callers that pass no state do not pretend a browser ran.
        self.assertEqual(rule.select(self.soup), [])
        self.assertEqual(rule.select(other), [])

    def test_nocache(self):
        """Test that state matching works with the cache disabled."""

        state = self.state(focus=self.in1)
        self.assertEqual(
            sv.select(':focus-within', self.soup, flags=sv.NOCACHE, state=state),
            sv.select(':focus-within', self.soup, state=state)
        )

    def test_pickle_and_copy(self):
        """Test that a compiled interactive selector survives pickling and copying."""

        rule = sv.compile('form input:focus:hover')
        for restored in (pickle.loads(pickle.dumps(rule)), copy.copy(rule), copy.deepcopy(rule)):
            self.assertEqual(restored, rule)
            state = self.state(focus=self.in1, hover=[self.in1])
            self.assertEqual(restored.select(self.soup, state=state), rule.select(self.soup, state=state))

    def test_invalid_state(self):
        """Test that state elements are validated."""

        with self.assertRaises(TypeError):
            sv.InteractiveState(focus='not a tag')
        with self.assertRaises(TypeError):
            sv.InteractiveState(focus=None, hover=['not a tag'])
        with self.assertRaises(TypeError):
            sv.InteractiveState(focus=None, focus_visible=['not a tag'])
        with self.assertRaises(TypeError):
            sv.InteractiveState(focus=None, hover=123)
        with self.assertRaises(TypeError):
            sv.InteractiveState(focus=None, focus_visible=123)

    def test_state_normalizes_iterables(self):
        """Test that any iterable is accepted for the hover and focus-visible elements."""

        def hover():
            yield self.link

        state = sv.InteractiveState(self.in1, {self.in1}, hover())
        self.assertEqual(sv.select(':hover', self.soup, state=state), [self.link])
        self.assertEqual(sv.select(':focus-visible', self.soup, state=state), [self.in1])
        self.assertIsInstance(state.hover, tuple)
        self.assertIsInstance(state.focus_visible, tuple)

    def test_state_accepts_single_tags(self):
        """A single tag is wrapped, not iterated into its children (a tag is itself iterable)."""

        state = sv.InteractiveState(self.in1, focus_visible=self.in1, hover=self.link)
        self.assertEqual(state.hover, (self.link,))
        self.assertEqual(state.focus_visible, self.in1)
        self.assertEqual(sv.select(':hover', self.soup, state=state), [self.link])
        self.assertEqual(sv.select(':focus-visible', self.soup, state=state), [self.in1])
