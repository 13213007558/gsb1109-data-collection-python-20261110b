""" Tablib - DataFrame Support
"""

__lazy_modules__ = {"pandas"}

try:
    from pandas import DataFrame
except ImportError:
    DataFrame = None


class DataFrameFormat:
    title = 'df'
    extensions = ('df',)

    @classmethod
    def detect(cls, stream):
        """Returns True if given stream is a DataFrame."""
        if DataFrame is None:
            return False
        elif isinstance(stream, DataFrame):
            return True
        try:
            DataFrame(stream.read())
            return True
        except ValueError:
            return False

    @classmethod
    def export_set(cls, dset, column_keys=None):
        """Returns DataFrame representation of DataBook.

        A DataFrame built from dictionaries cannot hold two columns with the
        same label, so duplicate display headers are resolved to the stable
        column keys instead of overwriting a column. Pass ``column_keys`` to
        provide caller-chosen unique column names (one per column).
        """
        if DataFrame is None:
            raise NotImplementedError(
                'DataFrame Format requires `pandas` to be installed.'
                ' Try `pip install "tablib[pandas]"`.')
        if dset.headers is None:
            return DataFrame(dset.dict)

        keys = dset._resolve_export_keys(column_keys)
        records = dset._package(key_headers=keys)
        return DataFrame(records, columns=keys if keys is not None else dset.headers)

    @classmethod
    def import_set(cls, dset, in_stream):
        """Returns dataset from DataFrame."""
        dset.wipe()
        dset.dict = in_stream.to_dict(orient='records')
