# -*- coding: utf-8 -*-

"""
Pure-Python CSV parser with support for multi-character delimiters.

This parser mirrors the state machine of the C parser in
:mod:`clevercsv.cparser` (see ``src/cparser.c``), but generalizes the
delimiter from a single character to a string. It is used as a fallback for
dialects whose delimiter has more than one character, which the C parser
(and the Python ``csv`` module) cannot handle.

Semantics are the same as for the C parser, with the delimiter match
generalized as follows:

* The delimiter is matched greedily, left to right. With delimiter ``||``,
  the input ``a|||b`` parses as ``["a", "|b"]``: the first ``||`` is a
  delimiter and the remaining ``|`` is part of the next field.

* A delimiter may span a chunk boundary of the input iterable. If a chunk
  ends with a proper prefix of the delimiter, more chunks are pulled to
  complete the match before deciding. Only at end-of-file is a dangling
  prefix treated as field content.

* As in the C parser, the end of a chunk otherwise terminates the current
  record (unless inside a quoted field), so iterables that yield rows
  without trailing newlines behave the same as with single-character
  delimiters.

Author: Gertjan van den Burg

"""

from typing import Iterable
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from .cparser import Error

# Parser states, mirroring src/cparser.c
_START_RECORD = 0
_START_FIELD = 1
_ESCAPED_CHAR = 2
_IN_FIELD = 3
_IN_QUOTED_FIELD = 4
_ESCAPE_IN_QUOTED_FIELD = 5
_QUOTE_IN_QUOTED_FIELD = 6
_EAT_CRNL = 7
_AFTER_ESCAPED_CRNL = 8

# States in which a delimiter can occur. START_RECORD falls through to
# START_FIELD in the C parser, so a delimiter can also start a record.
# AFTER_ESCAPED_CRNL similarly falls through to IN_FIELD.
_DELIMITER_STATES = (
    _START_RECORD,
    _START_FIELD,
    _IN_FIELD,
    _QUOTE_IN_QUOTED_FIELD,
    _AFTER_ESCAPED_CRNL,
)

RowType = Union[List[str], List[Tuple[str, bool]]]


class PyParser:
    """CSV parser with multi-character delimiter support.

    This class is a drop-in replacement for
    :class:`clevercsv.cparser.Parser` and accepts the same arguments.

    """

    def __init__(
        self,
        csvfile: Iterable[str],
        delimiter: Optional[str] = ",",
        quotechar: Optional[str] = "",
        escapechar: Optional[str] = "",
        field_limit: int = 128 * 1024,
        strict: bool = False,
        return_quoted: bool = False,
    ):
        self.input_iter = iter(csvfile)
        self.delimiter = delimiter if delimiter is not None else ""
        self.quotechar = quotechar if quotechar is not None else ""
        self.escapechar = escapechar if escapechar is not None else ""
        self.field_limit = field_limit
        self.strict = bool(strict)
        self.return_quoted = bool(return_quoted)
        self.doublequote = False

        # Characters that may be "escaped" (i.e. for which a preceding
        # escapechar is dropped). This generalizes the C parser, where the
        # set is {delimiter, quotechar, escapechar}.
        self._escapable = set()
        if self.delimiter:
            self._escapable.add(self.delimiter[0])
        if self.quotechar:
            self._escapable.add(self.quotechar)
        if self.escapechar:
            self._escapable.add(self.escapechar)

        self._buf = ""
        self._pos = 0
        self._chunk_open = False
        self._state = _START_RECORD
        self._fields: List = []
        self._field: List[str] = []

    def __iter__(self) -> "PyParser":
        return self

    def __next__(self) -> RowType:
        self._fields = []
        self._field = []
        self._state = _START_RECORD
        while True:
            if self._pos >= len(self._buf):
                if self._chunk_open:
                    # End of a chunk: process the sentinel, as the C parser
                    # does by feeding a '\0' after each chunk.
                    self._chunk_open = False
                    self._process_sentinel()
                    if self._state == _START_RECORD:
                        return self._fields
                if not self._pull_chunk():
                    # End of file
                    if len(self._field) > 0 or self._state == _IN_QUOTED_FIELD:
                        if self.strict:
                            raise Error("unexpected end of data")
                        self._save_field(trailing=True)
                        return self._fields
                    raise StopIteration
                continue
            char = self._buf[self._pos]
            if (
                self.delimiter
                and self._state in _DELIMITER_STATES
                and char == self.delimiter[0]
                and char != self.quotechar
                and (
                    self._state == _QUOTE_IN_QUOTED_FIELD
                    or char != self.escapechar
                )
                and self._match_delimiter()
            ):
                self._pos += len(self.delimiter)
                self._save_field()
                self._state = _START_FIELD
                continue
            self._pos += 1
            lookahead = (
                self._buf[self._pos] if self._pos < len(self._buf) else None
            )
            self._process_char(char, lookahead)

    def _pull_chunk(self) -> bool:
        """Pull the next chunk into the buffer. Returns False at EOF."""
        chunk = next(self.input_iter, None)
        if chunk is None:
            return False
        if not isinstance(chunk, str):
            raise Error(
                "iterator should return strings, not %.200s "
                "(did you open the file in text mode?)"
                % type(chunk).__name__
            )
        if "\0" in chunk:
            raise Error("line contains NULL byte")
        if self._pos > 0:
            self._buf = self._buf[self._pos :]
            self._pos = 0
        self._buf += chunk
        self._chunk_open = True
        return True

    def _match_delimiter(self) -> bool:
        """Check if the delimiter occurs at the current cursor position.

        The first character is known to match. The remaining characters
        are compared one by one, pulling additional chunks when the end of
        the current chunk is reached mid-match. This allows the delimiter
        to span a chunk boundary. Note that no chunks are pulled when the
        match fails within the current chunk, so a record boundary (such
        as a newline) later in the chunk is never consumed by the
        lookahead.

        """
        delimiter = self.delimiter
        for k in range(1, len(delimiter)):
            while self._pos + k >= len(self._buf):
                if not self._pull_chunk():
                    return False
            if self._buf[self._pos + k] != delimiter[k]:
                return False
        return True

    def _add_char(self, char: str) -> None:
        if len(self._field) >= self.field_limit:
            raise Error(
                "field larger than field limit (%ld)" % self.field_limit
            )
        self._field.append(char)

    def _save_field(self, trailing: bool = False) -> None:
        field = "".join(self._field)
        is_quoted = False
        quote = self.quotechar
        if quote and len(field) > 1 and field[0] == quote == field[-1]:
            field = field[1:-1]
            is_quoted = True
        if trailing and quote and field.startswith(quote):
            field = field[1:]
            is_quoted = True
        self._field = []
        if self.return_quoted:
            self._fields.append((field, is_quoted))
        else:
            self._fields.append(field)

    def _process_sentinel(self) -> None:
        """Process the end-of-chunk sentinel (the '\0' of the C parser)."""
        if self._state == _START_RECORD:
            pass
        elif self._state == _START_FIELD:
            self._save_field()
            self._state = _START_RECORD
        elif self._state == _ESCAPED_CHAR:
            self._state = _IN_FIELD
        elif self._state == _IN_FIELD:
            self._save_field()
            self._state = _START_RECORD
        elif self._state == _IN_QUOTED_FIELD:
            pass
        elif self._state == _ESCAPE_IN_QUOTED_FIELD:
            self._add_char("\n")
            self._state = _IN_QUOTED_FIELD
        elif self._state == _QUOTE_IN_QUOTED_FIELD:
            self._save_field()
            self._state = _START_RECORD
        elif self._state == _EAT_CRNL:
            self._state = _START_RECORD
        elif self._state == _AFTER_ESCAPED_CRNL:
            pass

    def _process_char(self, char: str, lookahead: Optional[str]) -> None:
        state = self._state
        if state == _START_RECORD:
            if char == "\r" or char == "\n":
                self._state = _EAT_CRNL
                return
            self._state = _START_FIELD
            state = _START_FIELD
        if state == _START_FIELD:
            if char == "\r" or char == "\n":
                self._save_field()
                self._state = _EAT_CRNL
            elif char == self.quotechar:
                self._add_char(char)
                self._state = _IN_QUOTED_FIELD
            elif char == self.escapechar:
                self._state = _ESCAPED_CHAR
            else:
                self._add_char(char)
                self._state = _IN_FIELD
        elif state == _ESCAPED_CHAR:
            if char == "\r" or char == "\n":
                self._add_char(char)
                self._state = _AFTER_ESCAPED_CRNL
                return
            if char not in self._escapable:
                self._add_char(self.escapechar)
            self._add_char(char)
            self._state = _IN_FIELD
        elif state == _AFTER_ESCAPED_CRNL or state == _IN_FIELD:
            if char == "\r" or char == "\n":
                self._save_field()
                self._state = _EAT_CRNL
            elif char == self.escapechar:
                self._state = _ESCAPED_CHAR
            elif char == self.quotechar:
                self._add_char(char)
                self._state = _IN_QUOTED_FIELD
            else:
                self._add_char(char)
        elif state == _IN_QUOTED_FIELD:
            if char == self.escapechar:
                self._state = _ESCAPE_IN_QUOTED_FIELD
            elif char == self.quotechar:
                if lookahead == self.quotechar:
                    self.doublequote = True
                    self._state = _QUOTE_IN_QUOTED_FIELD
                elif self.strict:
                    raise Error(
                        "'%s' expected after '%s'"
                        % (self.delimiter, self.quotechar)
                    )
                else:
                    self._add_char(char)
                    self._state = _IN_FIELD
            else:
                self._add_char(char)
        elif state == _ESCAPE_IN_QUOTED_FIELD:
            if char not in self._escapable:
                self._add_char(self.escapechar)
            self._add_char(char)
            self._state = _IN_QUOTED_FIELD
        elif state == _QUOTE_IN_QUOTED_FIELD:
            if char == self.quotechar:
                self._add_char(char)
                self._state = _IN_QUOTED_FIELD
            elif char == "\r" or char == "\n":
                self._save_field()
                self._state = _EAT_CRNL
            elif not self.strict:
                self._add_char(char)
                self._state = _IN_FIELD
            else:
                raise Error(
                    "'%s' expected after '%s'"
                    % (self.delimiter, self.quotechar)
                )
        elif state == _EAT_CRNL:
            if char == "\r" or char == "\n":
                pass
            else:
                raise Error(
                    "new-line character seen in unquoted field - do you "
                    "need to open the file in universal-newline mode?"
                )
