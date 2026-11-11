# -*- coding: utf-8 -*-

from typing import Any
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Literal
from typing import Optional
from typing import Tuple
from typing import Union
from typing import overload

from .dialect import SimpleDialect

def field_size_limit(*args: Any, **kwargs: Any) -> int: ...
@overload
def _parse_data(
    data: Iterable[str],
    delimiter: str,
    quotechar: str,
    escapechar: str,
    strict: bool,
    return_quoted: Literal[False] = ...,
    comment: Optional[str] = ...,
) -> Iterator[List[str]]: ...
@overload
def _parse_data(
    data: Iterable[str],
    delimiter: str,
    quotechar: str,
    escapechar: str,
    strict: bool,
    return_quoted: Literal[True],
    comment: Optional[str] = ...,
) -> Iterator[List[Tuple[str, bool]]]: ...
@overload
def _parse_data(
    data: Iterable[str],
    delimiter: str,
    quotechar: str,
    escapechar: str,
    strict: bool,
    return_quoted: bool = ...,
    comment: Optional[str] = ...,
) -> Iterator[Union[List[str], List[Tuple[str, bool]]]]: ...
def parse_data(
    data: Iterable[str],
    dialect: Optional[SimpleDialect] = None,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    escapechar: Optional[str] = None,
    strict: Optional[bool] = None,
    return_quoted: bool = False,
    comment: Optional[str] = None,
) -> Iterator[Union[List[str], List[Tuple[str, bool]]]]: ...
@overload
def parse_string(
    data: str,
    dialect: SimpleDialect,
    return_quoted: Literal[False] = ...,
    comment: Optional[str] = ...,
) -> Iterator[List[str]]: ...
@overload
def parse_string(
    data: str,
    dialect: SimpleDialect,
    return_quoted: Literal[True],
    comment: Optional[str] = ...,
) -> Iterator[List[Tuple[str, bool]]]: ...
@overload
def parse_string(
    data: str,
    dialect: SimpleDialect,
    return_quoted: bool = ...,
    comment: Optional[str] = ...,
) -> Iterator[Union[List[str], List[Tuple[str, bool]]]]: ...
