class TablibException(Exception):
    """Tablib common exception."""


class InvalidDatasetType(TablibException, TypeError):
    """Only Datasets can be added to a Databook."""


class InvalidDimensions(TablibException, ValueError):
    """The size of the column or row doesn't fit the table dimensions."""


class InvalidDatasetIndex(TablibException, IndexError):
    """Outside of Dataset size."""


class HeadersNeeded(TablibException, AttributeError):
    """Header parameter must be given when appending a column to this Dataset."""


class UnsupportedFormat(TablibException, NotImplementedError):
    """Format not supported."""


class DuplicateHeaders(TablibException, ValueError):
    """The Dataset has duplicate headers that cannot be represented uniquely.

    Raised by export formats relying on unique object keys (such as JSON,
    YAML or DataFrame) when :attr:`Dataset.headers` contains duplicates and no
    ``column_keys`` override has been supplied for the export.
    """


class InvalidColumnKey(TablibException, KeyError):
    """A column key does not exist or is not unique within the Dataset."""
