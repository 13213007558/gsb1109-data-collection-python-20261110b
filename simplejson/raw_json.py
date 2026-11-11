"""Implementation of RawJSON
"""

class RawJSON(object):
    """Wrap an encoded JSON document for direct embedding in the output

    Also the type of the values produced when decoding with the
    *preserve_raw* option of :class:`simplejson.JSONDecoder`: the
    original source text of each preserved value is available verbatim
    as ``encoded_json``. RawJSON is not a ``str`` subclass, so preserved
    values can be told apart from decoded strings with ``isinstance``.

    """
    def __init__(self, encoded_json):
        self.encoded_json = encoded_json

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self.encoded_json)
