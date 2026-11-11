'''
Pure-python parsing backend.
'''
from json.decoder import scanstring
import re

from ijson import common, utils
import codecs


LEXEME_RE = re.compile(r'[a-z0-9eE\.\+-]+|\S')
INTEGER_RE = re.compile(r'-?[0-9]+')
UNARY_LEXEMES = set('[]{},')
# Unescaped control characters are not allowed inside JSON strings, and the
# standard scanstring-based path rejects them; the streaming decoder must do
# the same rather than silently letting them through
RAW_CONTROL_RE = re.compile(r'[\x00-\x1f]')
EOF = -1, None

# Markers exchanged between the Lexer and parse_value when string chunking
# is enabled. A streaming string is a STRING_START marker, zero or more
# STRING_DATA markers carrying raw (still escaped) string contents, and a
# final STRING_END marker. Contents never include the surrounding quotes.
STRING_START = 0
STRING_DATA = 1
STRING_END = 2

_SIMPLE_ESCAPES = {
    '"': '"',
    '\\': '\\',
    '/': '/',
    'b': '\b',
    'f': '\f',
    'n': '\n',
    'r': '\r',
    't': '\t',
}


class UnexpectedSymbol(common.JSONError):
    def __init__(self, symbol, pos):
        super(UnexpectedSymbol, self).__init__(
            'Unexpected symbol %r at %d' % (symbol, pos)
        )


class IncrementalStringDecoder:
    '''
    Incrementally decodes the contents of a JSON string (without its
    surrounding quotes) into fully-decoded Python text.

    Input chunks are decoded as far as safely possible, while anything that
    can only be decoded with more input is retained internally:

    - a dangling backslash or an incomplete ``\\uXXXX`` escape sequence;
    - a high surrogate waiting for its matching low surrogate.

    Callers therefore never receive half-decoded output, and chunks can be
    split at arbitrary byte (and hence character) boundaries.
    '''

    def __init__(self):
        self._pending_escape = ''
        self._high_surrogate = 0

    def feed(self, content, final=False):
        data = self._pending_escape + content
        self._pending_escape = ''
        output = []
        append = output.append
        index = 0
        length = len(data)

        def emit(code_unit):
            if 0xD800 <= code_unit <= 0xDBFF:
                if self._high_surrogate:
                    append(chr(self._high_surrogate))
                self._high_surrogate = code_unit
            elif 0xDC00 <= code_unit <= 0xDFFF and self._high_surrogate:
                high = self._high_surrogate
                self._high_surrogate = 0
                append(chr(0x10000 + ((high - 0xD800) << 10) +
                           (code_unit - 0xDC00)))
            else:
                if self._high_surrogate:
                    append(chr(self._high_surrogate))
                    self._high_surrogate = 0
                append(chr(code_unit))

        while index < length:
            char = data[index]
            if char != '\\':
                # Emit the whole plain run at once; only its first character
                # can complete a surrogate held back from the previous chunk
                next_escape = data.find('\\', index + 1)
                if next_escape == -1:
                    run = data[index:]
                    index = length
                else:
                    run = data[index:next_escape]
                    index = next_escape
                # raw (unescaped) control characters are invalid JSON
                control = RAW_CONTROL_RE.search(run)
                if control:
                    raise common.JSONError(
                        'Invalid control character 0x%02x in string' %
                        ord(control.group()))
                if self._high_surrogate:
                    emit(ord(run[0]))
                    run = run[1:]
                if run:
                    append(run)
                continue

            # A backslash at the very end might start an escape continued
            # in the next chunk; keep it and resume there
            if index + 1 >= length:
                self._pending_escape = data[index:]
                break

            escaped = data[index + 1]
            if escaped != 'u':
                decoded = _SIMPLE_ESCAPES.get(escaped)
                if decoded is None:
                    raise common.JSONError(
                        'Invalid escape character: \\%s' % (escaped,))
                emit(ord(decoded))
                index += 2
                continue

            # A \uXXXX escape needs four hex digits; retain an incomplete
            # suffix so the remaining digits can arrive in the next chunk
            hex_digits = data[index + 2:index + 6]
            if len(hex_digits) < 4:
                self._pending_escape = data[index:]
                break
            try:
                code = int(hex_digits, 16)
            except ValueError:
                raise common.JSONError(
                    'Invalid \\uXXXX escape sequence: \\u%s' % (hex_digits,))
            emit(code)
            index += 6

        if final:
            if self._pending_escape:
                raise common.JSONError(
                    'Invalid string escape sequence: %s' %
                    (self._pending_escape,))
            if self._high_surrogate:
                append(chr(self._high_surrogate))
                self._high_surrogate = 0
        return ''.join(output)


def _send_string_chunks(send, text, chunk_size):
    '''Sends decoded text bounded by chunk_size, never emitting empty chunks'''
    if not text:
        return
    for start in range(0, len(text), chunk_size):
        send(('string', text[start:start + chunk_size]))


def _streaming_string(send, buf, pos, discarded):
    '''
    Generator driven via ``yield from`` from the Lexer. ``pos`` points at the
    opening quote of a JSON string. It emits STRING_START/STRING_DATA/
    STRING_END markers containing the raw (still escaped) contents of the
    string without its surrounding quotes, and returns the updated
    ``(buf, pos, discarded)`` lexer state.

    Escape sequences are always forwarded whole (never split across markers),
    but their raw ``\\...`` text is preserved so the incremental decoder can
    interpret them. Already-consumed buffer prefixes are discarded while
    waiting for more data, so a long string only keeps the current input
    chunk plus at most one truncated escape sequence in memory.
    '''
    send((discarded + pos, STRING_START))
    content_pos = pos + 1
    while True:
        quote = buf.find('"', content_pos)
        slash = buf.find('\\', content_pos)

        # The closing quote comes first, or there is no backslash before
        # either the quote or the end of the available buffer
        if slash == -1 or (quote != -1 and quote < slash):
            if quote == -1:
                if content_pos < len(buf):
                    send((discarded + content_pos,
                          (STRING_DATA, buf[content_pos:])))
                discarded += len(buf)
                buf = ''
                try:
                    data = yield
                except GeneratorExit:
                    data = ''
                if not data:
                    raise common.IncompleteJSONError('Incomplete string lexeme')
                buf = data
                content_pos = 0
                continue
            if quote > content_pos:
                send((discarded + content_pos,
                      (STRING_DATA, buf[content_pos:quote])))
            send((discarded + quote, STRING_END))
            return buf, quote + 1, discarded

        # A backslash is the next special character; forward the plain run
        # before it, then make sure the whole escape is available so it can
        # be sent as a single piece of raw content
        if slash > content_pos:
            send((discarded + content_pos,
                  (STRING_DATA, buf[content_pos:slash])))
        needed = slash + (6 if buf[slash + 1:slash + 2] == 'u' else 2)
        if len(buf) < needed:
            discarded += slash
            buf = buf[slash:]
            try:
                data = yield
            except GeneratorExit:
                data = ''
            if not data:
                raise common.IncompleteJSONError('Incomplete string lexeme')
            buf += data
            content_pos = 0
            continue
        send((discarded + slash, (STRING_DATA, buf[slash:needed])))
        content_pos = needed


@utils.coroutine
def utf8_encoder(target):
    decoder = codecs.getincrementaldecoder('utf-8')()
    decode = decoder.decode
    send = target.send
    while True:
        try:
            final = False
            bdata = (yield)
        except GeneratorExit:
            final = True
            bdata = b''
        try:
            sdata = decode(bdata, final)
        except UnicodeDecodeError as e:
            try:
                target.close()
            except:
                pass
            raise common.IncompleteJSONError(e)
        except:
            try:
                target.close()
            except:
                pass
            raise
        if sdata:
            send(sdata)
        elif not bdata:
            target.close()
            break

@utils.coroutine
def Lexer(target, chunk_strings=False):
    """
    Parses lexemes out of the incoming content, and sends them to parse_value.
    A special EOF result is sent when the data source has been exhausted to
    give parse_value the possibility of raising custom exceptions due to missing
    content.

    When ``chunk_strings`` is true the contents of JSON strings are delivered
    incrementally as STRING_START/STRING_DATA/STRING_END markers instead of
    being accumulated into a single complete lexeme.
    """
    try:
        data = (yield)
    except GeneratorExit:
        data = ''
    buf = data
    pos = 0
    discarded = 0
    send = target.send
    while True:
        match = LEXEME_RE.search(buf, pos)
        if match:
            lexeme = match.group()
            if lexeme == '"':
                pos = match.start()
                if chunk_strings:
                    buf, pos, discarded = yield from _streaming_string(
                        send, buf, pos, discarded)
                    continue
                start = pos + 1
                while True:
                    try:
                        end = buf.index('"', start)
                        escpos = end - 1
                        while buf[escpos] == '\\':
                            escpos -= 1
                        if (end - escpos) % 2 == 0:
                            start = end + 1
                        else:
                            break
                    except ValueError:
                        try:
                            data = (yield)
                        except GeneratorExit:
                            data = ''
                        if not data:
                            raise common.IncompleteJSONError('Incomplete string lexeme')
                        buf += data
                send((discarded + pos, buf[pos:end + 1]))
                pos = end + 1
            else:
                while lexeme not in UNARY_LEXEMES and match.end() == len(buf):
                    try:
                        data = (yield)
                    except GeneratorExit:
                        data = ''
                    if not data:
                        break
                    buf += data
                    match = LEXEME_RE.search(buf, pos)
                    lexeme = match.group()
                send((discarded + match.start(), lexeme))
                pos = match.end()
        else:
            # Don't ask data from an already exhausted source
            if data:
                try:
                    data = (yield)
                except GeneratorExit:
                    data = ''
            if not data:
                # Normally should raise StopIteration, but can raise
                # IncompleteJSONError too, which is the point of sending EOF
                try:
                    target.send(EOF)
                except StopIteration:
                    pass
                break
            discarded += len(buf)
            buf = data
            pos = 0


# Parsing states
_PARSE_VALUE = 0
_PARSE_ARRAY_ELEMENT_END = 1
_PARSE_OBJECT_KEY = 2
_PARSE_OBJECT_END = 3

# infinity singletons for overflow checks
inf = float("inf")
neg_inf = float("-inf")

@utils.coroutine
def parse_value(target, multivalue, use_float, chunk_strings=False,
                string_chunk_size=64 * 1024):
    """
    Parses results coming out of the Lexer into ijson events, which are sent to
    `target`. A stack keeps track of the type of object being parsed at the time
    (a value, and object or array -- the last two being values themselves).

    A special EOF result coming from the Lexer indicates that no more content is
    expected. This is used to check for incomplete content and raise the
    appropriate exception, which wouldn't be possible if the Lexer simply closed
    this co-routine (either explicitly via .close(), or implicitly by itself
    finishing and decreasing the only reference to the co-routine) since that
    causes a GeneratorExit exception that cannot be replaced with a custom one.

    When ``chunk_strings`` is true the Lexer delivers string values as
    STRING_START/STRING_DATA/STRING_END markers; the contents are decoded
    incrementally and sent as several ``string`` events, each no longer than
    ``string_chunk_size`` characters, followed by a ``string_end`` event
    marking the boundary of the complete value. A malformed or truncated
    string raises instead, so ``string_end`` is only seen for values that
    finished successfully. Map keys are still rebuilt into a single
    ``map_key`` event.
    """

    state_stack = [_PARSE_VALUE]
    pop = state_stack.pop
    push = state_stack.append
    send = target.send
    prev_pos, prev_symbol = None, None
    to_number = common.integer_or_float if use_float else common.integer_or_decimal

    def read_streaming_string():
        '''Consumes the markers of one streamed string value'''
        decoder = IncrementalStringDecoder()
        while True:
            _pos, marker = yield
            if marker == STRING_END:
                _send_string_chunks(send, decoder.feed('', final=True),
                                    string_chunk_size)
                send(('string_end', None))
                return
            _send_string_chunks(send, decoder.feed(marker[1]),
                                string_chunk_size)

    while True:

        if prev_pos is None:
            pos, symbol = (yield)
            if (pos, symbol) == EOF:
                if state_stack:
                    raise common.IncompleteJSONError('Incomplete JSON content')
                break
        else:
            pos, symbol = prev_pos, prev_symbol
            prev_pos, prev_symbol = None, None
        try:
            state = state_stack[-1]
        except IndexError:
            if multivalue:
                state = _PARSE_VALUE
                push(state)
            else:
                raise common.JSONError('Additional data found')
        assert state_stack

        if state == _PARSE_VALUE:
            # Streamed string value: decode its markers as they arrive
            if chunk_strings and symbol == STRING_START:
                yield from read_streaming_string()
                pop()
                continue
            # Simple, common cases
            if symbol == 'null':
                send(('null', None))
                pop()
            elif symbol == 'true':
                send(('boolean', True))
                pop()
            elif symbol == 'false':
                send(('boolean', False))
                pop()
            elif symbol[0] == '"':
                send(('string', parse_string(symbol)))
                pop()
            # Array start
            elif symbol == '[':
                send(('start_array', None))
                pos, symbol = (yield)
                if (pos, symbol) == EOF:
                    raise common.IncompleteJSONError('Incomplete JSON content')
                if symbol == ']':
                    send(('end_array', None))
                    pop()
                else:
                    prev_pos, prev_symbol = pos, symbol
                    push(_PARSE_ARRAY_ELEMENT_END)
                    push(_PARSE_VALUE)
            # Object start
            elif symbol == '{':
                send(('start_map', None))
                pos, symbol = (yield)
                if (pos, symbol) == EOF:
                    raise common.IncompleteJSONError('Incomplete JSON content')
                if symbol == '}':
                    send(('end_map', None))
                    pop()
                else:
                    prev_pos, prev_symbol = pos, symbol
                    push(_PARSE_OBJECT_KEY)
            # A number
            else:
                # JSON numbers can't contain leading zeros
                if ((len(symbol) > 1 and symbol[0] == '0' and symbol[1] not in ('e', 'E', '.')) or
                    (len(symbol) > 2 and symbol[0:2] == '-0' and symbol[2] not in ('e', 'E', '.'))):
                    raise common.JSONError('Invalid JSON number: %s' % (symbol,))
                # JSON numbers can't start with a plus sign
                if symbol[0] == '+':
                    raise common.JSONError(f'Invalid JSON number: {symbol}')
                # Fractions need a leading digit and must be followed by a digit
                dot = symbol.find('.')
                if dot != -1 and not (symbol[dot - 1:dot].isdigit() and symbol[dot + 1:dot + 2].isdigit()):
                    raise common.JSONError('Invalid JSON number: %s' % (symbol,))
                try:
                    number = to_number(symbol)
                    if number == inf or number == neg_inf:
                        raise common.JSONError("float overflow: %s" % (symbol,))
                except:
                    if 'true'.startswith(symbol) or 'false'.startswith(symbol) or 'null'.startswith(symbol):
                        raise common.IncompleteJSONError('Incomplete JSON content')
                    # a well-formed integer that int() still refused is one
                    # longer than sys.get_int_max_str_digits(); let that
                    # error through as the other backends do
                    if INTEGER_RE.fullmatch(symbol):
                        raise
                    raise UnexpectedSymbol(symbol, pos)
                else:
                    send(('number', number))
                    pop()

        elif state == _PARSE_OBJECT_KEY:
            if chunk_strings and symbol == STRING_START:
                # Keys are always delivered whole, even when values are
                # streamed, so their markers are reassembled here
                decoder = IncrementalStringDecoder()
                key_parts = []
                while True:
                    _pos, marker = (yield)
                    if marker == STRING_END:
                        key_parts.append(decoder.feed('', final=True))
                        break
                    key_parts.append(decoder.feed(marker[1]))
                symbol = '"'
                key = ''.join(key_parts)
                send(('map_key', key))
                pos, symbol = (yield)
                if (pos, symbol) == EOF:
                    raise common.IncompleteJSONError('Incomplete JSON content')
                if symbol != ':':
                    raise UnexpectedSymbol(symbol, pos)
                state_stack[-1] = _PARSE_OBJECT_END
                push(_PARSE_VALUE)
            else:
                if symbol[0] != '"':
                    raise UnexpectedSymbol(symbol, pos)
                send(('map_key', parse_string(symbol)))
                pos, symbol = (yield)
                if (pos, symbol) == EOF:
                    raise common.IncompleteJSONError('Incomplete JSON content')
                if symbol != ':':
                    raise UnexpectedSymbol(symbol, pos)
                state_stack[-1] = _PARSE_OBJECT_END
                push(_PARSE_VALUE)

        elif state == _PARSE_OBJECT_END:
            if symbol == ',':
                state_stack[-1] = _PARSE_OBJECT_KEY
            elif symbol != '}':
                raise UnexpectedSymbol(symbol, pos)
            else:
                send(('end_map', None))
                pop()
                pop()

        elif state == _PARSE_ARRAY_ELEMENT_END:
            if symbol == ',':
                state_stack[-1] = _PARSE_ARRAY_ELEMENT_END
                push(_PARSE_VALUE)
            elif symbol != ']':
                raise UnexpectedSymbol(symbol, pos)
            else:
                send(('end_array', None))
                pop()
                pop()


def parse_string(symbol):
    return scanstring(symbol, 1)[0]


def basic_parse_basecoro(target, multiple_values=False, allow_comments=False,
                         use_float=False, chunk_strings=False,
                         string_chunk_size=64 * 1024):
    '''
    Iterator yielding unprefixed events.

    Parameters:

    - file: a readable file-like object with JSON input
    - chunk_strings: deliver the decoded contents of string values as a
      sequence of ``string`` events instead of a single one
    - string_chunk_size: maximum number of characters of each delivered
      string chunk when ``chunk_strings`` is enabled
    '''
    if allow_comments:
        raise ValueError("Comments are not supported by the python backend")
    if chunk_strings:
        if not isinstance(string_chunk_size, int) or string_chunk_size <= 0:
            raise ValueError(
                "string_chunk_size must be a positive integer, got %r" %
                (string_chunk_size,))
    return utf8_encoder(Lexer(
        parse_value(target, multiple_values, use_float,
                    chunk_strings=chunk_strings,
                    string_chunk_size=string_chunk_size),
        chunk_strings=chunk_strings))


common.enrich_backend(globals(), c_comments=False, string_chunking=True)
