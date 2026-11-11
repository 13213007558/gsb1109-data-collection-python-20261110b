"""
    tablib.display
    ~~~~~~~~~~~~~~

    Display conventions that can be attached to a single export without
    mutating the underlying :class:`~tablib.Dataset`.

    A :class:`DisplayConfig` holds column formatters that are applied on top of
    the formatters registered with :meth:`Dataset.add_formatter
    <tablib.Dataset.add_formatter>`. Dataset-level formatters run first, then
    the per-export formatters; the raw values stored in the dataset are never
    changed.

    :copyright: (c) 2016 by Kenneth Reitz. 2019 Jazzband.
    :license: MIT, see LICENSE for more details.
"""

__all__ = ['DisplayConfig']


class DisplayConfig:
    """Per-export column formatting rules.

    Formatters are registered per column (by index or header name) or for all
    columns at once. They only affect a single export call, e.g.::

        display = tablib.DisplayConfig()
        display.add_formatter('price', lambda value: f'${value:,.2f}')
        csv_text = data.export('csv', display=display)

    The same :class:`DisplayConfig` can be reused for several exports and is
    safe to share between threads, as it never mutates dataset data.

    :param formatters: optional mapping of column index/header to formatter
                       callable.
    """

    def __init__(self, formatters=None):
        # (column, callback) tuples; column is None for all-column formatters
        self._formatters = []
        if formatters:
            for col, callback in formatters.items():
                self.add_formatter(col, callback)

    def add_formatter(self, col, handler):
        """Register a formatter for one export.

        :param col: column index (int), header name (str), or ``None`` to
                    apply the formatter to every column.
        :param handler: callback executed against each cell value.
        """
        if col is not None and not isinstance(col, (int, str)):
            raise TypeError(
                'Column must be an index, a header name, or None for all columns.'
            )
        if not callable(handler):
            raise TypeError('Formatter handler must be callable.')

        self._formatters.append((col, handler))
        return self

    def resolved_formatters(self, headers):
        """Return ``(col_index, callback)`` tuples for the given headers.

        Header names are resolved to column indices. Raises :class:`KeyError`
        if a formatter references a header that is not present.
        """
        resolved = []
        for col, callback in self._formatters:
            if isinstance(col, str):
                if headers is None or col not in headers:
                    raise KeyError(f"Unknown header '{col}'.")
                col = headers.index(col)
            resolved.append((col, callback))
        return resolved

    def __bool__(self):
        return bool(self._formatters)
