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


class FormatterError(TablibException):
    """Raised when a cell formatter fails.

    The original exception raised by the formatter is preserved as the
    exception cause (``__cause__``).

    :param row_number: 1-based position of the data row within the dataset.
    :param col: 0-based column index, or ``None`` when no column is involved.
    :param header: column header name, when the dataset has headers.
    """

    def __init__(self, row_number, col, header=None):
        self.row_number = row_number
        self.col = col
        self.header = header

        location = f'row {row_number}'
        if col is not None:
            location += f', column {col}'
            if header is not None:
                location += f" ('{header}')"

        super().__init__(f'Formatter failed at {location}.')
