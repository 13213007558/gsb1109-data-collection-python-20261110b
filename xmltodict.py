#!/usr/bin/env python
"Makes working with XML feel like you are working with JSON"

from xml.parsers import expat
from xml.sax.saxutils import XMLGenerator, escape
from xml.sax.xmlreader import AttributesImpl
from io import StringIO
from inspect import isgenerator
import codecs

#: Namespace of the canonical ``xsi:nil`` attribute.
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"

#: Sentinel representing a value that was explicitly cleared in the source
#: document, i.e. an element carrying the namespaced ``nil="true"`` attribute.
#: It is distinct from ``None``, which represents a plain empty/self-closing
#: element (a field that was simply left blank), and from a missing key, which
#: means the element was absent altogether.
Nil = type("Nil", (), {"__repr__": lambda self: "Nil"})()
Nil.__doc__ = "Sentinel for an explicitly nil (cleared) XML element."

# Internal marker distinguishing "prefix never declared in scope" from a
# declaration whose URI is ``None``/empty.
_NS_UNDECLARED = object()


class ParsingInterrupted(Exception):
    pass


class XSINilConflictError(ValueError):
    """Raised when an ``xsi:nil="true"`` element also carries a value.

    Per the XML Schema rules a nilled element must have no content; finding
    character data or child elements alongside ``xsi:nil="true"`` is a
    conflict. Plain empty elements and ``xsi:nil="false"`` are not affected.
    """


class _DictSAXHandler:
    def __init__(
        self,
        item_depth=0,
        item_callback=lambda *args: True,
        xml_attribs=True,
        attr_prefix="@",
        cdata_key="#text",
        force_cdata=False,
        cdata_separator="",
        postprocessor=None,
        dict_constructor=dict,
        strip_whitespace=True,
        namespace_separator=":",
        namespaces=None,
        force_list=None,
        comment_key="#comment",
        process_namespaces=False,
        process_xsi_nil=False,
        xsi_namespace=XSI_NAMESPACE,
        nil_sentinel=Nil,
    ):
        self.path = []
        self.stack = []
        self.data = []
        self.item = None
        self.item_depth = item_depth
        self.xml_attribs = xml_attribs
        self.item_callback = item_callback
        self.attr_prefix = attr_prefix
        self.cdata_key = cdata_key
        self.force_cdata = force_cdata
        self.cdata_separator = cdata_separator
        self.postprocessor = postprocessor
        self.dict_constructor = dict_constructor
        self.strip_whitespace = strip_whitespace
        self.namespace_separator = namespace_separator
        self.namespaces = namespaces
        self.process_namespaces = process_namespaces
        self.namespace_declarations = dict_constructor()
        self.force_list = force_list
        self.comment_key = comment_key
        self.process_xsi_nil = process_xsi_nil
        self.xsi_namespace = xsi_namespace
        self.nil_sentinel = nil_sentinel
        # Maps an in-scope namespace prefix to its URI. Only maintained while
        # ``process_xsi_nil`` is enabled so that a prefixed ``nil`` attribute
        # can be resolved against its actual namespace.
        self._ns_scope = {}
        # Undo records (prefix, previous-or-marker) for declarations that are
        # still open, so leaving an element restores the surrounding scope.
        self._ns_undo = []
        # Parallel stack for declarations seen as ``xmlns*`` attributes when
        # expat namespace processing is disabled (the default mode).
        self._ns_attr_undo = []
        # Parallel to ``stack``: per open element, ``None`` when it has no
        # namespaced nil attribute, otherwise the nil sentinel (nil="true")
        # or ``False`` (any other value such as nil="false").
        self._nil_stack = []

    def _build_name(self, full_name):
        if self.namespaces is None:
            return full_name
        i = full_name.rfind(self.namespace_separator)
        if i == -1:
            return full_name
        namespace, name = full_name[:i], full_name[i+1:]
        try:
            short_namespace = self.namespaces[namespace]
        except KeyError:
            short_namespace = namespace
        if not short_namespace:
            return name
        else:
            return self.namespace_separator.join((short_namespace, name))

    def _attrs_to_dict(self, attrs):
        if isinstance(attrs, dict):
            return attrs
        return self.dict_constructor(zip(attrs[0::2], attrs[1::2]))

    def startNamespaceDecl(self, prefix, uri):
        self.namespace_declarations[prefix or ''] = uri
        if self.process_xsi_nil:
            key = prefix or ''
            self._ns_undo.append((key, self._ns_scope.get(key, _NS_UNDECLARED)))
            self._ns_scope[key] = uri

    def endNamespaceDecl(self, prefix):
        if not self.process_xsi_nil:
            return
        key, previous = self._ns_undo.pop()
        if previous is _NS_UNDECLARED:
            self._ns_scope.pop(key, None)
        else:
            self._ns_scope[key] = previous

    def _push_namespace_decls(self, attrs):
        """Track ``xmlns`` declarations reported as plain attributes.

        Expat only invokes the namespace-declaration callbacks while namespace
        processing is enabled. In the default mode they arrive as ordinary
        ``xmlns``/``xmlns:prefix`` attributes, so they are tracked here to keep
        prefix resolution correct.
        """
        undo = []
        for raw_name, uri in attrs.items():
            if raw_name == 'xmlns':
                key = ''
            elif raw_name.startswith('xmlns:'):
                key = raw_name[len('xmlns:'):]
            else:
                continue
            undo.append((key, self._ns_scope.get(key, _NS_UNDECLARED)))
            self._ns_scope[key] = uri
        self._ns_attr_undo.append(undo)

    def _pop_namespace_decls(self):
        for key, previous in self._ns_attr_undo.pop():
            if previous is _NS_UNDECLARED:
                self._ns_scope.pop(key, None)
            else:
                self._ns_scope[key] = previous

    def _attribute_namespace(self, raw_name):
        """Return the resolved namespace URI of an attribute.

        With namespace processing enabled expat reports the name as
        ``'<uri><separator><local>'``; otherwise a prefixed name is resolved
        against the currently in-scope namespace prefixes. Unprefixed
        attributes have no namespace and yield ``None``.
        """
        if self.process_namespaces:
            if self.namespace_separator in raw_name:
                return raw_name.rsplit(self.namespace_separator, 1)[0]
            return None
        if ':' in raw_name:
            prefix = raw_name.split(':', 1)[0]
            return self._ns_scope.get(prefix)
        return None

    def _xsi_nil_value(self, attrs):
        """Return the value of the namespaced ``nil`` attribute, if present."""
        for raw_name, raw_value in attrs.items():
            if self.process_namespaces:
                local = raw_name.rsplit(self.namespace_separator, 1)[-1]
            else:
                local = raw_name.split(':', 1)[-1]
            if local != 'nil':
                continue
            if self._attribute_namespace(raw_name) == self.xsi_namespace:
                return raw_value
        return None

    def _start_nil(self, attrs):
        """Return the nil state for an opening element.

        Returns the nil sentinel when the namespaced attribute is a truthy
        boolean, ``False`` when it is present but not truthy, and ``None``
        when the element carries no namespaced ``nil`` attribute.
        """
        nil_value = self._xsi_nil_value(attrs)
        if nil_value is None:
            return None
        if nil_value.strip().lower() in ('true', '1'):
            return self.nil_sentinel
        return False

    def startElement(self, full_name, attrs):
        name = self._build_name(full_name)
        attrs = self._attrs_to_dict(attrs)
        if self.process_xsi_nil and not self.process_namespaces:
            self._push_namespace_decls(attrs)
        nil_state = self._start_nil(attrs) if self.process_xsi_nil else None
        self._nil_stack.append(nil_state)
        if self.namespace_declarations:
            if not attrs:
                attrs = self.dict_constructor()
            attrs['xmlns'] = self.namespace_declarations
            self.namespace_declarations = self.dict_constructor()
        self.path.append((name, attrs or None))
        if len(self.path) >= self.item_depth:
            self.stack.append((self.item, self.data))
            if self.xml_attribs:
                attr_entries = []
                for key, value in attrs.items():
                    key = self.attr_prefix+self._build_name(key)
                    if self.postprocessor:
                        entry = self.postprocessor(self.path, key, value)
                    else:
                        entry = (key, value)
                    if entry:
                        attr_entries.append(entry)
                attrs = self.dict_constructor(attr_entries)
            else:
                attrs = None
            self.item = attrs or None
            self.data = []

    def _has_element_children(self, item):
        if not isinstance(item, dict):
            return False
        for key in item:
            if key == self.cdata_key or key == self.comment_key:
                continue
            if isinstance(key, str) and key.startswith(self.attr_prefix):
                continue
            return True
        return False

    def _resolve_nil(self, name, nil_state, item, data):
        """Apply a namespaced nil state to a finished element.

        Returns the ``(item, data)`` to attach to the parent. A truthy nil on
        an empty element collapses to the nil sentinel; any character data or
        element child alongside it is a conflict and raises.
        """
        if nil_state is not self.nil_sentinel:
            return item, data
        if data or self._has_element_children(item):
            raise XSINilConflictError(
                "element %r carries xsi:nil=\"true\" but also has content"
                % name
            )
        return self.nil_sentinel, None

    def endElement(self, full_name):
        name = self._build_name(full_name)
        nil_state = self._nil_stack.pop()
        if self.process_xsi_nil and not self.process_namespaces:
            self._pop_namespace_decls()
        # If we just closed an item at the streaming depth, emit it and drop it
        # without attaching it back to its parent. This avoids accumulating all
        # streamed items in memory when using item_depth > 0.
        if len(self.path) == self.item_depth:
            data = (None if not self.data
                    else self.cdata_separator.join(self.data))
            item = self.item
            if self.strip_whitespace and data:
                data = data.strip() or None
            if data and self._should_force_cdata(name, data) and item is None:
                item = self.dict_constructor()
            if item is not None:
                if data:
                    self.push_data(item, self.cdata_key, data)
            else:
                item = data
            if self.process_xsi_nil:
                item, data = self._resolve_nil(name, nil_state, item, data)

            should_continue = self.item_callback(self.path, item)
            if not should_continue:
                raise ParsingInterrupted
            # Reset state for the parent context without keeping a reference to
            # the emitted item.
            if self.stack:
                self.item, self.data = self.stack.pop()
            else:
                self.item = None
                self.data = []
            self.path.pop()
            return
        if self.stack:
            data = (None if not self.data
                    else self.cdata_separator.join(self.data))
            item = self.item
            self.item, self.data = self.stack.pop()
            if self.strip_whitespace and data:
                data = data.strip() or None
            if data and self._should_force_cdata(name, data) and item is None:
                item = self.dict_constructor()
            if item is not None:
                if data:
                    self.push_data(item, self.cdata_key, data)
                if self.process_xsi_nil:
                    item, data = self._resolve_nil(name, nil_state, item, data)
                self.item = self.push_data(self.item, name, item)
            else:
                if self.process_xsi_nil:
                    item, data = self._resolve_nil(name, nil_state, item, data)
                    if item is self.nil_sentinel:
                        self.item = self.push_data(self.item, name, item)
                    else:
                        self.item = self.push_data(self.item, name, data)
                else:
                    self.item = self.push_data(self.item, name, data)
        else:
            self.item = None
            self.data = []
        self.path.pop()

    def characters(self, data):
        if not self.data:
            self.data = [data]
        else:
            self.data.append(data)

    def comments(self, data):
        if self.strip_whitespace:
            data = data.strip()
        self.item = self.push_data(self.item, self.comment_key, data)

    def push_data(self, item, key, data):
        if self.postprocessor is not None:
            result = self.postprocessor(self.path, key, data)
            if result is None:
                return item
            key, data = result
        if item is None:
            item = self.dict_constructor()
        try:
            value = item[key]
            if isinstance(value, list):
                value.append(data)
            else:
                item[key] = [value, data]
        except KeyError:
            if self._should_force_list(key, data):
                item[key] = [data]
            else:
                item[key] = data
        return item

    def _should_force_list(self, key, value):
        if not self.force_list:
            return False
        if isinstance(self.force_list, bool):
            return self.force_list
        try:
            return key in self.force_list
        except TypeError:
            return self.force_list(self.path[:-1], key, value)

    def _should_force_cdata(self, key, value):
        if not self.force_cdata:
            return False
        if isinstance(self.force_cdata, bool):
            return self.force_cdata
        try:
            return key in self.force_cdata
        except TypeError:
            return self.force_cdata(self.path[:-1], key, value)


def parse(xml_input, encoding=None, expat=expat, process_namespaces=False,
          namespace_separator=':', disable_entities=True, process_comments=False, **kwargs):
    """Parse the given XML input and convert it into a dictionary.

    `xml_input` can either be a `string`, a file-like object, or a generator of strings.

    If `xml_attribs` is `True`, element attributes are put in the dictionary
    among regular child elements, using `@` as a prefix to avoid collisions. If
    set to `False`, they are just ignored.

    Simple example::

        >>> import xmltodict
        >>> doc = xmltodict.parse(\"\"\"
        ... <a prop="x">
        ...   <b>1</b>
        ...   <b>2</b>
        ... </a>
        ... \"\"\")
        >>> doc['a']['@prop']
        'x'
        >>> doc['a']['b']
        ['1', '2']

    If `item_depth` is `0`, the function returns a dictionary for the root
    element (default behavior). Otherwise, it calls `item_callback` every time
    an item at the specified depth is found and returns `None` in the end
    (streaming mode).

    The callback function receives two parameters: the `path` from the document
    root to the item (name-attribs pairs), and the `item` (dict). If the
    callback's return value is false-ish, parsing will be stopped with the
    :class:`ParsingInterrupted` exception.

    Streaming example::

        >>> def handle(path, item):
        ...     print('path:%s item:%s' % (path, item))
        ...     return True
        ...
        >>> xmltodict.parse(\"\"\"
        ... <a prop="x">
        ...   <b>1</b>
        ...   <b>2</b>
        ... </a>\"\"\", item_depth=2, item_callback=handle)
        path:[('a', {'prop': 'x'}), ('b', None)] item:1
        path:[('a', {'prop': 'x'}), ('b', None)] item:2

    The optional argument `postprocessor` is a function that takes `path`,
    `key` and `value` as positional arguments and returns a new `(key, value)`
    pair where both `key` and `value` may have changed. Usage example::

        >>> def postprocessor(path, key, value):
        ...     try:
        ...         return key + ':int', int(value)
        ...     except (ValueError, TypeError):
        ...         return key, value
        >>> xmltodict.parse('<a><b>1</b><b>2</b><b>x</b></a>',
        ...                 postprocessor=postprocessor)
        {'a': {'b:int': [1, 2], 'b': 'x'}}

    You can pass an alternate version of `expat` (such as `defusedexpat`) by
    using the `expat` parameter. E.g:

        >>> import defusedexpat
        >>> xmltodict.parse('<a>hello</a>', expat=defusedexpat.pyexpat)
        {'a': 'hello'}

    You can use the force_list argument to force lists to be created even
    when there is only a single child of a given level of hierarchy. The
    force_list argument is a tuple of keys. If the key for a given level
    of hierarchy is in the force_list argument, that level of hierarchy
    will have a list as a child (even if there is only one sub-element).
    The index_keys operation takes precedence over this. This is applied
    after any user-supplied postprocessor has already run.

        For example, given this input:
        <servers>
          <server>
            <name>host1</name>
            <os>Linux</os>
            <interfaces>
              <interface>
                <name>em0</name>
                <ip_address>10.0.0.1</ip_address>
              </interface>
            </interfaces>
          </server>
        </servers>

        If called with force_list=('interface',), it will produce
        this dictionary:
        {'servers':
          {'server':
            {'name': 'host1',
             'os': 'Linux'},
             'interfaces':
              {'interface':
                [ {'name': 'em0', 'ip_address': '10.0.0.1' } ] } } }

        `force_list` can also be a callable that receives `path`, `key` and
        `value`. This is helpful in cases where the logic that decides whether
        a list should be forced is more complex.


        If `process_comments` is `True`, comments will be added using `comment_key`
        (default=`'#comment'`) to the tag that contains the comment.

            For example, given this input:
            <a>
              <b>
                <!-- b comment -->
                <c>
                    <!-- c comment -->
                    1
                </c>
                <d>2</d>
              </b>
            </a>

            If called with `process_comments=True`, it will produce
            this dictionary:
            'a': {
                'b': {
                    '#comment': 'b comment',
                    'c': {

                        '#comment': 'c comment',
                        '#text': '1',
                    },
                    'd': '2',
                },
            }
        Comment text is subject to the `strip_whitespace` flag: when it is left
        at the default `True`, comments will have leading and trailing
        whitespace removed. Disable `strip_whitespace` to keep comment
        indentation or padding intact.

    If `process_xsi_nil` is `True`, elements carrying the namespaced
    ``xsi:nil="true"`` attribute are collapsed to the `nil_sentinel` (the
    module-level :data:`Nil` object by default) instead of ``None``. This keeps
    three otherwise ambiguous cases distinct in the result:

    * ``<phone/>`` -> ``None``: the element is present but empty (no value).
    * a missing ``phone`` key: the element is absent from the document.
    * ``<phone xsi:nil="true"/>`` -> :data:`Nil`: the value was explicitly
      cleared. Only an attribute in the `xsi_namespace`
      (default ``http://www.w3.org/2001/XMLSchema-instance``) counts; an
      unprefixed ``nil="true"`` is ignored. An element that is nilled yet also
      carries text or child elements raises :class:`XSINilConflictError`.

    Pass a custom `nil_sentinel` to use a different marker. The same sentinel
    is understood by `unparse` to round-trip the explicit clear back into
    ``xsi:nil="true"``.
    """
    handler = _DictSAXHandler(namespace_separator=namespace_separator,
                              process_namespaces=process_namespaces,
                              **kwargs)
    if isinstance(xml_input, str):
        encoding = encoding or 'utf-8'
        xml_input = xml_input.encode(encoding)
    if not process_namespaces:
        namespace_separator = None
    parser = expat.ParserCreate(
        encoding,
        namespace_separator
    )
    parser.ordered_attributes = True
    parser.StartNamespaceDeclHandler = handler.startNamespaceDecl
    parser.EndNamespaceDeclHandler = handler.endNamespaceDecl
    parser.StartElementHandler = handler.startElement
    parser.EndElementHandler = handler.endElement
    parser.CharacterDataHandler = handler.characters
    if process_comments:
        parser.CommentHandler = handler.comments
    parser.buffer_text = True
    if disable_entities:
        def _forbid_entities(*_args, **_kwargs):
            raise ValueError("entities are disabled")

        parser.EntityDeclHandler = _forbid_entities
    if hasattr(xml_input, 'read'):
        parser.ParseFile(xml_input)
    elif isgenerator(xml_input):
        for chunk in xml_input:
            parser.Parse(chunk, False)
        parser.Parse(b'', True)
    else:
        parser.Parse(xml_input, True)
    return handler.item


def _convert_value_to_string(value, encoding='utf-8', bytes_errors='replace'):
    """Convert a value to its string representation for XML output.

    Handles boolean values consistently by converting them to lowercase.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode(encoding, errors=bytes_errors)
    return str(value)


def _validate_name(value, kind):
    """Validate an element/attribute name for XML safety.

    Raises ValueError with a specific reason when invalid.

    kind: 'element' or 'attribute' (used in error messages)
    """
    if not isinstance(value, str):
        raise ValueError(f"{kind} name must be a string")
    if value.startswith("?") or value.startswith("!"):
        raise ValueError(f'Invalid {kind} name: cannot start with "?" or "!"')
    if "<" in value or ">" in value:
        raise ValueError(f'Invalid {kind} name: "<" or ">" not allowed')
    if "/" in value:
        raise ValueError(f'Invalid {kind} name: "/" not allowed')
    if '"' in value or "'" in value:
        raise ValueError(f"Invalid {kind} name: quotes not allowed")
    if "=" in value:
        raise ValueError(f'Invalid {kind} name: "=" not allowed')
    if any(ch.isspace() for ch in value):
        raise ValueError(f"Invalid {kind} name: whitespace not allowed")


def _validate_comment(value):
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Comment text must be valid UTF-8") from exc
    if not isinstance(value, str):
        raise ValueError("Comment text must be a string")
    if "--" in value:
        raise ValueError("Comment text cannot contain '--'")
    if value.endswith("-"):
        raise ValueError("Comment text cannot end with '-'")
    return value


def _process_namespace(name, namespaces, ns_sep=':', attr_prefix='@'):
    if not isinstance(name, str):
        return name
    if not namespaces:
        return name
    try:
        ns, name = name.rsplit(ns_sep, 1)
    except ValueError:
        pass
    else:
        ns_res = namespaces.get(ns.strip(attr_prefix))
        name = '{}{}{}{}'.format(
            attr_prefix if ns.startswith(attr_prefix) else '',
            ns_res, ns_sep, name) if ns_res else name
    return name


def _emit(key, value, content_handler,
          attr_prefix='@',
          cdata_key='#text',
          depth=0,
          preprocessor=None,
          pretty=False,
          newl='\n',
          indent='\t',
          namespace_separator=':',
          namespaces=None,
          full_document=True,
          expand_iter=None,
          encoding='utf-8',
          bytes_errors='replace',
          comment_key='#comment',
          nil_sentinel=Nil,
          xsi_namespace=XSI_NAMESPACE,
          nil_attribute='xsi:nil',
          ns_prefixes=None):
    if isinstance(key, str) and key == comment_key:
        comments_list = value if isinstance(value, list) else [value]
        if isinstance(indent, int):
            indent = " " * indent
        for comment_text in comments_list:
            if comment_text is None:
                continue
            comment_text = _convert_value_to_string(
                comment_text, encoding=encoding, bytes_errors=bytes_errors
            )
            if not comment_text:
                continue
            if pretty:
                content_handler.ignorableWhitespace(depth * indent)
            content_handler.comment(comment_text)
            if pretty:
                content_handler.ignorableWhitespace(newl)
        return

    key = _process_namespace(key, namespaces, namespace_separator, attr_prefix)
    if preprocessor is not None:
        result = preprocessor(key, value)
        if result is None:
            return
        key, value = result
    # Minimal validation to avoid breaking out of tag context
    _validate_name(key, "element")
    if not hasattr(value, '__iter__') or isinstance(value, (str, bytes, bytearray, memoryview, dict)):
        value = [value]
    for index, v in enumerate(value):
        if full_document and depth == 0 and index > 0:
            raise ValueError('document with multiple roots')
        is_nil = v is nil_sentinel
        if v is None or is_nil:
            v = {}
        elif not isinstance(v, (dict, str)):
            if expand_iter and hasattr(v, '__iter__') and not isinstance(v, (bytes, bytearray, memoryview)):
                v = {expand_iter: v}
            else:
                v = _convert_value_to_string(v, encoding=encoding, bytes_errors=bytes_errors)
        if isinstance(v, str):
            v = {cdata_key: v}
        cdata = None
        attrs = {}
        children = []
        for ik, iv in v.items():
            if ik == cdata_key:
                if iv is nil_sentinel:
                    is_nil = True
                    cdata = None
                    continue
                if iv is None:
                    cdata = None
                else:
                    cdata = _convert_value_to_string(iv, encoding=encoding, bytes_errors=bytes_errors)
                continue
            if isinstance(ik, str) and ik.startswith(attr_prefix):
                ik = _process_namespace(ik, namespaces, namespace_separator,
                                        attr_prefix)
                if ik == attr_prefix + 'xmlns' and isinstance(iv, dict):
                    for k, v in iv.items():
                        _validate_name(k, "attribute")
                        attr = 'xmlns{}'.format(f':{k}' if k else '')
                        attrs[attr] = '' if v is None else _convert_value_to_string(
                            v, encoding=encoding, bytes_errors=bytes_errors
                        )
                    continue
                if iv is None:
                    iv = ''
                elif not isinstance(iv, str):
                    iv = _convert_value_to_string(iv, encoding=encoding, bytes_errors=bytes_errors)
                attr_name = ik[len(attr_prefix) :]
                _validate_name(attr_name, "attribute")
                attrs[attr_name] = iv
                continue
            if isinstance(iv, list) and not iv:
                continue # Skip empty lists to avoid creating empty child elements
            children.append((ik, iv))
        # Namespace prefixes made available by this element's own xmlns decls,
        # layered over those inherited from ancestors.
        scope = dict(ns_prefixes or {})
        for attr_name, attr_value in attrs.items():
            if attr_name == 'xmlns':
                scope[''] = attr_value
            elif attr_name.startswith('xmlns:'):
                scope[attr_name[len('xmlns:'):]] = attr_value
        if is_nil:
            if children or cdata is not None:
                raise XSINilConflictError(
                    "element %r is marked nil but also has content" % key
                )
            if ':' in nil_attribute:
                prefix = nil_attribute.split(':', 1)[0]
                declaration = 'xmlns' + (':%s' % prefix if prefix else '')
                if scope.get(prefix) != xsi_namespace and declaration not in attrs:
                    attrs[declaration] = xsi_namespace
                    scope[prefix] = xsi_namespace
            attrs.setdefault(nil_attribute, "true")
        if isinstance(indent, int):
            indent = ' ' * indent
        if pretty:
            content_handler.ignorableWhitespace(depth * indent)
        content_handler.startElement(key, AttributesImpl(attrs))
        if pretty and children:
            content_handler.ignorableWhitespace(newl)
        for child_key, child_value in children:
            _emit(child_key, child_value, content_handler,
                  attr_prefix, cdata_key, depth+1, preprocessor,
                  pretty, newl, indent, namespaces=namespaces,
                  namespace_separator=namespace_separator,
                  expand_iter=expand_iter, encoding=encoding,
                  bytes_errors=bytes_errors, comment_key=comment_key,
                  nil_sentinel=nil_sentinel, xsi_namespace=xsi_namespace,
                  nil_attribute=nil_attribute, ns_prefixes=scope)
        if cdata is not None:
            content_handler.characters(cdata)
        if pretty and children:
            content_handler.ignorableWhitespace(depth * indent)
        content_handler.endElement(key)
        if pretty and depth:
            content_handler.ignorableWhitespace(newl)


class _XMLGenerator(XMLGenerator):
    def comment(self, text):
        text = _validate_comment(text)
        self._write(f"<!--{escape(text)}-->")


def unparse(input_dict, output=None, encoding='utf-8', full_document=True,
            short_empty_elements=False, comment_key='#comment',
            nil_sentinel=Nil, xsi_namespace=XSI_NAMESPACE,
            nil_attribute='xsi:nil', **kwargs):
    """Emit an XML document for the given `input_dict` (reverse of `parse`).

    The resulting XML document is returned as a string, but if `output` (a
    file-like object) is specified, it is written there instead.

    Dictionary keys prefixed with `attr_prefix` (default=`'@'`) are interpreted
    as XML node attributes, whereas keys equal to `cdata_key`
    (default=`'#text'`) are treated as character data.

    Empty lists are omitted entirely: ``{"a": []}`` produces no ``<a>`` element.
    Provide a placeholder entry (for example ``{"a": [""]}``) when an explicit
    empty container element must be emitted.

    Values equal to `nil_sentinel` (the module-level :data:`Nil` object by
    default) are emitted as an empty element carrying
    ``<nil_attribute>="true"`` (default ``xsi:nil="true"``), so an explicit
    clear stays distinguishable from a plain empty element (``None``) or an
    absent key. A nil value that also carries text or child elements raises
    :class:`XSINilConflictError`. Use the same `nil_sentinel` and
    `xsi_namespace` passed to `parse` for a clean round-trip.

    The `pretty` parameter (default=`False`) enables pretty-printing. In this
    mode, lines are terminated with `'\n'` and indented with `'\t'`, but this
    can be customized with the `newl` and `indent` parameters.
    The `bytes_errors` parameter controls decoding errors for byte values and
    defaults to `'replace'`.

    """
    bytes_errors = kwargs.pop('bytes_errors', 'replace')
    try:
        codecs.lookup_error(bytes_errors)
    except LookupError as exc:
        raise ValueError(f"Invalid bytes_errors handler: {bytes_errors}") from exc

    must_return = False
    if output is None:
        output = StringIO()
        must_return = True
    if short_empty_elements:
        content_handler = _XMLGenerator(output, encoding, True)
    else:
        content_handler = _XMLGenerator(output, encoding)
    if full_document:
        content_handler.startDocument()
    seen_root = False
    for key, value in input_dict.items():
        if key != comment_key and full_document and seen_root:
            raise ValueError("Document must have exactly one root.")
        _emit(
            key,
            value,
            content_handler,
            full_document=full_document,
            encoding=encoding,
            bytes_errors=bytes_errors,
            comment_key=comment_key,
            nil_sentinel=nil_sentinel,
            xsi_namespace=xsi_namespace,
            nil_attribute=nil_attribute,
            **kwargs,
        )
        if key != comment_key:
            seen_root = True
    if full_document and not seen_root:
        raise ValueError("Document must have exactly one root.")
    if full_document:
        content_handler.endDocument()
    if must_return:
        value = output.getvalue()
        try:  # pragma no cover
            value = value.decode(encoding)
        except AttributeError:  # pragma no cover
            pass
        return value


if __name__ == '__main__':  # pragma: no cover
    import marshal
    import sys

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    (item_depth,) = sys.argv[1:]
    item_depth = int(item_depth)

    def handle_item(path, item):
        marshal.dump((path, item), stdout)
        return True

    try:
        root = parse(stdin,
                     item_depth=item_depth,
                     item_callback=handle_item,
                     dict_constructor=dict)
        if item_depth == 0:
            handle_item([], root)
    except KeyboardInterrupt:
        pass
