"""
Soup Sieve.

A CSS selector filter for BeautifulSoup4.

MIT License

Copyright (c) 2018 Isaac Muse

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations
from .__meta__ import __version__, __version_info__  # noqa: F401
from . import css_parser as cp
from . import css_match as cm
from . import css_types as ct
from .util import DEBUG, NOCACHE, NOSTRICT, SelectorSyntaxError  # noqa: F401
import bs4
from typing import Any, Hashable, Mapping, Iterator, Iterable, cast

__all__ = (
    'DEBUG', 'NOCACHE', 'NOSTRICT', 'SelectorSyntaxError', 'CustomSelectors', 'SoupSieve',
    'closest', 'compile', 'filter', 'iselect', 'iselect_quotas', 'match', 'select', 'select_one',
    'select_quotas'
)

SoupSieve = cm.SoupSieve
CustomSelectors = ct.CustomSelectors


def compile(  # noqa: A001
    pattern: str,
    namespaces: dict[str, str] | None = None,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> cm.SoupSieve:
    """Compile CSS pattern."""

    if isinstance(pattern, SoupSieve):
        if flags:
            raise ValueError("Cannot process 'flags' argument on a compiled selector list")
        elif namespaces is not None:
            raise ValueError("Cannot process 'namespaces' argument on a compiled selector list")
        elif custom is not None:
            raise ValueError("Cannot process 'custom' argument on a compiled selector list")
        return pattern

    return cp._cached_css_compile(
        pattern,
        ct.Namespaces(namespaces) if namespaces is not None else namespaces,
        ct.CustomSelectors(
            cast('dict[str, str | ct.SelectorList]', custom), max_selectors=max_selectors
        ) if custom is not None and not isinstance(custom, ct.CustomSelectors) else custom,
        tuple(i.lower() for i in ignore) if ignore is not None else ignore,
        flags,
        max_selectors
    )


def purge() -> None:
    """Purge cached patterns."""

    cp._purge_cache()


def closest(
    select: str,
    tag: bs4.Tag,
    namespaces: dict[str, str] | None = None,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> bs4.Tag | None:
    """Match closest ancestor."""

    return compile(
        select,
        namespaces,
        flags,
        custom=custom,
        ignore=ignore,
        max_selectors=max_selectors,
        **kwargs
    ).closest(tag)


def match(
    select: str,
    tag: bs4.Tag,
    namespaces: dict[str, str] | None = None,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> bool:
    """Match node."""

    return compile(
        select,
        namespaces,
        flags,
        custom=custom,
        ignore=ignore,
        max_selectors=max_selectors,
        **kwargs
    ).match(tag)


def filter(  # noqa: A001
    select: str,
    iterable: Iterable[bs4.Tag],
    namespaces: dict[str, str] | None = None,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> list[bs4.Tag]:
    """Filter list of nodes."""

    return compile(
        select,
        namespaces,
        flags,
        custom=custom,
        ignore=ignore,
        max_selectors=max_selectors,
        **kwargs
    ).filter(iterable)


def select_one(
    select: str,
    tag: bs4.Tag,
    namespaces: dict[str, str] | None = None,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> bs4.Tag | None:
    """Select a single tag."""

    return compile(
        select,
        namespaces,
        flags,
        custom=custom,
        ignore=ignore,
        max_selectors=max_selectors,
        **kwargs
    ).select_one(tag)


def select(
    select: str,
    tag: bs4.Tag,
    namespaces: dict[str, str] | None = None,
    limit: int = 0,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> list[bs4.Tag]:
    """Select the specified tags."""

    return compile(
        select,
        namespaces,
        flags,
        custom=custom,
        ignore=ignore,
        max_selectors=max_selectors,
        **kwargs
    ).select(tag, limit)


def iselect(
    select: str,
    tag: bs4.Tag,
    namespaces: dict[str, str] | None = None,
    limit: int = 0,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> Iterator[bs4.Tag]:
    """Iterate the specified tags."""

    yield from compile(
        select,
        namespaces,
        flags,
        custom=custom,
        ignore=ignore,
        max_selectors=max_selectors,
        **kwargs
    ).iselect(tag, limit)


def iselect_quotas(
    branches: Mapping[Hashable, tuple[str | SoupSieve, int]],
    tag: bs4.Tag,
    namespaces: dict[str, str] | None = None,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> Iterator[tuple[bs4.Tag, tuple[Hashable, ...]]]:
    """
    Iterate tags matching quota-limited selector branches in a single pass.

    `branches` maps a label to a `(selector, limit)` pair. Each branch is
    evaluated independently against every tag in document order and claims
    at most `limit` tags (a limit less than 1 means no quota). A tag matched
    by multiple branches is yielded only once, along with the labels of all
    branches that claimed it. A branch whose quota is exhausted claims no
    further tags, but never prevents other branches from claiming later
    tags. Iteration stops once every branch has reached its quota.
    """

    active: dict[Hashable, list[Any]] = {}
    for label, (pattern, limit) in branches.items():
        sieve = compile(
            pattern,
            namespaces,
            flags,
            custom=custom,
            ignore=ignore,
            max_selectors=max_selectors,
            **kwargs
        )
        matcher = cm.CSSMatch(sieve.selectors, tag, sieve.namespaces, sieve.flags)
        active[label] = [matcher, limit if limit > 0 else None]

    if not active:
        return

    walker = cast(cm.CSSMatch, next(iter(active.values()))[0])
    for child in walker.get_tag_descendants(tag, template_contents=True):
        hits = []
        for label, (matcher, remaining) in active.items():
            if matcher.match(child):
                hits.append(label)
                if remaining is not None:
                    active[label][1] = remaining - 1
        if hits:
            yield child, tuple(hits)
            for label in hits:
                if active[label][1] is not None and active[label][1] < 1:
                    del active[label]
            if not active:
                break


def select_quotas(
    branches: Mapping[Hashable, tuple[str | SoupSieve, int]],
    tag: bs4.Tag,
    namespaces: dict[str, str] | None = None,
    flags: int = 0,
    *,
    custom: Mapping[str, str | ct.SelectorList] | None = None,
    ignore: Iterable[str] | None = None,
    max_selectors: int = cp.SELECTOR_LIMIT,
    **kwargs: Any
) -> list[tuple[bs4.Tag, tuple[Hashable, ...]]]:
    """Select tags matching quota-limited selector branches in a single pass."""

    return list(iselect_quotas(
        branches,
        tag,
        namespaces,
        flags,
        custom=custom,
        ignore=ignore,
        max_selectors=max_selectors,
        **kwargs
    ))


def escape(ident: str) -> str:
    """Escape identifier."""

    return cp.escape(ident)
