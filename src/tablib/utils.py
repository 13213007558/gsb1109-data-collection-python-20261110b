__lazy_modules__ = {"io"}

from io import BytesIO, StringIO


class _Missing:
    """Sentinel marking a cell whose column does not exist in the source
    Dataset. It is intentionally distinct from ``None``, which represents a
    column that exists but holds an empty value."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return '<MISSING>'

    def __bool__(self):
        return False

    def __reduce__(self):
        return (MISSING.__class__, ())


MISSING = _Missing()


def normalize_input(stream):
    """
    Accept either a str/bytes stream or a file-like object and always return a
    file-like object.
    """
    if isinstance(stream, str):
        return StringIO(stream, newline='')
    elif isinstance(stream, bytes):
        return BytesIO(stream)
    return stream
