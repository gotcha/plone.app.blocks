from lxml import etree
from lxml import html
from plone.app.blocks import panel
from plone.app.blocks import tiles
from plone.tiles import esi
from plone.tiles.interfaces import ESI_HEADER
from plone.transformchain.interfaces import ITransform
from Products.CMFPlone.utils import safe_bytes
from repoze.xmliter.serializer import XMLSerializer
from repoze.xmliter.utils import getHTMLSerializer
from zope.interface import implementer

import logging
import re


logger = logging.getLogger(__name__)


@implementer(ITransform)
class DisableParsing:
    """A no-op transform which sets flags to stop plone.app.blocks
    transformations. You may register this for a particular published
    object or request as required. By default, it's registered for ESI-
    rendered tiles when they are fetched via ESI.
    """

    order = 8000

    def __init__(self, published, request):
        self.published = published
        self.request = request

    def transformBytes(self, result, encoding):
        self.request.set("plone.app.blocks.disabled", True)

    def transformUnicode(self, result, encoding):
        self.request.set("plone.app.blocks.disabled", True)

    def transformIterable(self, result, encoding):
        self.request.set("plone.app.blocks.disabled", True)


@implementer(ITransform)
class ParseXML:
    """First stage in the 8000's chain: parse the content to an lxml tree
    encapsulated in an XMLSerializer.

    The subsequent steps in this package will assume their result inputs are
    XMLSerializer iterables, and do nothing if it is not. This also gives us
    the option to parse the content here, and if we decide it's not HTML,
    we can avoid trying to parse it again.
    """

    order = 8000

    # Tests set this to True
    pretty_print = False

    def __init__(self, published, request):
        self.published = published
        self.request = request

    def transformBytes(self, result, encoding):
        return self.transformIterable([result], encoding)

    def transformUnicode(self, result, encoding):
        return self.transformIterable([result], encoding)

    def transformIterable(self, result, encoding):

        if self.request.get("plone.app.blocks.disabled", False):
            return

        content_type = self.request.response.getHeader("Content-Type")
        if content_type is None or not content_type.startswith("text/html"):
            return

        contentEncoding = self.request.response.getHeader("Content-Encoding")
        if contentEncoding and contentEncoding in (
            "zip",
            "deflate",
            "compress",
        ):
            return

        try:
            # Fix layouts with CR[+LF] line endings not to lose their heads
            # (this has been seen with downloaded themes with CR[+LF] endings)
            # The html serializer much prefers only bytes, no unicode/text,
            # and it return a serializer that returns bytes.
            # So we start with ensuring all items in the iterable are bytes.
            iterable = [
                re.sub(b"&#13;", b"\n", re.sub(b"&#13;\n", b"\n", safe_bytes(item)))
                for item in result
                if item
            ]
            result = getHTMLSerializer(
                iterable, pretty_print=self.pretty_print, encoding=encoding
            )
            # Fix XHTML layouts with where etree.tostring breaks <![CDATA[
            if any([b"<![CDATA[" in item for item in iterable]):
                result.serializer = html.tostring

            self.request["plone.app.blocks.enabled"] = True
            return result
        except (AttributeError, TypeError, etree.ParseError) as e:
            logger.error(e)


@implementer(ITransform)
class MergePanels:
    """Find the site layout and merge panels."""

    order = 8100

    def __init__(self, published, request):
        self.published = published
        self.request = request

    def transformBytes(self, result, encoding):
        return

    def transformUnicode(self, result, encoding):
        return

    def transformIterable(self, result, encoding):
        if not self.request.get("plone.app.blocks.enabled", False) or not isinstance(
            result, XMLSerializer
        ):
            return

        tree = panel.merge(self.request, result.tree)
        if tree is None:
            return

        # Set a marker in the request to let subsequent steps know the merging
        # has happened
        self.request["plone.app.blocks.merged"] = True

        result.tree = tree

        # Fix serializer when layout has changed doctype from XHTML to HTML
        if result.tree.docinfo.doctype and "XHTML" not in result.tree.docinfo.doctype:
            result.serializer = html.tostring

        return result


@implementer(ITransform)
class IncludeTiles:
    """Turn a panel-merged page into the final composition by including tiles.
    Assumes the input result is an lxml tree and returns an lxml tree for
    later serialization.
    """

    order = 8500

    def __init__(self, published, request):
        self.published = published
        self.request = request

    def transformBytes(self, result, encoding):
        return

    def transformUnicode(self, result, encoding):
        return

    def transformIterable(self, result, encoding):
        if not self.request.get("plone.app.blocks.enabled", False) or not isinstance(
            result, XMLSerializer
        ):
            return

        result.tree = tiles.renderTiles(self.request, result.tree)
        return result


@implementer(ITransform)
class ESIRender:
    """If ESI rendering was used, render the page down to a format that allows
    ESI to work.
    """

    order = 9900

    def __init__(self, published, request):
        self.published = published
        self.request = request

    def transformBytes(self, result, encoding):
        if self.request.getHeader(ESI_HEADER, "false").lower() != "true":
            return

        return esi.substituteESILinks([result])

    def transformUnicode(self, result, encoding):
        if self.request.getHeader(ESI_HEADER, "false").lower() != "true":
            return

        return esi.substituteESILinks([result.encode(encoding, "ignore")])

    def transformIterable(self, result, encoding):
        if self.request.getHeader(ESI_HEADER, "false").lower() != "true":
            return
        result = "".join(str(result))
        transformed = esi.substituteESILinks(result)
        if transformed != result:
            self.request.response.setHeader("X-Esi", "1")
        return transformed
