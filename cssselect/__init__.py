"""
CSS Selectors based on XPath
============================

This module supports selecting XML/HTML elements based on CSS selectors.

:copyright: (c) 2007-2012 Ian Bicking and contributors.
See AUTHORS for more details.
:license: BSD, see LICENSE for more details.

"""

from cssselect.parser import (
    FunctionalPseudoElement,
    LenientParse,
    ParsedBranch,
    ParseFailure,
    Selector,
    SelectorError,
    SelectorSyntaxError,
    SourceRange,
    parse,
    parse_lenient,
    split_selector_groups,
)
from cssselect.xpath import (
    BranchReport,
    ExpressionError,
    GenericTranslator,
    HTMLTranslator,
    TranslationIssue,
    ValidationReport,
)

__all__ = (
    "BranchReport",
    "ExpressionError",
    "FunctionalPseudoElement",
    "GenericTranslator",
    "HTMLTranslator",
    "LenientParse",
    "ParseFailure",
    "ParsedBranch",
    "Selector",
    "SelectorError",
    "SelectorSyntaxError",
    "SourceRange",
    "TranslationIssue",
    "ValidationReport",
    "parse",
    "parse_lenient",
    "split_selector_groups",
)

VERSION = "1.6.0"
__version__ = VERSION
