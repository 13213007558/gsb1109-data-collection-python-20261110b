"""Test focus visible selectors."""
import bs4

import soupsieve as sv
from .. import util


class TestFocusVisible(util.TestCase):
    """Test focus visible selectors."""

    MARKUP = """
    <form id="form">
      <input type="text">
    </form>
    """

    def test_focus_visible(self):
        """Test focus visible."""

        self.assert_selector(
            self.MARKUP,
            "form:focus-visible",
            [],
            flags=util.HTML
        )

    def test_not_focus_visible(self):
        """Test inverse of focus visible."""

        self.assert_selector(
            self.MARKUP,
            "form:not(:focus-visible)",
            ["form"],
            flags=util.HTML
        )

    def test_focus_visible_with_state(self):
        """Test focus visible when the elements are supplied explicitly."""

        soup = bs4.BeautifulSoup(self.MARKUP, 'html.parser')
        text_input = soup.find('input')
        rule = sv.compile('input:focus-visible')

        self.assertEqual(
            rule.select(soup, state=sv.InteractiveState(text_input, focus_visible=text_input, hover=[])),
            [text_input]
        )
        # If no focus-visible elements are supplied, the pseudo class matches nothing.
        self.assertEqual(rule.select(soup, state=sv.InteractiveState(text_input, hover=[])), [])
