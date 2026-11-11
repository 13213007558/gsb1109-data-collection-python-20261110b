"""Test custom selector aliases."""
from .. import util
import soupsieve as sv


class TestCustomSelectors(util.TestCase):
    """Test custom selector aliases."""

    MARKUP = """
    <body>
    <h1 id="1">Header 1</h1>
    <h2 id="2">Header 2</h2>
    <p id="3"></p>
    <p id="4"><span>child</span></p>
    </body>
    """

    def test_custom_select(self):
        """Ensure custom is properly passed via the API."""

        import bs4
        soup = bs4.BeautifulSoup(self.MARKUP, 'html.parser')

        custom_selectors = {
            ":--headers": "h1, h2, h3, h4, h5, h6"
        }
        tags = sv.select(':--headers', soup, custom=custom_selectors)
        self.assertEqual([t['id'] for t in tags], ['1', '2'])

    def test_custom_selectors(self):
        """Test custom selectors."""

        custom_selectors = {
            ":--headers": "h1, h2, h3, h4, h5, h6",
            ":--parent": ":has(> *|*)"
        }

        self.assert_selector(
            self.MARKUP,
            ':--headers',
            ['1', '2'],
            custom=custom_selectors,
            flags=util.HTML
        )

        self.assert_selector(
            self.MARKUP,
            ':--headers:nth-child(2)',
            ['2'],
            custom=custom_selectors,
            flags=util.HTML
        )

        self.assert_selector(
            self.MARKUP,
            'p:--parent',
            ['4'],
            custom=custom_selectors,
            flags=util.HTML
        )

    def test_custom_escapes(self):
        """Test custom selectors with escapes."""

        custom_selectors = {
            r":--Header\s": "h1, h2, h3, h4, h5, h6"
        }

        self.assert_selector(
            self.MARKUP,
            r':--\HeaderS',
            ['1', '2'],
            custom=custom_selectors,
            flags=util.HTML
        )

    def test_custom_selectors_exotic(self):
        """Test custom selectors."""

        custom_selectors = {":--": "h1, h2, h3, h4, h5, h6"}

        self.assert_selector(
            self.MARKUP,
            ':--',
            ['1', '2'],
            custom=custom_selectors,
            flags=util.HTML
        )

    def test_custom_dependency(self):
        """Test custom selector dependency on other custom selectors."""

        custom_selectors = {
            ":--parent": ":has(> *|*)",
            ":--parent-paragraph": "p:--parent"
        }

        self.assert_selector(
            self.MARKUP,
            ':--parent-paragraph',
            ['4'],
            custom=custom_selectors,
            flags=util.HTML
        )

    def test_custom_dependency_out_of_order(self):
        """Test custom selector out of order dependency."""

        custom_selectors = {
            ":--parent-paragraph": "p:--parent",
            ":--parent": ":has(> *|*)"
        }

        with self.assertRaises(sv.SelectorSyntaxError):
            sv.compile(':--parent-paragraph', custom = custom_selectors)

    def test_custom_dependency_recursion(self):
        """Test that we fail on dependency recursion."""

        custom_selectors = {
            ":--parent-paragraph": "p:--parent",
            ":--parent": "p:--parent-paragraph"
        }

        self.assert_raises(':--parent', sv.SelectorSyntaxError, custom=custom_selectors)

    def test_bad_custom(self):
        """Test that a bad custom raises a syntax error."""

        custom_selectors = {
            ":--parent": ":has(> *|*)",
            ":--parent-paragraph": "p:--parent"
        }

        self.assert_raises(':--wrong', sv.SelectorSyntaxError, custom=custom_selectors)

    def test_bad_custom_syntax(self):
        """Test that a custom selector with bad syntax in its name fails."""

        self.assert_raises('div', sv.SelectorSyntaxError, custom={":--parent.": ":has(> *|*)"})

    def test_pseudo_class_collision(self):
        """Test that a custom selector cannot match an already existing pseudo-class name."""

        self.assert_raises('div', sv.SelectorSyntaxError, custom={":hover": ":has(> *|*)"})

    def test_custom_collision(self):
        """Test that a custom selector cannot match an already existing custom name."""

        self.assert_raises('div', KeyError, custom={":--parent": ":has(> *|*)", ":--PARENT": ":has(> *|*)"})


class TestValidateCustomSelectors(util.TestCase):
    """Test offline validation of custom selector aliases."""

    def test_validate_ok(self):
        """Test that a valid alias package passes, including aliases nothing references."""

        sv.validate_custom_selectors({
            ":--headers": "h1, h2, h3, h4, h5, h6",
            ":--parent": ":has(> *|*)",
            ":--parent-paragraph": "p:--parent",
            ":--unused": "span:--headers"
        })

    def test_validate_no_sources(self):
        """Test that validating no sources is a no-op."""

        self.assertIsNone(sv.validate_custom_selectors())
        self.assertIsNone(sv.validate_custom_selectors({}))

    def test_validate_out_of_order_dependency(self):
        """Test that definition order does not matter when validating a package."""

        sv.validate_custom_selectors({
            ":--parent-paragraph": "p:--parent",
            ":--parent": ":has(> *|*)"
        })

    def test_validate_unreferenced_alias_checked(self):
        """Test that a broken alias is caught even when no main rule references it."""

        with self.assertRaises(sv.SelectorSyntaxError) as ctx:
            sv.validate_custom_selectors({
                ":--headers": "h1, h2",
                ":--broken": "p:--missing"
            })

        message = str(ctx.exception)
        self.assertIn(":--missing", message)
        self.assertIn(":--broken -> :--missing", message)
        self.assertIn("p:--missing", message)

    def test_validate_undefined_chain(self):
        """Test that an undefined name reports the full reference chain and original rules."""

        with self.assertRaises(sv.SelectorSyntaxError) as ctx:
            sv.validate_custom_selectors({
                ":--alpha": "div:--beta",
                ":--beta": "span:--missing",
                ":--unrelated": "p"
            })

        message = str(ctx.exception)
        self.assertIn("Undefined custom selector ':--missing'", message)
        self.assertIn(":--alpha -> :--beta -> :--missing", message)
        self.assertIn(":--alpha: 'div:--beta'", message)
        self.assertIn(":--beta: 'span:--missing'", message)

    def test_validate_circular(self):
        """Test that a circular reference reports the full chain and original rules."""

        with self.assertRaises(sv.SelectorSyntaxError) as ctx:
            sv.validate_custom_selectors({
                ":--a": ":--b",
                ":--b": ":--c",
                ":--c": ":--a"
            })

        message = str(ctx.exception)
        self.assertIn("Circular reference to custom selector ':--a'", message)
        self.assertIn(":--a -> :--b -> :--c -> :--a", message)
        self.assertIn(":--b: ':--c'", message)

    def test_validate_circular_across_sources(self):
        """Test that cycles spanning aliases from different sources are caught."""

        with self.assertRaises(sv.SelectorSyntaxError) as ctx:
            sv.validate_custom_selectors(
                {":--alpha": "div:--beta"},
                {":--beta": "span:--alpha"}
            )

        message = str(ctx.exception)
        self.assertIn(":--alpha -> :--beta -> :--alpha", message)
        self.assertIn(":--alpha: 'div:--beta'", message)
        self.assertIn(":--beta: 'span:--alpha'", message)

    def test_validate_circular_self(self):
        """Test that an alias referencing itself is caught."""

        with self.assertRaises(sv.SelectorSyntaxError) as ctx:
            sv.validate_custom_selectors({":--self": "p:--self"})

        self.assertIn(":--self -> :--self", str(ctx.exception))

    def test_validate_quoted_attribute_value_not_dependency(self):
        """Test that text in a quoted attribute value does not count as a dependency."""

        sv.validate_custom_selectors({
            ":--alpha": '[data-ref=":--beta"]',
            ":--gamma": "[data-ref=':--delta']"
        })

    def test_validate_quoted_contains_value_not_dependency(self):
        """Test that text in a quoted `:-soup-contains` value does not count as a dependency."""

        sv.validate_custom_selectors({":--alpha": 'p:-soup-contains(":--beta")'})

    def test_validate_duplicate_across_sources(self):
        """Test that a name defined in two sources fails."""

        with self.assertRaises(KeyError):
            sv.validate_custom_selectors({":--a": "div"}, {":--A": "span"})

    def test_validate_invalid_name(self):
        """Test that an invalid alias name fails."""

        with self.assertRaises(sv.SelectorSyntaxError):
            sv.validate_custom_selectors({":hover": "div"})

    def test_validate_bad_key_type(self):
        """Test that a non-string alias name fails."""

        with self.assertRaises(TypeError):
            sv.validate_custom_selectors({1: "div"})

    def test_validate_bad_value_type(self):
        """Test that a non-string rule fails."""

        with self.assertRaises(TypeError):
            sv.validate_custom_selectors({":--a": 1})

    def test_validate_bad_source_type(self):
        """Test that a non-mapping source fails."""

        with self.assertRaises(TypeError):
            sv.validate_custom_selectors([(":--a", "div")])

    def test_validate_syntax_error_names_alias(self):
        """Test that a malformed rule is reported along with the alias that defined it."""

        with self.assertRaises(sv.SelectorSyntaxError) as ctx:
            sv.validate_custom_selectors({":--bad": "div..x"})

        self.assertIn(":--bad", str(ctx.exception))

    def test_validate_does_not_change_default_behavior(self):
        """Test that validation does not compile or alter the alias package for later use."""

        custom = {":--headers": "h1, h2"}
        sv.validate_custom_selectors(custom)
        self.assertEqual(custom, {":--headers": "h1, h2"})

        import bs4
        soup = bs4.BeautifulSoup("<h1 id='1'></h1><h2 id='2'></h2><p id='3'></p>", 'html.parser')
        tags = sv.select(':--headers', soup, custom=custom)
        self.assertEqual([t['id'] for t in tags], ['1', '2'])
