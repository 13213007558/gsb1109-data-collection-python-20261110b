# -*- coding: utf-8 -*-

"""
Exceptions for CleverCSV

Author: Gertjan van den Burg

"""

from .cparser import Error as ParserError


class Error(ParserError):
    pass


class NoDetectionResult(Exception):
    pass


class HeaderNotFoundError(Exception):
    """Raised when no row contains all of the required header columns."""


class TableParseError(Exception):
    """Raised when the table itself cannot be parsed.

    This is kept separate from :class:`HeaderNotFoundError` so that callers
    can distinguish a missing or mislabeled header from a malformed table.
    """
