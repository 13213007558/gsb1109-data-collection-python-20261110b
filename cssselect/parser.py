"""
cssselect.parser
================

Tokenizer, parser and parsed objects for CSS selectors.


:copyright: (c) 2007-2012 Ian Bicking and contributors.
See AUTHORS for more details.
:license: BSD, see LICENSE for more details.

"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    Self,
    TypeAlias,
    Union,
    cast,
    overload,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


def ascii_lower(string: str) -> str:
    """Lower-case, but only in the ASCII range."""
    return string.encode("utf8").lower().decode("utf8")


class SelectorError(Exception):
    """Common parent for :class:`SelectorSyntaxError` and
    :class:`ExpressionError`.

    You can just use ``except SelectorError:`` when calling
    :meth:`~GenericTranslator.css_to_xpath` and handle both exceptions types.

    """


class SelectorSyntaxError(SelectorError, SyntaxError):
    """Parsing a selector that does not match the grammar."""


class _LocatedSyntaxError(SelectorSyntaxError):
    """A :class:`SelectorSyntaxError` that remembers the offending token.

    The strict parser raises it just like a :class:`SelectorSyntaxError`;
    :func:`parse_lenient` uses the stored token to mark the precise source
    range of the problem.
    """

    def __init__(self, message: str, token: Token) -> None:
        super().__init__(message)
        self.token = token


def _syntax_error(message: str, token: Token) -> _LocatedSyntaxError:
    return _LocatedSyntaxError(message, token)


@dataclass(frozen=True, slots=True)
class SourceRange:
    """A half-open range of character positions in the original CSS source.

    :attr:`start` is the index of the first character of the range and
    :attr:`end` the index just past its last character, so slicing the
    original source with ``source[start:end]`` reconstructs the covered
    text.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"Invalid source range: {self!r}")

    def __len__(self) -> int:
        return self.end - self.start

    def css(self, source: str) -> str:
        """Return the part of *source* covered by this range."""
        return source[self.start : self.end]


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """A parse error located in the original CSS source.

    :attr:`range` covers the offending token (or, when its end cannot be
    located, the remainder of the source), and :attr:`message` is the same
    text a :class:`SelectorSyntaxError` would carry.
    """

    message: str
    range: SourceRange


@dataclass(frozen=True, slots=True)
class ParsedBranch:
    """One comma-separated branch of a selector group.

    Exactly one of :attr:`selector` and :attr:`error` is populated:
    parsing succeeded for the former and failed for the latter.
    """

    text: str
    range: SourceRange
    comma: SourceRange | None
    selector: Selector | None
    error: ParseFailure | None


@dataclass(frozen=True, slots=True)
class LenientParse:
    """The result of :func:`parse_lenient`.

    :attr:`branches` lists every comma-separated branch, whether or not it
    parsed, and :attr:`unparsed` ranges cover source fragments that could
    not even be tokenized and therefore have not been checked at all.
    """

    branches: tuple[ParsedBranch, ...]
    unparsed: tuple[SourceRange, ...]


@overload
def attach_source(tree: Tree, source_range: SourceRange | None) -> Tree: ...


@overload
def attach_source(
    tree: FunctionalPseudoElement, source_range: SourceRange | None
) -> FunctionalPseudoElement: ...


def attach_source(tree: object, source_range: SourceRange | None) -> object:
    """Associate *source_range* with a parsed node, in place.

    The parser calls this on every node it creates. Nodes built by hand
    rather than through the parser keep their range set to ``None``.
    """
    cast("Any", tree)._source_range = source_range
    return tree


#### Parsed objects

Tree: TypeAlias = Union[
    "Element",
    "Hash",
    "Class",
    "Function",
    "Pseudo",
    "Attrib",
    "Negation",
    "Relation",
    "Matching",
    "SpecificityAdjustment",
    "CombinedSelector",
]
PseudoElement: TypeAlias = Union["FunctionalPseudoElement", str]


class Selector:
    """
    Represents a parsed selector.

    :meth:`~GenericTranslator.selector_to_xpath` accepts this object,
    but ignores :attr:`pseudo_element`. It is the user’s responsibility
    to account for pseudo-elements and reject selectors with unknown
    or unsupported pseudo-elements.

    """

    def __init__(
        self,
        tree: Tree,
        pseudo_element: PseudoElement | None = None,
        pseudo_element_range: SourceRange | None = None,
    ) -> None:
        self.parsed_tree = tree
        if pseudo_element is not None and not isinstance(
            pseudo_element, FunctionalPseudoElement
        ):
            pseudo_element = ascii_lower(pseudo_element)
        #: A :class:`FunctionalPseudoElement`,
        #: or the identifier for the pseudo-element as a string,
        #  or ``None``.
        #:
        #: +-------------------------+----------------+--------------------------------+
        #: |                         | Selector       | Pseudo-element                 |
        #: +=========================+================+================================+
        #: | CSS3 syntax             | ``a::before``  | ``'before'``                   |
        #: +-------------------------+----------------+--------------------------------+
        #: | Older syntax            | ``a:before``   | ``'before'``                   |
        #: +-------------------------+----------------+--------------------------------+
        #: | From the Lists3_ draft, | ``li::marker`` | ``'marker'``                   |
        #: | not in Selectors3       |                |                                |
        #: +-------------------------+----------------+--------------------------------+
        #: | Invalid pseudo-class    | ``li:marker``  | ``None``                       |
        #: +-------------------------+----------------+--------------------------------+
        #: | Functional              | ``a::foo(2)``  | ``FunctionalPseudoElement(…)`` |
        #: +-------------------------+----------------+--------------------------------+
        #:
        #: .. _Lists3: http://www.w3.org/TR/2011/WD-css3-lists-20110524/#marker-pseudoelement
        self.pseudo_element = pseudo_element
        #: The source range of the pseudo-element, or ``None`` when the
        #: selector has none or was built by hand rather than parsed.
        self.pseudo_element_range = pseudo_element_range

    def __repr__(self) -> str:
        if isinstance(self.pseudo_element, FunctionalPseudoElement):
            pseudo_element = repr(self.pseudo_element)
        elif self.pseudo_element:
            pseudo_element = f"::{self.pseudo_element}"
        else:
            pseudo_element = ""
        return f"{self.__class__.__name__}[{self.parsed_tree!r}{pseudo_element}]"

    def canonical(self) -> str:
        """Return a CSS representation for this selector (a string)"""
        if isinstance(self.pseudo_element, FunctionalPseudoElement):
            pseudo_element = f"::{self.pseudo_element.canonical()}"
        elif self.pseudo_element:
            pseudo_element = f"::{_serialize_ident(self.pseudo_element)}"
        else:
            pseudo_element = ""
        res = f"{self.parsed_tree.canonical()}{pseudo_element}"
        # Strip a redundant universal selector from e.g. "*.foo" (but not
        # from e.g. "* > foo").
        if len(res) > 1 and res[0] == "*" and res[1] in "#.[:":
            res = res[1:]
        return res

    def specificity(self) -> tuple[int, int, int]:
        """Return the specificity_ of this selector as a tuple of 3 integers.

        .. _specificity: http://www.w3.org/TR/selectors/#specificity

        """
        a, b, c = self.parsed_tree.specificity()
        if self.pseudo_element:
            c += 1
        return a, b, c


class Class:
    """
    Represents selector.class_name
    """

    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, class_name: str) -> None:
        self.selector = selector
        self.class_name = class_name

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}[{self.selector!r}.{self.class_name}]"

    def canonical(self) -> str:
        return f"{self.selector.canonical()}.{_serialize_ident(self.class_name)}"

    def specificity(self) -> tuple[int, int, int]:
        a, b, c = self.selector.specificity()
        b += 1
        return a, b, c


class FunctionalPseudoElement:
    """
    Represents selector::name(arguments)

    .. attribute:: name

        The name (identifier) of the pseudo-element, as a string.

    .. attribute:: arguments

        The arguments of the pseudo-element, as a list of tokens.

        **Note:** tokens are not part of the public API,
        and may change between cssselect versions.
        Use at your own risks.

    """

    _source_range: SourceRange | None = None

    def __init__(self, name: str, arguments: Sequence[Token]):
        self.name = ascii_lower(name)
        self.arguments = arguments

    def __repr__(self) -> str:
        token_values = [token.value for token in self.arguments]
        return f"{self.__class__.__name__}[::{self.name}({token_values!r})]"

    def argument_types(self) -> list[str]:
        return [token.type for token in self.arguments]

    def canonical(self) -> str:
        args = "".join(token.css() for token in self.arguments)
        return f"{_serialize_ident(self.name)}({args})"


class Function:
    """
    Represents selector:name(expr)
    """

    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, name: str, arguments: Sequence[Token]) -> None:
        self.selector = selector
        self.name = ascii_lower(name)
        self.arguments = arguments

    def __repr__(self) -> str:
        token_values = [token.value for token in self.arguments]
        return f"{self.__class__.__name__}[{self.selector!r}:{self.name}({token_values!r})]"

    def argument_types(self) -> list[str]:
        return [token.type for token in self.arguments]

    def canonical(self) -> str:
        args = "".join(token.css() for token in self.arguments)
        return f"{self.selector.canonical()}:{_serialize_ident(self.name)}({args})"

    def specificity(self) -> tuple[int, int, int]:
        a, b, c = self.selector.specificity()
        b += 1
        return a, b, c


class Pseudo:
    """
    Represents selector:ident
    """

    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, ident: str) -> None:
        self.selector = selector
        self.ident = ascii_lower(ident)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}[{self.selector!r}:{self.ident}]"

    def canonical(self) -> str:
        return f"{self.selector.canonical()}:{_serialize_ident(self.ident)}"

    def specificity(self) -> tuple[int, int, int]:
        a, b, c = self.selector.specificity()
        b += 1
        return a, b, c


class Negation:
    """
    Represents selector:not(subselector)
    """

    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, subselector: Tree) -> None:
        self.selector = selector
        self.subselector = subselector

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}[{self.selector!r}:not({self.subselector!r})]"

    def canonical(self) -> str:
        subsel = self.subselector.canonical()
        # Strip a redundant universal selector from e.g. "*.foo" (but not
        # from e.g. "* > foo").
        if len(subsel) > 1 and subsel[0] == "*" and subsel[1] in "#.[:":
            subsel = subsel[1:]
        return f"{self.selector.canonical()}:not({subsel})"

    def specificity(self) -> tuple[int, int, int]:
        a1, b1, c1 = self.selector.specificity()
        a2, b2, c2 = self.subselector.specificity()
        return a1 + a2, b1 + b2, c1 + c2


def _combinator_prefix(combinator: Token) -> str:
    # The descendant combinator is implicit in :has() arguments.
    if combinator.value == " ":
        return ""
    return f"{combinator.value} "


class Relation:
    """
    Represents selector:has(relative_selector_list)

    .. attribute:: arguments

        The relative selector list, as a list of ``(combinator, selector)``
        pairs. *combinator* is the leading combinator token of the argument,
        which is a space token when the argument does not spell one out.

    """

    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, arguments: Sequence[tuple[Token, Selector]]):
        self.selector = selector
        self.arguments = arguments

    def __repr__(self) -> str:
        args_str = ", ".join(
            f"{_combinator_prefix(combinator)}{subselector!r}"
            for combinator, subselector in self.arguments
        )
        return f"{self.__class__.__name__}[{self.selector!r}:has({args_str})]"

    def canonical(self) -> str:
        args_str = ", ".join(
            f"{_combinator_prefix(combinator)}{subselector.canonical()}"
            for combinator, subselector in self.arguments
        )
        return f"{self.selector.canonical()}:has({args_str})"

    def specificity(self) -> tuple[int, int, int]:
        a1, b1, c1 = self.selector.specificity()
        a2, b2, c2 = max(subselector.specificity() for _, subselector in self.arguments)
        return a1 + a2, b1 + b2, c1 + c2


class Matching:
    """
    Represents selector:is(selector_list)
    """

    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, selector_list: Iterable[Tree]):
        self.selector = selector
        self.selector_list = selector_list

    def __repr__(self) -> str:
        args_str = ", ".join(repr(s) for s in self.selector_list)
        return f"{self.__class__.__name__}[{self.selector!r}:is({args_str})]"

    def canonical(self) -> str:
        selector_arguments = []
        for s in self.selector_list:
            selarg = s.canonical()
            if len(selarg) > 1 and selarg[0] == "*" and selarg[1] in "#.[:":
                selarg = selarg[1:]
            selector_arguments.append(selarg)
        args_str = ", ".join(selector_arguments)
        return f"{self.selector.canonical()}:is({args_str})"

    def specificity(self) -> tuple[int, int, int]:
        a1, b1, c1 = self.selector.specificity()
        a2, b2, c2 = max(x.specificity() for x in self.selector_list)
        return a1 + a2, b1 + b2, c1 + c2


class SpecificityAdjustment:
    """
    Represents selector:where(selector_list)
    Same as selector:is(selector_list), but its specificity is always 0
    """

    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, selector_list: list[Tree]):
        self.selector = selector
        self.selector_list = selector_list

    def __repr__(self) -> str:
        args_str = ", ".join(repr(s) for s in self.selector_list)
        return f"{self.__class__.__name__}[{self.selector!r}:where({args_str})]"

    def canonical(self) -> str:
        selector_arguments = []
        for s in self.selector_list:
            selarg = s.canonical()
            if len(selarg) > 1 and selarg[0] == "*" and selarg[1] in "#.[:":
                selarg = selarg[1:]
            selector_arguments.append(selarg)
        args_str = ", ".join(selector_arguments)
        return f"{self.selector.canonical()}:where({args_str})"

    def specificity(self) -> tuple[int, int, int]:
        # :where() itself contributes no specificity, but the compound
        # selector it applies to does.
        return self.selector.specificity()


class Attrib:
    """
    Represents selector[namespace|attrib operator value flag]

    *flag* is ``'i'`` for a case-insensitive value match, ``'s'`` for a
    case-sensitive one, and `None` when the selector sets neither.
    """

    _source_range: SourceRange | None = None

    @overload
    def __init__(
        self,
        selector: Tree,
        namespace: str | None,
        attrib: str,
        operator: Literal["exists"],
        value: None,
        flag: None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        selector: Tree,
        namespace: str | None,
        attrib: str,
        operator: str,
        value: Token,
        flag: str | None = None,
    ) -> None: ...

    def __init__(
        self,
        selector: Tree,
        namespace: str | None,
        attrib: str,
        operator: str,
        value: Token | None,
        flag: str | None = None,
    ) -> None:
        self.selector = selector
        self.namespace = namespace
        self.attrib = attrib
        self.operator = operator
        self.value = value
        self.flag = flag

    def __repr__(self) -> str:
        attrib = f"{self.namespace}|{self.attrib}" if self.namespace else self.attrib
        if self.operator == "exists":
            return f"{self.__class__.__name__}[{self.selector!r}[{attrib}]]"
        assert self.value is not None
        flag = f" {self.flag}" if self.flag else ""
        return f"{self.__class__.__name__}[{self.selector!r}[{attrib} {self.operator} {self.value.value!r}{flag}]]"

    def canonical(self) -> str:
        attrib = _serialize_ident(self.attrib)
        if self.namespace:
            namespace = (
                "*" if self.namespace == "*" else _serialize_ident(self.namespace)
            )
            attrib = f"{namespace}|{attrib}"

        if self.operator == "exists":
            op = attrib
        else:
            assert self.value is not None
            flag = f" {self.flag}" if self.flag else ""
            op = f"{attrib}{self.operator}{self.value.css()}{flag}"

        return f"{self.selector.canonical()}[{op}]"

    def specificity(self) -> tuple[int, int, int]:
        a, b, c = self.selector.specificity()
        b += 1
        return a, b, c


class Element:
    """
    Represents namespace|element

    `None` is for the universal selector '*'. `namespace` is ``"*"``
    for the explicit namespace wildcard, e.g. ``*|div``, and `None`
    both for an unprefixed element and for the explicit ``|div``
    no-namespace syntax. ``|*`` (any element with no namespace) has
    `namespace` ``""``.

    """

    _source_range: SourceRange | None = None

    def __init__(
        self, namespace: str | None = None, element: str | None = None
    ) -> None:
        self.namespace = namespace
        self.element = element

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}[{self.canonical()}]"

    def canonical(self) -> str:
        element = _serialize_ident(self.element) if self.element else "*"
        if self.namespace is not None:
            namespace = (
                "*" if self.namespace == "*" else _serialize_ident(self.namespace)
            )
            element = f"{namespace}|{element}"
        return element

    def specificity(self) -> tuple[int, int, int]:
        if self.element:
            return 0, 0, 1
        return 0, 0, 0


class Hash:
    """
    Represents selector#id
    """

    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, id: str) -> None:  # noqa: A002
        self.selector = selector
        self.id = id

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}[{self.selector!r}#{self.id}]"

    def canonical(self) -> str:
        return f"{self.selector.canonical()}#{_serialize_ident(self.id)}"

    def specificity(self) -> tuple[int, int, int]:
        a, b, c = self.selector.specificity()
        a += 1
        return a, b, c


class CombinedSelector:
    _source_range: SourceRange | None = None

    def __init__(self, selector: Tree, combinator: str, subselector: Tree) -> None:
        assert selector is not None
        self.selector = selector
        self.combinator = combinator
        self.subselector = subselector

    def __repr__(self) -> str:
        comb = "<followed>" if self.combinator == " " else self.combinator
        return (
            f"{self.__class__.__name__}[{self.selector!r} {comb} {self.subselector!r}]"
        )

    def canonical(self) -> str:
        subsel = self.subselector.canonical()
        if len(subsel) > 1 and subsel[0] == "*" and subsel[1] in "#.[:":
            subsel = subsel[1:]
        combinator = " " if self.combinator == " " else f" {self.combinator} "
        return f"{self.selector.canonical()}{combinator}{subsel}"

    def specificity(self) -> tuple[int, int, int]:
        a1, b1, c1 = self.selector.specificity()
        a2, b2, c2 = self.subselector.specificity()
        return a1 + a2, b1 + b2, c1 + c2


#### Parser

# foo
_el_re = re.compile(r"^[ \t\r\n\f]*([a-zA-Z]+)[ \t\r\n\f]*$")

# foo#bar or #bar
_id_re = re.compile(r"^[ \t\r\n\f]*([a-zA-Z]*)#([a-zA-Z0-9_-]+)[ \t\r\n\f]*$")

# foo.bar or .bar
_class_re = re.compile(
    r"^[ \t\r\n\f]*([a-zA-Z]*)\.([a-zA-Z][a-zA-Z0-9_-]*)[ \t\r\n\f]*$"
)


def parse(css: str) -> list[Selector]:
    """Parse a CSS *group of selectors*.

    If you don't care about pseudo-elements or selector specificity,
    you can skip this and use :meth:`~GenericTranslator.css_to_xpath`.

    :param css:
        A *group of selectors* as a string.
    :raises:
        :class:`SelectorSyntaxError` on invalid selectors.
    :returns:
        A list of parsed :class:`Selector` objects, one for each
        selector in the comma-separated group.

    """
    # Fast path for simple cases
    match = _el_re.match(css)
    if match:
        element = Element(element=match.group(1))
        attach_source(element, SourceRange(match.start(1), match.end(1)))
        return [Selector(element)]
    match = _id_re.match(css)
    if match is not None:
        element = Element(element=match.group(1) or None)
        hash_ = Hash(element, match.group(2))
        element_start, hash_end = match.span(1)[0], match.end(2)
        if match.group(1):
            attach_source(element, SourceRange(match.start(1), match.end(1)))
        else:
            attach_source(element, SourceRange(element_start, element_start))
        attach_source(hash_, SourceRange(element_start, hash_end))
        return [Selector(hash_)]
    match = _class_re.match(css)
    if match is not None:
        element = Element(element=match.group(1) or None)
        class_ = Class(element, match.group(2))
        element_start, class_end = match.span(1)[0], match.end(2)
        if match.group(1):
            attach_source(element, SourceRange(match.start(1), match.end(1)))
        else:
            attach_source(element, SourceRange(element_start, element_start))
        attach_source(class_, SourceRange(element_start, class_end))
        return [Selector(class_)]

    stream = TokenStream(tokenize(css))
    stream.source = css
    return list(parse_selector_group(stream))


def _string_end(source: str, pos: int) -> int:
    """Return the position just past the string token opening at *pos*."""
    quote = source[pos]
    match = _match_string_by_quote[quote](source, pos=pos + 1)
    assert match is not None
    end_pos = match.end()
    if end_pos < len(source) and source[end_pos] == quote:
        return end_pos + 1
    # An unclosed string runs to the end of the source.
    return len(source)


def _token_end(token: Token, source: str) -> int:
    """Return the source position just past *token*.

    Identifier, hash and string tokens are decoded while tokenizing, so
    their value can be shorter than their source representation (escapes,
    quotes); re-run the tokenizer's own patterns to measure the source
    span instead of using the decoded value.
    """
    if token.type == "EOF":
        return token.pos
    if token.type == "IDENT":
        match = _match_ident(source, pos=token.pos)
        return match.end() if match else token.pos
    if token.type == "HASH":
        match = _match_hash(source, pos=token.pos)
        return match.end() if match else token.pos
    if token.type == "STRING":
        return _string_end(source, token.pos)
    end = token.pos + len(token.value or "")
    return min(end, len(source))


def _error_failure(stream: TokenStream, error: SelectorSyntaxError) -> ParseFailure:
    """Build a :class:`ParseFailure` for *error*, locating it in the source."""
    source = cast("str", stream.source)
    token = getattr(error, "token", None)
    if token is None:
        token = stream.peek()
    if token.type == "EOF":
        range_ = SourceRange(token.pos, len(source))
    else:
        range_ = SourceRange(token.pos, _token_end(token, source))
    return ParseFailure(str(error), range_)


def parse_lenient(css: str) -> LenientParse:
    """Parse a *group of selectors* without raising on syntax errors.

    This is the lenient counterpart of :func:`parse`, meant for tools such
    as editor preflight checks. Every comma-separated branch is reported
    independently: valid branches carry their parsed :class:`Selector`
    objects, invalid ones carry a :class:`ParseFailure` with the offending
    source range and an error message. Comma characters are never split on
    inside strings, attribute selectors or function arguments.

    Source that cannot even be tokenized (currently, an unclosed string)
    is reported as :attr:`LenientParse.unparsed` ranges rather than blamed
    on any branch, so that callers know which parts have not been checked.
    """
    branches: list[ParsedBranch] = []
    unparsed: list[SourceRange] = []
    stream = TokenStream(tokenize_lenient(css, unparsed))
    stream.source = css
    stream.skip_whitespace()
    while True:
        start = stream.peek().pos
        selector: Selector | None = None
        error: ParseFailure | None = None
        try:
            selector = Selector(*parse_selector(stream))
        except SelectorSyntaxError as ex:
            error = _error_failure(stream, ex)
        end, comma = _recover_to_comma(stream)
        # Trim whitespace surrounding the branch text.
        text = css[start:end]
        stripped = text.strip()
        text_start = start + (len(text) - len(text.lstrip()))
        text_end = text_start + len(stripped)
        branches.append(
            ParsedBranch(
                text=stripped,
                range=SourceRange(text_start, text_end),
                comma=comma,
                selector=selector,
                error=error,
            )
        )
        if stream.peek().type == "EOF":
            break
        # Skip the comma the recovery stopped on.
        stream.next()
        stream.skip_whitespace()
    return LenientParse(tuple(branches), tuple(unparsed))


def _recover_to_comma(stream: TokenStream) -> tuple[int, SourceRange | None]:
    """Skip tokens up to the next top-level comma or EOF after an error.

    Parentheses and attribute brackets are balanced while scanning, so a
    comma inside a function argument or an attribute value is not treated
    as a branch separator. Returns the end position of the branch text and
    the range of the terminating comma, if any. The terminating comma, if
    any, is left peekable rather than consumed.
    """
    source = cast("str", stream.source)
    end = stream.peek().pos
    depth = 0
    comma: SourceRange | None = None
    while True:
        token = stream.peek()
        if token.type == "EOF":
            if depth:
                # Unclosed parenthesis/bracket: include everything left.
                end = len(source)
            break
        if token.is_delim("(", "["):
            depth += 1
        elif token.is_delim(")", "]"):
            if depth:
                depth -= 1
        elif token.is_delim(",") and depth == 0:
            comma = SourceRange(token.pos, token.pos + 1)
            end = token.pos
            break
        end = _token_end(token, source)
        stream.next()
    return end, comma


def split_selector_groups(css: str) -> tuple[tuple[SourceRange, ...], tuple[SourceRange, ...]]:
    """Split a *group of selectors* into its comma-separated branches.

    The split follows the tokenizer rather than the raw text: a comma
    inside an attribute value, a string or a functional pseudo-class
    argument is not a separator. The first item holds one range per
    branch, the second one the ranges of the separating commas.

    Source that cannot be tokenized (an unclosed string) is skipped: the
    surrounding branches are still returned, and the gap simply does not
    appear in any range. Use :func:`parse_lenient` to learn about such
    gaps explicitly.
    """
    unparsed: list[SourceRange] = []
    stream = TokenStream(tokenize_lenient(css, unparsed))
    stream.source = css
    branch_ranges: list[SourceRange] = []
    comma_ranges: list[SourceRange] = []
    stream.skip_whitespace()
    start = stream.peek().pos
    depth = 0
    while True:
        token = stream.peek()
        if token.type == "EOF":
            break
        if token.is_delim("(", "["):
            depth += 1
        elif token.is_delim(")", "]") and depth:
            depth -= 1
        elif token.is_delim(",") and depth == 0:
            comma_pos = token.pos
            text = css[start:comma_pos]
            stripped = text.rstrip()
            branch_ranges.append(SourceRange(start, start + len(stripped)))
            comma_ranges.append(SourceRange(comma_pos, comma_pos + 1))
            stream.next()
            stream.skip_whitespace()
            start = stream.peek().pos
            continue
        stream.next()
    text = css[start:]
    branch_ranges.append(SourceRange(start, start + len(text.rstrip())))
    return tuple(branch_ranges), tuple(comma_ranges)


#    except SelectorSyntaxError:
#        e = sys.exc_info()[1]
#        message = "%s at %s -> %r" % (
#            e, stream.used, stream.peek())
#        e.msg = message
#        e.args = tuple([message])
#        raise


def parse_selector_group(stream: TokenStream) -> Iterator[Selector]:
    stream.skip_whitespace()
    while 1:
        yield Selector(*parse_selector(stream))
        if stream.peek() == ("DELIM", ","):
            stream.next()
            stream.skip_whitespace()
        else:
            break


def parse_selector(
    stream: TokenStream,
) -> tuple[Tree, PseudoElement | None, SourceRange | None]:
    stream.skip_whitespace()
    selector_start = stream.peek().pos
    result, pseudo_element, pseudo_element_range = parse_simple_selector(stream)
    while 1:
        stream.skip_whitespace()
        peek = stream.peek()
        if peek in (("EOF", None), ("DELIM", ",")):
            break
        if pseudo_element:
            raise _syntax_error(
                f"Got pseudo-element ::{pseudo_element} not at the end of a selector",
                stream.peek(),
            )
        if peek.is_delim("+", ">", "~"):
            # A combinator
            combinator = cast("str", stream.next().value)
            stream.skip_whitespace()
        else:
            # By exclusion, the last parse_simple_selector() ended
            # at peek == ' '
            combinator = " "
        next_selector, pseudo_element, pseudo_element_range = parse_simple_selector(
            stream
        )
        result = CombinedSelector(result, combinator, next_selector)
    attach_source(result, SourceRange(selector_start, _last_used_end(stream)))
    return result, pseudo_element, pseudo_element_range


def _last_used_end(stream: TokenStream) -> int:
    """Source position just past the last consumed non-whitespace token."""
    source = cast("str", stream.source)
    for token in reversed(stream.used):
        if token.type != "S":
            return _token_end(token, source)
    return 0


def parse_simple_selector(
    stream: TokenStream,
    inside_negation: bool = False,
    inside_selector_list: bool = False,
    inside_relative_selector: bool = False,
) -> tuple[Tree, PseudoElement | None, SourceRange | None]:
    stream.skip_whitespace()
    selector_start = len(stream.used)
    selector_pos = stream.peek().pos
    # Whether the tokens preceding this compound selector (ignoring
    # whitespace) are the start of the input or a comma, for :scope.
    preceding = selector_start - 1
    while preceding >= 0 and stream.used[preceding].type == "S":
        preceding -= 1
    at_selector_start = preceding < 0 or stream.used[preceding].is_delim(",")
    peek = stream.peek()
    if peek.type == "IDENT" or peek == ("DELIM", "*"):
        if peek.type == "IDENT":
            namespace = stream.next().value
        else:
            stream.next()
            namespace = "*"
        if stream.peek() == ("DELIM", "|"):
            if peek.type == "IDENT" and namespace == "*":
                # An escaped "*" (e.g. "\*|f") is a prefix named "*",
                # not the namespace wildcard, and no XPath prefix can be "*".
                raise _syntax_error(f"Expected namespace prefix, got {peek}", peek)
            stream.next()
            element = stream.next_ident_or_star()
        else:
            # An escaped "*" (e.g. "\*") is a type selector for elements
            # named "*", not the universal selector.
            element = namespace if peek.type == "IDENT" else None
            namespace = None
    else:
        element = namespace = None
    result: Tree = Element(namespace, element)
    element_end = _last_used_end(stream)
    # An implicit universal selector (".foo", ":hover", ...) consumes no
    # token: give its node a zero-width range at the compound start.
    element_end = max(element_end, selector_pos)
    attach_source(result, SourceRange(selector_pos, element_end))
    pseudo_element: PseudoElement | None = None
    pseudo_element_range: SourceRange | None = None
    while 1:
        peek = stream.peek()
        if peek.type in ("S", "EOF") or peek.is_delim(",", "+", ">", "~", ")"):
            break
        if pseudo_element:
            raise _syntax_error(
                f"Got pseudo-element ::{pseudo_element} not at the end of a selector",
                peek,
            )
        if peek.type == "HASH":
            start = peek.pos
            result = Hash(result, cast("str", stream.next().value))
            attach_source(result, SourceRange(start, _last_used_end(stream)))
        elif peek == ("DELIM", "."):
            start = peek.pos
            stream.next()
            result = Class(result, stream.next_ident())
            attach_source(result, SourceRange(start, _last_used_end(stream)))
        elif peek == ("DELIM", "|"):
            # The explicit "no namespace" syntax, e.g. |div: only valid at
            # the very start of a simple selector.
            if len(stream.used) != selector_start:
                raise _syntax_error(f"Expected selector, got {peek}", peek)
            stream.next()
            element = stream.next_ident_or_star()
            # Unlike *, |* only matches elements with no namespace.
            result = Element("" if element is None else None, element)
            attach_source(result, SourceRange(selector_pos, _last_used_end(stream)))
        elif peek == ("DELIM", "["):
            start = peek.pos
            stream.next()
            result = parse_attrib(result, stream)
            attach_source(result, SourceRange(start, _last_used_end(stream)))
        elif peek == ("DELIM", ":"):
            start = peek.pos
            stream.next()
            if stream.peek() == ("DELIM", ":"):
                stream.next()
                pseudo_element = stream.next_ident()
                if stream.peek() == ("DELIM", "("):
                    stream.next()
                    functional = FunctionalPseudoElement(
                        pseudo_element, parse_arguments(stream)
                    )
                    attach_source(
                        functional, SourceRange(start, _last_used_end(stream))
                    )
                    pseudo_element = functional
                pseudo_element_range = SourceRange(start, _last_used_end(stream))
                continue
            ident = stream.next_ident()
            if ident.lower() in ("first-line", "first-letter", "before", "after"):
                # Special case: CSS 2.1 pseudo-elements can have a single ':'
                # Any new pseudo-element must have two.
                pseudo_element = str(ident)
                pseudo_element_range = SourceRange(start, _last_used_end(stream))
                continue
            if stream.peek() != ("DELIM", "("):
                result = Pseudo(result, ident)
                attach_source(result, SourceRange(start, _last_used_end(stream)))
                # :scope is only supported at the start of a selector,
                # i.e. never in :is()/:where()/:matches() or :has()
                # arguments (where a preceding comma separates arguments,
                # not selectors), and otherwise only when the tokens
                # preceding its compound selector are the start of the
                # input or a comma.
                if result.ident == "scope" and (
                    inside_selector_list
                    or inside_relative_selector
                    or not at_selector_start
                ):
                    raise _syntax_error(
                        'Got pseudo-class ":scope" not at the start of a selector',
                        stream.used[-1],
                    )
                continue
            stream.next()
            stream.skip_whitespace()
            if ident.lower() == "not":
                if inside_selector_list:
                    raise _syntax_error(
                        ":not() is not supported inside :is(), :where() and :matches()",
                        stream.used[-1],
                    )
                if inside_negation:
                    raise _syntax_error("Got nested :not()", stream.used[-1])
                argument, argument_pseudo_element, _ = parse_simple_selector(
                    stream, inside_negation=True
                )
                while 1:
                    # Whitespace before the closing parenthesis is not a
                    # descendant combinator.
                    stream.skip_whitespace()
                    peek = stream.peek()
                    if argument_pseudo_element:
                        raise _syntax_error(
                            f"Got pseudo-element ::{argument_pseudo_element} inside :not() at {peek.pos}",
                            peek,
                        )
                    if peek == ("DELIM", ")"):
                        stream.next()
                        break
                    if peek.is_delim("+", ">", "~"):
                        argument_combinator = cast("str", stream.next().value)
                        stream.skip_whitespace()
                    elif peek.type == "EOF" or peek.is_delim(","):
                        # A selector list is not supported in :not().
                        raise _syntax_error(f"Expected ')', got {peek}", peek)
                    else:
                        argument_combinator = " "
                    (
                        next_selector,
                        argument_pseudo_element,
                        _,
                    ) = parse_simple_selector(stream, inside_negation=True)
                    argument = CombinedSelector(
                        argument, argument_combinator, next_selector
                    )
                result = Negation(result, argument)
                attach_source(result, SourceRange(start, _last_used_end(stream)))
            elif ident.lower() == "has":
                result = Relation(result, parse_relative_selector(stream))
                attach_source(result, SourceRange(start, _last_used_end(stream)))
            elif ident.lower() in ("matches", "is"):
                selectors = parse_simple_selector_arguments(stream)
                result = Matching(result, selectors)
                attach_source(result, SourceRange(start, _last_used_end(stream)))
            elif ident.lower() == "where":
                selectors = parse_simple_selector_arguments(stream)
                result = SpecificityAdjustment(result, selectors)
                attach_source(result, SourceRange(start, _last_used_end(stream)))
            else:
                result = Function(result, ident, parse_arguments(stream))
                attach_source(result, SourceRange(start, _last_used_end(stream)))
        else:
            raise _syntax_error(f"Expected selector, got {peek}", peek)
    if len(stream.used) == selector_start:
        raise _syntax_error(
            f"Expected selector, got {stream.peek()}", stream.peek()
        )
    return result, pseudo_element, pseudo_element_range


def parse_arguments(stream: TokenStream) -> list[Token]:  # noqa: RET503
    arguments: list[Token] = []
    while 1:
        stream.skip_whitespace()
        next_ = stream.next()
        if next_.type in ("IDENT", "STRING", "NUMBER") or next_ in [
            ("DELIM", "+"),
            ("DELIM", "-"),
        ]:
            arguments.append(next_)
        elif next_ == ("DELIM", ")"):
            return arguments
        else:
            raise _syntax_error(f"Expected an argument, got {next_}", next_)


def parse_relative_selector(stream: TokenStream) -> list[tuple[Token, Selector]]:
    """Parse the relative selector list of a :has() argument, i.e. a
    comma-separated list of complex selectors, each optionally preceded by a
    combinator."""
    arguments = []
    while 1:
        stream.skip_whitespace()
        peek = stream.peek()
        if peek.is_delim("+", ">", "~"):
            combinator = stream.next()
            stream.skip_whitespace()
        else:
            combinator = Token("DELIM", " ", pos=peek.pos)
        result, pseudo_element, _ = parse_simple_selector(
            stream, inside_relative_selector=True
        )
        while 1:
            if pseudo_element:
                raise _syntax_error(
                    f"Got pseudo-element ::{pseudo_element} inside function",
                    stream.peek(),
                )
            # Whitespace before a comma or the closing parenthesis is not a
            # descendant combinator.
            stream.skip_whitespace()
            peek = stream.peek()
            if peek.is_delim(",", ")"):
                break
            if peek.type == "EOF":
                raise _syntax_error(f"Expected ')', got {peek}", peek)
            if peek.is_delim("+", ">", "~"):
                argument_combinator = cast("str", stream.next().value)
                stream.skip_whitespace()
            else:
                argument_combinator = " "
            next_selector, pseudo_element, _ = parse_simple_selector(stream)
            combined_start = result._source_range.start if result._source_range else None
            result = CombinedSelector(result, argument_combinator, next_selector)
            if combined_start is not None:
                attach_source(
                    result, SourceRange(combined_start, _last_used_end(stream))
                )
        arguments.append((combinator, Selector(result)))
        if stream.next().is_delim(")"):
            break
    return arguments


def parse_simple_selector_arguments(stream: TokenStream) -> list[Tree]:
    arguments = []
    while 1:
        result, pseudo_element, _ = parse_simple_selector(
            stream, inside_selector_list=True
        )
        if pseudo_element:
            raise _syntax_error(
                f"Got pseudo-element ::{pseudo_element} inside function",
                stream.peek(),
            )
        stream.skip_whitespace()
        next_ = stream.next()
        if next_ == ("DELIM", ","):
            stream.skip_whitespace()
            arguments.append(result)
        elif next_ == ("DELIM", ")"):
            arguments.append(result)
            break
        else:
            raise _syntax_error(f"Expected an argument, got {next_}", next_)
    return arguments


def parse_attrib(selector: Tree, stream: TokenStream) -> Attrib:
    stream.skip_whitespace()
    attrib: str | None
    namespace: str | None
    op: str | None
    if stream.peek() == ("DELIM", "|"):
        # The explicit "no namespace" syntax, e.g. [|attr].
        stream.next()
        namespace = None
        attrib = stream.next_ident()
        op = None
    else:
        attrib = stream.next_ident_or_star()
        if attrib is None and stream.peek() != ("DELIM", "|"):
            raise _syntax_error(f"Expected '|', got {stream.peek()}", stream.peek())
        if stream.peek() == ("DELIM", "|"):
            stream.next()
            if attrib is not None and stream.peek() == ("DELIM", "="):
                namespace = None
                stream.next()
                op = "|="
            else:
                if attrib == "*":
                    # An escaped "*" (e.g. "[\*|attr]"), not the wildcard.
                    raise _syntax_error(
                        f"Expected namespace prefix, got {stream.used[-2]}",
                        stream.used[-2],
                    )
                namespace = "*" if attrib is None else attrib
                attrib = stream.next_ident()
                op = None
        else:
            namespace = op = None
    if op is None:
        stream.skip_whitespace()
        next_ = stream.next()
        if next_ == ("DELIM", "]"):
            return Attrib(selector, namespace, cast("str", attrib), "exists", None)
        if next_ == ("DELIM", "="):
            op = "="
        elif next_.is_delim("^", "$", "*", "~", "|", "!") and (
            stream.peek() == ("DELIM", "=")
        ):
            op = cast("str", next_.value) + "="
            stream.next()
        else:
            raise _syntax_error(f"Operator expected, got {next_}", next_)
    stream.skip_whitespace()
    value = stream.next()
    if value.type not in ("IDENT", "STRING"):
        raise _syntax_error(f"Expected string or ident, got {value}", value)
    stream.skip_whitespace()
    next_ = stream.next()
    flag = None
    if next_.type == "IDENT":
        flag = ascii_lower(cast("str", next_.value))
        if flag not in ("i", "s"):
            raise _syntax_error(f"Expected ']', got {next_}", next_)
        stream.skip_whitespace()
        next_ = stream.next()
    if next_ != ("DELIM", "]"):
        raise _syntax_error(f"Expected ']', got {next_}", next_)
    return Attrib(selector, namespace, cast("str", attrib), op, value, flag)


# An An+B integer: ASCII digits only, unlike int(), which also accepts other
# Unicode decimal digits, underscores and surrounding whitespace.
_match_series_integer = re.compile("[+-]?[0-9]+").fullmatch


def _parse_series_integer(s: str) -> int:
    if not _match_series_integer(s):
        raise ValueError(f"Invalid integer in series: {s!r}")
    return int(s)


# The B of An+B with whitespace: only around a sign that is a token of its
# own, as in "2n + 1" or "2n- 1".
_match_series_spaced_b = re.compile(" ?[+-] ?[0-9]+").fullmatch


def parse_series(tokens: Iterable[Token]) -> tuple[int, int]:
    """Parses the arguments for :nth-child() and friends."""
    parts = []
    end = None
    for token in tokens:
        if token.type == "STRING":
            raise ValueError("String tokens not allowed in series.")
        value = cast("str", token.value)
        if end is not None and token.pos != end:
            # Whitespace or a comment between two tokens, which must not
            # join them: "1 2" is not "12". An escaped identifier is longer
            # in the source than its value, but whitespace is allowed after
            # the identifier that ends An anyway.
            parts.append(" ")
        parts.append(value)
        end = token.pos + len(value)
    # The An+B microsyntax is ASCII-case-insensitive: 2N+1, EVEN, Odd...
    s = ascii_lower("".join(parts).strip())
    if s == "odd":
        return 2, 1
    if s == "even":
        return 2, 0
    if s == "n":
        return 1, 0
    if "n" not in s:
        # Just b
        return 0, _parse_series_integer(s)
    a, b = s.split("n", 1)
    a_as_int: int
    if not a:
        a_as_int = 1
    elif a in {"-", "+"}:
        a_as_int = int(a + "1")
    else:
        a_as_int = _parse_series_integer(a)
    if " " in b:
        if not _match_series_spaced_b(b):
            raise ValueError(f"Invalid integer in series: {b!r}")
        b = b.replace(" ", "")
    b_as_int = _parse_series_integer(b) if b else 0
    return a_as_int, b_as_int


#### Token objects


class Token(tuple[str, str | None]):  # noqa: SLOT001
    @overload
    def __new__(
        cls,
        type_: Literal["IDENT", "HASH", "STRING", "S", "DELIM", "NUMBER"],
        value: str,
        pos: int,
    ) -> Self: ...

    @overload
    def __new__(cls, type_: Literal["EOF"], value: None, pos: int) -> Self: ...

    def __new__(cls, type_: str, value: str | None, pos: int) -> Self:
        obj = tuple.__new__(cls, (type_, value))
        obj.pos = pos
        return obj

    def __repr__(self) -> str:
        return f"<{self.type} '{self.value}' at {self.pos}>"

    def is_delim(self, *values: str) -> bool:
        return self.type == "DELIM" and self.value in values

    pos: int

    @property
    def type(self) -> str:
        return self[0]

    @property
    def value(self) -> str | None:
        return self[1]

    def css(self) -> str:
        if self.type == "STRING":
            # Escape as CSS (repr() would use Python escapes, which mean
            # something else in CSS, e.g. '\n' is just the letter 'n').
            escaped = cast("str", self.value).replace("\\", "\\\\").replace("'", "\\'")
            escaped = _sub_string_control_char(_replace_string_control_char, escaped)
            return f"'{escaped}'"
        if self.type == "IDENT":
            return _serialize_ident(cast("str", self.value))
        return cast("str", self.value)


class EOFToken(Token):
    def __new__(cls, pos: int) -> Self:
        return Token.__new__(cls, "EOF", None, pos)

    def __repr__(self) -> str:
        return f"<{self.type} at {self.pos}>"


#### Tokenizer


class TokenMacros:
    unicode_escape = r"\\([0-9a-f]{1,6})(?:\r\n|[ \n\r\t\f])?"
    escape = unicode_escape + r"|\\[^\n\r\f0-9a-f]"
    string_escape = r"\\(?:\n|\r\n|\r|\f)|" + escape
    nonascii = r"[^\0-\177]"
    nmchar = f"[_a-z0-9-]|{escape}|{nonascii}"
    nmstart = f"[_a-z]|{escape}|{nonascii}"


class MatchFunc(Protocol):
    def __call__(
        self, string: str, pos: int = ..., endpos: int = ...
    ) -> re.Match[str] | None: ...


def _compile(pattern: str) -> MatchFunc:
    return re.compile(pattern % vars(TokenMacros), re.IGNORECASE).match


_match_whitespace = _compile(r"[ \t\r\n\f]+")
_match_number = _compile(r"[+-]?(?:[0-9]*\.[0-9]+|[0-9]+)")
_match_hash = _compile("#(?:%(nmchar)s)+")
_match_ident = _compile("-?(?:%(nmstart)s)(?:%(nmchar)s)*")
_match_string_by_quote = {
    "'": _compile(r"([^\n\r\f\\']|%(string_escape)s)*"),
    '"': _compile(r'([^\n\r\f\\"]|%(string_escape)s)*'),
}

# Each escape is decoded in a single left-to-right pass, so that the output
# of one escape is never read as (part of) another.
_sub_escape = re.compile(TokenMacros.unicode_escape + r"|\\(.)", re.IGNORECASE).sub
_sub_string_escape = re.compile(
    TokenMacros.unicode_escape + r"|\\(?:\n|\r\n|\r|\f)|\\(.)", re.IGNORECASE
).sub
_sub_string_control_char = re.compile(r"[\x00-\x1f\x7f]").sub

# CSS Syntax Level 3, §3.3: fold a raw U+0000 or surrogate code point to U+FFFD.
_sub_invalid_input_char = re.compile("[\x00\ud800-\udfff]").sub


def _replace_unicode(match: re.Match[str]) -> str:
    codepoint = int(match.group(1), 16)
    if codepoint == 0 or codepoint > sys.maxunicode or 0xD800 <= codepoint <= 0xDFFF:
        codepoint = 0xFFFD
    return chr(codepoint)


def _replace_escape(match: re.Match[str]) -> str:
    if match.group(1) is not None:
        return _replace_unicode(match)
    # A simple escape, or an escaped newline (only in strings).
    return match.group(2) or ""


def _replace_string_control_char(match: re.Match[str]) -> str:
    # The trailing space ends the escape sequence, in case the next
    # character is a hexadecimal digit.
    return f"\\{ord(match.group()):x} "


def unescape_ident(value: str) -> str:
    return _sub_escape(_replace_escape, value)


def _serialize_ident(value: str) -> str:
    """Serialize a string as a CSS identifier, escaping special characters.

    Implements the CSSOM "serialize an identifier" algorithm:
    https://drafts.csswg.org/cssom/#serialize-an-identifier
    """
    result = []
    for i, char in enumerate(value):
        code = ord(char)
        serialized = char
        if code == 0:
            serialized = "\N{REPLACEMENT CHARACTER}"
        elif code <= 0x1F or code == 0x7F:
            serialized = f"\\{code:x} "
        elif "0" <= char <= "9":
            if i == 0 or (i == 1 and value[0] == "-"):
                # An identifier cannot start with a digit
                # (or a '-' followed by a digit).
                serialized = f"\\{code:x} "
        elif char == "-":
            if len(value) == 1 or (i == 0 and value[1] == "-"):
                # CSSOM leaves a leading "--" unescaped (such identifiers
                # are valid since CSS Syntax 3), but the tokenizer only
                # implements the CSS 2.1 identifier grammar and would not
                # be able to parse the result, so escape the first "-".
                serialized = "\\-"
        elif not (
            code >= 0x80 or char == "_" or "a" <= char <= "z" or "A" <= char <= "Z"
        ):
            serialized = f"\\{char}"
        result.append(serialized)
    return "".join(result)


def tokenize(s: str) -> Iterator[Token]:
    yield from _tokenize(s, lenient=False, unparsed=None)


def tokenize_lenient(
    s: str, unparsed: list[SourceRange] | None = None
) -> Iterator[Token]:
    """Tokenize *s* without raising on an invalid string.

    The malformed string is recorded as an :class:`SourceRange` in
    *unparsed* (when provided) and skipped, so that the rest of the source
    can still be tokenized and checked. Everything else behaves like
    :func:`tokenize`.
    """
    yield from _tokenize(s, lenient=True, unparsed=unparsed)


def _tokenize(
    s: str, lenient: bool, unparsed: list[SourceRange] | None
) -> Iterator[Token]:
    # Preprocess the input stream (§3.3); the substitution is length-preserving.
    s = _sub_invalid_input_char("\N{REPLACEMENT CHARACTER}", s)
    pos = 0
    len_s = len(s)
    while pos < len_s:
        match = _match_whitespace(s, pos=pos)
        if match:
            yield Token("S", " ", pos)
            pos = match.end()
            continue

        match = _match_ident(s, pos=pos)
        if match:
            value = unescape_ident(match.group())
            yield Token("IDENT", value, pos)
            pos = match.end()
            continue

        match = _match_hash(s, pos=pos)
        if match:
            value = unescape_ident(match.group()[1:])
            yield Token("HASH", value, pos)
            pos = match.end()
            continue

        quote = s[pos]
        if quote in _match_string_by_quote:
            match = _match_string_by_quote[quote](s, pos=pos + 1)
            assert match, "Should have found at least an empty match"
            end_pos = match.end()
            if end_pos == len_s:
                if lenient:
                    if unparsed is not None:
                        unparsed.append(SourceRange(pos, len_s))
                    pos = len_s
                    break
                raise SelectorSyntaxError(f"Unclosed string at {pos}")
            if s[end_pos] != quote:
                if lenient:
                    # A raw control character (e.g. a newline) ended the
                    # string: leave the offending character to be tokenized
                    # again on the next iteration.
                    if unparsed is not None:
                        unparsed.append(SourceRange(pos, end_pos))
                    pos = end_pos
                    continue
                raise SelectorSyntaxError(f"Invalid string at {pos}")
            value = _sub_string_escape(_replace_escape, match.group())
            yield Token("STRING", value, pos)
            pos = end_pos + 1
            continue

        match = _match_number(s, pos=pos)
        if match:
            value = match.group()
            yield Token("NUMBER", value, pos)
            pos = match.end()
            continue

        pos2 = pos + 2
        if s[pos:pos2] == "/*":
            pos = s.find("*/", pos2)
            if pos == -1:
                pos = len_s
            else:
                pos += 2
            continue

        yield Token("DELIM", s[pos], pos)
        pos += 1

    assert pos == len_s
    yield EOFToken(pos)


class TokenStream:
    def __init__(self, tokens: Iterable[Token], source: str | None = None) -> None:
        self.used: list[Token] = []
        self.tokens = iter(tokens)
        self.source = source
        self.peeked: Token | None = None
        self._peeking = False
        self.next_token = self.tokens.__next__

    def next(self) -> Token:
        if self._peeking:
            self._peeking = False
            assert self.peeked is not None
            self.used.append(self.peeked)
            return self.peeked
        next_ = self._pull()
        self.used.append(next_)
        return next_

    def peek(self) -> Token:
        if not self._peeking:
            self.peeked = self._pull()
            self._peeking = True
        assert self.peeked is not None
        return self.peeked

    def _pull(self) -> Token:
        # The tokenizer ends with a single EOF token: once it has been
        # consumed and something peeks again (as lenient recovery does),
        # keep returning an EOF at the end of the source instead of letting
        # the exhausted iterator raise StopIteration.
        try:
            return self.next_token()
        except StopIteration:
            end = len(self.source) if self.source else 0
            return EOFToken(end)

    def next_ident(self) -> str:
        next_ = self.next()
        if next_.type != "IDENT":
            raise _syntax_error(f"Expected ident, got {next_}", next_)
        return cast("str", next_.value)

    def next_ident_or_star(self) -> str | None:
        next_ = self.next()
        if next_.type == "IDENT":
            return next_.value
        if next_ == ("DELIM", "*"):
            return None
        raise _syntax_error(f"Expected ident or '*', got {next_}", next_)

    def skip_whitespace(self) -> None:
        # A comment between two whitespace runs yields two consecutive
        # whitespace tokens, so a single check is not enough.
        while self.peek().type == "S":
            self.next()
