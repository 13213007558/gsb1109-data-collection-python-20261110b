"""
Tests for the editor-oriented, non-raising preflight API.

These cover :func:`parse_lenient`, :func:`split_selector_groups` and
:meth:`GenericTranslator.validate_css`, which report every comma-separated
selector branch independently instead of stopping at the first error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from cssselect import (
    FunctionalPseudoElement,
    GenericTranslator,
    HTMLTranslator,
    LenientParse,
    ParsedBranch,
    ParseFailure,
    SelectorError,
    SelectorSyntaxError,
    SourceRange,
    parse,
    parse_lenient,
    split_selector_groups,
)

if TYPE_CHECKING:
    from cssselect.parser import Class, CombinedSelector

TRANSLATOR = GenericTranslator()


class TestSplitSelectorGroups:
    def test_plain_groups(self) -> None:
        css = "div, p > a , .foo"
        branches, commas = split_selector_groups(css)
        assert [css[r.start : r.end] for r in branches] == ["div", "p > a", ".foo"]
        assert [css[r.start : r.end] for r in commas] == [",", ","]
        # The ranges tile the source apart from surrounding whitespace.
        assert branches[0].start == 0
        assert branches[-1].end == len(css)

    def test_comma_inside_attribute_value(self) -> None:
        css = 'a[href="x,y"], b'
        branches, commas = split_selector_groups(css)
        assert [css[r.start : r.end] for r in branches] == ['a[href="x,y"]', "b"]
        assert len(commas) == 1
        assert css[commas[0].start] == ","

    def test_comma_inside_function_arguments(self) -> None:
        css = ":has(a, b), :is(.c, .d), div"
        branches, commas = split_selector_groups(css)
        assert [css[r.start : r.end] for r in branches] == [
            ":has(a, b)",
            ":is(.c, .d)",
            "div",
        ]
        assert len(commas) == 2

    def test_unclosed_string_does_not_split(self) -> None:
        css = 'a[href="x, y'
        branches, commas = split_selector_groups(css)
        # The comma inside the unclosed string is not a separator.
        assert len(branches) == 1
        assert commas == ()

    def test_single_selector(self) -> None:
        branches, commas = split_selector_groups("div")
        assert len(branches) == 1
        assert branches[0] == SourceRange(0, 3)
        assert commas == ()


class TestParseLenient:
    def test_valid_group(self) -> None:
        result = parse_lenient("div, p.foo")
        assert isinstance(result, LenientParse)
        assert len(result.branches) == 2
        for branch in result.branches:
            assert branch.error is None
            assert branch.selector is not None
        assert result.unparsed == ()
        assert [branch.text for branch in result.branches] == ["div", "p.foo"]

    def test_branch_ranges_roundtrip(self) -> None:
        css = "  div.foo , p  "
        result = parse_lenient(css)
        for branch in result.branches:
            assert css[branch.range.start : branch.range.end] == branch.text
            assert branch.text == branch.text.strip()

    def test_syntax_error_isolated_to_branch(self) -> None:
        css = "div, [a=1, p"
        result = parse_lenient(css)
        assert len(result.branches) == 3
        good, bad, also_good = result.branches
        assert good.error is None
        assert good.selector is not None
        assert also_good.error is None
        assert also_good.selector is not None
        assert isinstance(bad, ParsedBranch)
        assert bad.selector is None
        assert isinstance(bad.error, ParseFailure)
        # Ranges are positioned in the original, complete source.
        assert bad.error.range.css(css) == "1"
        assert "Expected string or ident" in bad.error.message

    def test_empty_branch_between_commas(self) -> None:
        result = parse_lenient("div, , p")
        assert len(result.branches) == 3
        empty = result.branches[1]
        assert empty.text == ""
        assert empty.error is not None
        assert "Expected selector" in empty.error.message
        # A valid branch on each side still parses on its own.
        assert result.branches[0].selector is not None
        assert result.branches[2].selector is not None

    def test_trailing_comma(self) -> None:
        result = parse_lenient("div,")
        assert len(result.branches) == 2
        assert result.branches[0].error is None
        assert result.branches[1].error is not None
        assert result.branches[0].comma == SourceRange(3, 4)
        assert result.branches[1].comma is None

    def test_unclosed_string_reported_as_unparsed(self) -> None:
        result = parse_lenient('a[href="unclosed, b')
        assert result.unparsed
        for range_ in result.unparsed:
            assert isinstance(range_, SourceRange)
            assert range_.end <= len('a[href="unclosed, b')
        # The branch containing the unclosed string is itself flagged, but
        # nothing beyond the string is silently treated as checked.
        assert any(branch.error is not None for branch in result.branches)

    def test_strict_parser_still_raises(self) -> None:
        with pytest.raises(SelectorSyntaxError):
            parse("div, [a=1")

    def test_selector_source_ranges(self) -> None:
        selector = parse("div.foo > p#x")[0]
        assert selector.parsed_tree._source_range == SourceRange(0, 13)
        combined = cast("CombinedSelector", selector.parsed_tree)
        class_node = cast("Class", combined.selector)
        assert class_node.selector._source_range == SourceRange(0, 3)

    def test_pseudo_element_range(self) -> None:
        selector = parse("div::before")[0]
        assert selector.pseudo_element == "before"
        assert selector.pseudo_element_range == SourceRange(3, 11)

        functional = parse("a::foo(2)")[0]
        assert functional.pseudo_element_range == SourceRange(1, 9)
        pseudo = functional.pseudo_element
        assert isinstance(pseudo, FunctionalPseudoElement)
        assert pseudo._source_range == SourceRange(1, 9)


class TestValidateCss:
    def test_all_valid_branches_translate(self) -> None:
        css = "div, p.foo, a > b"
        report = TRANSLATOR.validate_css(css)
        assert report.unparsed == ()
        xpaths = [branch.xpath for branch in report.branches]
        assert all(xpath is not None for xpath in xpaths)
        # The per-branch expressions join back into the strict result.
        joined = " | ".join(xpath for xpath in xpaths if xpath is not None)
        assert joined == TRANSLATOR.css_to_xpath(css)

    def test_prefix_is_honored(self) -> None:
        report = TRANSLATOR.validate_css("div", prefix="")
        assert report.branches[0].xpath == "div"

    def test_unsupported_pseudo_class_kind_and_range(self) -> None:
        report = TRANSLATOR.validate_css("div, :lorem-ipsum, p")
        issue = report.branches[1].issue
        assert issue is not None
        assert issue.kind == "unsupported"
        assert issue.range == SourceRange(5, 17)
        assert issue.css == ":lorem-ipsum"
        assert "unknown" in issue.message
        # The neighbors are unaffected.
        assert report.branches[0].xpath is not None
        assert report.branches[2].xpath is not None

    def test_syntax_kind_and_range(self) -> None:
        css = "div, [a=1, p"
        report = TRANSLATOR.validate_css(css)
        issue = report.branches[1].issue
        assert issue is not None
        assert issue.kind == "syntax"
        assert issue.css == "1"
        assert issue.range == SourceRange(8, 9)

    def test_pseudo_element_unsupported_and_located(self) -> None:
        report = TRANSLATOR.validate_css("span::before")
        issue = report.branches[0].issue
        assert issue is not None
        assert issue.kind == "unsupported"
        assert issue.css == "::before"
        assert "Pseudo-elements are not supported" in issue.message

    def test_wildcard_of_type_is_unsupported(self) -> None:
        report = TRANSLATOR.validate_css("*:first-of-type")
        issue = report.branches[0].issue
        assert issue is not None
        assert issue.kind == "unsupported"
        assert issue.css == "*:first-of-type"

    def test_invalid_series_is_unsupported_not_syntax(self) -> None:
        # :nth-child(n-) parses fine but cannot be translated, so it is a
        # backend limitation rather than a spelling mistake.
        report = TRANSLATOR.validate_css(":nth-child(n-)")
        issue = report.branches[0].issue
        assert issue is not None
        assert issue.kind == "unsupported"
        assert "Invalid series" in issue.message

    def test_comma_in_attribute_value_not_split(self) -> None:
        report = TRANSLATOR.validate_css('a[href="x,y"], div')
        assert len(report.branches) == 2
        assert report.branches[0].issue is None
        assert report.branches[0].xpath is not None
        assert report.branches[1].issue is None

    def test_comma_in_nested_function_not_split(self) -> None:
        report = TRANSLATOR.validate_css(":is(a, :unknown), div")
        assert len(report.branches) == 2
        issue = report.branches[0].issue
        assert issue is not None
        assert issue.kind == "unsupported"
        # The range points at the unknown pseudo-class, not the whole branch.
        assert issue.css == ":unknown"
        assert report.branches[1].xpath is not None

    def test_unclosed_string_surfaces_unparsed_range(self) -> None:
        report = TRANSLATOR.validate_css('a[href="unclosed, b')
        assert report.unparsed
        # The editor must not mistake the post-string text for checked work.
        assert all(
            r.start <= r.end <= len('a[href="unclosed, b') for r in report.unparsed
        )

    def test_html_translator_shares_the_api(self) -> None:
        report = HTMLTranslator().validate_css("div, :lorem-ipsum")
        assert report.branches[0].xpath is not None
        assert report.branches[1].issue is not None
        assert report.branches[1].issue.kind == "unsupported"

    def test_never_raises_on_bad_input(self) -> None:
        for css in ("", " ", ",,,", "[", "(", "div,", ":::", "[a=", "a { b"):
            report = TRANSLATOR.validate_css(css)
            assert isinstance(report.branches, tuple)
            for branch in report.branches:
                assert (branch.xpath is None) != (branch.issue is None)

    def test_strict_execution_api_unchanged(self) -> None:
        # css_to_xpath still raises on the first problem, as documented.
        with pytest.raises(SelectorError):
            TRANSLATOR.css_to_xpath("div, :lorem-ipsum, p")
        with pytest.raises(SelectorSyntaxError):
            TRANSLATOR.css_to_xpath("div, [a=1")

    def test_each_branch_is_independent(self) -> None:
        # Several broken branches must each be reported in one pass.
        report = TRANSLATOR.validate_css(
            ":unknown-a, :unknown-b, [a=1], :nth-child(n-)"
        )
        issues = [branch.issue for branch in report.branches if branch.issue]
        assert len(issues) == 4
        assert [issue.css for issue in issues] == [
            ":unknown-a",
            ":unknown-b",
            "1",
            ":nth-child(n-)",
        ]
        assert [issue.kind for issue in issues] == [
            "unsupported",
            "unsupported",
            "syntax",
            "unsupported",
        ]

    def test_unclosed_bracket_keeps_tail_unchecked(self) -> None:
        # With an unclosed bracket the boundary cannot be confirmed, so the
        # remainder is grouped into one branch rather than split wrongly;
        # the editor is told the tail has not been checked independently.
        report = TRANSLATOR.validate_css(
            ":unknown-a, [bad, :nth-child(n-)"
        )
        assert len(report.branches) == 2
        assert report.branches[0].issue is not None
        tail = report.branches[1]
        assert tail.issue is not None
        assert tail.text == "[bad, :nth-child(n-)"
