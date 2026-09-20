"""Small, resource-free HTML sanitization helpers for cached wiki excerpts."""

from __future__ import annotations

import html
from html.parser import HTMLParser
from typing import ClassVar


class _SafeWikiHtmlParser(HTMLParser):
    _ALLOWED: ClassVar[set[str]] = {
        "p", "br", "strong", "b", "em", "i", "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li", "blockquote", "pre", "code",
    }
    _DANGEROUS: ClassVar[set[str]] = {"script", "iframe", "object", "embed", "style", "img", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._dangerous_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        tag = tag.casefold()
        if tag in self._DANGEROUS:
            self._dangerous_depth += 1
            return
        if self._dangerous_depth:
            return
        if tag in self._ALLOWED:
            self.parts.append(f"<{tag}>")
        elif tag == "div":
            # Layout wrappers from the source are not editorial structure.
            pass

    def handle_startendtag(self, tag: str, _attrs) -> None:
        if tag.casefold() == "br" and not self._dangerous_depth:
            self.parts.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._DANGEROUS:
            self._dangerous_depth = max(0, self._dangerous_depth - 1)
            return
        if self._dangerous_depth:
            return
        if tag in self._ALLOWED:
            self.parts.append(f"</{tag}>")
        elif tag == "div":
            pass

    def handle_data(self, data: str) -> None:
        if not self._dangerous_depth:
            self.parts.append(html.escape(data, quote=False))


def sanitize_wiki_html(content: str) -> str:
    """Keep basic editorial structure while removing active/external content."""
    parser = _SafeWikiHtmlParser()
    parser.feed(content)
    parser.close()
    return "".join(parser.parts).strip()
