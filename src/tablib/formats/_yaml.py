""" Tablib - YAML Support
"""

__lazy_modules__ = {"yaml"}

import yaml

import tablib


class YAMLFormat:
    title = 'yaml'
    extensions = ('yaml', 'yml')

    @classmethod
    def export_set(cls, dataset, column_keys=None):
        """Returns YAML representation of Dataset.

        YAML mappings require unique keys. When the Dataset has duplicate
        display headers, the stable column keys are used instead of silently
        overwriting a column. Pass ``column_keys`` to provide caller-chosen
        unique names (one per column).
        """
        keys = dataset._resolve_export_keys(column_keys)
        return yaml.safe_dump(
            dataset._package(key_headers=keys),
            default_flow_style=None,
            allow_unicode=True,
            sort_keys=False,
        )

    @classmethod
    def export_book(cls, databook):
        """Returns YAML representation of Databook."""
        return yaml.safe_dump(
            databook._package(),
            default_flow_style=None,
            allow_unicode=True,
            sort_keys=False,
        )

    @classmethod
    def import_set(cls, dset, in_stream):
        """Returns dataset from YAML stream."""

        dset.wipe()
        dset.dict = yaml.safe_load(in_stream)

    @classmethod
    def import_book(cls, dbook, in_stream):
        """Returns databook from YAML stream."""

        dbook.wipe()

        for sheet in yaml.safe_load(in_stream):
            data = tablib.Dataset()
            data.title = sheet['title']
            data.dict = sheet['data']
            dbook.add_sheet(data)

    @classmethod
    def detect(cls, stream):
        """Returns True if given stream is valid YAML."""
        try:
            _yaml = yaml.safe_load(stream)
            if isinstance(_yaml, (list, tuple, dict)):
                return True
            else:
                return False
        except (yaml.parser.ParserError, yaml.reader.ReaderError,
                yaml.scanner.ScannerError):
            return False
