"""Test focus within selectors."""
import bs4

import soupsieve as sv
from .. import util


class TestFocusWithin(util.TestCase):
    """Test focus within selectors."""

    MARKUP = """
    <form id="form">
      <input type="text">
    </form>
    """

    def test_focus_within(self):
        """Test focus within."""

        self.assert_selector(
            self.MARKUP,
            "form:focus-within",
            [],
            flags=util.HTML
        )

    def test_not_focus_within(self):
        """Test inverse of focus within."""

        self.assert_selector(
            self.MARKUP,
            "form:not(:focus-within)",
            ["form"],
            flags=util.HTML
        )

    def test_focus_within_with_state(self):
        """Test focus within when the focused element is supplied explicitly."""

        soup = bs4.BeautifulSoup(self.MARKUP, 'html.parser')
        form = soup.find(id='form')
        text_input = form.find('input')
        rule = sv.compile(':focus-within')

        # The focused element and its ancestors match.
        matched = rule.select(soup, state=sv.InteractiveState(text_input, hover=[]))
        self.assertIn(text_input, matched)
        self.assertIn(form, matched)
        self.assertTrue(rule.match(form, state=sv.InteractiveState(text_input, hover=[])))
        # Without state the pseudo class still matches nothing.
        self.assertEqual(rule.select(soup), [])
