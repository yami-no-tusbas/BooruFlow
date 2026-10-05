"""Network clients used by the single-tag Wiki Draft workflow."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from booruflow.infrastructure.gelbooru_client import DEFAULT_USER_AGENT
from booruflow.infrastructure.wiki_audit import fetch_text
from booruflow.infrastructure.wiki_tag_importer import (
    WikiPageNotFoundError,
    fetch_wiki_page_details,
)


@dataclass(frozen=True, slots=True)
class RemoteWikiPage:
    tag: str
    text: str
    source_url: str
    wiki_id: int | None = None
    warnings: tuple[str, ...] = ()


class DanbooruWikiClient:
    API_URL = "https://danbooru.donmai.us/wiki_pages.json"

    def __init__(self, fetcher=None) -> None:
        self.fetcher = fetcher or self._fetch_json

    def fetch(self, tag: str) -> RemoteWikiPage:
        query = urllib.parse.urlencode({"search[title]": tag, "limit": 20})
        rows = self.fetcher(f"{self.API_URL}?{query}")
        sought = self._normalize(tag)
        page = next(
            (row for row in rows if self._normalize(str(row.get("title", ""))) == sought),
            None,
        )
        if page is None:
            raise WikiPageNotFoundError(f"Danbooru wiki page not found: {tag}")
        page_id = int(page["id"])
        body = str(page.get("body", ""))
        if "body" not in page:
            detail = self.fetcher(f"https://danbooru.donmai.us/wiki_pages/{page_id}.json")
            body = str(detail.get("body", ""))
        return RemoteWikiPage(
            tag=str(page.get("title") or tag),
            text=body,
            source_url=f"https://danbooru.donmai.us/wiki_pages/{page_id}",
            wiki_id=page_id,
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return value.strip().replace(" ", "_").casefold()

    @staticmethod
    def _fetch_json(url: str):
        request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))


class _TextAreaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active = False
        self.value: list[str] = []
        self.result: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "textarea" and values.get("name", "").casefold() in {
            "body", "content", "wiki[body]", "wiki_body",
        }:
            self.active = True
            self.value = []

    def handle_data(self, data: str) -> None:
        if self.active:
            self.value.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.active and tag.casefold() == "textarea":
            self.result = "".join(self.value)
            self.active = False


class _RenderedWikiParser(HTMLParser):
    """Conservative fallback when Gelbooru does not expose its edit textarea."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.href = ""
        self.link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        tag = tag.casefold()
        if tag in {"br", "p", "div", "li", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("* ")
        if tag in {"h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("[h2]")
        if tag in {"b", "strong"}:
            self.parts.append("[b]")
        if tag in {"i", "em"}:
            self.parts.append("[i]")
        if tag == "a":
            self.href = html.unescape(values.get("href", ""))
            self.link_text = []

    def handle_data(self, data: str) -> None:
        if self.href:
            self.link_text.append(data)
        else:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self.href:
            label = "".join(self.link_text).strip()
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.href).query)
            linked_tag = query.get("search", [""])[0]
            self.parts.append(f"[[{linked_tag}]]" if linked_tag else f"{label} ({self.href})")
            self.href = ""
            self.link_text = []
        if tag in {"h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("[/h2]\n")
        if tag in {"b", "strong"}:
            self.parts.append("[/b]")
        if tag in {"i", "em"}:
            self.parts.append("[/i]")
        if tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def text(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


class GelbooruWikiClient:
    def __init__(self, fetcher=None) -> None:
        self.fetcher = fetcher or fetch_text

    def fetch(self, tag: str) -> RemoteWikiPage:
        responses = []

        def record(url: str):
            response = self.fetcher(url)
            responses.append(response)
            return response

        details = fetch_wiki_page_details("gelbooru", tag, fetcher=record)
        wiki_id = int(details["wiki_id"]) if details.get("wiki_id") is not None else None
        source_url = str(details.get("source_url") or "")
        if wiki_id is not None:
            edit_url = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(
                {"page": "wiki", "s": "edit", "id": wiki_id}
            )
            try:
                edit_response = self.fetcher(edit_url)
                parser = _TextAreaParser()
                parser.feed(self._body(edit_response))
                parser.close()
                if parser.result is not None:
                    return RemoteWikiPage(tag, parser.result, source_url, wiki_id)
            except (OSError, TimeoutError, ValueError):
                pass
        direct_body = self._body(responses[-1]) if responses else ""
        parser = _RenderedWikiParser()
        marker = re.search(
            r"<h2\b[^>]*>\s*Now\s+Viewing:.*?</h2>",
            direct_body,
            re.IGNORECASE | re.DOTALL,
        )
        editorial = direct_body[marker.end():] if marker else direct_body
        stop = re.search(r"Other\s+Wiki\s+Information", editorial, re.IGNORECASE)
        parser.feed(editorial[: stop.start()] if stop else editorial)
        parser.close()
        text = parser.text() or str(details.get("content") or "")
        return RemoteWikiPage(
            tag,
            text,
            source_url,
            wiki_id,
            ("Gelbooru source textarea was unavailable; reconstructed from the public wiki for review.",),
        )

    @staticmethod
    def _body(response) -> str:
        return str(response.body if hasattr(response, "body") else response[0])
