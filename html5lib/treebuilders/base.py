from __future__ import absolute_import, division, unicode_literals
from six import text_type

from ..constants import scopingElements, tableInsertModeElements, namespaces

# The scope markers are inserted when entering object elements,
# marquees, table cells, and table captions, and are used to prevent formatting
# from "leaking" into tables, object elements, and marquees.
Marker = None

listElementsMap = {
    None: (frozenset(scopingElements), False),
    "button": (frozenset(scopingElements | {(namespaces["html"], "button")}), False),
    "list": (frozenset(scopingElements | {(namespaces["html"], "ol"),
                                          (namespaces["html"], "ul")}), False),
    "table": (frozenset([(namespaces["html"], "html"),
                         (namespaces["html"], "table")]), False),
    "select": (frozenset([(namespaces["html"], "optgroup"),
                          (namespaces["html"], "option")]), True)
}


class Node(object):
    """Represents an item in the tree"""
    def __init__(self, name):
        """Creates a Node

        :arg name: The tag name associated with the node

        """
        # The tag name associated with the node
        self.name = name
        # The parent of the current node (or None for the document node)
        self.parent = None
        # The value of the current node (applies to text nodes and comments)
        self.value = None
        # A dict holding name -> value pairs for attributes of the node
        self.attributes = {}
        # A list of child nodes of the current node. This must include all
        # elements but not necessarily other node types.
        self.childNodes = []
        # A list of miscellaneous flags that can be set on the node.
        self._flags = []

    @property
    def publicNode(self):
        """The tree object handed back to the caller for this wrapper node.

        Backends whose wrapper *is* the returned node use the default; the
        ElementTree and DOM wrappers override it to return their underlying
        native node, which is what source information is ultimately keyed by.
        """
        return self

    def __str__(self):
        attributesStr = " ".join(["%s=\"%s\"" % (name, value)
                                  for name, value in
                                  self.attributes.items()])
        if attributesStr:
            return "<%s %s>" % (self.name, attributesStr)
        else:
            return "<%s>" % (self.name)

    def __repr__(self):
        return "<%s>" % (self.name)

    def appendChild(self, node):
        """Insert node as a child of the current node

        :arg node: the node to insert

        """
        raise NotImplementedError

    def insertText(self, data, insertBefore=None):
        """Insert data as text in the current node, positioned before the
        start of node insertBefore or to the end of the node's text.

        :arg data: the data to insert

        :arg insertBefore: True if you want to insert the text before the node
            and False if you want to insert it after the node

        """
        raise NotImplementedError

    def insertBefore(self, node, refNode):
        """Insert node as a child of the current node, before refNode in the
        list of child nodes. Raises ValueError if refNode is not a child of
        the current node

        :arg node: the node to insert

        :arg refNode: the child node to insert the node before

        """
        raise NotImplementedError

    def removeChild(self, node):
        """Remove node from the children of the current node

        :arg node: the child node to remove

        """
        raise NotImplementedError

    def reparentChildren(self, newParent):
        """Move all the children of the current node to newParent.
        This is needed so that trees that don't store text as nodes move the
        text in the correct way

        :arg newParent: the node to move all this node's children to

        """
        # XXX - should this method be made more general?
        for child in self.childNodes:
            newParent.appendChild(child)
        self.childNodes = []

    def cloneNode(self):
        """Return a shallow copy of the current node i.e. a node with the same
        name and attributes but with no parent or child nodes
        """
        raise NotImplementedError

    def hasContent(self):
        """Return true if the node has children or text, false otherwise
        """
        raise NotImplementedError


class ActiveFormattingElements(list):
    def append(self, node):
        """Append node to the end of the list."""
        equalCount = 0
        if node != Marker:
            for element in self[::-1]:
                if element == Marker:
                    break
                if self.nodesEqual(element, node):
                    equalCount += 1
                if equalCount == 3:
                    self.remove(element)
                    break
        list.append(self, node)

    def nodesEqual(self, node1, node2):
        if not node1.nameTuple == node2.nameTuple:
            return False

        if not node1.attributes == node2.attributes:
            return False

        return True


class TreeBuilder(object):
    """Base treebuilder implementation

    * documentClass - the class to use for the bottommost node of a document
    * elementClass - the class to use for HTML Elements
    * commentClass - the class to use for comments
    * doctypeClass - the class to use for doctypes

    """
    # pylint:disable=not-callable

    # Document class
    documentClass = None

    # The class to use for creating a node
    elementClass = None

    # The class to use for creating comments
    commentClass = None

    # The class to use for creating doctypes
    doctypeClass = None

    # Fragment class
    fragmentClass = None

    def __init__(self, namespaceHTMLElements, locator=None):
        """Create a TreeBuilder

        :arg namespaceHTMLElements: whether or not to namespace HTML elements

        :arg locator: an optional
            :class:`~html5lib._sourcelocator.SourceLocator` that records the
            provenance of every node.  When :obj:`None` (the default) source
            tracking is disabled and parsing behaves exactly as before.

        """
        if namespaceHTMLElements:
            self.defaultNamespace = "http://www.w3.org/1999/xhtml"
        else:
            self.defaultNamespace = None
        self.locator = locator
        self.reset()

    def reset(self):
        self.openElements = []
        self.activeFormattingElements = ActiveFormattingElements()

        # XXX - rename these to headElement, formElement
        self.headPointer = None
        self.formPointer = None

        self.insertFromTable = False

        self.document = self.documentClass()
        if self.locator is not None:
            # The synthetic document node is parser-generated.
            self._recordDocumentNode()

    def _recordDocumentNode(self):
        """Record the document node as parser-generated.

        Most backends expose the returned document through ``publicNode``; the
        DOM builder keeps its native document separately and overrides this.
        """
        node = getattr(self.document, "publicNode", self.document)
        if node is not None:
            self.locator.implied(node)

    # -- source tracking helpers ------------------------------------------

    def _tokenIsImplied(self, token):
        """Return whether ``token`` was synthesized by the parser rather than
        produced from input markup."""
        return isinstance(token, dict) and "source" not in token

    def _recordElement(self, element, token):
        if self.locator is None:
            return
        if self._tokenIsImplied(token):
            self.locator.implied(element.publicNode)
        else:
            self.locator.present(element.publicNode, token)

    def recordClone(self, clone):
        """Mark ``clone`` as a parser-generated copy.

        Called by the adoption agency algorithm; a cloned element is a new
        node produced during error recovery even if its template came from real
        markup.
        """
        if self.locator is not None:
            self.locator.clone(clone.publicNode)

    def reparentText(self, fromNode, toNode):
        """Move the recorded text ranges of ``fromNode`` onto ``toNode``.

        Mirrors :meth:`Node.reparentChildren` for the text provenance kept by
        the locator; child elements keep their own provenance.
        """
        if self.locator is not None:
            self.locator.reparentText(fromNode.publicNode, toNode.publicNode)

    def elementInScope(self, target, variant=None):

        # If we pass a node in we match that. if we pass a string
        # match any node with that name
        exactNode = hasattr(target, "nameTuple")
        if not exactNode:
            if isinstance(target, text_type):
                target = (namespaces["html"], target)
            assert isinstance(target, tuple)

        listElements, invert = listElementsMap[variant]

        for node in reversed(self.openElements):
            if exactNode and node == target:
                return True
            elif not exactNode and node.nameTuple == target:
                return True
            elif (invert ^ (node.nameTuple in listElements)):
                return False

        assert False  # We should never reach this point

    def reconstructActiveFormattingElements(self):
        # Within this algorithm the order of steps described in the
        # specification is not quite the same as the order of steps in the
        # code. It should still do the same though.

        # Step 1: stop the algorithm when there's nothing to do.
        if not self.activeFormattingElements:
            return

        # Step 2 and step 3: we start with the last element. So i is -1.
        i = len(self.activeFormattingElements) - 1
        entry = self.activeFormattingElements[i]
        if entry == Marker or entry in self.openElements:
            return

        # Step 6
        while entry != Marker and entry not in self.openElements:
            if i == 0:
                # This will be reset to 0 below
                i = -1
                break
            i -= 1
            # Step 5: let entry be one earlier in the list.
            entry = self.activeFormattingElements[i]

        while True:
            # Step 7
            i += 1

            # Step 8
            entry = self.activeFormattingElements[i]
            clone = entry.cloneNode()  # Mainly to get a new copy of the attributes

            # Step 9
            element = self.insertElement({"type": "StartTag",
                                          "name": clone.name,
                                          "namespace": clone.namespace,
                                          "data": clone.attributes})

            # Step 10
            self.activeFormattingElements[i] = element

            # Step 11
            if element == self.activeFormattingElements[-1]:
                break

    def clearActiveFormattingElements(self):
        entry = self.activeFormattingElements.pop()
        while self.activeFormattingElements and entry != Marker:
            entry = self.activeFormattingElements.pop()

    def elementInActiveFormattingElements(self, name):
        """Check if an element exists between the end of the active
        formatting elements and the last marker. If it does, return it, else
        return false"""

        for item in self.activeFormattingElements[::-1]:
            # Check for Marker first because if it's a Marker it doesn't have a
            # name attribute.
            if item == Marker:
                break
            elif item.name == name:
                return item
        return False

    def insertRoot(self, token):
        element = self.createElement(token)
        self.openElements.append(element)
        self.document.appendChild(element)

    def insertDoctype(self, token):
        name = token["name"]
        publicId = token["publicId"]
        systemId = token["systemId"]

        doctype = self.doctypeClass(name, publicId, systemId)
        self.document.appendChild(doctype)
        if self.locator is not None:
            if self._tokenIsImplied(token):
                self.locator.implied(doctype.publicNode)
            else:
                self.locator.present(doctype.publicNode, token)

    def insertComment(self, token, parent=None):
        if parent is None:
            parent = self.openElements[-1]
        comment = self.commentClass(token["data"])
        parent.appendChild(comment)
        if self.locator is not None:
            if self._tokenIsImplied(token):
                self.locator.implied(comment.publicNode)
            else:
                self.locator.present(comment.publicNode, token)

    def createElement(self, token):
        """Create an element but don't insert it anywhere"""
        name = token["name"]
        namespace = token.get("namespace", self.defaultNamespace)
        element = self.elementClass(name, namespace)
        element.attributes = token["data"]
        self._recordElement(element, token)
        return element

    def _getInsertFromTable(self):
        return self._insertFromTable

    def _setInsertFromTable(self, value):
        """Switch the function used to insert an element from the
        normal one to the misnested table one and back again"""
        self._insertFromTable = value
        if value:
            self.insertElement = self.insertElementTable
        else:
            self.insertElement = self.insertElementNormal

    insertFromTable = property(_getInsertFromTable, _setInsertFromTable)

    def insertElementNormal(self, token):
        name = token["name"]
        assert isinstance(name, text_type), "Element %s not unicode" % name
        namespace = token.get("namespace", self.defaultNamespace)
        element = self.elementClass(name, namespace)
        element.attributes = token["data"]
        self._recordElement(element, token)
        self.openElements[-1].appendChild(element)
        self.openElements.append(element)
        return element

    def insertElementTable(self, token):
        """Create an element and insert it into the tree"""
        element = self.createElement(token)
        if self.openElements[-1].name not in tableInsertModeElements:
            return self.insertElementNormal(token)
        else:
            # We should be in the InTable mode. This means we want to do
            # special magic element rearranging
            parent, insertBefore = self.getTableMisnestedNodePosition()
            if insertBefore is None:
                parent.appendChild(element)
            else:
                parent.insertBefore(element, insertBefore)
            self.openElements.append(element)
        return element

    def insertText(self, data, parent=None, token=None):
        """Insert text data.

        When source tracking is enabled ``token`` is the character token the
        text came from, so that the element receiving it records a source
        range even if the text was foster-parented away from the table that was
        open when it was tokenized.
        """
        if parent is None:
            parent = self.openElements[-1]

        if (not self.insertFromTable or (self.insertFromTable and
                                         self.openElements[-1].name
                                         not in tableInsertModeElements)):
            parent.insertText(data)
            textParent = parent
            insertBefore = None
        else:
            # We should be in the InTable mode. This means we want to do
            # special magic element rearranging
            textParent, insertBefore = self.getTableMisnestedNodePosition()
            textParent.insertText(data, insertBefore)

        if self.locator is not None and token is not None:
            # A merged character token (e.g. table text) carries the list of
            # original tokens that were concatenated to produce ``data``.
            sourceTokens = token.get("sourceTokens") if isinstance(token, dict) else None
            if sourceTokens:
                for sourceToken in sourceTokens:
                    self.locator.addText(textParent.publicNode,
                                         sourceToken, sourceToken["data"])
            else:
                self.locator.addText(textParent.publicNode, token, data)

    def getTableMisnestedNodePosition(self):
        """Get the foster parent element, and sibling to insert before
        (or None) when inserting a misnested table node"""
        # The foster parent element is the one which comes before the most
        # recently opened table element
        # XXX - this is really inelegant
        lastTable = None
        fosterParent = None
        insertBefore = None
        for elm in self.openElements[::-1]:
            if elm.name == "table":
                lastTable = elm
                break
        if lastTable:
            # XXX - we should really check that this parent is actually a
            # node here
            if lastTable.parent:
                fosterParent = lastTable.parent
                insertBefore = lastTable
            else:
                fosterParent = self.openElements[
                    self.openElements.index(lastTable) - 1]
        else:
            fosterParent = self.openElements[0]
        return fosterParent, insertBefore

    def generateImpliedEndTags(self, exclude=None):
        name = self.openElements[-1].name
        # XXX td, th and tr are not actually needed
        if (name in frozenset(("dd", "dt", "li", "option", "optgroup", "p", "rp", "rt")) and
                name != exclude):
            self.openElements.pop()
            # XXX This is not entirely what the specification says. We should
            # investigate it more closely.
            self.generateImpliedEndTags(exclude)

    def getDocument(self):
        """Return the final tree"""
        return self.document

    def getFragment(self):
        """Return the final fragment"""
        # assert self.innerHTML
        fragment = self.fragmentClass()
        root = self.openElements[0]
        # The root's direct text becomes the fragment's text / its children's
        # tails, so move the recorded ranges across before the reparent.
        self.reparentText(root, fragment)
        root.reparentChildren(fragment)
        return fragment

    def testSerializer(self, node):
        """Serialize the subtree of node in the format required by unit tests

        :arg node: the node from which to start serializing

        """
        raise NotImplementedError
