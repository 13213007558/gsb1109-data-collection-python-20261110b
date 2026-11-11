#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Compatibility reports for exporting detected dialects to Python's csv module.

CleverCSV detects dialects described by a :class:`SimpleDialect` (delimiter,
quotechar, escapechar, strict).  Downstream machines that only have the
Python :mod:`csv` module cannot always express these dialects directly:

* the empty quotechar (no quoting) must be mapped to ``csv.QUOTE_NONE``,
* the csv module drops the escapechar before *any* character, whereas
  CleverCSV only treats it as an escape before the delimiter, quotechar, or
  escapechar itself,
* the csv module only treats the quotechar as special at the start of a
  field, CleverCSV also starts a quoted section mid-field,
* the empty delimiter (single-column files) has no csv module equivalent.

This module turns a detected dialect into an explicit compatibility report.
For each direction (reading existing files, writing files of the same
class) it reports a status, the ``csv.reader``/``csv.writer`` format
parameters to use, and -- where the csv module cannot faithfully reproduce
CleverCSV -- a list of divergences, each with a short verified sample.

Statuses:

* ``direct``: the fmtparams can be used as-is.
* ``workaround``: expressible, but only through a remapping (e.g.
  ``QUOTE_NONE`` for the empty quotechar).
* ``partial``: works for a subset of files; known divergences are listed
  with samples that are read or written incorrectly.
* ``impossible``: cannot be expressed with the csv module at all.

Note that the report is generated with the csv module of the Python that
runs it.  Escapechar handling of ``csv.writer`` differs between Python
versions, so when the report is produced on a different machine than the
one that will use it, re-run :func:`csv_compat_report` on the target
machine to verify the escapechar-related claims.

Author: Gertjan van den Burg

"""

import csv
import io
import json

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from .dialect import SimpleDialect
from .read import reader as _clevercsv_reader

DIRECT = "direct"
WORKAROUND = "workaround"
PARTIAL = "partial"
IMPOSSIBLE = "impossible"

STATUSES = (DIRECT, WORKAROUND, PARTIAL, IMPOSSIBLE)

_QUOTING_NAMES = {
    csv.QUOTE_MINIMAL: "csv.QUOTE_MINIMAL",
    csv.QUOTE_NONE: "csv.QUOTE_NONE",
    csv.QUOTE_ALL: "csv.QUOTE_ALL",
    csv.QUOTE_NONNUMERIC: "csv.QUOTE_NONNUMERIC",
}


@dataclass
class Divergence:
    """A verified difference between CleverCSV and the csv module.

    For reading, ``sample`` is file text, ``expected`` the CleverCSV parse
    and ``actual`` the csv module parse (or an error message).  For
    writing, ``sample`` is the list of rows, ``expected`` the same rows
    (lossless round-trip) and ``actual`` a description of what the csv
    module did instead (error message or read-back values).
    """

    description: str
    sample: Any
    expected: Any
    actual: Any

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "sample": self.sample,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class DirectionReport:
    """Compatibility of one direction (reading or writing)."""

    direction: str
    status: str
    fmtparams: Optional[Dict[str, Any]]
    notes: List[str] = dataclass_field(default_factory=list)
    divergences: List[Divergence] = dataclass_field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "status": self.status,
            "fmtparams": {
                key: _jsonable(value) for key, value in self.fmtparams.items()
            }
            if self.fmtparams is not None
            else None,
            "notes": list(self.notes),
            "divergences": [d.to_dict() for d in self.divergences],
        }

    def render(self) -> str:
        lines = [
            "%s: status=%s" % (self.direction.capitalize(), self.status)
        ]
        if self.fmtparams is None:
            lines.append("  fmtparams: none (not expressible)")
        else:
            args = ", ".join(
                "%s=%s" % (key, _format_value(value))
                for key, value in self.fmtparams.items()
            )
            target = "csv.reader" if self.direction == "read" else "csv.writer"
            lines.append("  fmtparams: %s(file, %s)" % (target, args))
        for note in self.notes:
            lines.append("  note: %s" % note)
        for i, divergence in enumerate(self.divergences, start=1):
            lines.append("  difference %d: %s" % (i, divergence.description))
            lines.append("    sample:    %r" % (divergence.sample,))
            lines.append("    expected:  %r" % (divergence.expected,))
            lines.append("    csv module: %r" % (divergence.actual,))
        return "\n".join(lines)


@dataclass
class CompatReport:
    """Read/write compatibility of a SimpleDialect with the csv module."""

    dialect: SimpleDialect
    read: DirectionReport
    write: DirectionReport

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dialect": self.dialect.to_dict(),
            "read": self.read.to_dict(),
            "write": self.write.to_dict(),
        }

    def serialize(self) -> str:
        """Serialize the report to a JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def render(self) -> str:
        """Render the report as human-readable text."""
        return "\n".join(
            [
                "Dialect: %r" % (self.dialect,),
                self.read.render(),
                self.write.render(),
            ]
        )


def _is_quoting_constant(value: Any) -> bool:
    # bool is a subclass of int and True == csv.QUOTE_ALL, so exclude it
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value in _QUOTING_NAMES
    )


def _jsonable(value: Any) -> Any:
    if _is_quoting_constant(value):
        return _QUOTING_NAMES[value]
    return value


def _format_value(value: Any) -> str:
    if _is_quoting_constant(value):
        return _QUOTING_NAMES[value]
    return repr(value)


def _read_fmtparams(dialect: SimpleDialect) -> Optional[Dict[str, Any]]:
    """Format parameters for csv.reader, or None if not expressible."""
    if dialect.delimiter == "":
        return None
    fmtparams: Dict[str, Any] = {"delimiter": dialect.delimiter}
    if dialect.quotechar == "":
        fmtparams["quoting"] = csv.QUOTE_NONE
        fmtparams["quotechar"] = None
    else:
        fmtparams["quotechar"] = dialect.quotechar
        fmtparams["doublequote"] = True
    if dialect.escapechar != "":
        fmtparams["escapechar"] = dialect.escapechar
    if dialect.strict:
        fmtparams["strict"] = True
    return fmtparams


def _write_fmtparams(dialect: SimpleDialect) -> Optional[Dict[str, Any]]:
    """Format parameters for csv.writer, or None if not expressible."""
    if dialect.delimiter == "":
        return None
    fmtparams: Dict[str, Any] = {
        "delimiter": dialect.delimiter,
        "lineterminator": "\n",
    }
    if dialect.quotechar == "":
        fmtparams["quoting"] = csv.QUOTE_NONE
        # quotechar=None is required: with the default quotechar the writer
        # escapes double quotes, which a quotechar-less dialect reads back
        # with a spurious escapechar.
        fmtparams["quotechar"] = None
    else:
        fmtparams["quoting"] = csv.QUOTE_MINIMAL
        fmtparams["quotechar"] = dialect.quotechar
        fmtparams["doublequote"] = True
    if dialect.escapechar != "":
        fmtparams["escapechar"] = dialect.escapechar
    return fmtparams


def _ordinary_char(dialect: SimpleDialect) -> str:
    """A character with no special meaning in the dialect."""
    special = {
        dialect.delimiter,
        dialect.quotechar,
        dialect.escapechar,
        "\r",
        "\n",
    }
    for char in "nxtrzqw":
        if char not in special:
            return char
    raise ValueError("Could not find an ordinary character for dialect")


def _read_probes(dialect: SimpleDialect) -> List[Tuple[str, str]]:
    """Candidate read divergences as (description, sample text) pairs."""
    probes = []
    escapechar = dialect.escapechar
    quotechar = dialect.quotechar
    delimiter = dialect.delimiter
    if escapechar != "":
        char = _ordinary_char(dialect)
        probes.append(
            (
                "escapechar before an ordinary character: the csv module "
                "drops the escapechar before any character, CleverCSV keeps "
                "it unless it precedes the delimiter, quotechar, or "
                "escapechar",
                "path%s%sname%sok\n" % (escapechar, char, delimiter),
            )
        )
    if quotechar != "":
        sample = "a%sb%sc%sd%se\n" % (
            quotechar,
            delimiter,
            quotechar,
            delimiter,
        )
        probes.append(
            (
                "quotechar inside an unquoted field: the csv module treats "
                "it as data and keeps splitting fields, CleverCSV starts a "
                "quoted section (and keeps the quotechar)",
                sample,
            )
        )
    return probes


def _write_probes(dialect: SimpleDialect) -> List[Tuple[str, List[List[str]]]]:
    """Candidate write divergences as (description, rows) pairs."""
    probes = []
    escapechar = dialect.escapechar
    quotechar = dialect.quotechar
    delimiter = dialect.delimiter
    if quotechar == "" and escapechar == "":
        probes.append(
            (
                "a field containing the delimiter cannot be represented "
                "without quoting or escaping",
                [["a%sb" % delimiter, "ok"]],
            )
        )
        probes.append(
            (
                "a field containing a newline cannot be represented without "
                "quoting or escaping",
                [["a\nb", "ok"]],
            )
        )
    if escapechar != "":
        probes.append(
            (
                "the writer must escape the escapechar itself for a "
                "lossless round-trip",
                [["a%sb" % escapechar, "c%s%sd" % (escapechar, escapechar)]],
            )
        )
    return probes


def _as_error_string(func: Any) -> Any:
    """Run a parse, returning a normalized string if it raises."""
    try:
        return func()
    except Exception as err:
        # normalize so that both sides raising the same error compares
        # equal (CleverCSV's Error is the csv module's Error)
        return "raises %s: %s" % (type(err).__name__, err)


def _check_read(
    dialect: SimpleDialect, fmtparams: Dict[str, Any], text: str
) -> Tuple[Any, Any]:
    expected = _as_error_string(
        lambda: list(_clevercsv_reader(io.StringIO(text), dialect=dialect))
    )
    actual = _as_error_string(
        lambda: list(csv.reader(io.StringIO(text), **fmtparams))
    )
    return expected, actual


def _check_write(
    dialect: SimpleDialect, fmtparams: Dict[str, Any], rows: List[List[str]]
) -> Optional[Dict[str, Any]]:
    """Write rows with the csv module and read the result back.

    Returns None on a lossless round-trip, otherwise a dict describing
    what went wrong.
    """
    buffer = io.StringIO()
    try:
        writer = csv.writer(buffer, **fmtparams)
        writer.writerows(rows)
    except csv.Error as err:
        return {"error": "csv.Error: %s" % err}
    written = buffer.getvalue()
    result: Dict[str, Any] = {"written": written}
    read_fmtparams = _read_fmtparams(dialect)
    if read_fmtparams is not None:
        read_back = _as_error_string(
            lambda: list(csv.reader(io.StringIO(written), **read_fmtparams))
        )
        if read_back != rows:
            result["csv_module_read_back"] = read_back
    read_back = _as_error_string(
        lambda: list(_clevercsv_reader(io.StringIO(written), dialect=dialect))
    )
    if read_back != rows:
        result["clevercsv_read_back"] = read_back
    if len(result) == 1:
        return None
    return result


def _build_read_report(dialect: SimpleDialect) -> DirectionReport:
    notes: List[str] = []
    divergences: List[Divergence] = []
    fmtparams = _read_fmtparams(dialect)
    if fmtparams is None:
        error = "delimiter='' is not accepted"
        try:
            list(csv.reader(io.StringIO("alpha\nbeta\n"), delimiter=""))
        except TypeError as err:
            error = "TypeError: %s" % err
        divergences.append(
            Divergence(
                description=(
                    "the empty delimiter (single-column file) has no csv "
                    "module equivalent; read lines and strip newlines instead"
                ),
                sample="alpha\nbeta\n",
                expected=[["alpha"], ["beta"]],
                actual=error,
            )
        )
        return DirectionReport("read", IMPOSSIBLE, None, notes, divergences)

    status = DIRECT
    if dialect.quotechar == "":
        status = WORKAROUND
        notes.append(
            "quotechar='' is rejected by the csv module; it is mapped to "
            "quoting=csv.QUOTE_NONE with quotechar=None, which makes the "
            "csv module treat quote characters as ordinary data"
        )
    for description, text in _read_probes(dialect):
        expected, actual = _check_read(dialect, fmtparams, text)
        if expected != actual:
            divergences.append(
                Divergence(
                    description=description,
                    sample=text,
                    expected=expected,
                    actual=actual,
                )
            )
    if divergences:
        status = PARTIAL
    return DirectionReport("read", status, fmtparams, notes, divergences)


def _build_write_report(dialect: SimpleDialect) -> DirectionReport:
    notes: List[str] = []
    divergences: List[Divergence] = []
    fmtparams = _write_fmtparams(dialect)
    if fmtparams is None:
        divergences.append(
            Divergence(
                description=(
                    "the csv module requires a one-character delimiter; "
                    "write single-column files as plain lines instead"
                ),
                sample=[["alpha"], ["beta"]],
                expected=[["alpha"], ["beta"]],
                actual="TypeError: \"delimiter\" must be a 1-character string",
            )
        )
        return DirectionReport("write", IMPOSSIBLE, None, notes, divergences)

    notes.append(
        "lineterminator='\\n' is set explicitly; the csv module default "
        "is '\\r\\n' (both are read back correctly)"
    )
    status = DIRECT
    if dialect.quotechar == "":
        status = WORKAROUND
        notes.append(
            "quoting=csv.QUOTE_NONE with quotechar=None is required; with "
            "the default quotechar the writer escapes double quotes, which "
            "this dialect reads back with a spurious escapechar"
        )
    if dialect.escapechar != "":
        notes.append(
            "lossless writing relies on csv.writer escaping the escapechar "
            "itself; this is verified for the Python running this report, "
            "re-verify on the target machine"
        )
    for description, rows in _write_probes(dialect):
        problem = _check_write(dialect, fmtparams, rows)
        if problem is not None:
            divergences.append(
                Divergence(
                    description=description,
                    sample=rows,
                    expected=rows,
                    actual=problem,
                )
            )
    if divergences:
        status = PARTIAL
    return DirectionReport("write", status, fmtparams, notes, divergences)


def csv_compat_report(dialect: SimpleDialect) -> CompatReport:
    """Build a csv-module compatibility report for a detected dialect.

    Parameters
    ----------
    dialect : SimpleDialect
        The dialect detected by CleverCSV.

    Returns
    -------
    CompatReport
        Report with a ``read`` and a ``write`` section.  Each section
        carries a status (``direct``, ``workaround``, ``partial``, or
        ``impossible``), the format parameters for ``csv.reader`` or
        ``csv.writer``, and a list of verified divergences with samples.

    """
    dialect.validate()
    return CompatReport(
        dialect=dialect,
        read=_build_read_report(dialect),
        write=_build_write_report(dialect),
    )
