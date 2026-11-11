#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Drop-in replacement for the Python csv writer class.

Author: Gertjan van den Burg

"""

from __future__ import annotations

import csv

from typing import TYPE_CHECKING
from typing import Any
from typing import Iterable
from typing import List

import _csv

if TYPE_CHECKING:
    from clevercsv._types import SupportsWrite

from clevercsv._types import _DialectLike

from .dialect import SimpleDialect
from .exceptions import Error

DIALECT_KEYS = [
    "skipinitialspace",
    "doublequote",
    "strict",
    "delimiter",
    "escapechar",
    "lineterminator",
    "quotechar",
    "quoting",
]


class writer:
    def __init__(
        self,
        csvfile: SupportsWrite[str],
        dialect: _DialectLike = "excel",
        **fmtparams: Any,
    ) -> None:
        self.original_dialect = dialect
        self.dialect: type[_csv.Dialect] = self._make_python_dialect(
            dialect, **fmtparams
        )
        delimiter = getattr(self.dialect, "delimiter", ",")
        if isinstance(delimiter, str) and len(delimiter) > 1:
            # The csv module only supports single-character delimiters, so
            # use the pure-Python writer for multi-character delimiters.
            self._writer: Any = _MultiCharDelimiterWriter(
                csvfile, self.dialect
            )
        else:
            self._writer = _csv.writer(csvfile, dialect=self.dialect)

    def _make_python_dialect(
        self, dialect: _DialectLike, **fmtparams: Any
    ) -> type[_csv.Dialect]:
        d: Any = ""
        if isinstance(dialect, str):
            d = _csv.get_dialect(dialect)
        elif isinstance(dialect, _csv.Dialect):
            d = dialect
        elif isinstance(dialect, SimpleDialect):
            if dialect.delimiter is not None and len(dialect.delimiter) > 1:
                # A dialect with a multi-character delimiter cannot be
                # represented as a csv.Dialect, so use a simple object
                # with the same attributes instead.
                d = _DialectAttrProxy(dialect)
            else:
                d = dialect.to_csv_dialect()
        elif dialect in [csv.excel, csv.excel_tab, csv.unix_dialect]:
            d = dialect
        else:
            raise ValueError(f"Unknown dialect type: {dialect}")

        # Override properties from format parameters
        props = {k: getattr(d, k) for k in DIALECT_KEYS if hasattr(d, k)}
        for key, value in fmtparams.items():
            props[key] = value

        # lineterminator must be set
        if "lineterminator" not in props or props["lineterminator"] is None:
            props["lineterminator"] = "\n"

        # We have to subclass the csv.Dialect
        newdialect = type("dialect", (csv.Dialect,), props)
        return newdialect

    def writerow(self, row: Iterable[Any]) -> Any:
        try:
            return self._writer.writerow(row)
        except csv.Error as e:
            raise Error(str(e))

    def writerows(self, rows: Iterable[Iterable[Any]]) -> Any:
        try:
            return self._writer.writerows(rows)
        except csv.Error as e:
            raise Error(str(e))


class _DialectAttrProxy:
    """Attribute proxy for a SimpleDialect with a multi-char delimiter.

    A dialect whose delimiter consists of more than one character cannot
    be represented as a :class:`csv.Dialect` (the ``csv`` module only
    supports single-character delimiters). This class exposes the same
    dialect attributes, so that the writer can be configured uniformly.

    """

    def __init__(self, dialect: SimpleDialect) -> None:
        self.delimiter = dialect.delimiter
        self.quotechar = (
            None if dialect.quotechar == "" else dialect.quotechar
        )
        self.escapechar = (
            None if dialect.escapechar == "" else dialect.escapechar
        )
        self.doublequote = True
        self.quoting = (
            csv.QUOTE_NONE
            if dialect.quotechar == ""
            else csv.QUOTE_MINIMAL
        )
        self.skipinitialspace = False
        self.lineterminator = "\n"
        self.strict = dialect.strict


class _MultiCharDelimiterWriter:
    """Writer for dialects with a multi-character delimiter.

    The ``csv`` module of the standard library only supports
    single-character delimiters, so this class implements the same writing
    behavior (quoting, escaping, and line termination) for delimiters that
    consist of more than one character. Rows written with this class can be
    read back with :class:`clevercsv.pyparser.PyParser` using the same
    dialect.

    """

    def __init__(
        self, csvfile: SupportsWrite[str], dialect: type[_csv.Dialect]
    ) -> None:
        self.csvfile = csvfile
        self.dialect = dialect
        # Lengths k for which the delimiter overlaps itself, i.e. for
        # which delimiter[:-k] == delimiter[k:]. If a field ends with a
        # prefix of the delimiter of such a length, the field and the
        # separator that follows it can be misread as a single delimiter,
        # so such fields must be quoted. For example, "||" overlaps itself
        # with k=1, so the field "a|" must be quoted: written unquoted,
        # "a|" + "||" reads back as "a" + "||" + "|".
        delimiter = self.dialect.delimiter
        self._delimiter_periods = [
            k
            for k in range(1, len(delimiter))
            if delimiter[:-k] == delimiter[k:]
        ]

    def writerow(self, row: Iterable[Any]) -> Any:
        fields: List[Any] = list(row)
        solo = len(fields) == 1
        parts = [self._format_field(field, solo) for field in fields]
        line = self.dialect.delimiter.join(parts)
        line += self.dialect.lineterminator
        return self.csvfile.write(line)

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)

    def _format_field(self, field: Any, solo: bool) -> str:
        quoting = self.dialect.quoting
        quotechar = self.dialect.quotechar or ""
        escapechar = self.dialect.escapechar or ""
        doublequote = self.dialect.doublequote

        numeric = isinstance(field, (int, float))
        if field is None:
            text = ""
        elif isinstance(field, str):
            text = field
        else:
            text = str(field)

        if quoting == csv.QUOTE_NONE:
            if solo and text == "":
                raise Error("single empty field record must be quoted")
            return self._escape(text)

        if not quotechar:
            raise Error("quotechar must be set if quoting enabled")

        if quoting == csv.QUOTE_NONNUMERIC and numeric:
            return text

        needs_quote = (
            quoting in (csv.QUOTE_ALL, csv.QUOTE_NONNUMERIC)
            or self._needs_quoting(text)
            or (solo and text == "")
        )
        if not needs_quote:
            return text
        if quotechar in text:
            if doublequote:
                text = text.replace(quotechar, quotechar + quotechar)
            elif escapechar:
                text = text.replace(quotechar, escapechar + quotechar)
            else:
                raise Error("need to escape, but no escapechar set")
        return quotechar + text + quotechar

    def _needs_quoting(self, text: str) -> bool:
        delimiter = self.dialect.delimiter
        quotechar = self.dialect.quotechar or ""
        if (
            delimiter in text
            or (quotechar != "" and quotechar in text)
            or "\r" in text
            or "\n" in text
        ):
            return True
        return any(
            text.endswith(delimiter[:k]) for k in self._delimiter_periods
        )

    def _escape(self, text: str) -> str:
        # Escape a field for QUOTE_NONE. Every occurrence of the first
        # delimiter character is escaped, which ensures the reader cannot
        # recognize a delimiter inside the field (also when delimiter
        # occurrences overlap, as in "|||" with delimiter "||").
        escapechar = self.dialect.escapechar or ""
        quotechar = self.dialect.quotechar or ""
        first = self.dialect.delimiter[0]
        out = []
        for char in text:
            if (
                char == first
                or (escapechar != "" and char == escapechar)
                or (quotechar != "" and char == quotechar)
                or char == "\r"
                or char == "\n"
            ):
                if escapechar == "":
                    raise Error("need to escape, but no escapechar set")
                out.append(escapechar)
            out.append(char)
        return "".join(out)
