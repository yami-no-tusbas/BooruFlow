"""Read-only Gelbooru post/wiki audit with an explicit freshness cache."""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

from booruflow.infrastructure.gelbooru_client import DEFAULT_USER_AGENT, GelbooruDapiClient
from booruflow.infrastructure.tag_browser import TagRow, enrich_tag_rows, exact_tags
from booruflow.infrastructure.wiki_page_cache import WikiPageCache, WikiPageRecord
from booruflow.infrastructure.wiki_tag_importer import (
    WikiFetchResponse,
    WikiPageNotFoundError,
    WikiPageParseError,
    fetch_wiki_page_details,
)

GELBOORU_URL = "https://gelbooru.com/index.php"
SHORT_WIKI_MAX_WORDS = 20
_CACHE_UNSET = object()


def _normalized_title(value: str) -> str:
    return urllib.parse.unquote_plus(value).strip().casefold()


def _parse_timestamp(value: str) -> datetime | None:
    cleaned = value.strip().removeprefix("about ").strip()
    for pattern in ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


@dataclass(frozen=True, slots=True)
class WikiStatus:
    tag_name: str
    exists: bool
    wiki_id: int | None = None
    title: str | None = None
    version: int | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None
    source: str = "list"
    checked_at: datetime | None = None
    error: str | None = None
    error_kind: str | None = None
    content: str | None = None
    content_format: str = "plain"
    word_count: int | None = None
    character_count: int | None = None
    source_url: str = ""


@dataclass(frozen=True, slots=True)
class AuditTag:
    name: str
    category: int | None
    post_count: int
    direct_alias: str | None
    canonical_name: str | None
    wiki: WikiStatus


@dataclass(frozen=True, slots=True)
class AuditResult:
    post_id: int
    post_url: str
    tags: tuple[AuditTag, ...]


class _VisibleTextParser(HTMLParser):
    """Extract visible text while preserving structural word boundaries."""

    _SEPARATORS: ClassVar[set[str]] = {
        "br", "div", "p", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.casefold() in self._SEPARATORS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._SEPARATORS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalize_wiki_text(content: str, content_format: str = "html") -> str:
    """Return normalized visible editorial text, never raw HTML markup."""
    if content_format == "html":
        parser = _VisibleTextParser()
        parser.feed(content)
        parser.close()
        value = "".join(parser.parts)
    elif content_format == "dtext":
        value = re.sub(r"\[\[[^]|]+\|([^]]+)]]", r"\1", content)
        value = re.sub(r"\[\[([^]]+)]]", r"\1", value)
        value = re.sub(r"\[/?[^]]+]", " ", value)
        value = re.sub(r"(?m)^h\d\.\s*", "", value)
    else:
        value = content
    normalized = " ".join(html.unescape(value).split())
    return re.sub(r"\s+([.,;:!?])", r"\1", normalized)


def wiki_text_metrics(content: str, content_format: str = "html") -> tuple[str, int, int]:
    text = normalize_wiki_text(content, content_format)
    return text, len(text.split()), len(text)


def _cached_status(record: WikiPageRecord) -> WikiStatus:
    _content, words, characters = wiki_text_metrics(record.content, record.content_format)
    try:
        updated_at = (
            datetime.fromisoformat(record.remote_updated_at)
            if record.remote_updated_at
            else None
        )
    except ValueError:
        updated_at = None
    try:
        checked_at = datetime.fromisoformat(record.cached_at) if record.cached_at else None
    except ValueError:
        checked_at = None
    return WikiStatus(
        tag_name=record.tag,
        exists=True,
        wiki_id=record.wiki_id,
        title=record.tag,
        version=record.version,
        updated_at=updated_at,
        updated_by=record.author,
        source="cache",
        checked_at=checked_at,
        content=record.content,
        content_format=record.content_format,
        word_count=words,
        character_count=characters,
        source_url=record.source_url,
    )


class _WikiPageParser(HTMLParser):
    """Collect structural wiki markers and result rows without fuzzy matching."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._heading_depth = 0
        self._heading_parts: list[str] = []
        self.headings: list[str] = []
        self._row_depth = 0
        self._row_text: list[str] = []
        self._row_links: list[tuple[str, str]] = []
        self._link_href = ""
        self._link_parts: list[str] = []
        self.rows: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self.all_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag in {"h1", "h2", "h3", "h4"}:
            self._heading_depth += 1
            self._heading_parts = []
        if tag == "tr":
            self._row_depth += 1
            if self._row_depth == 1:
                self._row_text = []
                self._row_links = []
        if tag == "a":
            self._link_href = values.get("href", "").replace("&amp;", "&")
            self._link_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self._link_href:
            label = " ".join(self._link_parts).strip()
            if self._row_depth:
                self._row_links.append((self._link_href, label))
            self._link_href = ""
            self._link_parts = []
        if tag in {"h1", "h2", "h3", "h4"} and self._heading_depth:
            heading = " ".join(self._heading_parts).strip()
            if heading:
                self.headings.append(heading)
            self._heading_depth -= 1
            self._heading_parts = []
        if tag == "tr" and self._row_depth:
            self._row_depth -= 1
            if self._row_depth == 0:
                self.rows.append((" ".join(self._row_text), tuple(self._row_links)))
                self._row_text = []
                self._row_links = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value:
            return
        self.all_text.append(value)
        if self._heading_depth:
            self._heading_parts.append(value)
        if self._row_depth:
            self._row_text.append(value)
        if self._link_href:
            self._link_parts.append(value)


def page_kind(source: str) -> str:
    """Return ``direct``, ``list`` or ``unknown`` from structural headings."""
    parser = _WikiPageParser()
    parser.feed(source)
    headings = [heading.casefold() for heading in parser.headings]
    if any(heading.startswith("now viewing:") for heading in headings):
        return "direct"
    if any("wiki listing" in heading for heading in headings):
        return "list"
    return "unknown"


def parse_last_updated(text: str) -> tuple[datetime | None, str | None]:
    match = re.search(
        r"Last\s+updated:\s*(?:about\s+)?(\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s+[AP]M)\s+by\s+([^|\n<]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    return _parse_timestamp(match.group(1)), match.group(2).strip()


def parse_direct_wiki(source: str, tag_name: str, final_url: str = "") -> WikiStatus:
    parser = _WikiPageParser()
    parser.feed(source)
    title = next(
        (heading.split(":", 1)[1].strip() for heading in parser.headings if heading.casefold().startswith("now viewing:")),
        None,
    )
    exact = title is not None and _normalized_title(title) == _normalized_title(tag_name)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query)
    try:
        wiki_id = int(query.get("id", [""])[0])
    except ValueError:
        wiki_id = None
    updated_at, updated_by = parse_last_updated("\n".join(parser.all_text))
    return WikiStatus(
        tag_name=tag_name,
        exists=exact,
        wiki_id=wiki_id if exact else None,
        title=title if exact else None,
        updated_at=updated_at if exact else None,
        updated_by=updated_by if exact else None,
        source="direct",
    )


def _view_link(href: str) -> int | None:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    if query.get("page") != ["wiki"] or query.get("s") != ["view"]:
        return None
    try:
        return int(query.get("id", [""])[0])
    except ValueError:
        return None


def parse_wiki_list(source: str, tag_name: str) -> WikiStatus:
    parser = _WikiPageParser()
    parser.feed(source)
    sought = _normalized_title(tag_name)
    for row_text, links in parser.rows:
        for href, label in links:
            wiki_id = _view_link(href)
            if wiki_id is None or _normalized_title(label) != sought:
                continue
            date_match = re.search(
                r"Last\s+updated\s+by\s+(.+?)\s*\(about\s+([^)]+)\)", row_text, re.IGNORECASE
            )
            version_match = re.search(r"Version\s+(\d+)", row_text, re.IGNORECASE)
            return WikiStatus(
                tag_name=tag_name,
                exists=True,
                wiki_id=wiki_id,
                title=label.strip(),
                version=int(version_match.group(1)) if version_match else None,
                updated_at=_parse_timestamp(date_match.group(2)) if date_match else None,
                updated_by=date_match.group(1).strip() if date_match else None,
                source="list",
            )
    return WikiStatus(tag_name=tag_name, exists=False, source="list")


def parse_wiki_response(source: str, tag_name: str, final_url: str = "") -> WikiStatus:
    kind = page_kind(source)
    if kind == "direct":
        return parse_direct_wiki(source, tag_name, final_url)
    if kind == "list":
        return parse_wiki_list(source, tag_name)
    return WikiStatus(tag_name=tag_name, exists=False, source="list")


FetchText = Callable[[str], WikiFetchResponse | tuple[str, str]]


class _WikiRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.redirect_urls: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirect_urls.append(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_text(url: str) -> WikiFetchResponse:
    redirects = _WikiRedirectHandler()
    opener = urllib.request.build_opener(redirects)
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with opener.open(request, timeout=45) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return WikiFetchResponse(
            requested_url=url,
            final_url=response.geturl(),
            status=int(response.getcode()),
            body=response.read().decode(charset, errors="replace"),
            redirect_urls=tuple(redirects.redirect_urls),
        )


def random_post_id(client: GelbooruDapiClient) -> int:
    try:
        return int(client.random_post().get("id", ""))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gelbooru random post response has no post ID") from exc


def parse_post_reference(value: str) -> int:
    value = value.strip()
    if value.isdigit():
        return int(value)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(value).query)
    try:
        return int(query.get("id", [""])[0])
    except ValueError as exc:
        raise ValueError("Enter a Gelbooru post ID or post URL") from exc


def _post_tags(post: dict) -> list[str]:
    value = post.get("tags", "")
    if isinstance(value, list):
        return list(dict.fromkeys(str(tag).strip() for tag in value if str(tag).strip()))
    return list(dict.fromkeys(str(value).split()))


class GelbooruWikiAuditService:
    def __init__(
        self,
        tag_database: Path,
        alias_database: Path | None,
        cache: WikiPageCache,
        *,
        dapi_client: GelbooruDapiClient | None = None,
        fetcher: FetchText = fetch_text,
        workers: int = 6,
    ) -> None:
        self.tag_database = Path(tag_database)
        self.alias_database = Path(alias_database) if alias_database else None
        self.cache = cache
        self.dapi_client = dapi_client or GelbooruDapiClient()
        self.fetcher = fetcher
        self.workers = max(1, min(workers, 8))

    def wiki_status(
        self,
        tag_name: str,
        *,
        force: bool = False,
        previous: WikiPageRecord | None | object = _CACHE_UNSET,
    ) -> WikiStatus:
        if previous is _CACHE_UNSET:
            previous = self.cache.get("gelbooru", tag_name)
        if not force and isinstance(previous, WikiPageRecord):
            return _cached_status(previous)
        try:
            page = fetch_wiki_page_details(
                "gelbooru", tag_name, fetcher=self.fetcher
            )
        except WikiPageNotFoundError:
            return WikiStatus(tag_name, False, source="remote")
        except Exception as exc:
            if isinstance(previous, WikiPageRecord):
                return replace(
                    _cached_status(previous),
                    error=str(exc),
                    error_kind="parse" if isinstance(exc, WikiPageParseError) else "network",
                )
            raise
        record = self.cache.put(
            WikiPageRecord(
                site="gelbooru",
                tag=tag_name,
                content=str(page["content"]),
                source_url=str(page["source_url"]),
                content_format=str(page.get("content_format", "plain")),
                wiki_id=page.get("wiki_id"),
                tag_type=page.get("tag_type"),
                author=page.get("author"),
                remote_updated_at=page.get("remote_updated_at"),
                version=page.get("version"),
                referenced_tags=tuple(page.get("referenced_tags", [])),
            )
        )
        return _cached_status(record)

    def audit_post(
        self,
        post_id: int,
        *,
        force: bool = False,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> AuditResult:
        post = self.dapi_client.fetch_post(post_id)
        names = _post_tags(post)
        local = exact_tags(self.tag_database, names)
        local = enrich_tag_rows(self.tag_database, self.alias_database, local)
        by_name = {row.name.casefold(): row for row in local}
        statuses: dict[str, WikiStatus] = {}
        cached_names: set[str] = set()
        cached_pages = self.cache.get_many("gelbooru", names)
        if not force:
            for name in names:
                cached = cached_pages.get(name.casefold())
                if cached is not None:
                    statuses[name] = _cached_status(cached)
                    cached_names.add(name)
        pending = [name for name in names if name not in cached_names]
        completed = len(cached_names)
        if progress:
            progress(completed, len(names), "")
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(
                    self.wiki_status,
                    name,
                    force=True,
                    previous=cached_pages.get(name.casefold()),
                ): name
                for name in pending
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    statuses[name] = future.result()
                except Exception as exc:  # noqa: BLE001 - preserve per-row network failures
                    statuses[name] = WikiStatus(
                        name,
                        False,
                        error=str(exc),
                        error_kind="parse" if isinstance(exc, WikiPageParseError) else "network",
                    )
                completed += 1
                if progress:
                    progress(completed, len(names), name)
        rows: list[AuditTag] = []
        for name in names:
            tag: TagRow | None = by_name.get(name.casefold())
            rows.append(AuditTag(
                name=name,
                category=tag.category if tag else None,
                post_count=tag.post_count if tag else 0,
                direct_alias=tag.direct_alias if tag else None,
                canonical_name=tag.canonical_name if tag else None,
                wiki=statuses[name],
            ))
        rows.sort(key=lambda row: (-row.post_count, row.name.casefold()))
        return AuditResult(post_id, f"{GELBOORU_URL}?page=post&s=view&id={post_id}", tuple(rows))


def summarize(rows: Iterable[AuditTag]) -> tuple[int, int, int, int, int]:
    values = tuple(rows)
    present = sum(row.wiki.exists for row in values)
    missing = sum(not row.wiki.exists and row.wiki.error is None for row in values)
    errors = sum(bool(row.wiki.error) and not row.wiki.exists for row in values)
    suspicious = sum(row.category == 6 or bool(row.canonical_name) for row in values)
    return len(values), present, missing, errors, suspicious
