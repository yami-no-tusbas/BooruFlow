"""Recoverable Imgbrd-Grabber batch sessions."""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STATE_NAME = "artist_by_tag_session.json"


def compose_search_tags(prefix: str, tag: str, suffix: str) -> list[str]:
    return [token for section in (prefix, tag, suffix) for token in section.strip().split() if token]


def remaining_review_tabs(data: dict) -> list[dict]:
    """Return only non-empty Grabber tag tabs still awaiting review."""

    return [
        tab
        for tab in data.get("tabs", [])
        if isinstance(tab, dict)
        and tab.get("type") == "tag"
        and any(str(tag).strip() for tag in tab.get("tags", []))
    ]


def _credentials_from_tabs(path: Path) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for tab in data.get("tabs", []):
            urls = tab.get("lastUrls", {}).get("gelbooru.com", {})
            for url in urls.values():
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                user_id = query.get("user_id", [""])[0]
                api_key = query.get("api_key", [""])[0]
                if user_id and api_key:
                    return user_id, api_key
    except (OSError, ValueError, TypeError):
        pass
    return "", ""


def find_grabber_credentials(directory: Path) -> tuple[str, str]:
    """Find credentials already embedded in local Grabber session files."""

    candidates = [directory / "tabs.json", *sorted(directory.glob("tabs_*.json"))]
    sessions = directory / "sessions_tabs"
    if sessions.is_dir():
        candidates.extend(
            sorted(sessions.rglob("tabs*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        )
    for candidate in candidates:
        credentials = _credentials_from_tabs(candidate)
        if all(credentials):
            return credentials
    user_id = os.getenv("GELBOORU_USER_ID", "")
    api_key = os.getenv("GELBOORU_API_KEY", "")
    if user_id and api_key:
        return user_id, api_key
    legacy_script = Path(r"D:\IGL\TagsToIGL\generate_tabs.py")
    try:
        source = legacy_script.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return "", ""
    user = re.search(r"(?:USER_ID|GELBOORU_USER_ID[^,]*,)\s*[=(,]?\s*['\"]([^'\"]+)", source)
    key = re.search(r"(?:API_KEY|GELBOORU_API_KEY[^,]*,)\s*[=(,]?\s*['\"]([^'\"]+)", source)
    return (user.group(1), key.group(1)) if user and key else ("", "")


def build_tab(
    tag: str, user_id: str, api_key: str, *, site: str = "gelbooru",
    images_per_tab: int = 20, prefix: str = "-rating:general", suffix: str = "",
) -> dict:
    search_tags = compose_search_tags(prefix, tag, suffix)
    encoded = urllib.parse.quote(" ".join(search_tags), safe="")
    if site == "e621":
        return {
            "columns": 1, "endpoint": "", "isLocked": False,
            "lastUrls": {"e621.net": {
                "Html": f"https://e621.net/posts?tags={encoded}",
                "Json": f"https://e621.net/posts.json?limit={images_per_tab}&page=1&tags={encoded}",
            }},
            "mergeResults": False, "page": 1, "perpage": images_per_tab,
            "postFiltering": [], "sites": ["e621.net"], "tags": search_tags, "type": "tag",
        }
    auth = ""
    if user_id:
        auth += f"&user_id={urllib.parse.quote(user_id, safe='')}"
    if api_key:
        auth += f"&api_key={urllib.parse.quote(api_key, safe='')}"
    base = "https://gelbooru.com/index.php?page=dapi&s=post&q=index"
    return {
        "columns": 1, "endpoint": "", "isLocked": False,
        "lastUrls": {"gelbooru.com": {
            "Html": f"https://gelbooru.com/index.php?page=post&s=list&tags={encoded}&pid=0{auth}",
            "Json": f"{base}&limit={images_per_tab}&pid=0&tags={encoded}&json=1{auth}",
            "Xml": f"{base}&limit={images_per_tab}&pid=0&tags={encoded}{auth}",
        }},
        "mergeResults": False, "page": 1, "perpage": images_per_tab,
        "postFiltering": [], "sites": ["gelbooru.com"], "tags": search_tags, "type": "tag",
    }


@dataclass(frozen=True, slots=True)
class BatchRequest:
    entries: tuple[tuple[str, str], ...]
    tabs_per_batch: int
    images_per_tab: int
    prefix: str
    suffix: str

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("at least one tag is required")
        if self.tabs_per_batch < 1 or self.images_per_tab < 1:
            raise ValueError("batch sizes must be positive")


def _atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class GrabberSessionStore:
    def __init__(self, grabber_directory: Path) -> None:
        self.directory = grabber_directory
        self.state_path = grabber_directory / STATE_NAME

    def load(self) -> dict | None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def save(self, state: dict) -> None:
        _atomic_json(self.state_path, state)

    def create(
        self, request: BatchRequest, user_id: str, api_key: str,
        unavailable: set[str] | None = None,
    ) -> tuple[dict, int]:
        filtered = [entry for entry in request.entries if entry[1] not in (unavailable or set())]
        if not filtered:
            raise ValueError("all tags are already blacklisted or ignored")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005 - local session name
        session_dir = self.directory / "sessions_tabs" / stamp
        session_dir.mkdir(parents=True, exist_ok=False)
        files: list[str] = []
        for offset in range(0, len(filtered), request.tabs_per_batch):
            tabs = [
                build_tab(tag, user_id, api_key, site=site,
                          images_per_tab=request.images_per_tab,
                          prefix=request.prefix, suffix=request.suffix)
                for site, tag in filtered[offset:offset + request.tabs_per_batch]
            ]
            path = session_dir / f"tabs_{offset // request.tabs_per_batch + 1:04d}.json"
            _atomic_json(path, {"current": 0, "tabs": tabs, "version": 2})
            files.append(str(path))
        (session_dir / "tags_source.txt").write_text(
            "\n".join(f"{site}\t{tag}" for site, tag in filtered) + "\n", encoding="utf-8"
        )
        state = {
            "version": 1, "created": datetime.now().isoformat(timespec="seconds"),  # noqa: DTZ005
            "session_dir": str(session_dir), "files": files, "current": 0,
            "completed": [], "total_tags": len(filtered),
            "tabs_per_batch": request.tabs_per_batch,
            "images_per_tab": request.images_per_tab,
            "tab_prefix": request.prefix, "tab_suffix": request.suffix,
        }
        self.save(state)
        self.activate(state)
        return state, len(request.entries) - len(filtered)

    def activate(self, state: dict) -> None:
        current = int(state.get("current", 0))
        files = list(state.get("files", []))
        if current >= len(files):
            return
        source = Path(files[current])
        destination = self.directory / "tabs.json"
        if destination.is_file():
            backup_dir = Path(state["session_dir"]) / "active_backups"
            backup_dir.mkdir(exist_ok=True)
            shutil.copy2(
                destination,
                backup_dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}-tabs.json",  # noqa: DTZ005
            )
        temporary = destination.with_suffix(".json.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)

    def previous(self, state: dict) -> dict:
        state["current"] = max(0, int(state.get("current", 0)) - 1)
        self.save(state)
        self.activate(state)
        return state

    def finish_current_if_empty(self, state: dict) -> tuple[dict, int]:
        data = json.loads((self.directory / "tabs.json").read_text(encoding="utf-8-sig"))
        remaining = [tab for tab in data.get("tabs", []) if tab.get("type") == "tag" and tab.get("tags")]
        current = int(state.get("current", 0))
        if remaining:
            data["tabs"] = remaining
            _atomic_json(Path(state["files"][current]), data)
            return state, len(remaining)
        state.setdefault("completed", []).append(current)
        state["current"] = current + 1
        self.save(state)
        self.activate(state)
        return state, 0


def read_tag_entries(text: str, default_site: str) -> tuple[tuple[str, str], ...]:
    entries = []
    seen = set()
    for raw in text.replace(";", "\n").splitlines():
        value = raw.strip()
        if not value:
            continue
        site, tag = (value.split("\t", 1) if "\t" in value else (default_site, value))
        entry = (site.strip().casefold(), tag.strip())
        if entry[0] not in {"gelbooru", "e621"}:
            entry = (default_site, value)
        if entry not in seen:
            seen.add(entry)
            entries.append(entry)
    return tuple(entries)
