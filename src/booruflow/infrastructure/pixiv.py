"""Pixiv App API adapter for Image Finder.

The endpoint shape follows Grabber's Pixiv source, while this implementation is
independent Python code and exposes only Image Finder domain objects.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from booruflow.domain.image_finder import (
    RemoteArtist,
    RemoteArtwork,
    RemoteImage,
    RemoteMediaType,
    SearchMode,
    SearchQuery,
    SearchResult,
)

API_ROOT = "https://app-api.pixiv.net"
TOKEN_URL = "https://oauth.secure.pixiv.net/auth/token"
PIXIV_WEB = "https://www.pixiv.net"
CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
CLIENT_SECRET = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
APP_HEADERS = {
    "Accept-Language": "en-us",
    "App-OS": "android",
    "App-OS-Version": "9.0",
    "App-Version": "5.0.234",
    "User-Agent": "PixivAndroidApp/5.0.234 (Android 9.0; Pixel 3)",
}
SEARCH_TARGETS = {
    SearchMode.PARTIAL: "partial_match_for_tags",
    SearchMode.EXACT: "exact_match_for_tags",
    SearchMode.TITLE_CAPTION: "title_and_caption",
}


class PixivError(RuntimeError):
    pass


class PixivAuthenticationError(PixivError):
    pass


JsonRequest = Callable[[str, str, Mapping[str, str], bytes | None], object]
BytesRequest = Callable[[str, Mapping[str, str]], bytes]


def _json_request(method: str, url: str, headers: Mapping[str, str], data: bytes | None) -> object:
    request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise PixivAuthenticationError("Pixiv authentication was rejected") from exc
        if exc.code == 404:
            raise PixivError("Pixiv artwork is unavailable or private") from exc
        raise PixivError(f"Pixiv HTTP error {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise PixivError(f"Pixiv request failed: {exc}") from exc


def _bytes_request(url: str, headers: Mapping[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise PixivError(f"Pixiv image download failed: {exc}") from exc


def parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _rating(payload: Mapping[str, object]) -> str:
    age_limit = str(payload.get("age_limit") or "").casefold()
    if age_limit == "all-age":
        return "safe"
    if age_limit in {"r18", "r18g"}:
        return "explicit"
    return "explicit" if int(payload.get("x_restrict") or 0) else "safe"


def parse_artwork(payload: Mapping[str, object]) -> RemoteArtwork:
    artwork_id = str(payload.get("id") or "").strip()
    if not artwork_id:
        raise PixivError("Pixiv artwork response has no ID")
    user = payload.get("user") if isinstance(payload.get("user"), Mapping) else {}
    artist_id = str(user.get("id") or "")
    artist = RemoteArtist(
        "pixiv", artist_id, str(user.get("name") or ""),
        f"{PIXIV_WEB}/users/{artist_id}" if artist_id else None,
    )
    source_url = f"{PIXIV_WEB}/artworks/{artwork_id}"
    meta_pages = payload.get("meta_pages")
    pages = meta_pages if isinstance(meta_pages, list) and meta_pages else [payload]
    images: list[RemoteImage] = []
    illust_type = str(payload.get("type") or "")
    for index, page in enumerate(pages):
        page_data = page if isinstance(page, Mapping) else {}
        urls = page_data.get("image_urls")
        if not isinstance(urls, Mapping):
            urls = payload.get("image_urls") if isinstance(payload.get("image_urls"), Mapping) else {}
        single = payload.get("meta_single_page")
        original = str(urls.get("original") or "")
        if not original and isinstance(single, Mapping):
            original = str(single.get("original_image_url") or "")
        images.append(RemoteImage(
            source="pixiv", artwork_id=artwork_id, page_index=index, source_url=source_url,
            preview_url=str(urls.get("small") or "") or None,
            sample_url=str(urls.get("medium") or urls.get("large") or "") or None,
            original_url=original or None,
            width=int(payload.get("width") or 0) or None,
            height=int(payload.get("height") or 0) or None,
            media_type=RemoteMediaType.UGOIRA if illust_type == "ugoira" else RemoteMediaType.IMAGE,
        ))
    raw_tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    tags = tuple(
        str(tag.get("name") or "") for tag in raw_tags if isinstance(tag, Mapping) and tag.get("name")
    )
    return RemoteArtwork(
        source="pixiv", source_post_id=artwork_id, source_url=source_url, artist=artist,
        title=str(payload.get("title") or ""), description=str(payload.get("caption") or ""),
        tags=tags, rating=_rating(payload), created_at=parse_datetime(payload.get("create_date")),
        images=tuple(images),
    )


class PixivSource:
    source_id = "pixiv"

    def __init__(
        self,
        refresh_token: str,
        *,
        json_request: JsonRequest = _json_request,
        bytes_request: BytesRequest = _bytes_request,
    ) -> None:
        self.refresh_token = refresh_token.strip()
        self.json_request = json_request
        self.bytes_request = bytes_request
        self.access_token = ""

    @property
    def connected(self) -> bool:
        return bool(self.refresh_token)

    def refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise PixivAuthenticationError("Pixiv is not connected")
        body = urllib.parse.urlencode({
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token", "refresh_token": self.refresh_token,
            "include_policy": "true",
        }).encode()
        payload = self.json_request(
            "POST", TOKEN_URL,
            {**APP_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}, body,
        )
        if not isinstance(payload, Mapping) or not str(payload.get("access_token") or ""):
            raise PixivAuthenticationError("Pixiv token refresh returned no access token")
        self.access_token = str(payload["access_token"])
        replacement = str(payload.get("refresh_token") or "").strip()
        if replacement:
            self.refresh_token = replacement

    def _get(self, url: str) -> Mapping[str, object]:
        if not self.access_token:
            self.refresh_access_token()
        headers = {**APP_HEADERS, "Authorization": f"Bearer {self.access_token}"}
        try:
            payload = self.json_request("GET", url, headers, None)
        except PixivAuthenticationError:
            self.access_token = ""
            self.refresh_access_token()
            headers["Authorization"] = f"Bearer {self.access_token}"
            payload = self.json_request("GET", url, headers, None)
        if not isinstance(payload, Mapping):
            raise PixivError("Pixiv returned an invalid response")
        return payload

    def search(self, query: SearchQuery) -> SearchResult:
        if query.cursor:
            url = query.cursor
        elif query.artist_id:
            url = f"{API_ROOT}/v1/user/illusts?" + urllib.parse.urlencode({
                "user_id": query.artist_id, "filter": "for_ios", "type": "illust",
            })
        else:
            parameters = {
                "word": query.text.strip(), "search_target": SEARCH_TARGETS[query.mode],
                "sort": "date_desc", "filter": "for_ios",
            }
            url = f"{API_ROOT}/v1/search/illust?{urllib.parse.urlencode(parameters)}"
        payload = self._get(url)
        rows = payload.get("illusts")
        if not isinstance(rows, list):
            rows = payload.get("response") if isinstance(payload.get("response"), list) else []
        artworks = tuple(parse_artwork(row) for row in rows if isinstance(row, Mapping))
        next_url = str(payload.get("next_url") or "").strip() or None
        return SearchResult(artworks, next_url)

    def artwork(self, artwork_id: str) -> RemoteArtwork:
        payload = self._get(
            f"{API_ROOT}/v1/illust/detail?" + urllib.parse.urlencode({"illust_id": artwork_id})
        )
        row = payload.get("illust")
        if not isinstance(row, Mapping):
            raise PixivError("Pixiv artwork is unavailable or private")
        return parse_artwork(row)

    def download_original(self, image: RemoteImage, destination: Path) -> Path:
        if image.media_type is RemoteMediaType.UGOIRA:
            raise PixivError("Ugoira original download is not supported yet")
        if not image.original_url:
            raise PixivError("This Pixiv page has no original URL")
        data = self.bytes_request(image.original_url, {**APP_HEADERS, "Referer": image.source_url})
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(destination)
        return destination
