"""
This file should give any type checking errors.
"""

import io
import json
import random

import numbers
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional


if not TYPE_CHECKING:

    def reveal_type(obj: Any) -> None:
        pass


import jsonlines


def something_with_reader() -> None:
    reader: jsonlines.Reader
    reader = jsonlines.Reader(io.StringIO())
    reader = jsonlines.Reader(io.BytesIO())
    reader = jsonlines.Reader(['"text"'])
    reader = jsonlines.Reader([b'"bytes"'])

    r1 = reader.read()
    r2 = reader.read(allow_none=True)
    r3: numbers.Number = reader.read(type=random.choice([int, float]))

    # For debugging:
    # reveal_type(r1)
    # reveal_type(r2)
    # reveal_type(r3)

    some_int: int = reader.read(type=int)
    maybe_int: Optional[int] = reader.read(type=int, allow_none=True)

    some_float: float = reader.read(type=float)
    maybe_float: Optional[float] = reader.read(type=float, allow_none=True)

    some_bool: bool = reader.read(type=bool)
    maybe_bool: Optional[bool] = reader.read(type=bool, allow_none=True)

    some_dict: Dict[str, Any] = reader.read(type=dict)
    optional_dict: Optional[Dict[str, Any]] = reader.read(type=dict, allow_none=True)

    some_list: List[Any] = reader.read(type=list)
    maybe_list: Optional[List[Any]] = reader.read(type=list, allow_none=True)

    iter_int: Iterable[int] = reader.iter(type=int)
    iter_str: Iterable[str] = reader.iter(type=str)
    iter_dict: Iterable[Dict[str, Any]] = reader.iter(type=dict)
    iter_optional_str: Iterable[Optional[str]] = reader.iter(type=str, allow_none=True)

    locals()  # Silence flake8 F841


def something_with_writer() -> None:
    writer: jsonlines.Writer
    writer = jsonlines.Writer(io.StringIO())
    writer = jsonlines.Writer(io.BytesIO())

    locals()  # Silence flake8 F841


def something_with_indexed_reader() -> None:
    name = "/nonexistent"

    indexed: jsonlines.IndexedReader
    indexed = jsonlines.index(name)
    indexed = jsonlines.index(name, loads=json.loads, skip_empty=True)
    indexed = jsonlines.IndexedReader(name)
    indexed = jsonlines.IndexedReader(name, loads=json.loads)

    count: int = len(indexed)
    record: jsonlines.IndexedRecord = indexed.get(0)
    record = indexed.read_at(0, type=dict, allow_none=True)
    record_index: int = record.record
    record_lineno: int = record.lineno
    record_offset: int = record.offset
    record_value: object = record.value

    for each in indexed.iter_all():
        each_lineno: int = each.lineno
        locals()  # Silence flake8 F841

    indexed.rebuild()
    locals()  # Silence flake8 F841


def something_with_open() -> None:
    name = "/nonexistent"

    reader: jsonlines.Reader
    reader = jsonlines.open(name)
    reader = jsonlines.open(name, "r")
    reader = jsonlines.open(name, mode="r")
    reader = jsonlines.open(
        name,
        mode="r",
        loads=json.loads,
    )

    writer: jsonlines.Writer
    writer = jsonlines.open(name, "w")
    writer = jsonlines.open(name, mode="w")
    writer = jsonlines.open(name, "a")
    writer = jsonlines.open(
        name,
        mode="w",
        dumps=json.dumps,
        compact=True,
        sort_keys=True,
        flush=True,
    )

    locals()  # Silence flake8 F841
