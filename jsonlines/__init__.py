"""
Module for the jsonlines data format.
"""

# expose only public api
from .jsonlines import (
    Error,
    IncompleteLastLineError,
    InvalidLineError,
    Reader,
    Writer,
    open,
    open_append,
)

__all__ = [
    "Error",
    "IncompleteLastLineError",
    "InvalidLineError",
    "Reader",
    "Writer",
    "open",
    "open_append",
]
