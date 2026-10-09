"""Local and booru image sources normalized for the analysis domain."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Self

from PIL import Image, ImageOps, UnidentifiedImageError

from booruflow.domain.image_analysis import (
    AnalysisItem,
    AnalysisState,
    DetectedLocalSource,
    InputKind,
    ObservationSource,
    SourceReference,
    SourceTag,
    collection_site_from_path,
    parse_booru_filename,
)
from booruflow.infrastructure.gelbooru_client import API_URL as GELBOORU_API_URL
from booruflow.infrastructure.gelbooru_client import DEFAULT_USER_AGENT, normalize_posts
from booruflow.infrastructure.gelbooru_normalization import decode_tag, normalize_post, post_tags
from booruflow.infrastructure.http_config import BOORUFLOW_USER_AGENT
from booruflow.infrastructure.image_analysis_cache import (
    ImageAnalysisCache,
    image_cache_database_path,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository

E621_API_URL = "https://e621.net/posts"
WD14_MIN_SAMPLE_SIZE = 448
VIDEO_SUFFIXES = frozenset({".webm", ".mp4", ".mov", ".mkv", ".avi"})


def analysis_cache_directory_for_repository(repository: ImageAnalysisRepository) -> Path:
    parent = repository.path.parent
    return (
        parent.parent / "cache" / "image_analysis"
        if parent.name == "state"
        else parent / "cache"
    )


def post_page_url(site: str, post_id: str) -> str:
    if site == "gelbooru":
        return f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
    if site == "e621":
        return f"https://e621.net/posts/{post_id}"
    raise ValueError(f"unsupported booru site: {site}")


def artist_page_url(site:str,artist_tag:str)->str:
    query=urllib.parse.quote(artist_tag)
    if site=="gelbooru":return f"https://gelbooru.com/index.php?page=post&s=list&tags={query}"
    if site=="e621":return f"https://e621.net/posts?tags={query}"
    raise ValueError(f"unsupported booru site: {site}")
E621_USER_AGENT = BOORUFLOW_USER_AGENT


class ImageSourceError(RuntimeError):
    """A source could not be resolved into a usable local image."""


class ImageCacheCapacityError(ImageSourceError):
    """Active jobs own the cache capacity; acquisition must wait for completion."""


class PostNotFoundError(ImageSourceError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedPost:
    site: str
    post_id: str
    file_url: str
    tags: tuple[SourceTag, ...]
    artist_tags: tuple[str, ...]
    original_url: str = ""
    preview_url: str = ""
    sample_url: str = ""
    sample_width: int = 0
    sample_height: int = 0
    width: int = 0
    height: int = 0
    md5: str = ""
    media_type: str = ""


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    sha256: str
    mime_type: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class LocalImportResult:
    item_id: int
    outcome: str
    matched_same_path: bool = False
    matched_local: bool = False
    matched_remote: bool = False


JsonFetcher = Callable[[str, Mapping[str, str]], object]
BytesFetcher = Callable[[str, Mapping[str, str]], bytes]
CategoryLookup = Callable[[tuple[str, ...]], Mapping[str, str]]


def _default_json_fetcher(url: str, headers: Mapping[str, str]) -> object:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PostNotFoundError("post not found or deleted") from exc
        raise ImageSourceError(f"HTTP error while reading post: {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise ImageSourceError(f"could not read post metadata: {exc}") from exc


def _default_bytes_fetcher(url: str, headers: Mapping[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise ImageSourceError(f"could not download image: {exc}") from exc


def inspect_image(path: Path) -> ImageMetadata:
    try:
        return inspect_image_bytes(path.read_bytes(), filename=path.name)
    except OSError as exc:
        raise ImageSourceError(f"invalid or unreadable image {path}: {exc}") from exc


def inspect_image_bytes(data: bytes, *, filename: str = "image") -> ImageMetadata:
    try:
        digest = hashlib.sha256(data).hexdigest()
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            oriented = ImageOps.exif_transpose(image)
            prepared = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")
            width, height = prepared.size
            mime_type = Image.MIME.get(image.format or "") or mimetypes.guess_type(filename)[0]
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ImageSourceError(f"invalid or unreadable image bytes: {exc}") from exc
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "application/octet-stream"
    return ImageMetadata(digest, mime_type, width, height)


class GelbooruPostProvider:
    def __init__(
        self,
        user_id: str = "",
        api_key: str = "",
        *,
        json_fetcher: JsonFetcher = _default_json_fetcher,
        category_lookup: CategoryLookup | None = None,
    ) -> None:
        self.user_id = user_id
        self.api_key = api_key
        self.json_fetcher = json_fetcher
        self.category_lookup = category_lookup
        self._remote_category_cache: dict[str, str] = {}

    def fetch_post(self, post_id: str) -> NormalizedPost:
        parameters = {
            "page": "dapi", "s": "post", "q": "index", "json": "1",
            "limit": "1", "tags": f"id:{post_id}",
        }
        if self.user_id and self.api_key:
            parameters.update(user_id=self.user_id, api_key=self.api_key)
        payload = self.json_fetcher(
            f"{GELBOORU_API_URL}?{urllib.parse.urlencode(parameters)}",
            {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
        )
        posts = normalize_posts(payload)
        if not posts:
            raise PostNotFoundError(f"Gelbooru post {post_id} was not found")
        self._prime_remote_categories(posts)
        return self._normalize(posts[0],post_id)

    def _prime_remote_categories(self, posts: list[dict]) -> None:
        field_categories={"tag_string_artist":"artist","tag_string_character":"character","tag_string_copyright":"copyright","tag_string_meta":"metadata","tag_string_general":"general"}
        for post in posts:
            for field,category in field_categories.items():
                self._remote_category_cache.update((name,category) for name in str(post.get(field,"")).split())
        names = sorted({
            name for post in posts
            for name in str(post.get("tags", post.get("tag_string", ""))).split()
            if name not in self._remote_category_cache
        })
        if self.category_lookup and names:
            self._remote_category_cache.update(self.category_lookup(tuple(names)))
        unresolved = [name for name in names if name not in self._remote_category_cache]
        type_names = {0:"general",1:"artist",3:"copyright",4:"character",5:"metadata"}
        for offset in range(0, len(unresolved), 80):
            chunk = unresolved[offset:offset+80]
            if not chunk: continue
            parameters={"page":"dapi","s":"tag","q":"index","json":"1","limit":str(len(chunk)),"names":" ".join(chunk)}
            if self.user_id and self.api_key:parameters.update(user_id=self.user_id,api_key=self.api_key)
            payload=self.json_fetcher(f"{GELBOORU_API_URL}?{urllib.parse.urlencode(parameters)}",{"User-Agent":DEFAULT_USER_AGENT,"Accept":"application/json"})
            rows=payload.get("tag",[]) if isinstance(payload,dict) else payload if isinstance(payload,list) else []
            for row in rows:
                if isinstance(row,dict) and str(row.get("name","")).strip():
                    self._remote_category_cache[decode_tag(str(row["name"]))]=type_names.get(int(row.get("type",0)),"general")

    def _normalize(self,post:dict,post_id:str)->NormalizedPost:
        post = normalize_post(post)
        file_url = str(post.get("file_url") or "").strip()
        if Path(urllib.parse.urlparse(file_url).path).suffix.casefold() in {".webm", ".mp4", ".mov", ".mkv"}:
            file_url = str(post.get("sample_url") or post.get("preview_url") or "").strip()
        file_url = file_url or str(post.get("sample_url") or post.get("preview_url") or "").strip()
        if not file_url:
            raise ImageSourceError(f"Gelbooru post {post_id} has no image URL")
        names = tuple(post_tags(post))
        categories = dict(self._remote_category_cache)
        categories.update(post.get("_tag_categories", {}) or {})
        if self.category_lookup: categories.update(self.category_lookup(names))
        category_fields = {
            "artist": post.get("tag_string_artist", ""),
            "character": post.get("tag_string_character", ""),
            "copyright": post.get("tag_string_copyright", ""),
            "metadata": post.get("tag_string_meta", ""),
            "general": post.get("tag_string_general", ""),
        }
        for category, values in category_fields.items():
            if isinstance(values, str):
                categories.update((name, category) for name in values.split())
        tags = tuple(
            SourceTag(name, ObservationSource.GELBOORU, categories.get(name)) for name in names
        )
        artists = tuple(name for name in names if categories.get(name) == "artist")
        return NormalizedPost(
            "gelbooru", str(post.get("id", post_id)), file_url, tags, artists,
            original_url=str(post.get("file_url") or "").strip(),
            preview_url=str(post.get("preview_url") or "").strip(),
            sample_url=str(post.get("sample_url") or "").strip(),
            sample_width=int(post.get("sample_width") or 0),
            sample_height=int(post.get("sample_height") or 0),
            width=int(post.get("width") or 0),
            height=int(post.get("height") or 0),
            md5=str(post.get("md5") or "").strip(),
            media_type=str(post.get("image") or post.get("type") or "").strip().casefold(),
        )

    def normalize_search_post(self, post: dict, post_id: str = "") -> NormalizedPost:
        """Normalize a post already returned by a Gelbooru search page."""
        return self._normalize(post, post_id or str(post.get("id", "")))

    def search_posts(self,tags:list[str],limit:int)->list[NormalizedPost]:
        parameters={"page":"dapi","s":"post","q":"index","json":"1","limit":str(max(1,min(limit,100))),"tags":" ".join(tags)}
        if self.user_id and self.api_key:parameters.update(user_id=self.user_id,api_key=self.api_key)
        payload=self.json_fetcher(f"{GELBOORU_API_URL}?{urllib.parse.urlencode(parameters)}",{"User-Agent":DEFAULT_USER_AGENT,"Accept":"application/json"})
        posts=normalize_posts(payload);self.last_search_post_count=len(posts);self._prime_remote_categories(posts)
        return [self._normalize(post,str(post.get("id",""))) for post in posts]

    def discover_candidates(self,tags:list[str],limit:int)->list[str]:
        candidates=[]
        for post in self.search_posts(tags,max(limit*2,20)):
            candidates.extend(post.artist_tags)
        return list(dict.fromkeys(candidates))[:limit]

    def sample_artist_posts(self,artist_tag:str,limit:int)->list[NormalizedPost]:return self.search_posts([artist_tag],limit)
    def resolve_post_by_md5(self,source_md5:str)->NormalizedPost:
        posts=self.search_posts([f"md5:{source_md5}"],1)
        if not posts:raise PostNotFoundError(f"Gelbooru MD5 {source_md5} was not found")
        return posts[0]


class E621PostProvider:
    def __init__(
        self,
        username: str = "",
        api_key: str = "",
        *,
        json_fetcher: JsonFetcher | None = None,
        client=None,
    ) -> None:
        if json_fetcher is None and (client is not None or (username and api_key)):
            from booruflow.infrastructure.e621_client import E621Client

            client = client or E621Client(username, api_key)

            def authenticated_fetcher(url: str, _headers: Mapping[str, str]) -> object:
                parsed = urllib.parse.urlsplit(url)
                return client.request_json(parsed.path, dict(urllib.parse.parse_qsl(parsed.query)))

            self.json_fetcher = authenticated_fetcher
        else:
            self.json_fetcher = json_fetcher or _default_json_fetcher

    def fetch_post(self, post_id: str) -> NormalizedPost:
        payload = self.json_fetcher(
            f"{E621_API_URL}/{urllib.parse.quote(str(post_id))}.json",
            {"User-Agent": E621_USER_AGENT, "Accept": "application/json"},
        )
        post = payload.get("post") if isinstance(payload, dict) else None
        if not isinstance(post, dict):
            raise PostNotFoundError(f"e621 post {post_id} was not found")
        return self._normalize(post,post_id)

    def _normalize(self,post:dict,post_id:str)->NormalizedPost:
        file_data = post.get("file", {})
        file_url = str(file_data.get("url") or "").strip() if isinstance(file_data, dict) else ""
        if not file_url:
            raise ImageSourceError(f"e621 post {post_id} has no image URL")
        groups = post.get("tags", {})
        tags: list[SourceTag] = []
        artists: list[str] = []
        if isinstance(groups, dict):
            for category, values in groups.items():
                if not isinstance(values, list):
                    continue
                for value in values:
                    name = str(value).strip()
                    if name:
                        tags.append(SourceTag(name, ObservationSource.E621, str(category)))
                        if str(category) == "artist":
                            artists.append(name)
        return NormalizedPost(
            "e621", str(post.get("id", post_id)), file_url, tuple(tags), tuple(artists)
        )

    def search_posts(self,tags:list[str],limit:int)->list[NormalizedPost]:
        query=urllib.parse.urlencode({"tags":" ".join(tags),"limit":str(max(1,min(limit,320)))})
        payload=self.json_fetcher(f"{E621_API_URL}.json?{query}",{"User-Agent":E621_USER_AGENT,"Accept":"application/json"});posts=payload.get("posts",[]) if isinstance(payload,dict) else [];self.last_search_post_count=len(posts)
        return [self._normalize(post,str(post.get("id",""))) for post in posts if isinstance(post,dict)]

    def discover_candidates(self,tags:list[str],limit:int)->list[str]:
        candidates=[]
        for post in self.search_posts(tags,max(limit*2,20)):candidates.extend(post.artist_tags)
        return list(dict.fromkeys(candidates))[:limit]

    def sample_artist_posts(self,artist_tag:str,limit:int)->list[NormalizedPost]:return self.search_posts([artist_tag],limit)
    def resolve_post_by_md5(self,source_md5:str)->NormalizedPost:
        posts=self.search_posts([f"md5:{source_md5}"],1)
        if not posts:raise PostNotFoundError(f"e621 MD5 {source_md5} was not found")
        return posts[0]


class ImageSourceService:
    def __init__(
        self,
        repository: ImageAnalysisRepository,
        cache_directory: Path,
        *,
        bytes_fetcher: BytesFetcher = _default_bytes_fetcher,
    ) -> None:
        self.repository = repository
        self.cache_directory = cache_directory
        self._cache_path = image_cache_database_path(cache_directory)
        self._cache: ImageAnalysisCache | None = None
        self.bytes_fetcher = bytes_fetcher

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def cache(self) -> ImageAnalysisCache:
        if self._cache is None:
            self._cache = ImageAnalysisCache(
                self._cache_path, protected_keys=self.repository.active_source_cache_keys
            )
        return self._cache

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()
            self._cache = None

    def add_local(self, path: Path) -> int:
        return self.add_local_with_result(path).item_id

    def add_local_with_result(self, path: Path) -> LocalImportResult:
        resolved = path.resolve()
        if not resolved.is_file():
            raise ImageSourceError(f"local image does not exist: {path}")
        metadata = inspect_image(resolved)
        source = SourceReference(InputKind.LOCAL_FILE, original_path=resolved)
        existing = self.repository.item_by_sha256(metadata.sha256)
        if existing is not None:
            previous = [dict(value) for value in self.repository.provenances(existing.id)]
            matched_same_path = any(
                value.get("local_path") == str(resolved) for value in previous
            )
            matched_local = any(value.get("kind") == "local_file" for value in previous)
            matched_remote = any(value.get("site") for value in previous)
            visible = self.repository.item_queue_visible(existing.id)
            self.repository.reuse_item(existing.id, source)
            outcome = (
                "already_queued"
                if visible and existing.state in {
                    AnalysisState.PENDING, AnalysisState.PROCESSING,
                    AnalysisState.READY_FOR_REVIEW, AnalysisState.FAILED,
                }
                else f"known_{existing.state.value}"
            )
            self.apply_filename_metadata(existing.id,resolved)
            return LocalImportResult(
                existing.id, outcome, matched_same_path, matched_local, matched_remote
            )
        item_id = self.repository.add_item(
            AnalysisItem(
                source,
                cached_path=resolved,
                content_sha256=metadata.sha256,
                mime_type=metadata.mime_type,
                width=metadata.width,
                height=metadata.height,
            )
        )
        self.apply_filename_metadata(item_id,resolved)
        return LocalImportResult(item_id, "new")

    def apply_filename_metadata(self,item_id:int,path:Path)->dict|None:
        parsed=parse_booru_filename(path)
        if parsed is None:return None
        site=collection_site_from_path(path) or "local";associations=self.repository.artist_associations(item_id)
        parsed_names={value.casefold() for value in parsed.artists}
        conflicts=[value for value in associations if value["artist_tag"].casefold() not in parsed_names]
        if conflicts:
            reason="artist metadata conflict: "+", ".join(f"{value['site']}:{value['artist_tag']} ({value['provenance']})" for value in conflicts)
            self.repository.record_filename_metadata(item_id,path,parsed.artist,parsed.post_id,parsed.rating,parsed.source_md5,site,"conflict",reason)
            return {"state":"conflict","item_id":item_id,"artist":parsed.artist,"site":site,"reason":reason}
        reliable=any(value["provenance"] in {"source_tag","manual"} for value in associations)
        if not reliable:
            for artist in parsed.artists:self.repository.assign_artist([item_id],site,artist,"filename_metadata")
        if site in {"gelbooru","e621"}:self.repository.link_local_source(item_id,site,parsed.post_id,"high")
        self.repository.record_filename_metadata(item_id,path,parsed.artist,parsed.post_id,parsed.rating,parsed.source_md5,site,"applied")
        return {"state":"applied","item_id":item_id,"artist":parsed.artist,"artists":parsed.artists,"ambiguous":len(parsed.artists)>2,"site":site,"post_id":parsed.post_id,"rating":parsed.rating,"source_md5":parsed.source_md5}

    def preview_filename_repairs(self)->dict:
        rows=self.repository.unassigned_artist_diagnostics();result={"without_artist":len(rows),"compatible":0,"artists":set(),"gelbooru":0,"e621":0,"local":0,"unrecognized":0,"items":[]}
        for row in rows:
            paths=[Path(str(value["local_path"])) for value in row["provenances"] if value.get("local_path")]
            parsed_entries=[(path,parse_booru_filename(path)) for path in paths];parsed_entries=[value for value in parsed_entries if value[1] is not None]
            if not parsed_entries:result["unrecognized"]+=1;continue
            identities={(collection_site_from_path(path) or "local",artist) for path,parsed in parsed_entries for artist in parsed.artists}
            if len(identities)!=1:result["unrecognized"]+=1;continue
            site,artist=next(iter(identities));result["compatible"]+=1;result["artists"].add((site,artist));result[site]+=1;result["items"].append((row["item_id"],parsed_entries[0][0]))
        result["artist_count"]=len(result.pop("artists"));return result

    def repair_filename_metadata(self,preview:dict|None=None)->dict:
        report=preview or self.preview_filename_repairs();applied=conflicts=0
        for item_id,path in report["items"]:
            outcome=self.apply_filename_metadata(int(item_id),Path(path))
            applied+=int(bool(outcome) and outcome["state"]=="applied");conflicts+=int(bool(outcome) and outcome["state"]=="conflict")
        return {**{key:value for key,value in report.items() if key!="items"},"associated":applied,"conflicts":conflicts}

    def enrich_local(
        self,
        item_id: int,
        detected: DetectedLocalSource,
        provider: GelbooruPostProvider | E621PostProvider,
    ) -> None:
        """Attach booru metadata to a local item without downloading its remote image."""
        self.repository.link_local_source(
            item_id, detected.site, detected.post_id, detected.confidence
        )
        cached = self.repository.cached_post_metadata(detected.site, detected.post_id)
        if cached is None:
            try:
                post = provider.fetch_post(detected.post_id)
            except PostNotFoundError as exc:
                self.repository.cache_missing_post(
                    detected.site, detected.post_id, str(exc)
                )
                raise
            self.repository.cache_post_metadata(
                detected.site, detected.post_id, post.file_url, post.tags, post.artist_tags
            )
            tags, artists = post.tags, post.artist_tags
        else:
            if cached["state"] == "missing":
                raise PostNotFoundError(str(cached["last_error"] or "post not found or deleted"))
            source = (
                ObservationSource.GELBOORU
                if detected.site == "gelbooru" else ObservationSource.E621
            )
            tags = tuple(
                SourceTag(str(value["name"]), source, value.get("category"))
                for value in json.loads(str(cached["tags_json"]))
            )
            artists = tuple(str(value) for value in json.loads(str(cached["artist_tags_json"])))
        self.repository.apply_local_enrichment(
            item_id, detected.site, detected.post_id, tags, artists
        )

    def add_post(
        self,
        provider: GelbooruPostProvider | E621PostProvider,
        post_id: str,
        *,
        request_analysis: bool = True,
        trace: Callable[[str, object], None] | None = None,
    ) -> int:
        started = perf_counter()
        post = provider.fetch_post(post_id)
        if trace:
            trace("metadata_fetch_ms", (perf_counter() - started) * 1000)
        if trace:
            trace("fallback_fetch", True)
        item = self._download_post(post, source_kind="original", trace=trace)
        return self._store_remote_item(item, post, request_analysis=request_analysis)

    def add_post_data(
        self,
        provider: GelbooruPostProvider,
        raw_post: dict,
        post_id: str,
        *,
        request_analysis: bool = True,
        trace: Callable[[str, object], None] | None = None,
    ) -> int:
        """Acquire a search result without refetching its metadata when possible."""
        raw_urls = (
            str(raw_post.get("file_url") or "").strip(),
            str(raw_post.get("sample_url") or "").strip(),
            str(raw_post.get("preview_url") or "").strip(),
        )
        fallback = not any(raw_urls)
        if fallback:
            started = perf_counter()
            post = provider.fetch_post(post_id)
            if trace:
                trace("metadata_fetch_ms", (perf_counter() - started) * 1000)
                trace("fallback_fetch", True)
        else:
            post = provider.normalize_search_post(raw_post, post_id)
            if trace:
                trace("fallback_fetch", False)
        selected, source_kind = self._select_wd14_source(post)
        if trace:
            trace("source_kind", source_kind)
        item = self._download_post(selected, source_kind=source_kind, trace=trace)
        return self._store_remote_item(item, post, request_analysis=request_analysis)

    def _store_remote_item(
        self,
        item: AnalysisItem,
        post: NormalizedPost,
        *,
        request_analysis: bool,
    ) -> int:
        existing = self.repository.item_by_sha256(str(item.content_sha256))
        if existing is not None:
            self.repository.reuse_item(existing.id, item.source, post.tags, post.artist_tags, queue_visible=True if request_analysis else None)
            return existing.id
        return self.repository.add_item(item, post.tags, post.artist_tags, request_analysis=request_analysis)

    def resolve_post(
        self,
        item_id: int,
        provider: GelbooruPostProvider | E621PostProvider,
        post_id: str,
        *,
        trace: Callable[[str, object], None] | None = None,
    ) -> None:
        started = perf_counter()
        post = provider.fetch_post(post_id)
        if trace:
            trace("metadata_fetch_ms", (perf_counter() - started) * 1000)
        if trace:
            trace("fallback_fetch", True)
        item = self._download_post(post, source_kind="original", trace=trace)
        self.repository.resolve_item(item_id, item, post.tags, post.artist_tags)

    def resolve_post_data(
        self,
        item_id: int,
        provider: GelbooruPostProvider,
        raw_post: dict,
        post_id: str,
        *,
        trace: Callable[[str, object], None] | None = None,
    ) -> None:
        raw_urls = (
            str(raw_post.get("file_url") or "").strip(),
            str(raw_post.get("sample_url") or "").strip(),
            str(raw_post.get("preview_url") or "").strip(),
        )
        if any(raw_urls):
            post = provider.normalize_search_post(raw_post, post_id)
            if trace:
                trace("fallback_fetch", False)
        else:
            started = perf_counter()
            post = provider.fetch_post(post_id)
            if trace:
                trace("metadata_fetch_ms", (perf_counter() - started) * 1000)
                trace("fallback_fetch", True)
        selected, source_kind = self._select_wd14_source(post)
        if trace:
            trace("source_kind", source_kind)
        item = self._download_post(selected, source_kind=source_kind, trace=trace)
        self.repository.resolve_item(item_id, item, post.tags, post.artist_tags)

    def _select_wd14_source(self, post: NormalizedPost) -> tuple[NormalizedPost, str]:
        original_url = post.original_url or post.file_url
        original_suffix = Path(urllib.parse.urlparse(original_url).path).suffix.casefold()
        if original_suffix in VIDEO_SUFFIXES or post.media_type in {"webm", "mp4", "mov", "mkv", "avi"}:
            if not post.preview_url:
                raise ImageSourceError("video post has no preview image for WD14")
            return self._with_download_url(post, post.preview_url), "preview"
        if (
            post.sample_url
            and post.sample_width >= WD14_MIN_SAMPLE_SIZE
            and post.sample_height >= WD14_MIN_SAMPLE_SIZE
        ):
            return self._with_download_url(post, post.sample_url), "sample"
        if original_url:
            return self._with_download_url(post, original_url), "original"
        if post.preview_url:
            return self._with_download_url(post, post.preview_url), "preview"
        raise ImageSourceError("post has no usable static image URL")

    @staticmethod
    def _with_download_url(post: NormalizedPost, url: str) -> NormalizedPost:
        return NormalizedPost(
            post.site, post.post_id, url, post.tags, post.artist_tags,
            post.original_url, post.preview_url, post.sample_url,
            post.sample_width, post.sample_height, post.width, post.height, post.md5,
            post.media_type,
        )

    def _download_post(
        self,
        post: NormalizedPost,
        *,
        source_kind: str = "original",
        trace: Callable[[str, object], None] | None = None,
    ) -> AnalysisItem:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT if post.site == "gelbooru" else E621_USER_AGENT,
            "Referer": "https://gelbooru.com/" if post.site == "gelbooru" else "https://e621.net/",
        }
        started = perf_counter()
        data = self.bytes_fetcher(post.file_url, headers)
        if trace:
            trace("download_bytes_ms", (perf_counter() - started) * 1000)
            trace("download_size_bytes", len(data))
        started = perf_counter()
        digest = hashlib.sha256(data).hexdigest()
        if trace:
            trace("hash_ms", (perf_counter() - started) * 1000)
        suffix = Path(urllib.parse.urlparse(post.file_url).path).suffix.lower() or ".img"
        started = perf_counter()
        metadata = inspect_image_bytes(data, filename=f"image{suffix}")
        if trace:
            trace("inspect_image_ms", (perf_counter() - started) * 1000)
        started = perf_counter()
        stored = self.cache.put(
            digest, data, site=post.site, post_id=post.post_id,
            source_kind=source_kind, mime_type=metadata.mime_type,
            width=metadata.width, height=metadata.height, sha256=metadata.sha256,
        )
        if not stored.stored:
            if stored.reason == "active_sources_full":
                raise ImageCacheCapacityError("analysis cache is occupied by active jobs; acquisition deferred")
            raise ImageSourceError(
                f"image exceeds analysis cache limit ({len(data)} bytes)"
            )
        if trace:
            trace("cache_write_ms", (perf_counter() - started) * 1000)
            trace("cache_db_path", str(self._cache_path))
            trace("cache_entry_stored", True)
            trace("cache_key", digest)
            trace("cache_logical_bytes", stored.logical_bytes)
            trace("cache_entry_count", stored.entry_count)
            trace("cache_evicted", stored.evicted)
        kind = InputKind.GELBOORU_POST if post.site == "gelbooru" else InputKind.E621_POST
        item = AnalysisItem(
            SourceReference(kind, site=post.site, post_id=post.post_id),
            cached_path=None,
            content_sha256=metadata.sha256,
            mime_type=metadata.mime_type,
            width=metadata.width,
            height=metadata.height,
        )
        return item

    def validate_item_file(self, item: AnalysisItem) -> Path:
        path = item.cached_path
        if path is None or not path.is_file():
            raise ImageSourceError(f"source image is missing: {path or item.source.original_path}")
        metadata = inspect_image(path)
        if metadata.sha256 != item.content_sha256:
            raise ImageSourceError(
                "source image content changed after import; add it again as a new source"
            )
        return path

    def cached_bytes(
        self, item: AnalysisItem, *, trace: Callable[[str, object], None] | None = None
    ) -> bytes:
        cache_key = str(item.content_sha256 or "")
        if cache_key:
            entry = self.cache.get(cache_key)
            if entry is not None:
                if trace:
                    trace("preview_source", "blob")
                    trace("cache_hit", cache_key)
                    trace("cache_read_bytes", entry.byte_size)
                if entry.sha256 != cache_key:
                    raise ImageSourceError("source image cache hash mismatch")
                return entry.data
            if trace:
                trace("cache_miss", cache_key)
        if item.cached_path is not None and item.cached_path.is_file():
            if trace:
                trace("preview_source", "legacy_path")
                trace("cache_hit", "legacy_path")
            return item.cached_path.read_bytes()
        if trace:
            trace("preview_source", "unavailable")
        if cache_key:
            raise ImageSourceError(
                f"source image unavailable: cache miss {cache_key} and missing legacy path"
            )
        raise ImageSourceError(
            f"source image is missing: {item.cached_path or item.source.original_path}"
        )

    def cached_items_for_posts(
        self, sources: list[tuple[str, str]] | tuple[tuple[str, str], ...]
    ) -> dict[tuple[str, str], AnalysisItem]:
        """Resolve posts whose source bytes are currently available locally."""
        items = self.repository.items_by_remote_sources(sources)
        blob_keys = [str(item.content_sha256) for item in items.values() if item.content_sha256]
        available_blobs = self.cache.contains_many(blob_keys) if blob_keys else set()
        return {
            key: item
            for key, item in items.items()
            if (
                bool(item.content_sha256 and str(item.content_sha256) in available_blobs)
                or bool(item.cached_path is not None and item.cached_path.is_file())
            )
        }

    def materialize_item(self, item: AnalysisItem):
        """Yield a real local path, deleting cache-backed temporaries on exit."""
        if item.cached_path is not None:
            if not item.cached_path.is_file():
                raise ImageSourceError(f"source image is missing: {item.cached_path}")
            from contextlib import nullcontext

            return nullcontext(item.cached_path)
        if not item.content_sha256:
            raise ImageSourceError("source image has no cache key")
        suffix = mimetypes.guess_extension(item.mime_type or "") or ".img"
        return self.cache.materialize(item.content_sha256, suffix=suffix)

    def migrate_legacy_cache(
        self,
        *,
        limit: int | None = None,
        logger: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Import only DB-linked loose files; delete each one after both commits."""
        log = logger or (lambda _message: None)
        report = {"imported": 0, "discarded": 0, "deleted": 0, "skipped": 0,
                  "failed": 0, "remaining": 0, "remaining_bytes": 0}
        if not self.cache_directory.is_dir():
            return report
        files = sorted(path for path in self.cache_directory.iterdir() if path.is_file())
        selected = files if limit is None else files[:max(0, int(limit))]
        total = len(selected)
        if progress:
            progress(0, total)
        for index, path in enumerate(selected, start=1):
            if cancelled and cancelled():
                break
            rows = self.repository.connection.execute(
                """SELECT id,source_site,source_post_id,content_sha256,mime_type,width,height
                   FROM analysis_items WHERE cached_path=? ORDER BY id""",
                (str(path),),
            ).fetchall()
            if not rows:
                # A crash after committing the DB updates but before unlinking
                # leaves an orphan named with its content hash.  Delete it only
                # when both the filename and the already committed cache row
                # prove that it is the same payload.
                try:
                    data = path.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    if path.stem == digest and self.cache.contains(digest):
                        path.unlink()
                        report["deleted"] += 1
                    else:
                        report["skipped"] += 1
                except OSError as exc:
                    report["failed"] += 1
                    log(f"legacy image cache cleanup failed path={path.name}: {exc}")
                if progress:
                    progress(index, total)
                continue
            try:
                data = path.read_bytes()
                metadata = inspect_image_bytes(data, filename=path.name)
                expected = {str(row["content_sha256"] or "") for row in rows}
                if expected != {metadata.sha256}:
                    raise ImageSourceError("legacy cache hash does not match analysis item")
                first = rows[0]
                result = self.cache.put(
                    metadata.sha256, data, site=str(first["source_site"] or ""),
                    post_id=str(first["source_post_id"] or ""), source_kind="legacy",
                    mime_type=metadata.mime_type, width=metadata.width,
                    height=metadata.height, sha256=metadata.sha256,
                )
                if not result.stored:
                    raise ImageSourceError("legacy file exceeds cache limit")
                with self.repository.connection:
                    self.repository.connection.execute(
                        "UPDATE analysis_items SET cached_path=NULL WHERE cached_path=?",
                        (str(path),),
                    )
                report["imported"] += 1
                path.unlink()
                report["deleted"] += 1
            except Exception as exc:  # noqa: BLE001 - preserve loose file on any failure
                report["failed"] += 1
                log(f"legacy image cache migration failed path={path.name}: {exc}")
            if progress:
                progress(index, total)
        remaining = [path for path in self.cache_directory.iterdir() if path.is_file()]
        report["remaining"] = len(remaining)
        report["remaining_bytes"] = sum(path.stat().st_size for path in remaining)
        log(
            "legacy image cache migration "
            + " ".join(f"{key}={value}" for key, value in report.items())
        )
        return report
