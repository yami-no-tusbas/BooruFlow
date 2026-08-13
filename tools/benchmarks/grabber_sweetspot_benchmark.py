#!/usr/bin/env python3
"""Banc d'essai autonome onglets × posts pour Imgbrd-Grabber."""

from __future__ import annotations

import argparse
import csv
import ctypes
import html
import json
import random
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from booruflow.application.grabber_batches import build_tab, find_grabber_credentials
from booruflow.cli.gelbooru_scan import fetch_counts_parallel
from tools.benchmarks.grabber_load_benchmark import monitor_load

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRABBER = Path(r"D:\0ZGrabber_blacklist")
DEFAULT_LOCAL_DB = PROJECT_ROOT / "g_tags_260712_blacklist.db"
RATING_FILTER = "rating:general"


def configuration_grid(tab_step: int = 5, post_step: int = 5) -> list[tuple[int, int]]:
    grid = [
        (tabs, posts)
        for tabs in range(10, 101, tab_step)
        for posts in range(20, 101, post_step)
    ]
    random.Random(20260802).shuffle(grid)
    return grid


def paged_tab(tag: str, user_id: str, api_key: str, posts: int, page_index: int) -> dict:
    tab = build_tab(tag, user_id, api_key, images_per_tab=posts, prefix=RATING_FILTER)
    tab["page"] = page_index + 1
    for url_type, url in tab["lastUrls"]["gelbooru.com"].items():
        tab["lastUrls"]["gelbooru.com"][url_type] = re.sub(
            r"([?&])pid=\d+", rf"\g<1>pid={page_index}", url
        )
    return tab


def write_tabs(path: Path, query_pool: list[dict], user_id: str, api_key: str,
               tabs: int, posts: int, first_query: int, page_index: int) -> list[str]:
    selected = [
        query_pool[(first_query + offset) % len(query_pool)]["tag"]
        for offset in range(tabs)
    ]
    data = {
        "current": 0,
        "tabs": [paged_tab(tag, user_id, api_key, posts, page_index) for tag in selected],
        "version": 2,
    }
    temporary = path.with_suffix(".benchmark.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return selected


def discover_query_pool(local_db: Path, user_id: str, api_key: str,
                        destination: Path, wanted: int = 100) -> list[dict]:
    """Trouve des tags distincts ayant au moins 1 000 résultats rating:general."""
    if destination.is_file():
        saved = json.loads(destination.read_text(encoding="utf-8"))
        if len(saved) >= wanted and all(
            item.get("general_count", 0) >= 1000
            and html.unescape(item.get("tag", "")) == item.get("tag", "")
            for item in saved
        ):
            return saved[:wanted]
    with sqlite3.connect(local_db) as connection:
        candidates = [html.unescape(row[0]) for row in connection.execute(
            "SELECT name FROM tags WHERE category IN (3, 4) AND post_count >= 2000 "
            "AND ambiguous = 0 ORDER BY post_count DESC LIMIT 1200"
        )]
    candidates = list(dict.fromkeys(candidates))
    random.Random(20260802).shuffle(candidates)
    accepted: list[dict] = []
    checked = 0
    for start in range(0, len(candidates), 50):
        batch = candidates[start:start + 50]
        queries = {tag: f"{RATING_FILTER} {tag}" for tag in batch}
        counts, errors = fetch_counts_parallel(
            queries, user_id, api_key, workers=8, progress_label="Sélection benchmark"
        )
        checked += len(batch)
        for tag in batch:
            if tag in counts and counts[tag][0] >= 1000:
                accepted.append({"tag": tag, "general_count": counts[tag][0]})
                if len(accepted) >= wanted:
                    break
        print(
            f"Requêtes vérifiées : {checked}; retenues : {len(accepted)}/{wanted}; "
            f"erreurs API : {len(errors)}",
            flush=True,
        )
        if len(accepted) >= wanted:
            break
    if len(accepted) < wanted:
        raise RuntimeError(
            f"Seulement {len(accepted)} requêtes rating:general avec au moins 1000 résultats"
        )
    destination.write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding="utf-8")
    return accepted


def close_windows_for_pid(pid: int) -> bool:
    user32 = ctypes.windll.user32
    closed = False
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        nonlocal closed
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            closed = True
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return closed


def stop_grabber(process: subprocess.Popen, timeout: float = 20) -> str:
    if process.poll() is not None:
        return "already_closed"
    mode = "wm_close" if close_windows_for_pid(process.pid) else "terminate"
    if mode == "terminate":
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        mode = "terminate_timeout"
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            mode = "kill_timeout"
    return mode


def launch_grabber_minimized(exe: Path, working_dir: Path) -> subprocess.Popen:
    """Lance Grabber réduit sans prendre le premier plan sous Windows."""
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    return subprocess.Popen([str(exe)], cwd=working_dir, startupinfo=startupinfo)


def append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_completed(path: Path) -> set[tuple[int, int]]:
    completed = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
                if item.get("status") == "ok":
                    completed.add((int(item["tabs"]), int(item["posts_per_tab"])))
            except (ValueError, TypeError, json.JSONDecodeError, KeyError):
                pass
    return completed


def write_statistics(results_path: Path, output_dir: Path) -> None:
    rows = []
    for line in results_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
            if item.get("status") == "ok":
                rows.append(item)
        except json.JSONDecodeError:
            pass
    rows.sort(key=lambda row: (-float(row["posts_per_second"]), float(row["seconds"])))
    fields = ["rank", "tabs", "posts_per_tab", "total_posts", "seconds", "seconds_per_tab", "posts_per_second", "idle_seconds", "log_file", "close_mode"]
    with (output_dir / "classement.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, 1):
            writer.writerow({
                "rank": rank, "tabs": row["tabs"], "posts_per_tab": row["posts_per_tab"],
                "total_posts": row["tabs"] * row["posts_per_tab"], "seconds": row["seconds"],
                "seconds_per_tab": row["seconds_per_tab"], "posts_per_second": row["posts_per_second"],
                "idle_seconds": row["idle_seconds"], "log_file": row["log_file"],
                "close_mode": row.get("close_mode", ""),
            })
    summary = {
        "successful_tests": len(rows),
        "best_throughput": rows[0] if rows else None,
        "fastest_total": min(rows, key=lambda row: row["seconds"], default=None),
        "under_30_seconds": next((row for row in rows if row["seconds"] <= 30), None),
        "under_60_seconds": next((row for row in rows if row["seconds"] <= 60), None),
    }
    (output_dir / "resume.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def run(args) -> Path:
    grabber_dir = args.grabber.resolve()
    tabs_path = grabber_dir / "tabs.json"
    exe = grabber_dir / "Grabber.exe"
    if not exe.is_file() or not tabs_path.is_file():
        raise FileNotFoundError("Grabber.exe ou tabs.json introuvable")
    user_id, api_key = find_grabber_credentials(grabber_dir)
    if not user_id or not api_key:
        raise RuntimeError("Identifiants Gelbooru introuvables dans le profil Grabber")
    output_dir = args.resume.resolve() if args.resume else (
        PROJECT_ROOT
        / "var"
        / "results"
        / "grabber_benchmark"
        / datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005 - local run name
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    query_pool = discover_query_pool(
        args.local_db.resolve(), user_id, api_key, output_dir / "requetes_valides.json"
    )
    backup = output_dir / "tabs.original.json"
    if not backup.exists():
        shutil.copy2(tabs_path, backup)
    results_path = output_dir / "mesures.jsonl"
    completed = load_completed(results_path)
    query_cursor = 0
    page_cursor = 0
    stop_event = threading.Event()
    try:
        grid = configuration_grid(args.tab_step, args.post_step)
        total_tests = len(grid)
        for index, (tabs, posts) in enumerate(grid, 1):
            if (tabs, posts) in completed:
                continue
            print(
                f"[{datetime.now():%H:%M:%S}] Test {index}/{total_tests} : "  # noqa: DTZ005
                f"{tabs} onglets × {posts} posts",
                flush=True,
            )
            selected_queries = write_tabs(
                tabs_path, query_pool, user_id, api_key, tabs, posts,
                query_cursor, page_cursor,
            )
            query_cursor = (query_cursor + tabs + 7) % len(query_pool)
            page_cursor = (page_cursor + 1) % 10
            process = launch_grabber_minimized(exe, grabber_dir)
            measurement = monitor_load(
                grabber_dir / "logs", tabs, posts, args.idle_seconds, stop_event,
                progress=lambda done, total: print(f"  chargement {done}/{total}", flush=True),
                max_seconds=args.timeout,
            )
            close_mode = stop_grabber(process)
            if measurement is None:
                append_jsonl(results_path, {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),  # noqa: DTZ005
                    "tabs": tabs,
                    "posts_per_tab": posts, "status": "timeout", "close_mode": close_mode,
                })
            else:
                payload = asdict(measurement)
                payload.update(
                    status="ok", close_mode=close_mode,
                    queries=selected_queries,
                    rating_filter=RATING_FILTER,
                )
                append_jsonl(results_path, payload)
                print(f"  terminé en {measurement.seconds:.1f}s — {measurement.posts_per_second:.1f} posts/s", flush=True)
            write_statistics(results_path, output_dir)
            time.sleep(args.cooldown)
    finally:
        shutil.copy2(backup, tabs_path)
        write_statistics(results_path, output_dir)
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grabber", type=Path, default=DEFAULT_GRABBER)
    parser.add_argument("--local-db", type=Path, default=DEFAULT_LOCAL_DB)
    parser.add_argument("--idle-seconds", type=float, default=8)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--cooldown", type=float, default=3)
    parser.add_argument("--tab-step", type=int, default=5)
    parser.add_argument("--post-step", type=int, default=5)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    output = run(args)
    print(f"Résultats : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
