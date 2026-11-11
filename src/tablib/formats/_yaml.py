""" Tablib - YAML Support.
"""

__lazy_modules__ = {"yaml"}

import yaml

import tablib


class _TablibDumper(yaml.SafeDumper):
    """SafeDumper that serializes the tablib.MISSING sentinel as a string,
    keeping it distinct from empty (null) values."""


_TablibDumper.add_representer(
    type(tablib.MISSING),
    lambda dumper, data: dumper.represent_str(str(data)),
)


class YAMLFormat:
    title = 'yaml'
    extensions = ('yaml', 'yml')

    @classmethod
    def export_set(cls, dataset):
        """Returns YAML representation of Dataset."""
        return yaml.dump(
            dataset._package(),
            Dumper=_TablibDumper,
            default_flow_style=None,
            allow_unicode=True,
            sort_keys=False,
        )

    @classmethod
    def export_book(cls, databook):
        """Returns YAML representation of Databook."""
        return yaml.dump(
            databook._package(),
            Dumper=_TablibDumper,
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
