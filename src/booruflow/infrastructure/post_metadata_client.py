"""Conservative single-post readers for Gelbooru and e621."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from booruflow.domain.auto_organize import PostMetadata
from booruflow.infrastructure.gelbooru_client import API_URL, DEFAULT_USER_AGENT, normalize_posts


class PostNotFoundError(LookupError):
    def __init__(self, message: str, *, site: str = "", endpoint: str = "") -> None:
        super().__init__(message)
        self.site = site
        self.endpoint = endpoint


@dataclass(frozen=True, slots=True)
class FetchFailure:
    site: str
    stage: str
    endpoint: str
    exception_type: str
    message: str
    status: int | None = None
    attempt: int = 1

    @property
    def signature(self) -> str:
        return f"{self.site}|{self.stage}|{self.status or ''}|{self.exception_type}|{self.message}"


class MetadataFetchError(RuntimeError):
    def __init__(self, failure: FetchFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def safe_endpoint(url: str) -> str:
    parsed=urllib.parse.urlsplit(url); query=urllib.parse.parse_qsl(parsed.query,keep_blank_values=True)
    filtered=[(key,value) for key,value in query if key.casefold() not in {"user_id","api_key"}]
    return urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urllib.parse.urlencode(filtered),""))


def _read_json(url: str, *, site: str, user_agent: str = DEFAULT_USER_AGENT) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    endpoint=safe_endpoint(url)
    try:
        # Bounded so cooperative cancellation cannot remain stuck indefinitely.
        with urllib.request.urlopen(request, timeout=10) as response:
            raw=response.read(); charset=response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PostNotFoundError(
                "post not found or deleted", site=site, endpoint=endpoint
            ) from exc
        raise MetadataFetchError(FetchFailure(site,"remote_fetch",endpoint,type(exc).__name__,str(exc),exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MetadataFetchError(FetchFailure(site,"remote_fetch",endpoint,type(exc).__name__,str(exc))) from exc
    try:
        return json.loads(raw.decode(charset,errors="replace"))
    except (UnicodeError,json.JSONDecodeError,TypeError) as exc:
        raise MetadataFetchError(FetchFailure(site,"response_parsing",endpoint,type(exc).__name__,str(exc))) from exc


def fetch_post(site: str, post_id: str, user_id: str = "", api_key: str = "") -> PostMetadata:
    if site == "gelbooru":
        params = {"page":"dapi","s":"post","q":"index","json":"1","tags":f"id:{post_id}","limit":"1"}
        if user_id and api_key: params.update(user_id=user_id, api_key=api_key)
        url=f"{API_URL}?{urllib.parse.urlencode(params)}"
        posts = normalize_posts(_read_json(url,site=site))
        if not posts: raise PostNotFoundError(post_id,site=site,endpoint=safe_endpoint(url))
        post = posts[0]; tags = tuple(str(post.get("tags", "")).split())
        raw_categories = post.get("_tag_categories", {})
        categories = raw_categories if isinstance(raw_categories, dict) else {}
        # Gelbooru post DAPI has no dependable per-tag categories; caller may enrich later.
        artists = tuple(tag for tag in tags if str(categories.get(tag, "")) == "artist")
        return PostMetadata(site, post_id, tags, {str(k):str(v) for k,v in categories.items()}, artists,
                            rating=str(post.get("rating", "")), source=str(post.get("source", "")),
                            md5=str(post.get("md5", "")), extra={"created_at":post.get("created_at", "")})
    if site == "e621":
        data = _read_json(f"https://e621.net/posts/{post_id}.json",site=site,
                          user_agent="BooruFlow/0.1 (personal library tool; local application)")
        post = data.get("post", {}) if isinstance(data, dict) else {}
        if not post:
            raise PostNotFoundError(
                post_id,site=site,endpoint=f"https://e621.net/posts/{post_id}.json"
            )
        groups = post.get("tags", {}) if isinstance(post.get("tags"), dict) else {}
        mapping = {"artist":"artist","copyright":"copyright","character":"character","species":"species"}
        categories = {str(tag): mapping.get(str(group), str(group)) for group, values in groups.items()
                      if isinstance(values, list) for tag in values}
        tags = tuple(sorted(categories))
        rating={"s":"safe","q":"questionable","e":"explicit"}.get(str(post.get("rating", "")),str(post.get("rating", "")))
        return PostMetadata(site, post_id, tags, categories,
            tuple(groups.get("artist", ())), tuple(groups.get("copyright", ())),
            tuple(groups.get("character", ())), tuple(groups.get("species", ())),
            rating, str(post.get("sources", [""])[0] if post.get("sources") else ""),
            str(post.get("file", {}).get("md5", "")), {"created_at":post.get("created_at", {})})
    raise ValueError(f"Unsupported site: {site}")
