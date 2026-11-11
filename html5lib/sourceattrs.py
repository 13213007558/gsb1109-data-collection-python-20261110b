"""Source-text oriented attribute rewriting.

The regular html5lib pipeline parses HTML into a tree; serializing that tree
back re-formats whitespace, quote styles and much of the surrounding text,
which makes source-level changes hard to audit.  This module instead drives
the tokenizer directly, records the exact source span of every attribute,
and performs surgical replacements on the original text so that only the
targeted attribute values change.

Offsets returned by the tokenizer index the *normalized* source text: CR and
CR LF are converted to LF.  Byte input is decoded first and its leading
byte-order mark is stripped, matching the characters the tokenizer actually
consumes.  Use :func:`normalize_source` to obtain that text.

Duplicate attributes are reported individually.  The first occurrence of an
attribute name is the one the parser keeps (``used`` is true); later
occurrences are parse errors that are ignored by the tree builder and are
skipped unless ``include_duplicates`` is requested.

Example::

    from html5lib.sourceattrs import replace_attribute_values

    def update(record):
        if record["value"] == "source-7":
            return "source-42"
        return None

    new_text, changes = replace_attribute_values(
        html, update, tags={"iframe"}, attributes={"data-source"})
"""

from __future__ import absolute_import, division, unicode_literals

from io import BytesIO, StringIO

from six import text_type

from ._inputstream import EOF, HTMLInputStream
from .html5parser import HTMLParser


def _drain(stream):
    chunks = []
    while True:
        char = stream.char()
        if char is EOF:
            break
        chunks.append(char)
    return "".join(chunks)


def normalize_source(source):
    """Return the unicode text whose character offsets the tokenizer uses.

    :arg source: a unicode string, a byte string, or a text/binary file-like
        object, identical to what :func:`html5lib.parse` accepts.

    :returns: normalized unicode text with CR and CR LF converted to LF.
        For byte input a leading byte-order mark is removed during decoding.
    """
    if isinstance(source, bytes):
        return _drain(HTMLInputStream(BytesIO(source)))
    if isinstance(source, text_type):
        # Mirror HTMLUnicodeInputStream.readChunk() normalization exactly.
        return source.replace("\r\n", "\n").replace("\r", "\n")
    return _drain(HTMLInputStream(source))


def _as_name_set(values):
    if values is None:
        return None
    return {value.lower() for value in values}


def find_attributes(source, tags=None, attributes=None,
                    include_duplicates=False):
    """Find attribute source spans in ``source``.

    :arg source: the HTML document as accepted by :func:`normalize_source`.
    :arg tags: optional iterable of tag names to restrict matches to, matched
        case-insensitively. ``None`` matches every tag.
    :arg attributes: optional iterable of attribute names to restrict matches
        to, matched case-insensitively. ``None`` matches every attribute.
    :arg include_duplicates: when false (the default) only the occurrence of a
        duplicated attribute name that the parser actually uses is returned;
        when true ignored duplicate occurrences are returned as well.

    :returns: a ``(text, records)`` tuple where ``text`` is the normalized
        source text and ``records`` is a list of dictionaries, in source
        order, each containing:

        ``tag``
            lowercased tag name the attribute belongs to
        ``name`` / ``value``
            attribute name and decoded value as seen by the parser
        ``nameStart`` / ``nameEnd``
            half-open offsets of the attribute name, so the raw name is
            ``text[nameStart:nameEnd]``
        ``valueStart`` / ``valueEnd``
            half-open offsets of the raw attribute value excluding quote
            characters, so the raw value is ``text[valueStart:valueEnd]``;
            both are ``None`` for valueless attributes
        ``duplicate`` / ``used``
            whether the attribute is a later duplicate and whether the parser
            retains this occurrence
        ``line`` / ``column``
            1-based location of the attribute name
    """
    text = normalize_source(source)
    tag_names = _as_name_set(tags)
    attribute_names = _as_name_set(attributes)

    records = []
    # Drive the full parser rather than the bare tokenizer so that
    # script/style/textarea content switches into raw text states and
    # lookalike markup inside them is never reported as attributes.
    parser = HTMLParser()
    parser.parse(StringIO(text), sourceAttrs=records)

    line_starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            line_starts.append(index + 1)

    def location(offset):
        line_lo, line_hi = 0, len(line_starts) - 1
        while line_lo < line_hi:
            mid = (line_lo + line_hi + 1) // 2
            if line_starts[mid] <= offset:
                line_lo = mid
            else:
                line_hi = mid - 1
        return line_lo + 1, offset - line_starts[line_lo] + 1

    matches = []
    for record in records:
        if tag_names is not None and record["tag"] not in tag_names:
            continue
        if attribute_names is not None and record["name"] not in attribute_names:
            continue
        if not include_duplicates and not record["used"]:
            continue
        line, column = location(record["nameStart"])
        record = dict(record)
        record["line"] = line
        record["column"] = column
        matches.append(record)
    return text, matches


def replace_attribute_values(source, callback, tags=None, attributes=None,
                             include_duplicates=False):
    """Rewrite attribute values directly in the source text.

    Only raw attribute values (never their surrounding quotes, the attribute
    name, whitespace, comments, script contents or display text) are touched.
    Replacements are applied from the end of the document backwards so the
    recorded offsets stay valid within a single pass.

    :arg source: the HTML document as accepted by :func:`normalize_source`.
    :arg callback: either a mapping of decoded old values to replacement
        strings, or a callable invoked with each matched record that returns
        the new value or ``None`` to leave that attribute unchanged.
    :arg tags: optional iterable of tag names to restrict matches to.
    :arg attributes: optional iterable of attribute names to restrict matches
        to.
    :arg include_duplicates: whether ignored duplicate occurrences may be
        rewritten as well.

    :returns: a ``(new_text, changes)`` tuple.  ``changes`` lists the applied
        edits in source order; each is the attribute record augmented with
        ``oldValue`` (the raw source substring that was replaced),
        ``newValue``, ``line`` and ``column``.
    """
    text, records = find_attributes(
        source, tags=tags, attributes=attributes,
        include_duplicates=include_duplicates)

    if not callable(callback):
        mapping = dict(callback)

        def callback(record):
            return mapping.get(record["value"])

    edits = []
    for record in records:
        if record["valueStart"] is None:
            continue
        new_value = callback(record)
        if new_value is None or new_value == record["value"]:
            continue
        edits.append((record, new_value))

    new_text = text
    changes = []
    # Splice from the end backwards so earlier offsets remain valid.
    for record, new_value in reversed(edits):
        start = record["valueStart"]
        end = record["valueEnd"]
        change = dict(record)
        change["oldValue"] = text[start:end]
        change["newValue"] = new_value
        new_text = new_text[:start] + new_value + new_text[end:]
        changes.append(change)
    changes.reverse()
    return new_text, changes


def parsed_attribute_values(text, tags=None, attributes=None):
    """Return ``(tag, attribute, value)`` triples from a fresh parse.

    Useful for verifying that rewritten source text reparses to the new
    attribute values.
    """
    import html5lib

    tag_names = _as_name_set(tags)
    attribute_names = _as_name_set(attributes)
    document = html5lib.parse(text)
    result = []
    for element in document.iter():
        # Comments and processing instructions expose a callable as their tag.
        if not isinstance(element.tag, text_type):
            continue
        tag = element.tag.split("}", 1)[-1]
        if tag_names is not None and tag not in tag_names:
            continue
        for key, value in element.attrib.items():
            name = key.split("}", 1)[-1]
            if attribute_names is not None and name not in attribute_names:
                continue
            result.append((tag, name, value))
    return result


def _build_mapping(pairs):
    mapping = {}
    for pair in pairs:
        old, separator, new = pair.partition("=")
        if not separator:
            raise ValueError("mapping %r must use OLD=NEW form" % pair)
        mapping[old] = new
    return mapping


def main(argv=None):
    """Command line entry point: ``python -m html5lib.sourceattrs``."""
    import argparse
    import difflib
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m html5lib.sourceattrs",
        description="Rewrite attribute values in HTML source text without "
                    "re-serializing the document.")
    parser.add_argument("files", nargs="+",
                        help="HTML files to process ('-' reads standard input)")
    parser.add_argument("--attr", action="append", required=True,
                        help="attribute name to target (repeatable)")
    parser.add_argument("--tag", action="append",
                        help="restrict to these tag names (repeatable)")
    parser.add_argument("--map", action="append", default=[],
                        dest="mapping", metavar="OLD=NEW",
                        help="decoded old value to replacement (repeatable)")
    parser.add_argument("--include-duplicates", action="store_true",
                        help="also rewrite duplicate occurrences the parser "
                             "would otherwise ignore")
    parser.add_argument("--in-place", action="store_true",
                        help="write rewritten UTF-8 text back to each file")
    parser.add_argument("--verify", action="store_true",
                        help="reparse the result and print the resolved "
                             "attribute values")
    args = parser.parse_args(argv)

    mapping = _build_mapping(args.mapping)
    total_changes = 0

    for path in args.files:
        if path == "-":
            source = sys.stdin.buffer.read()
            label = "<stdin>"
        else:
            label = path
            with open(path, "rb") as handle:
                source = handle.read()

        original = normalize_source(source)
        new_text, changes = replace_attribute_values(
            source, mapping, tags=args.tag, attributes=args.attr,
            include_duplicates=args.include_duplicates)
        total_changes += len(changes)

        for change in changes:
            sys.stdout.write(
                "%s:%d:%d: %s@%s %r -> %r\n" % (
                    label, change["line"], change["column"],
                    change["tag"], change["name"],
                    change["oldValue"], change["newValue"]))

        if changes:
            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=label, tofile=label)
            sys.stdout.write("".join(diff))

        if args.in_place and path != "-" and changes:
            with open(path, "wb") as handle:
                handle.write(new_text.encode("utf-8"))

        if args.verify:
            for tag, name, value in parsed_attribute_values(
                    new_text, tags=args.tag, attributes=args.attr):
                sys.stdout.write(
                    "verified %s: %s@%s=%r\n" % (label, tag, name, value))

    return 1 if total_changes == 0 else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
