"""Local and booru image sources normalized for the analysis domain."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

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
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository

E621_API_URL = "https://e621.net/posts"


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
E621_USER_AGENT = "BooruFlow/0.1 (personal local image analysis tool)"


class ImageSourceError(RuntimeError):
    """A source could not be resolved into a usable local image."""


class PostNotFoundError(ImageSourceError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedPost:
    site: str
    post_id: str
    file_url: str
    tags: tuple[SourceTag, ...]
    artist_tags: tuple[str, ...]


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
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with Image.open(path) as image:
            image.seek(0)
            oriented = ImageOps.exif_transpose(image)
            prepared = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")
            width, height = prepared.size
            mime_type = Image.MIME.get(image.format or "") or mimetypes.guess_type(path.name)[0]
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ImageSourceError(f"invalid or unreadable image {path}: {exc}") from exc
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
                    self._remote_category_cache[str(row["name"])]=type_names.get(int(row.get("type",0)),"general")

    def _normalize(self,post:dict,post_id:str)->NormalizedPost:
        file_url = str(post.get("file_url") or post.get("sample_url") or "").strip()
        if not file_url:
            raise ImageSourceError(f"Gelbooru post {post_id} has no image URL")
        raw_tags = post.get("tags", post.get("tag_string", ""))
        names = tuple(str(raw_tags).split()) if isinstance(raw_tags, str) else ()
        categories = dict(self._remote_category_cache)
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
        return NormalizedPost("gelbooru", str(post.get("id", post_id)), file_url, tags, artists)

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
    def __init__(self, *, json_fetcher: JsonFetcher = _default_json_fetcher) -> None:
        self.json_fetcher = json_fetcher

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
        self.bytes_fetcher = bytes_fetcher

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

    def add_post(self, provider: GelbooruPostProvider | E621PostProvider, post_id: str, *, request_analysis: bool = True) -> int:
        post = provider.fetch_post(post_id)
        item = self._download_post(post)
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
    ) -> None:
        post = provider.fetch_post(post_id)
        item = self._download_post(post)
        self.repository.resolve_item(item_id, item, post.tags, post.artist_tags)

    def _download_post(self, post: NormalizedPost) -> AnalysisItem:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT if post.site == "gelbooru" else E621_USER_AGENT,
            "Referer": "https://gelbooru.com/" if post.site == "gelbooru" else "https://e621.net/",
        }
        data = self.bytes_fetcher(post.file_url, headers)
        digest = hashlib.sha256(data).hexdigest()
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        suffix = Path(urllib.parse.urlparse(post.file_url).path).suffix.lower() or ".img"
        cached = self.cache_directory / f"{digest}{suffix}"
        if not cached.exists():
            temporary = cached.with_suffix(cached.suffix + ".part")
            temporary.write_bytes(data)
            os.replace(temporary, cached)
        metadata = inspect_image(cached)
        kind = InputKind.GELBOORU_POST if post.site == "gelbooru" else InputKind.E621_POST
        item = AnalysisItem(
            SourceReference(kind, site=post.site, post_id=post.post_id),
            cached_path=cached,
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
