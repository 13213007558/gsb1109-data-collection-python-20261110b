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
    """The Dataset contains repeated header names, so columns cannot be
    aligned by name. The caller must rename or remove the duplicates
    first."""


class UnexpectedColumns(TablibException, ValueError):
    """The incoming Dataset declares columns that the target Dataset is
    configured to reject."""
