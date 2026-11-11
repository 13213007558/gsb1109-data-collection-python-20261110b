"""Storage binding ``<template>`` elements to their contents fragments.

A template's children are parsed into a separate DocumentFragment instead of
being attached as ordinary children of the template element. A plain traversal
of the rendered tree therefore never descends into unexpanded template
contents, while the fragment remains reachable explicitly and during
serialization.

The relationship is keyed on the *raw*, tree-specific element objects (the
ones returned by ``getDocument``/``getFragment``) so it survives the tree
builders discarding their internal wrapper nodes.

``xml.etree`` and ``xml.dom`` elements support weak references and use a
:class:`weakref.WeakKeyDictionary` that cleans itself up. Raw lxml elements do
not, so the lxml builder creates template elements from a small
``ElementBase`` subclass carrying a ``__dict__`` and the fragment is stored on
the element itself; this also keeps the binding alive independently of any
single parser run.
"""
from __future__ import absolute_import, division, unicode_literals

import weakref

# Attribute used to hold the contents fragment on element types (lxml) that
# cannot be weakly referenced.
CONTENT_ATTRIBUTE = "templateContent"

# Raw template element -> raw DocumentFragment holding its template contents,
# for element types that support weak references.
weakContents = weakref.WeakKeyDictionary()


def register(template, content):
    """Bind a template element to its contents DocumentFragment."""
    try:
        weakContents[template] = content
    except TypeError:
        # lxml elements do not support weak references; the lxml builder gives
        # template elements a __dict__ so the fragment can live on the element.
        setattr(template, CONTENT_ATTRIBUTE, content)


def get(template):
    """Return the contents fragment bound to ``template``, or ``None``."""
    try:
        content = weakContents.get(template)
    except TypeError:
        # Raw lxml elements do not support weak references.
        content = None
    if content is None:
        content = getattr(template, CONTENT_ATTRIBUTE, None)
    return content
