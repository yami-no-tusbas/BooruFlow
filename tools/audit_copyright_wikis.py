from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from booruflow.application.wiki_aliases import resolve_copyright_alias


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data/databases/g_tags_260810.db"
DEFAULT_OUTPUT = ROOT / "var/wiki_audit/copyright_wikis_1_300.json"
WIKI_LIST = "https://gelbooru.com/index.php?page=wiki&s=list&search={}"
USER_AGENT = "BooruFlow/1.0 (personal Gelbooru wiki audit)"
WRITE_LOCK = threading.Lock()


def credentials() -> tuple[str, str]:
    path = ROOT / "config/booruflow_credentials.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    gelbooru = data.get("gelbooru", {}) if isinstance(data, dict) else {}
    return str(gelbooru.get("user_id", "")).strip(), str(gelbooru.get("api_key", "")).strip()


def normalize_tag(value: str) -> str:
    return re.sub(r"\s+", "_", html.unescape(value).strip()).casefold()


def fetch_html(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return response.geturl(), raw.decode(charset, errors="replace")


def wiki_state(tag: str) -> dict[str, object]:
    requested_url = WIKI_LIST.format(urllib.parse.quote(tag, safe=""))
    final_url, source = fetch_html(requested_url)
    view_match = re.search(r"[?&]id=(\d+)", final_url) if "page=wiki" in final_url and "s=view" in final_url else None
    wiki_id = view_match.group(1) if view_match else ""
    wiki_tag = ""
    if wiki_id:
        heading = re.search(r"Now Viewing:\s*([^<\r\n]+)", source, re.IGNORECASE)
        wiki_tag = html.unescape(heading.group(1)).strip() if heading else tag
    else:
        for match in re.finditer(r'<a[^>]+href="([^"]*page=wiki&amp;s=view&amp;id=(\d+)[^"]*)"[^>]*>(.*?)</a>', source, re.IGNORECASE | re.DOTALL):
            label = re.sub(r"<[^>]+>", "", match.group(3))
            if normalize_tag(label) == normalize_tag(tag):
                wiki_id = match.group(2)
                wiki_tag = html.unescape(label).strip()
                break
    if not wiki_id:
        return {"wiki_exists": False, "wiki_url": "", "wiki_tag": "", "wiki_id": "", "last_updated": "", "author": ""}
    wiki_url = f"https://gelbooru.com/index.php?page=wiki&s=view&id={wiki_id}"
    date_match = re.search(r"Last updated:\s*([0-9]{2}/[0-9]{2}/[0-9]{2}\s+[0-9]{1,2}:[0-9]{2}\s+[AP]M)\s+by", source, re.IGNORECASE)
    author_match = re.search(r"Last updated:.*?by\s*</?[^>]*>\s*<a[^>]*>([^<]+)</a>", source, re.IGNORECASE | re.DOTALL)
    return {
        "wiki_exists": True,
        "wiki_url": wiki_url,
        "wiki_tag": wiki_tag or tag,
        "wiki_id": wiki_id,
        "last_updated": date_match.group(1) if date_match else "",
        "author": html.unescape(author_match.group(1)).strip() if author_match else "",
    }


def load_cache(path: Path) -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {row["tag"]: row for row in payload.get("rows", [])}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def save(path: Path, rows: list[dict[str, object]]) -> None:
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "rows": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    with sqlite3.connect(args.database) as connection:
        top = connection.execute(
            "SELECT name, post_count FROM tags WHERE category=3 ORDER BY post_count DESC, name LIMIT ?",
            (args.limit,),
        ).fetchall()
    cached = load_cache(args.output)
    rows = [cached.get(tag, {"rank": rank, "tag": tag, "post_count": count}) for rank, (tag, count) in enumerate(top, 1)]
    pending = [row for row in rows if "wiki_exists" not in row]
    print(f"Wiki audit: {len(rows)} tags | cached {len(rows) - len(pending)} | pending {len(pending)}", flush=True)

    def inspect(row: dict[str, object]) -> dict[str, object]:
        return {**row, **wiki_state(str(row["tag"])), "error": ""}

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(inspect, row): row for row in pending}
        for future in as_completed(futures):
            original = futures[future]
            try:
                updated = future.result()
            except Exception as exc:
                updated = {**original, "error": f"{type(exc).__name__}: {exc}"}
            cached[str(original["tag"])] = updated
            completed += 1
            if completed % 20 == 0 or completed == len(pending):
                ordered = [cached.get(tag, {"rank": rank, "tag": tag, "post_count": count}) for rank, (tag, count) in enumerate(top, 1)]
                with WRITE_LOCK:
                    save(args.output, ordered)
                print(f"Wiki pages {completed}/{len(pending)}", flush=True)

    rows = [cached[tag] for tag, _count in top]
    user_id, api_key = credentials()
    if not user_id or not api_key:
        raise SystemExit("Gelbooru credentials are missing from config/booruflow_credentials.json")
    missing = [row for row in rows if row.get("wiki_exists") is False and not row.get("alias_status")]
    print(f"Alias audit: {len(missing)} candidates", flush=True)

    def alias_check(row: dict[str, object]) -> dict[str, object]:
        result = resolve_copyright_alias(str(row["tag"]), args.database, user_id, api_key, sample_size=20)
        update: dict[str, object] = {
            "alias_status": result.status,
            "canonical_tag": result.canonical_tag or "",
            "alias_candidates": list(result.common_copyrights),
            "sampled_posts": result.sampled_posts,
        }
        if result.canonical_tag:
            update.update({f"canonical_{key}": value for key, value in wiki_state(result.canonical_tag).items()})
        return {**row, **update}

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(alias_check, row): row for row in missing}
        for future in as_completed(futures):
            original = futures[future]
            try:
                updated = future.result()
            except Exception as exc:
                updated = {**original, "alias_status": "error", "alias_error": f"{type(exc).__name__}: {exc}"}
            cached[str(original["tag"])] = updated
            completed += 1
            if completed % 10 == 0 or completed == len(missing):
                ordered = [cached[tag] for tag, _count in top]
                save(args.output, ordered)
                print(f"Aliases {completed}/{len(missing)}", flush=True)
    save(args.output, [cached[tag] for tag, _count in top])
    print(f"Wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
