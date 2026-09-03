"""Mesure le chargement des onglets Grabber à partir de ses journaux."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PARSED_PAGE_RE = re.compile(r"\]\[(?:Xml|Json)\] Parsed page `([^`]+)`")


def parsed_tab_keys(text: str) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for url in PARSED_PAGE_RE.findall(text.replace("\x00", "")):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        tags = query.get("tags", [""])[0].strip()
        # Grabber applique parfois ses placeholders Qt au "%3A" de rating:general
        # lorsqu'il réimprime l'URL dans la ligne Parsed page (ex. rating95Ageneral).
        tags = re.sub(r"\brating\d+Ageneral\b", "rating:general", tags)
        if "pid" in query:
            page = query["pid"][0]
        elif parsed.netloc.casefold() == "e621.net":
            page = query.get("page", ["0"])[0]
        else:
            page = "0"
        if tags:
            keys.add((parsed.netloc.casefold(), tags, page))
    return keys


def newest_log_since(log_dir: Path, started_wall_time: float) -> Path | None:
    candidates = [p for p in log_dir.glob("main_*.log") if p.is_file() and p.stat().st_mtime >= started_wall_time - 2]
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


@dataclass(frozen=True)
class LoadMeasurement:
    timestamp: str
    tabs: int
    posts_per_tab: int
    seconds: float
    seconds_per_tab: float
    posts_per_second: float
    parsed_tabs: int
    idle_seconds: float
    log_file: str


def monitor_load(log_dir: Path, expected_tabs: int, posts_per_tab: int,
                 idle_seconds: float, stop_event, progress=None,
                 poll_seconds: float = 0.5, max_seconds: float = 900) -> LoadMeasurement | None:
    """Attend tous les onglets analysés puis une période sans écriture."""
    started_wall = time.time()
    started = time.monotonic()
    log_path = None
    last_size = -1
    last_activity = started
    parsed_count = 0
    while not stop_event.is_set() and time.monotonic() - started <= max_seconds:
        current = newest_log_since(log_dir, started_wall)
        if current is not None and current != log_path:
            log_path, last_size, last_activity = current, -1, time.monotonic()
        if log_path is not None:
            try:
                size = log_path.stat().st_size
                if size != last_size:
                    last_size, last_activity = size, time.monotonic()
                    parsed_count = len(parsed_tab_keys(log_path.read_text(encoding="utf-8", errors="replace")))
                    if progress:
                        progress(parsed_count, expected_tabs)
                idle = time.monotonic() - last_activity
                if parsed_count >= expected_tabs and idle >= idle_seconds:
                    seconds = last_activity - started
                    return LoadMeasurement(
                        time.strftime("%Y-%m-%dT%H:%M:%S"), expected_tabs, posts_per_tab,
                        round(seconds, 3), round(seconds / expected_tabs, 3),
                        round(expected_tabs * posts_per_tab / max(seconds, .001), 3),
                        parsed_count, idle_seconds, log_path.name,
                    )
            except OSError:
                pass
        stop_event.wait(poll_seconds)
    return None


def append_measurement(path: Path, measurement: LoadMeasurement) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(asdict(measurement), ensure_ascii=False) + "\n")


def read_measurements(path: Path) -> list[LoadMeasurement]:
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            result.append(LoadMeasurement(**json.loads(line)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return result


def best_measurement(measurements: list[LoadMeasurement]) -> LoadMeasurement | None:
    return max(measurements, key=lambda item: (item.posts_per_second, -item.seconds), default=None)
