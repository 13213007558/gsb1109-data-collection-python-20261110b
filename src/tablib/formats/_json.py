""" Tablib - JSON Support
"""

__lazy_modules__ = {"decimal", "json", "uuid"}

import decimal
import json
from uuid import UUID

import tablib


def serialize_objects_handler(obj):
    if isinstance(obj, (decimal.Decimal, UUID)):
        return str(obj)
    elif hasattr(obj, 'isoformat'):
        return obj.isoformat()
    else:
        return obj


class JSONFormat:
    title = 'json'
    extensions = ('json', 'jsn')

    @classmethod
    def export_set(cls, dataset, column_keys=None):
        """Returns JSON representation of Dataset.

        JSON objects require unique keys. When the Dataset has duplicate
        display headers, the stable column keys are used instead of silently
        overwriting a column. Pass ``column_keys`` to provide caller-chosen
        unique names (one per column); a non-unique or wrong-sized value
        raises :class:`tablib.DuplicateHeaders` /
        :class:`tablib.InvalidDimensions`.
        """
        keys = dataset._resolve_export_keys(column_keys)
        return json.dumps(
            dataset._package(key_headers=keys),
            default=serialize_objects_handler,
            ensure_ascii=False,
        )

    @classmethod
    def export_book(cls, databook):
        """Returns JSON representation of Databook.

        Sheets with duplicate headers fall back to their stable column keys
        instead of overwriting one column with another.
        """
        return json.dumps(
            databook._package(),
            default=serialize_objects_handler,
            ensure_ascii=False,
        )

    @classmethod
    def import_set(cls, dset, in_stream):
        """Returns dataset from JSON stream."""

        dset.wipe()
        dset.dict = json.load(in_stream)

    @classmethod
    def import_book(cls, dbook, in_stream):
        """Returns databook from JSON stream."""

        dbook.wipe()
        for sheet in json.load(in_stream):
            data = tablib.Dataset()
            data.title = sheet['title']
            data.dict = sheet['data']
            dbook.add_sheet(data)

    @classmethod
    def detect(cls, stream):
        """Returns True if given stream is valid JSON."""
        try:
            json.load(stream)
            return True
        except (TypeError, ValueError):
            return False
