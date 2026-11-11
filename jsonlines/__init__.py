"""
Module for the jsonlines data format.
"""

# expose only public api
from .jsonlines import (
    Error,
    InvalidLineError,
    OversizedRecordError,
    Reader,
    ShardInfo,
    Writer,
    open,
    write_shards,
)

__all__ = [
    "Error",
    "InvalidLineError",
    "OversizedRecordError",
    "Reader",
    "ShardInfo",
    "Writer",
    "open",
    "write_shards",
]
