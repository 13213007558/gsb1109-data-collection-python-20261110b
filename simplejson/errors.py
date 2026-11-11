"""Error classes used by simplejson
"""
__all__ = ['JSONDecodeError', 'JSONNestedError']


def linecol(doc, pos):
    lineno = doc.count('\n', 0, pos) + 1
    if lineno == 1:
        colno = pos + 1
    else:
        colno = pos - doc.rindex('\n', 0, pos)
    return lineno, colno


def errmsg(msg, doc, pos, end=None):
    lineno, colno = linecol(doc, pos)
    msg = msg.replace('%r', repr(doc[pos:pos + 1]))
    if end is None:
        fmt = '%s: line %d column %d (char %d)'
        return fmt % (msg, lineno, colno, pos)
    endlineno, endcolno = linecol(doc, end)
    fmt = '%s: line %d column %d - line %d column %d (char %d - %d)'
    return fmt % (msg, lineno, colno, endlineno, endcolno, pos, end)


class JSONDecodeError(ValueError):
    """Subclass of ValueError with the following additional properties:

    msg: The unformatted error message
    doc: The JSON document being parsed
    pos: The start index of doc where parsing failed
    end: The end index of doc where parsing failed (may be None)
    lineno: The line corresponding to pos
    colno: The column corresponding to pos
    endlineno: The line corresponding to end (may be None)
    endcolno: The column corresponding to end (may be None)

    """
    # Note that this exception is used from _speedups
    def __init__(self, msg, doc, pos, end=None):
        ValueError.__init__(self, errmsg(msg, doc, pos, end=end))
        self.msg = msg
        self.doc = doc
        self.pos = pos
        self.end = end
        self.lineno, self.colno = linecol(doc, pos)
        if end is not None:
            self.endlineno, self.endcolno = linecol(doc, end)
        else:
            self.endlineno, self.endcolno = None, None

    def __reduce__(self):
        return self.__class__, (self.msg, self.doc, self.pos, self.end)


class JSONNestedError(JSONDecodeError):
    """Subclass of :class:`JSONDecodeError` raised when a document nests
    objects or arrays deeper than the decoder's configured limit.

    Additional properties:

    depth: the 1-based nesting depth of the object or array whose opening
        bracket exceeded the limit (the first container is depth 1)
    limit: the maximum nesting depth configured on the decoder

    As with :class:`JSONDecodeError`, *pos* (and *lineno*/*colno*) points at
    the opening ``{`` or ``[`` bracket of the offending container. Bracket
    characters inside JSON strings are ordinary string contents and do not
    count, so only structural nesting is measured.
    """

    def __init__(self, doc, pos, depth, limit):
        msg = ("Nesting level %d exceeds the maximum allowed nesting "
               "depth of %d" % (depth, limit))
        JSONDecodeError.__init__(self, msg, doc, pos)
        self.depth = depth
        self.limit = limit

    def __reduce__(self):
        return self.__class__, (self.doc, self.pos, self.depth, self.limit)
