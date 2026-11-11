from __future__ import absolute_import, division, unicode_literals
from six import text_type

import re

from codecs import register_error, xmlcharrefreplace_errors

from .constants import voidElements, booleanAttributes, spaceCharacters
from .constants import rcdataElements, entities, xmlEntities
from .constants import namespaces, unadjustForeignAttributes
from . import treewalkers, _utils
from xml.sax.saxutils import escape

_quoteAttributeSpecChars = "".join(spaceCharacters) + "\"'=<>`"
_quoteAttributeSpec = re.compile("[" + _quoteAttributeSpecChars + "]")
_quoteAttributeLegacy = re.compile("[" + _quoteAttributeSpecChars +
                                   "\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n"
                                   "\x0b\x0c\r\x0e\x0f\x10\x11\x12\x13\x14\x15"
                                   "\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
                                   "\x20\x2f\x60\xa0\u1680\u180e\u180f\u2000"
                                   "\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
                                   "\u2008\u2009\u200a\u2028\u2029\u202f\u205f"
                                   "\u3000]")


_encode_entity_map = {}
_is_ucs4 = len("\U0010FFFF") == 1
for k, v in list(entities.items()):
    # skip multi-character entities
    if ((_is_ucs4 and len(v) > 1) or
            (not _is_ucs4 and len(v) > 2)):
        continue
    if v != "&":
        if len(v) == 2:
            v = _utils.surrogatePairToCodepoint(v)
        else:
            v = ord(v)
        if v not in _encode_entity_map or k.islower():
            # prefer &lt; over &LT; and similarly for &amp;, &gt;, etc.
            _encode_entity_map[v] = k


def htmlentityreplace_errors(exc):
    if isinstance(exc, (UnicodeEncodeError, UnicodeTranslateError)):
        res = []
        codepoints = []
        skip = False
        for i, c in enumerate(exc.object[exc.start:exc.end]):
            if skip:
                skip = False
                continue
            index = i + exc.start
            if _utils.isSurrogatePair(exc.object[index:min([exc.end, index + 2])]):
                codepoint = _utils.surrogatePairToCodepoint(exc.object[index:index + 2])
                skip = True
            else:
                codepoint = ord(c)
            codepoints.append(codepoint)
        for cp in codepoints:
            e = _encode_entity_map.get(cp)
            if e:
                res.append("&")
                res.append(e)
                if not e.endswith(";"):
                    res.append(";")
            else:
                res.append("&#x%s;" % (hex(cp)[2:]))
        return ("".join(res), exc.end)
    else:
        return xmlcharrefreplace_errors(exc)


register_error("htmlentityreplace", htmlentityreplace_errors)


def serialize(input, tree="etree", encoding=None, **serializer_opts):
    """Serializes the input token stream using the specified treewalker

    :arg input: the token stream to serialize

    :arg tree: the treewalker to use

    :arg encoding: the encoding to use

    :arg serializer_opts: any options to pass to the
        :py:class:`html5lib.serializer.HTMLSerializer` that gets created

    :returns: the tree serialized as a string

    Example:

    >>> from html5lib.html5parser import parse
    >>> from html5lib.serializer import serialize
    >>> token_stream = parse('<html><body><p>Hi!</p></body></html>')
    >>> serialize(token_stream, omit_optional_tags=False)
    '<html><head></head><body><p>Hi!</p></body></html>'

    """
    # XXX: Should we cache this?
    walker = treewalkers.getTreeWalker(tree)
    s = HTMLSerializer(**serializer_opts)
    return s.render(walker(input), encoding)


def serializeFragment(input, tree="etree", container="div", encoding=None,
                      **serializer_opts):
    """Serializes the input tree as an HTML fragment for a given container

    The output is intended to be parsed back with
    :py:func:`html5lib.html5parser.parseFragment` using the same
    ``container``: element names, attribute namespaces and attribute
    order are preserved by the round-trip. This makes it suitable for
    archiving a subtree (say, the product region of a page, a set of
    table rows, or an SVG subtree) and later splicing it back into the
    container it was taken from.

    If the tree cannot be faithfully represented as an HTML fragment in
    the given container context, a :py:class:`SerializeError` is raised
    instead of silently emitting output that would parse back into a
    different tree (or degenerate into a full document). This happens,
    for example, when a ``<tr>`` element is serialized with a ``div``
    container (the parser would drop the row), when the tree contains a
    doctype, or when an attribute namespace has no HTML representation.

    :arg input: the tree to serialize

    :arg tree: the treewalker to use

    :arg container: the name of the element the fragment will be
        re-inserted into (the context element), e.g. ``"tbody"`` for a
        set of table rows; the same container must be used when
        re-parsing the output. Defaults to ``"div"``.

    :arg encoding: the encoding to use

    :arg serializer_opts: any options to pass to the
        :py:class:`html5lib.serializer.HTMLSerializer` that gets created

    :returns: the tree serialized as a string

    :raises SerializeError: if the tree cannot be represented as an HTML
        fragment in the given container context

    Example:

    >>> from html5lib.html5parser import parse, parseFragment
    >>> from html5lib.serializer import serializeFragment
    >>> doc = parse('<table><tbody><tr><td>Hi!</td></tr></tbody></table>')
    >>> row = doc.find('.//{http://www.w3.org/1999/xhtml}tr')
    >>> fragment = serializeFragment(row, container='tbody')
    >>> parseFragment(fragment, container='tbody') is not None
    True

    """
    if container is None:
        container = "div"
    walker = treewalkers.getTreeWalker(tree)
    tokens = list(walker(input))
    for token in tokens:
        if token["type"] == "Doctype":
            raise SerializeError(
                "Fragments cannot contain a doctype; "
                "serialize a subtree instead of a whole document")
    serializer = HTMLSerializer(**serializer_opts)
    serializer.strict = True
    output = serializer.render(iter(tokens), encoding)
    _verifyFragmentRoundTrip(tokens, output, container, encoding,
                             serializer.resolve_entities)
    return output


def _verifyFragmentRoundTrip(tokens, output, container, encoding,
                             resolve_entities):
    """Checks that output re-parses to the same fragment in container

    Parses ``output`` with :py:func:`html5lib.html5parser.parseFragment`
    in the given ``container`` context and compares the resulting token
    stream with the original one. Raises :py:class:`SerializeError`
    describing the first difference if the trees don't match.

    """
    # imported here to avoid a circular import at module level
    from .html5parser import parseFragment

    if isinstance(output, bytes):
        # re-read the fragment the way a consumer that honors the
        # declared encoding would
        output = output.decode(encoding)
    reparsed = parseFragment(output, container=container)
    reparsed_tokens = list(treewalkers.getTreeWalker("etree")(reparsed))
    expected = _normalizeFragmentTokens(tokens, resolve_entities)
    actual = _normalizeFragmentTokens(reparsed_tokens, resolve_entities)
    if expected == actual:
        return
    for i, (expected_token, actual_token) in enumerate(zip(expected, actual)):
        if expected_token != actual_token:
            raise SerializeError(
                "Fragment cannot be represented in container %r: token %d "
                "re-parses as %r instead of %r; use the container the "
                "fragment was taken from" %
                (container, i, actual_token, expected_token))
    raise SerializeError(
        "Fragment cannot be represented in container %r: re-parsing "
        "yields %d tokens instead of %d; use the container the fragment "
        "was taken from" % (container, len(actual), len(expected)))


def _normalizeFragmentTokens(tokens, resolve_entities=True):
    """Normalizes a token stream for round-trip comparison

    Reduces a treewalker token stream to a list of tuples that capture
    exactly what must survive a serialize/parse round-trip: element
    names and namespaces, attribute names, namespaces, order and
    values, character data, and comments. Tokenizer-dependent artifacts
    (character token boundaries, empty-tag style, entity spelling) are
    smoothed out.

    """
    html_namespace = namespaces["html"]
    normalized = []

    def appendText(data):
        if not data:
            return
        if normalized and normalized[-1][0] == "text":
            normalized[-1][1] += data
        else:
            normalized.append(["text", data])

    for token in tokens:
        type = token["type"]
        if type in ("Characters", "SpaceCharacters"):
            appendText(token["data"])
        elif type == "Entity":
            # resolve exactly like HTMLSerializer does
            key = token["name"] + ";"
            if resolve_entities and key in entities and key not in xmlEntities:
                appendText(entities[key])
            else:
                appendText("&%s;" % token["name"])
        elif type in ("StartTag", "EmptyTag"):
            namespace = token.get("namespace") or html_namespace
            attrs = tuple(token["data"].items())
            normalized.append(("start", namespace, token["name"], attrs))
        elif type == "EndTag":
            namespace = token.get("namespace") or html_namespace
            if namespace == html_namespace and token["name"] in voidElements:
                # void elements never serialize an end tag
                continue
            normalized.append(("end", namespace, token["name"]))
        elif type == "Comment":
            normalized.append(("comment", token["data"]))
        else:
            normalized.append((type, token.get("data")))
    return [tuple(token) if isinstance(token, list) else token
            for token in normalized]


class HTMLSerializer(object):

    # attribute quoting options
    quote_attr_values = "legacy"  # be secure by default
    quote_char = '"'
    use_best_quote_char = True

    # tag syntax options
    omit_optional_tags = True
    minimize_boolean_attributes = True
    use_trailing_solidus = False
    space_before_trailing_solidus = True

    # escaping options
    escape_lt_in_attrs = False
    escape_rcdata = False
    resolve_entities = True

    # miscellaneous options
    alphabetical_attributes = False
    inject_meta_charset = True
    strip_whitespace = False
    sanitize = False

    options = ("quote_attr_values", "quote_char", "use_best_quote_char",
               "omit_optional_tags", "minimize_boolean_attributes",
               "use_trailing_solidus", "space_before_trailing_solidus",
               "escape_lt_in_attrs", "escape_rcdata", "resolve_entities",
               "alphabetical_attributes", "inject_meta_charset",
               "strip_whitespace", "sanitize")

    def __init__(self, **kwargs):
        """Initialize HTMLSerializer

        :arg inject_meta_charset: Whether or not to inject the meta charset.

            Defaults to ``True``.

        :arg quote_attr_values: Whether to quote attribute values that don't
            require quoting per legacy browser behavior (``"legacy"``), when
            required by the standard (``"spec"``), or always (``"always"``).

            Defaults to ``"legacy"``.

        :arg quote_char: Use given quote character for attribute quoting.

            Defaults to ``"`` which will use double quotes unless attribute
            value contains a double quote, in which case single quotes are
            used.

        :arg escape_lt_in_attrs: Whether or not to escape ``<`` in attribute
            values.

            Defaults to ``False``.

        :arg escape_rcdata: Whether to escape characters that need to be
            escaped within normal elements within rcdata elements such as
            style.

            Defaults to ``False``.

        :arg resolve_entities: Whether to resolve named character entities that
            appear in the source tree. The XML predefined entities &lt; &gt;
            &amp; &quot; &apos; are unaffected by this setting.

            Defaults to ``True``.

        :arg strip_whitespace: Whether to remove semantically meaningless
            whitespace. (This compresses all whitespace to a single space
            except within ``pre``.)

            Defaults to ``False``.

        :arg minimize_boolean_attributes: Shortens boolean attributes to give
            just the attribute value, for example::

              <input disabled="disabled">

            becomes::

              <input disabled>

            Defaults to ``True``.

        :arg use_trailing_solidus: Includes a close-tag slash at the end of the
            start tag of void elements (empty elements whose end tag is
            forbidden). E.g. ``<hr/>``.

            Defaults to ``False``.

        :arg space_before_trailing_solidus: Places a space immediately before
            the closing slash in a tag using a trailing solidus. E.g.
            ``<hr />``. Requires ``use_trailing_solidus=True``.

            Defaults to ``True``.

        :arg sanitize: Strip all unsafe or unknown constructs from output.
            See :py:class:`html5lib.filters.sanitizer.Filter`.

            Defaults to ``False``.

        :arg omit_optional_tags: Omit start/end tags that are optional.

            Defaults to ``True``.

        :arg alphabetical_attributes: Reorder attributes to be in alphabetical order.

            Defaults to ``False``.

        """
        unexpected_args = frozenset(kwargs) - frozenset(self.options)
        if len(unexpected_args) > 0:
            raise TypeError("__init__() got an unexpected keyword argument '%s'" % next(iter(unexpected_args)))
        if 'quote_char' in kwargs:
            self.use_best_quote_char = False
        for attr in self.options:
            setattr(self, attr, kwargs.get(attr, getattr(self, attr)))
        self.errors = []
        self.strict = False

    def encode(self, string):
        assert isinstance(string, text_type)
        if self.encoding:
            return string.encode(self.encoding, "htmlentityreplace")
        else:
            return string

    def encodeStrict(self, string):
        assert isinstance(string, text_type)
        if self.encoding:
            return string.encode(self.encoding, "strict")
        else:
            return string

    def serialize(self, treewalker, encoding=None):
        # pylint:disable=too-many-nested-blocks
        self.encoding = encoding
        in_cdata = False
        self.errors = []

        if encoding and self.inject_meta_charset:
            from .filters.inject_meta_charset import Filter
            treewalker = Filter(treewalker, encoding)
        # Alphabetical attributes is here under the assumption that none of
        # the later filters add or change order of attributes; it needs to be
        # before the sanitizer so escaped elements come out correctly
        if self.alphabetical_attributes:
            from .filters.alphabeticalattributes import Filter
            treewalker = Filter(treewalker)
        # WhitespaceFilter should be used before OptionalTagFilter
        # for maximum efficiently of this latter filter
        if self.strip_whitespace:
            from .filters.whitespace import Filter
            treewalker = Filter(treewalker)
        if self.sanitize:
            from .filters.sanitizer import Filter
            treewalker = Filter(treewalker)
        if self.omit_optional_tags:
            from .filters.optionaltags import Filter
            treewalker = Filter(treewalker)

        for token in treewalker:
            type = token["type"]
            if type == "Doctype":
                doctype = "<!DOCTYPE %s" % token["name"]

                if token["publicId"]:
                    doctype += ' PUBLIC "%s"' % token["publicId"]
                elif token["systemId"]:
                    doctype += " SYSTEM"
                if token["systemId"]:
                    if token["systemId"].find('"') >= 0:
                        if token["systemId"].find("'") >= 0:
                            self.serializeError("System identifier contains both single and double quote characters")
                        quote_char = "'"
                    else:
                        quote_char = '"'
                    doctype += " %s%s%s" % (quote_char, token["systemId"], quote_char)

                doctype += ">"
                yield self.encodeStrict(doctype)

            elif type in ("Characters", "SpaceCharacters"):
                if type == "SpaceCharacters" or in_cdata:
                    if in_cdata and token["data"].find("</") >= 0:
                        self.serializeError("Unexpected </ in CDATA")
                    yield self.encode(token["data"])
                else:
                    yield self.encode(escape(token["data"]))

            elif type in ("StartTag", "EmptyTag"):
                name = token["name"]
                yield self.encodeStrict("<%s" % name)
                if name in rcdataElements and not self.escape_rcdata:
                    in_cdata = True
                elif in_cdata:
                    self.serializeError("Unexpected child element of a CDATA element")
                for (attr_namespace, attr_name), attr_value in token["data"].items():
                    k = attr_name
                    if attr_namespace is not None:
                        k = unadjustForeignAttributes.get((attr_namespace, attr_name))
                        if k is None:
                            self.serializeError("Attribute %s in namespace %s "
                                                "cannot be represented in HTML" %
                                                (attr_name, attr_namespace))
                            k = attr_name
                    v = attr_value
                    yield self.encodeStrict(' ')

                    yield self.encodeStrict(k)
                    if not self.minimize_boolean_attributes or \
                        (k not in booleanAttributes.get(name, tuple()) and
                         k not in booleanAttributes.get("", tuple())):
                        yield self.encodeStrict("=")
                        if self.quote_attr_values == "always" or len(v) == 0:
                            quote_attr = True
                        elif self.quote_attr_values == "spec":
                            quote_attr = _quoteAttributeSpec.search(v) is not None
                        elif self.quote_attr_values == "legacy":
                            quote_attr = _quoteAttributeLegacy.search(v) is not None
                        else:
                            raise ValueError("quote_attr_values must be one of: "
                                             "'always', 'spec', or 'legacy'")
                        v = v.replace("&", "&amp;")
                        if self.escape_lt_in_attrs:
                            v = v.replace("<", "&lt;")
                        if quote_attr:
                            quote_char = self.quote_char
                            if self.use_best_quote_char:
                                if "'" in v and '"' not in v:
                                    quote_char = '"'
                                elif '"' in v and "'" not in v:
                                    quote_char = "'"
                            if quote_char == "'":
                                v = v.replace("'", "&#39;")
                            else:
                                v = v.replace('"', "&quot;")
                            yield self.encodeStrict(quote_char)
                            yield self.encode(v)
                            yield self.encodeStrict(quote_char)
                        else:
                            yield self.encode(v)
                if name in voidElements and self.use_trailing_solidus:
                    if self.space_before_trailing_solidus:
                        yield self.encodeStrict(" /")
                    else:
                        yield self.encodeStrict("/")
                yield self.encode(">")

            elif type == "EndTag":
                name = token["name"]
                if name in rcdataElements:
                    in_cdata = False
                elif in_cdata:
                    self.serializeError("Unexpected child element of a CDATA element")
                yield self.encodeStrict("</%s>" % name)

            elif type == "Comment":
                data = token["data"]
                if data.find("--") >= 0:
                    self.serializeError("Comment contains --")
                yield self.encodeStrict("<!--%s-->" % token["data"])

            elif type == "Entity":
                name = token["name"]
                key = name + ";"
                if key not in entities:
                    self.serializeError("Entity %s not recognized" % name)
                if self.resolve_entities and key not in xmlEntities:
                    data = entities[key]
                else:
                    data = "&%s;" % name
                yield self.encodeStrict(data)

            else:
                self.serializeError(token["data"])

    def render(self, treewalker, encoding=None):
        """Serializes the stream from the treewalker into a string

        :arg treewalker: the treewalker to serialize

        :arg encoding: the string encoding to use

        :returns: the serialized tree

        Example:

        >>> from html5lib import parse, getTreeWalker
        >>> from html5lib.serializer import HTMLSerializer
        >>> token_stream = parse('<html><body>Hi!</body></html>')
        >>> walker = getTreeWalker('etree')
        >>> serializer = HTMLSerializer(omit_optional_tags=False)
        >>> serializer.render(walker(token_stream))
        '<html><head></head><body>Hi!</body></html>'

        """
        if encoding:
            return b"".join(list(self.serialize(treewalker, encoding)))
        else:
            return "".join(list(self.serialize(treewalker)))

    def serializeError(self, data="XXX ERROR MESSAGE NEEDED"):
        # XXX The idea is to make data mandatory.
        self.errors.append(data)
        if self.strict:
            raise SerializeError(data)


class SerializeError(Exception):
    """Error in serialized tree"""
    pass
