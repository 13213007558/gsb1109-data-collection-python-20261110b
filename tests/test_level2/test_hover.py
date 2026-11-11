"""Test hover selector."""
import bs4

import soupsieve as sv
from .. import util


class TestHover(util.TestCase):
    """Test hover selector."""

    def test_hover(self):
        """Test hover."""

        markup = """
        <div>
        <p>Some text <span id="1" class="foo:bar:foobar"> in a paragraph</span>.
        <a id="2" class="bar" href="http://google.com">Link</a>
        <a id="3">Placeholder text.</a>
        </p>
        </div>
        """

        self.assert_selector(
            markup,
            "a:hover",
            [],
            flags=util.HTML
        )

    def test_hover_with_state(self):
        """Test hover when the pointer path is supplied explicitly."""

        markup = """
        <div id="div">
        <p id="p"><a id="2" class="bar" href="http://google.com">Link</a><a id="3">Placeholder text.</a></p>
        </div>
        """
        soup = bs4.BeautifulSoup(markup, 'html.parser')
        link = soup.find(id='2')
        state = sv.InteractiveState(None, hover=[link, soup.find(id='p'), soup.find(id='div')])
        rule = sv.compile('a:hover')

        self.assertEqual(rule.select(soup, state=state), [link])
        self.assertTrue(rule.match(link, state=state))
        self.assertFalse(rule.match(soup.find(id='3'), state=state))
        # The hover path is per call, so without state nothing matches.
        self.assertEqual(rule.select(soup), [])
