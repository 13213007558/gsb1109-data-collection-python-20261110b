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
from typing import Tuple
from typing import Union

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


def _make_python_dialect(
    dialect: _DialectLike, **fmtparams: Any
) -> type[_csv.Dialect]:
    d: _DialectLike = ""
    if isinstance(dialect, str):
        d = _csv.get_dialect(dialect)
    elif isinstance(dialect, _csv.Dialect):
        d = dialect
    elif isinstance(dialect, SimpleDialect):
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


class writer:
    def __init__(
        self,
        csvfile: SupportsWrite[str],
        dialect: _DialectLike = "excel",
        **fmtparams: Any,
    ) -> None:
        self.original_dialect = dialect
        self.dialect: type[_csv.Dialect] = _make_python_dialect(
            dialect, **fmtparams
        )
        self._writer = _csv.writer(csvfile, dialect=self.dialect)

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


class quoted_writer:
    """Writer that preserves the provenance of each field.

    This writer accepts rows of ``(value, is_quoted)`` tuples as returned by
    :class:`clevercsv.reader` with ``return_quoted=True``, and quotes each
    field exactly when it was quoted in the input or when quoting is required
    by the dialect. This makes it possible to distinguish an intentionally
    empty quoted field (written as ``""``) from an unquoted empty field
    (written as nothing between two delimiters), so that files following this
    convention round-trip faithfully. Plain (non-tuple) values are treated as
    unquoted fields.

    Note that the file object should be opened with ``newline=""``.

    """

    def __init__(
        self,
        csvfile: SupportsWrite[str],
        dialect: _DialectLike = "excel",
        **fmtparams: Any,
    ) -> None:
        self.original_dialect = dialect
        self.dialect: type[_csv.Dialect] = _make_python_dialect(
            dialect, **fmtparams
        )
        self._csvfile = csvfile

    def _serialize_cell(self, cell: Union[Any, Tuple[Any, bool]]) -> str:
        if isinstance(cell, tuple):
            value, quoted = cell
        else:
            value, quoted = cell, False
        text = "" if value is None else str(value)

        delimiter = self.dialect.delimiter
        quotechar = self.dialect.quotechar
        escapechar = self.dialect.escapechar
        doublequote = getattr(self.dialect, "doublequote", True)

        specials = ["\r", "\n"]
        if delimiter:
            specials.append(delimiter)
        if quotechar:
            specials.append(quotechar)

        needs_quote = bool(quoted) or any(s in text for s in specials)
        if not needs_quote and escapechar and escapechar in text:
            needs_quote = True
        if not needs_quote:
            return text

        if quotechar:
            if doublequote or not escapechar:
                escaped = text.replace(quotechar, quotechar * 2)
            else:
                escaped = text.replace(quotechar, escapechar + quotechar)
            return quotechar + escaped + quotechar

        if escapechar:
            for special in [escapechar] + specials:
                text = text.replace(special, escapechar + special)
            return text

        raise Error(
            "field contains delimiter or newline but the dialect has no "
            "quotechar or escapechar to serialize it with"
        )

    def writerow(self, row: Iterable[Union[Any, Tuple[Any, bool]]]) -> None:
        cells = list(row)
        delimiter = self.dialect.delimiter
        if not delimiter and len(cells) > 1:
            raise Error(
                "cannot write a row with multiple fields without a delimiter"
            )
        line = delimiter.join(self._serialize_cell(cell) for cell in cells)
        self._csvfile.write(line + self.dialect.lineterminator)

    def writerows(
        self, rows: Iterable[Iterable[Union[Any, Tuple[Any, bool]]]]
    ) -> None:
        for row in rows:
            self.writerow(row)
