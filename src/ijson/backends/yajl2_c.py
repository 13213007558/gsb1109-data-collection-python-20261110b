#
# Contributed by Rodrigo Tobar <rtobar@icrar.org>
#
# ICRAR - International Centre for Radio Astronomy Research
# (c) UWA - The University of Western Australia, 2016
# Copyright by UWA (in the framework of the ICRAR)
#
'''
Wrapper for _yajl2 C extension module
'''

from ijson import common, compat, utils
from . import _yajl2


_get_buf_size = lambda kwargs: kwargs.pop('buf_size', 64 * 1024)


def _check_string_chunking(kwargs):
    if kwargs.get('chunk_strings'):
        raise ValueError(
            "The yajl2_c backend cannot deliver strings in chunks: yajl "
            "only invokes its string callback once the whole string has "
            "been decoded. Use the python backend, which streams strings "
            "incrementally")
    # irrelevant when chunking is off; drop it so the C extension never
    # receives a keyword argument it does not know about
    kwargs.pop('string_chunk_size', None)


@utils.coroutine
def basic_parse_basecoro(target, **kwargs):
    _check_string_chunking(kwargs)
    return _yajl2.basic_parse_basecoro(target.send, **kwargs)

def basic_parse_gen(file, **kwargs):
    _check_string_chunking(kwargs)
    f = compat.bytes_reader(file)
    buf_size = _get_buf_size(kwargs)
    return _yajl2.basic_parse(f, buf_size, **kwargs)

def basic_parse_async(file, **kwargs):
    _check_string_chunking(kwargs)
    buf_size = _get_buf_size(kwargs)
    return _yajl2.basic_parse_async(file, buf_size, **kwargs)

@utils.coroutine
def parse_basecoro(target, **kwargs):
    _check_string_chunking(kwargs)
    return _yajl2.parse_basecoro(target.send, **kwargs)

def parse_gen(file, **kwargs):
    _check_string_chunking(kwargs)
    f = compat.bytes_reader(file)
    buf_size = _get_buf_size(kwargs)
    return _yajl2.parse(f, buf_size, **kwargs)

def parse_async(file, **kwargs):
    _check_string_chunking(kwargs)
    buf_size = _get_buf_size(kwargs)
    return _yajl2.parse_async(file, buf_size, **kwargs)

@utils.coroutine
def kvitems_basecoro(target, prefix, map_type=None, **kwargs):
    _check_string_chunking(kwargs)
    return _yajl2.kvitems_basecoro(target.send, prefix, map_type, **kwargs)

def kvitems_gen(file, prefix, map_type=None, **kwargs):
    _check_string_chunking(kwargs)
    f = compat.bytes_reader(file)
    buf_size = _get_buf_size(kwargs)
    return _yajl2.kvitems(f, buf_size, prefix, map_type, **kwargs)

def kvitems_async(file, prefix, map_type=None, **kwargs):
    _check_string_chunking(kwargs)
    buf_size = _get_buf_size(kwargs)
    return _yajl2.kvitems_async(file, buf_size, prefix, map_type, **kwargs)

@utils.coroutine
def items_basecoro(target, prefix, map_type=None, **kwargs):
    _check_string_chunking(kwargs)
    return _yajl2.items_basecoro(target.send, prefix, map_type, **kwargs)

def items_gen(file, prefix, map_type=None, **kwargs):
    _check_string_chunking(kwargs)
    f = compat.bytes_reader(file)
    buf_size = _get_buf_size(kwargs)
    return _yajl2.items(f, buf_size, prefix, map_type, **kwargs)

def items_async(file, prefix, map_type=None, **kwargs):
    _check_string_chunking(kwargs)
    buf_size = _get_buf_size(kwargs)
    return _yajl2.items_async(file, buf_size, prefix, map_type, **kwargs)

common.enrich_backend(globals())
