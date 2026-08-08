#!/usr/bin/env python3
"""Interface pour rechercher des entités par tags et piloter Grabber."""

from __future__ import annotations

import copy
import html
import fnmatch
import json
import math
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
import urllib.error
import urllib.request
import webbrowser
import zlib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tkinter import BOTH, END, EXTENDED, LEFT, RIGHT, X, BooleanVar, Canvas, IntVar, Listbox, Menu, StringVar, Text, Tk, Toplevel
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import parse_qs, urlparse

from legacy.entity_types import ENTITY_TYPES, entity_type
from legacy.gelbooru_artistes_par_tags_ignore import fetch_result_count
from legacy.retro_cleanup import (
    Match as CleanupMatch,
    iter_image_files,
    match_file as cleanup_match_file,
    parse_blacklist as parse_cleanup_blacklist,
    send_to_recycle_bin,
    write_report as write_cleanup_report,
)
from legacy.wiki_tag_importer import (
    analyze_pasted_tag_list,
    gelbooru_page_tree,
    import_catalogues,
    iter_tags,
    merge_catalogues,
    parse_pasted_tag_list,
    tag_definition,
)
from legacy.tag_taxonomy_db import TaxonomyDatabase

try:
    from PIL import Image, ImageTk
except ImportError:  # L'application reste utilisable sans dépendance optionnelle.
    Image = None
    ImageTk = None

LEGACY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = LEGACY_DIR.parent
APP_DIR = PROJECT_ROOT
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"
VAR_DIR = PROJECT_ROOT / "var"
SCANNER = LEGACY_DIR / "gelbooru_artistes_par_tags_ignore.py"
DEFAULT_DB = DATA_DIR / "databases" / "g_tags_260712_blacklist.db"
DEFAULT_OUTPUT = VAR_DIR / "results" / "resultats_artistes_gelbooru"
DEFAULT_GRABBER = Path(r"D:\0ZGrabber_blacklist")
STATE_NAME = "artist_by_tag_session.json"
SESSION_DIR_NAME = "sessions_tabs"
TABS_PER_BATCH = 10
GUI_SETTINGS_NAME = "artist_by_tag_gui_settings.json"
TAG_ORGANIZATION_NAME = "tag_organization.json"


def gelbooru_post_tags(post: dict) -> list[str]:
    """Retourne les tags non vides d'un post de l'API Gelbooru."""
    raw = post.get("tags", "")
    if isinstance(raw, list):
        return [str(tag).strip() for tag in raw if str(tag).strip()]
    decoded = html.unescape(str(raw))
    return [tag for tag in decoded.split() if tag]


def tagging_priority(tag_count: int, critical_max: int, high_max: int) -> str:
    if tag_count <= critical_max:
        return "Critique"
    if tag_count <= high_max:
        return "Haute"
    return "Faible"


def gelbooru_posts_from_payload(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [post for post in payload if isinstance(post, dict)]
    if isinstance(payload, dict):
        posts = payload.get("post", [])
        if isinstance(posts, dict):
            return [posts]
        if isinstance(posts, list):
            return [post for post in posts if isinstance(post, dict)]
    return []


def unique_lines(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        decoded = raw
        while (unescaped := html.unescape(decoded)) != decoded:
            decoded = unescaped
        for part in decoded.split(";"):
            value = part.strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def read_nonempty_lines(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if line.strip()
    }


def remaining_review_tabs(data: dict) -> list[dict]:
    """Retourne uniquement les onglets de recherche encore ouverts dans Grabber."""
    return [
        tab
        for tab in data.get("tabs", [])
        if isinstance(tab, dict)
        and tab.get("type") == "tag"
        and any(str(tag).strip() for tag in tab.get("tags", []))
    ]


def credentials_from_tabs(path: Path) -> tuple[str, str]:
    """Récupère les identifiants déjà utilisés par Grabber, sans les journaliser."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for tab in data.get("tabs", []):
            urls = tab.get("lastUrls", {}).get("gelbooru.com", {})
            for url in urls.values():
                query = parse_qs(urlparse(url).query)
                user_id = query.get("user_id", [""])[0]
                api_key = query.get("api_key", [""])[0]
                if user_id and api_key:
                    return user_id, api_key
    except (OSError, ValueError, TypeError):
        pass
    return os.getenv("GELBOORU_USER_ID", ""), os.getenv("GELBOORU_API_KEY", "")


def find_grabber_credentials(grabber_dir: Path) -> tuple[str, str]:
    """Cherche l'authentification dans les fichiers Grabber connus."""
    candidates = [grabber_dir / "tabs.json"]
    candidates.extend(sorted(grabber_dir.glob("tabs_*.json")))
    sessions = grabber_dir / SESSION_DIR_NAME
    if sessions.is_dir():
        candidates.extend(
            sorted(
                sessions.rglob("tabs*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )
    for candidate in candidates:
        user_id, api_key = credentials_from_tabs(candidate)
        if user_id and api_key:
            return user_id, api_key
    env_user = os.getenv("GELBOORU_USER_ID", "")
    env_key = os.getenv("GELBOORU_API_KEY", "")
    if env_user and env_key:
        return env_user, env_key

    # Compatibilité avec les scripts historiques déjà configurés par l'utilisateur.
    legacy_scripts = [
        SCANNER,
        Path(r"D:\IGL\TagsToIGL\generate_tabs.py"),
    ]
    for script in legacy_scripts:
        try:
            source = script.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        user_patterns = [
            r"""USER_ID\s*=\s*["']([^"']+)["']""",
            r"""GELBOORU_USER_ID["']\s*,\s*["']([^"']+)["']""",
        ]
        key_patterns = [
            r"""API_KEY\s*=\s*["']([^"']+)["']""",
            r"""GELBOORU_API_KEY["']\s*,\s*["']([^"']+)["']""",
        ]
        user_id = next(
            (match.group(1) for pattern in user_patterns if (match := re.search(pattern, source))),
            "",
        )
        api_key = next(
            (match.group(1) for pattern in key_patterns if (match := re.search(pattern, source))),
            "",
        )
        if user_id and api_key:
            return user_id, api_key
    return "", ""


def compose_search_tags(prefix: str, tag: str, suffix: str) -> list[str]:
    """Assemble les champs avant/tag/après en tokens de recherche Grabber."""
    return [
        token
        for section in (prefix, tag, suffix)
        for token in section.strip().split()
        if token
    ]


def build_tab(
    tag: str,
    user_id: str,
    api_key: str,
    site: str = "gelbooru",
    images_per_tab: int = 20,
    prefix: str = "-rating:general",
    suffix: str = "",
) -> dict:
    search_tags = compose_search_tags(prefix, tag, suffix)
    tags_param = " ".join(search_tags)
    if site == "e621":
        encoded = urllib.parse.quote(tags_param, safe="")
        return {
            "columns": 1,
            "endpoint": "",
            "isLocked": False,
            "lastUrls": {
                "e621.net": {
                    "Html": f"https://e621.net/posts?tags={encoded}",
                    "Json": (
                        "https://e621.net/posts.json?"
                        f"limit={images_per_tab}&page=1&tags={encoded}"
                    ),
                }
            },
            "mergeResults": False,
            "page": 1,
            "perpage": images_per_tab,
            "postFiltering": [],
            "sites": ["e621.net"],
            "tags": search_tags,
            "type": "tag",
        }
    encoded = urllib.parse.quote(tags_param, safe="")
    auth = ""
    if user_id:
        auth += f"&user_id={urllib.parse.quote(user_id, safe='')}"
    if api_key:
        auth += f"&api_key={urllib.parse.quote(api_key, safe='')}"
    return {
        "columns": 1,
        "endpoint": "",
        "isLocked": False,
        "lastUrls": {
            "gelbooru.com": {
                "Html": (
                    "https://gelbooru.com/index.php?page=post&s=list"
                    f"&tags={encoded}&pid=0{auth}"
                ),
                "Json": (
                    "https://gelbooru.com/index.php?page=dapi&s=post&q=index"
                    f"&limit={images_per_tab}&pid=0&tags={encoded}&json=1{auth}"
                ),
                "Xml": (
                    "https://gelbooru.com/index.php?page=dapi&s=post&q=index"
                    f"&limit={images_per_tab}&pid=0&tags={encoded}{auth}"
                ),
            }
        },
        "mergeResults": False,
        "page": 1,
        "perpage": images_per_tab,
        "postFiltering": [],
        "sites": ["gelbooru.com"],
        "tags": search_tags,
        "type": "tag",
    }


def repair_tabs_auth(path: Path, user_id: str, api_key: str) -> int:
    """Reconstruit les URL des onglets tout en préservant leurs autres réglages."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    changed = 0
    for tab in data.get("tabs", []):
        if "gelbooru.com" not in tab.get("sites", []):
            continue
        tags = tab.get("tags", [])
        if not tags:
            continue
        images_per_tab = max(1, int(tab.get("perpage", 20)))
        rebuilt = build_tab(
            " ".join(tags),
            user_id,
            api_key,
            images_per_tab=images_per_tab,
            prefix="",
            suffix="",
        )
        if tab.get("lastUrls") != rebuilt["lastUrls"]:
            tab["lastUrls"] = rebuilt["lastUrls"]
            changed += 1
    if changed:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
        )
        os.replace(temporary, path)
    return changed


class App:
    def __init__(self, root: Tk) -> None:
        self.root = root
        root.title("Artist by Tag — artistes, copyrights, personnages et espèces")
        root.geometry("1080x860")
        root.minsize(900, 700)

        self.settings_path = CONFIG_DIR / GUI_SETTINGS_NAME

        self.tag_organization_path = DATA_DIR / "taxonomy" / TAG_ORGANIZATION_NAME
        saved = self._read_settings()
        self.tag_organization = self._load_tag_organization()
        self.organizer_undo_history: list[tuple[str, bytes]] = []
        self.taxonomy_databases = {
            board: TaxonomyDatabase(DATA_DIR / "databases" / f"tag_organization_{board}.sqlite", board)
            for board in ("gelbooru", "e621")
        }
        self._sync_taxonomy_databases()
        self.organizer_board_var = StringVar(value="gelbooru")
        self.organizer_search_var = StringVar()
        self.organizer_definition_var = StringVar(
            value="Sélectionne un tag pour charger sa définition."
        )
        self.organizer_source_var = StringVar()
        self.organizer_update_var = StringVar(value="Catalogue local prêt.")
        self.organizer_basket: set[str] = set()
        self.output_var = StringVar(value=saved.get("output", str(DEFAULT_OUTPUT)))
        self.grabber_var = StringVar(
            value=saved.get("grabber", str(DEFAULT_GRABBER))
        )
        self.pages_var = IntVar(value=int(saved.get("pages", 10)))
        self.start_page_var = IntVar(value=int(saved.get("start_page", 1)))
        self.min_hits_var = IntVar(value=int(saved.get("min_hits", 2)))
        self.min_posts_var = IntVar(value=int(saved.get("min_posts", 0)))
        self.max_posts_var = IntVar(value=int(saved.get("max_posts", 0)))
        self.min_percent_var = IntVar(
            value=round(float(saved.get("min_percent", 0)))
        )
        self.cache_days_var = IntVar(value=int(saved.get("cache_days", 30)))
        self.auto_continue_var = BooleanVar(
            value=bool(saved.get("auto_continue", True))
        )
        self.batch_size_var = IntVar(
            value=int(saved.get("batch_size", TABS_PER_BATCH))
        )
        self.images_per_tab_var = IntVar(
            value=int(saved.get("images_per_tab", 100))
        )
        self.tab_prefix_var = StringVar(
            value=str(saved.get("tab_prefix", "-rating:general"))
        )
        self.tab_suffix_var = StringVar(value=str(saved.get("tab_suffix", "")))
        self.remember_queries_var = BooleanVar(
            value=bool(saved.get("remember_queries", False))
        )
        self.status_var = StringVar(value="Prêt.")
        self.scan_progress_text = StringVar(value="Aucune recherche en cours.")
        self.scan_progress_value = IntVar(value=0)
        self.percentage_explanation = StringVar()
        self.progress_var = StringVar(value="Aucune session chargée.")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.scan_process: subprocess.Popen[str] | None = None
        self.scan_running = False
        self.scan_stop_requested = False
        self.grabber_process: subprocess.Popen[str] | None = None
        self.database_process: subprocess.Popen[str] | None = None
        self.cleanup_running = False
        self.cleanup_matches: list[CleanupMatch] = []
        self.cleanup_report: Path | None = None
        self.session: dict | None = None
        self.before_blacklist: set[str] = set()
        self.before_ignore: set[str] = set()
        self.update_gelbooru_var = BooleanVar(
            value=bool(saved.get("update_gelbooru", True))
        )
        self.update_e621_var = BooleanVar(
            value=bool(saved.get("update_e621", True))
        )
        # Chaque moteur utilise sa base locale attitrée.
        self.local_gel_db_var = StringVar(
            value=str(saved.get("local_gel_db", DEFAULT_DB))
        )
        self.local_e621_db_var = StringVar(
            value=str(saved.get("local_e621_db", DATA_DIR / "databases" / "e621_tags.db"))
        )
        self.database_status_var = StringVar(value="Aucune mise à jour en cours.")
        self.search_gelbooru_var = BooleanVar(
            value=bool(saved.get("search_gelbooru", True))
        )
        self.search_e621_var = BooleanVar(
            value=bool(saved.get("search_e621", False))
        )
        self.entity_type_var = StringVar(value=saved.get("entity_type", "artists"))
        self.e621_reached_end = False
        self.scan_query_total = 0
        self.scan_query_current = 0
        self.scan_pages_total = 0
        self.scan_total_pages = 0
        self.scan_fetched_pages = False
        self.scan_reported_next_page: int | None = None
        self.query_count_running = False
        self.auto_continue_cancelled = False
        self.active_scan_signature: str | None = None
        self.active_scan_start_page = 1
        self.last_successful_query_signature: str | None = saved.get(
            "last_successful_query_signature"
        )
        self.last_successful_start_page = int(
            saved.get("last_successful_start_page", 0)
        )
        self.last_successful_page_count = int(
            saved.get("last_successful_page_count", 0)
        )
        self.tagging_query_var = StringVar(value=str(saved.get("tagging_query", "")))
#        self.tagging_site_var = StringVar(value=str(saved.get("tagging_site", "gelbooru")))
        self.tagging_min_var = IntVar(value=int(saved.get("tagging_min", 0)))
        self.tagging_max_var = IntVar(value=int(saved.get("tagging_max", 15)))
        self.tagging_critical_var = IntVar(
            value=int(saved.get("tagging_critical_max", 5))
        )
        self.tagging_high_var = IntVar(value=int(saved.get("tagging_high_max", 10)))
        self.tagging_pages_var = IntVar(value=int(saved.get("tagging_pages", 10)))
        self.tagging_start_page_var = IntVar(
            value=int(saved.get("tagging_start_page", 1))
        )
        self.tagging_current_page_var = StringVar(value="Page actuelle : —")
        self.tagging_status_var = StringVar(value="Aucune recherche lancée.")
        self.tagging_progress_var = IntVar(value=0)
        self.tagging_running = False
        self.tagging_stop_event = threading.Event()
        self.tagging_generation = 0
        self.tagging_cards: dict[int, ttk.Button] = {}
        self.tagging_images: dict[int, object] = {}

        self.gel_db_name_var = StringVar()
        self.e621_db_name_var = StringVar()
        self.output_name_var = StringVar()
        self.grabber_name_var = StringVar()
        self.min_posts_var.trace_add("write", self._update_percentage_explanation)
        self.min_percent_var.trace_add("write", self._update_percentage_explanation)
        self._refresh_path_labels()
        self._update_percentage_explanation()

        self._build_ui()
        saved_queries = str(saved.get("queries", ""))
        if saved_queries:
            self.query_text.insert("1.0", saved_queries)
            self.query_text.edit_modified(False)
        self.root.after(150, self._drain_events)
        self._load_existing_state(silent=True)
        self._refresh_buttons()
        self.root.protocol("WM_DELETE_WINDOW", self._close_app)

    @property
    def grabber_dir(self) -> Path:
        return Path(self.grabber_var.get().strip())

    @property
    def state_path(self) -> Path:
        return self.grabber_dir / STATE_NAME

    def _read_settings(self) -> dict:
        if not self.settings_path.is_file():
            return {}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}
            
    def _load_tag_organization(self) -> dict:
        if self.tag_organization_path.is_file():
            try:
                data = json.loads(
                    self.tag_organization_path.read_text(encoding="utf-8-sig")
                )
                if isinstance(data, dict) and isinstance(data.get("boards"), dict):
                    data.setdefault("metadata", {})
                    data.setdefault("sources", [])
                    data.setdefault("excluded_imported_tags", {})
                    return data
            except (OSError, ValueError, TypeError):
                pass
        return {
            "version": 1,
            "boards": {
                "gelbooru": {},
                "e621": {
                    "Espèces": {
                        "Mammifères": {
                            "Canidés": ["german_shepherd"]
                        }
                    }
                },
            },
            "metadata": {},
            "sources": [],
            "excluded_imported_tags": {},
        }

    def _save_tag_organization(self) -> None:
        temporary = self.tag_organization_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.tag_organization, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.tag_organization_path)
        self._sync_taxonomy_databases()

    def _sync_taxonomy_databases(self) -> None:
        for board, database in self.taxonomy_databases.items():
            database.sync_from_document(
                self.tag_organization.get("boards", {}).get(board, {}),
                self.tag_organization.get("metadata", {}).get(board, {}),
                self.tag_organization.get("excluded_imported_tags", {}).get(board, []),
                self.tag_organization.get("sources", []),
            )

    def _save_settings(self) -> None:
        data = {
            "version": 1,
            "output": self.output_var.get(),
            "grabber": self.grabber_var.get(),
            "pages": self.pages_var.get(),
            "start_page": self.start_page_var.get(),
            "min_hits": self.min_hits_var.get(),
            "min_posts": self.min_posts_var.get(),
            "max_posts": self.max_posts_var.get(),
            "min_percent": self.min_percent_var.get(),
            "cache_days": self.cache_days_var.get(),
            "auto_continue": self.auto_continue_var.get(),
            "batch_size": self.batch_size_var.get(),
            "images_per_tab": self.images_per_tab_var.get(),
            "tab_prefix": self.tab_prefix_var.get(),
            "tab_suffix": self.tab_suffix_var.get(),
            "remember_queries": self.remember_queries_var.get(),
            "queries": self._query_signature(),
            "last_successful_query_signature": self.last_successful_query_signature,
            "last_successful_start_page": self.last_successful_start_page,
            "last_successful_page_count": self.last_successful_page_count,
            "local_gel_db": self.local_gel_db_var.get(),
            "local_e621_db": self.local_e621_db_var.get(),
            "update_gelbooru": self.update_gelbooru_var.get(),
            "update_e621": self.update_e621_var.get(),
            "search_gelbooru": self.search_gelbooru_var.get(),
            "search_e621": self.search_e621_var.get(),
            "entity_type": self.entity_type_var.get(),
            "tagging_query": self.tagging_query_var.get(),
#            "tagging_site": self.tagging_site_var.get(),
            "tagging_min": self.tagging_min_var.get(),
            "tagging_max": self.tagging_max_var.get(),
            "tagging_critical_max": self.tagging_critical_var.get(),
            "tagging_high_max": self.tagging_high_var.get(),
            "tagging_pages": self.tagging_pages_var.get(),
            "tagging_start_page": self.tagging_start_page_var.get(),
        }
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.settings_path)

    def _close_app(self) -> None:
        try:
            self._save_settings()
        except (OSError, ValueError, TypeError):
            pass
        for database in self.taxonomy_databases.values():
            database.close()
        self.root.destroy()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=BOTH, expand=True)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill=BOTH, expand=True)
        artist_tab = ttk.Frame(notebook, padding=8)
        self.tag_list_tab = ttk.Frame(notebook, padding=8)
        self.database_tab = ttk.Frame(notebook, padding=8)
        self.cleanup_tab = ttk.Frame(notebook, padding=8)
        self.options_tab = ttk.Frame(notebook, padding=8)
        self.tag_organizer_tab = ttk.Frame(notebook, padding=8)
        self.tagging_tab = ttk.Frame(notebook, padding=8)
        notebook.add(artist_tab, text="Revue par catégorie")
        notebook.add(self.tag_list_tab, text="Revue depuis une liste")
        notebook.add(self.database_tab, text="Bases locales")
        notebook.add(self.cleanup_tab, text="Nettoyage rétroactif")
        notebook.add(self.tag_organizer_tab, text="Organisation des tags")
        notebook.add(self.tagging_tab, text="Tagging")
        notebook.add(self.options_tab, text="Options")
        self.notebook = notebook

        search = ttk.LabelFrame(
            artist_tab, text="1 — Générer la liste à examiner", padding=8
        )
        search.pack(fill=BOTH, expand=False)
        sites = ttk.Frame(search)
        sites.pack(fill=X, pady=(0, 5))
        ttk.Label(sites, text="Sites recherchés :").pack(side=LEFT)
        ttk.Checkbutton(
            sites,
            text="Gelbooru",
            variable=self.search_gelbooru_var,
            command=self._on_autocomplete_site_changed,
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Checkbutton(
            sites,
            text="e621",
            variable=self.search_e621_var,
            command=self._on_autocomplete_site_changed,
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Label(sites, text="Mode :").pack(side=LEFT, padx=(20, 4))
        mode_box = ttk.Combobox(
            sites,
            state="readonly",
            width=14,
            textvariable=self.entity_type_var,
            values=list(ENTITY_TYPES),
        )
        mode_box.pack(side=LEFT)
        mode_box.bind("<<ComboboxSelected>>", self._on_mode_changed)
        ttk.Label(
            search,
            text="Une recherche par ligne (ou séparées par « ; ») :",
        ).pack(anchor="w")
        self.query_text = ScrolledText(search, height=4, wrap="word")
        self.query_text.pack(fill=X, pady=(4, 6))
        self.query_text.bind("<<Modified>>", self._on_query_text_modified)
        self.query_text.bind(
            "<KeyRelease>", self._on_query_autocomplete_key, add="+"
        )
        self.query_text.bind("<Down>", self._focus_query_autocomplete)
        self.query_text.bind("<Tab>", self._accept_query_autocomplete)
        self.query_text.bind("<Escape>", self._hide_query_autocomplete)
        self.query_text.edit_modified(False)

        self.query_autocomplete_after: str | None = None
        self.query_autocomplete_generation = 0
        self.query_autocomplete_values: list[str] = []
        self.query_autocomplete_frame = ttk.Frame(search)
        self.query_autocomplete_source = StringVar(value="")
        ttk.Label(
            self.query_autocomplete_frame,
            textvariable=self.query_autocomplete_source,
        ).pack(anchor="w")
        self.query_autocomplete_list = Listbox(
            self.query_autocomplete_frame,
            height=5,
            exportselection=False,
        )
        self.query_autocomplete_list.pack(fill=X)
        self.query_autocomplete_list.bind(
            "<Double-1>", self._accept_query_autocomplete
        )
        self.query_autocomplete_list.bind(
            "<Return>", self._accept_query_autocomplete
        )
        self.query_autocomplete_list.bind(
            "<Tab>", self._accept_query_autocomplete
        )
        self.query_autocomplete_list.bind(
            "<Escape>", self._return_from_query_autocomplete
        )

        opts = ttk.Frame(search)
        opts.pack(fill=X)
        self._spin(opts, "Pages à explorer", self.pages_var, 1, 100)
        self._spin(opts, "Première page", self.start_page_var, 1, 1000000)
        self._spin(opts, "Nombre de résultats min.", self.min_posts_var, 0, 100000000)
        self._spin(opts, "Nombre de résultats max.", self.max_posts_var, 0, 100000000)
        percent_frame = ttk.Frame(search)
        percent_frame.pack(fill=X, pady=(6, 0))
        ttk.Label(percent_frame, text="Correspondance des résultats :").pack(
            side=LEFT
        )
        ttk.Spinbox(
            percent_frame,
            from_=0,
            to=100,
            increment=1,
            textvariable=self.min_percent_var,
            width=7,
        ).pack(side=LEFT, padx=(5, 12))
        ttk.Label(percent_frame, text="%").pack(side=LEFT, padx=(0, 12))
        ttk.Checkbutton(
            percent_frame,
            text="Continuer automatiquement si aucune entrée ne correspond",
            variable=self.auto_continue_var,
        ).pack(side=LEFT)
        ttk.Label(
            search,
            textvariable=self.percentage_explanation,
            foreground="#555555",
            wraplength=980,
        ).pack(fill=X, anchor="w", pady=(4, 0))

        scan_actions = ttk.Frame(search)
        scan_actions.pack(fill=X, pady=(7, 0))
        ttk.Checkbutton(
            scan_actions,
            text="Mémoriser les recherches terminées",
            variable=self.remember_queries_var,
        ).pack(side=LEFT)
        ttk.Button(
            scan_actions,
            text="Arrêter après ce bloc",
            command=self.stop_auto_continue,
        ).pack(side=RIGHT)
        self.stop_scan_button = ttk.Button(
            scan_actions,
            text="Stop",
            command=self.stop_scan,
            state="disabled",
        )
        self.stop_scan_button.pack(side=RIGHT, padx=(0, 6))
        self.scan_button = ttk.Button(
            scan_actions, text="Lancer la recherche", command=self.start_scan
        )
        self.scan_button.pack(side=RIGHT, padx=(0, 6))
        self.count_button = ttk.Button(
            scan_actions, text="Compter", command=self.start_query_count
        )
        self.count_button.pack(side=RIGHT, padx=(0, 6))
        self.query_count_status = StringVar(value="")
        self.query_count_label = ttk.Label(
            search,
            textvariable=self.query_count_status,
            wraplength=980,
        )
        self.query_count_status.trace_add("write", self._toggle_query_count_status)
        progress_frame = ttk.Frame(search)
        self.scan_progress_frame = progress_frame
        progress_frame.pack(fill=X, pady=(7, 0))
        self.scan_progress = ttk.Progressbar(
            progress_frame,
            variable=self.scan_progress_value,
            maximum=100,
            mode="determinate",
        )
        self.scan_progress.pack(fill=X)
        ttk.Label(progress_frame, textvariable=self.scan_progress_text).pack(
            anchor="w", pady=(2, 0)
        )

        batches = ttk.LabelFrame(
            artist_tab,
            text="2 — Paramètres et génération de lots d’onglets",
            padding=10,
        )
        batches.pack(fill=X, pady=(8, 0))
        modifiers = ttk.Frame(batches)
        modifiers.pack(fill=X, pady=(0, 8))
        ttk.Label(modifiers, text="Ajouter avant la recherche :").pack(side=LEFT)
        ttk.Entry(modifiers, textvariable=self.tab_prefix_var, width=30).pack(
            side=LEFT, padx=(5, 18)
        )
        ttk.Label(modifiers, text="Ajouter après la recherche :").pack(side=LEFT)
        ttk.Entry(modifiers, textvariable=self.tab_suffix_var, width=30).pack(
            side=LEFT, padx=(5, 0)
        )

        batch_controls = ttk.Frame(batches)
        batch_controls.pack(fill=X)
        ttk.Label(batch_controls, text="Onglets par lot :").pack(side=LEFT)
        ttk.Spinbox(
            batch_controls,
            from_=1,
            to=200,
            textvariable=self.batch_size_var,
            width=6,
        ).pack(side=LEFT, padx=(5, 12))
        ttk.Label(batch_controls, text="Images par onglet :").pack(side=LEFT)
        ttk.Spinbox(
            batch_controls,
            from_=1,
            to=100,
            textvariable=self.images_per_tab_var,
            width=6,
        ).pack(side=LEFT, padx=(5, 12))
        self.import_button = ttk.Button(
                        batch_controls, text="Choisir une liste TXT…", command=self.import_tag_list
        )
        self.import_button.pack(side=LEFT)
        self.generate_button = ttk.Button(
            batch_controls,
            text="Créer les lots depuis les derniers résultats",
            command=self.generate_from_results,
        )
        self.generate_button.pack(side=RIGHT)

        control = ttk.LabelFrame(artist_tab, text="3 — Traiter les lots", padding=8)
        control.pack(fill=X, pady=(8, 0))
        ttk.Label(control, textvariable=self.progress_var).pack(anchor="w")
        buttons = ttk.Frame(control)
        buttons.pack(fill=X, pady=(6, 0))
        self.load_button = ttk.Button(
            buttons, text="Reprendre la session", command=self.load_existing_state
        )
        self.load_button.pack(side=LEFT)
        self.previous_button = ttk.Button(
            buttons, text="Lot précédent", command=self.previous_batch
        )
        self.previous_button.pack(side=LEFT, padx=(6, 0))
        self.launch_button = ttk.Button(
            buttons, text="Lancer Grabber", command=self.launch_grabber
        )
        self.launch_button.pack(side=RIGHT)

        logs = ttk.LabelFrame(artist_tab, text="Journal", padding=8)
        logs.pack(fill=BOTH, expand=True, pady=(8, 0))
        self.log_text = ScrolledText(logs, height=12, state="disabled", wrap="word")
        self.log_text.pack(fill=BOTH, expand=True)
        log_footer = ttk.Frame(logs)
        log_footer.pack(fill=X, pady=(7, 0))
        ttk.Label(
            log_footer,
            textvariable=self.status_var,
            wraplength=850,
        ).pack(side=LEFT, fill=X, expand=True, padx=(2, 10), pady=2)
        ttk.Button(
            log_footer, text="Effacer les logs", command=self.clear_logs
        ).pack(side=RIGHT)
        self._build_tag_list_tab()
        self._build_database_tab()
        self._build_cleanup_tab()
        self._build_tag_organizer_tab()
        self._build_tagging_tab()
        self._build_options_tab()

    def _build_tagging_tab(self) -> None:
        controls = ttk.LabelFrame(
            self.tagging_tab,
            text="Repérer les posts Gelbooru pauvres en tags",
            padding=10,
        )
        controls.pack(fill=X)
        ttk.Label(controls, text="Requête Gelbooru :").pack(anchor="w")
        ttk.Entry(controls, textvariable=self.tagging_query_var).pack(
            fill=X, pady=(4, 7)
        )

        limits = ttk.Frame(controls)
        limits.pack(fill=X)
        self._spin(limits, "Pages", self.tagging_pages_var, 1, 1000)
        self._spin(
            limits, "Page de début", self.tagging_start_page_var, 1, 1000000
        )
        self._spin(limits, "Tags min.", self.tagging_min_var, 0, 1000)
        self._spin(limits, "Tags max.", self.tagging_max_var, 0, 1000)
        self._spin(
            limits, "Critique jusqu'à", self.tagging_critical_var, 0, 1000
        )
        self._spin(limits, "Haute jusqu'à", self.tagging_high_var, 0, 1000)
        self.tagging_stop_button = ttk.Button(
            limits,
            text="Stop",
            state="disabled",
            command=self.stop_tagging_scan,
        )
        self.tagging_stop_button.pack(side=RIGHT)
        self.tagging_start_button = ttk.Button(
            limits, text="Rechercher", command=self.start_tagging_scan
        )
        self.tagging_start_button.pack(side=RIGHT, padx=(0, 6))

        ttk.Progressbar(
            controls,
            maximum=100,
            variable=self.tagging_progress_var,
            mode="determinate",
        ).pack(fill=X, pady=(8, 2))
        ttk.Label(
            controls,
            textvariable=self.tagging_status_var,
            wraplength=980,
        ).pack(anchor="w")
        ttk.Label(
            controls,
            textvariable=self.tagging_current_page_var,
            foreground="#555555",
        ).pack(anchor="w", pady=(2, 0))

        results = ttk.Frame(self.tagging_tab)
        results.pack(fill=BOTH, expand=True, pady=(8, 0))
        self.tagging_canvas = Canvas(results, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            results, orient="vertical", command=self.tagging_canvas.yview
        )
        self.tagging_results = ttk.Frame(self.tagging_canvas, padding=(2, 2, 10, 8))
        self.tagging_window = self.tagging_canvas.create_window(
            (0, 0), window=self.tagging_results, anchor="nw"
        )
        self.tagging_canvas.configure(yscrollcommand=scrollbar.set)
        self.tagging_results.bind(
            "<Configure>",
            lambda _event: self.tagging_canvas.configure(
                scrollregion=self.tagging_canvas.bbox("all")
            ),
        )
        self.tagging_canvas.bind(
            "<Configure>",
            lambda event: self.tagging_canvas.itemconfigure(
                self.tagging_window, width=event.width
            ),
        )
        self.tagging_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill="y")
        self.tagging_canvas.bind_all(
            "<MouseWheel>",
            lambda event: self.tagging_canvas.yview_scroll(
                int(-event.delta / 120), "units"
            )
            if self.notebook.select() == str(self.tagging_tab)
            else None,
            add="+",
        )

    def start_tagging_scan(self) -> None:
        if self.tagging_running:
            return
        query = self.tagging_query_var.get().strip()
        try:
            pages = int(self.tagging_pages_var.get())
            start_page = int(self.tagging_start_page_var.get())
            minimum = int(self.tagging_min_var.get())
            maximum = int(self.tagging_max_var.get())
            critical_max = int(self.tagging_critical_var.get())
            high_max = int(self.tagging_high_var.get())
        except (TypeError, ValueError):
            messagebox.showerror("Tagging", "Les seuils doivent être des nombres entiers.")
            return
        if pages < 1 or start_page < 1 or minimum < 0 or maximum < minimum:
            messagebox.showerror(
                "Tagging", "Vérifie les pages et l'intervalle tags min./max."
            )
            return
        if critical_max < minimum or high_max < critical_max or high_max > maximum:
            messagebox.showerror(
                "Tagging",
                "Les limites doivent respecter : min. ≤ Critique ≤ Haute ≤ max.",
            )
            return
        user_id, api_key = find_grabber_credentials(self.grabber_dir)
        if not user_id or not api_key:

            messagebox.showerror(
                "Identifiants Gelbooru absents",
                "Aucun ancien onglet Grabber contenant user_id et api_key n'a été trouvé.",
            )
            return

        self._save_settings()
        self.tagging_generation += 1
        generation = self.tagging_generation
        self.tagging_stop_event.clear()
        self.tagging_running = True
        self.tagging_progress_var.set(0)
        self.tagging_current_page_var.set(f"Page actuelle : {start_page}")
        self.tagging_status_var.set(f"Démarrage — bloc de {pages} page(s).")
        self.tagging_start_button.configure(state="disabled")
        self.tagging_stop_button.configure(state="normal")
        for child in self.tagging_results.winfo_children():
            child.destroy()
        self.tagging_cards.clear()
        self.tagging_images.clear()
        threading.Thread(
            target=self._run_tagging_scan,
            args=(
                generation,
                #site,
                query,
                pages,
                start_page,
                minimum,
                maximum,
                critical_max,
                high_max,
                user_id,
                api_key,
            ),
            daemon=True,
        ).start()

    def stop_tagging_scan(self) -> None:
        if self.tagging_running:
            self.tagging_stop_event.set()
            self.tagging_stop_button.configure(state="disabled")
            self.tagging_status_var.set("Arrêt demandé…")

    def _run_tagging_scan(
        self,
        generation: int,
        #site: str,
        query: str,
        pages: int,
        start_page: int,
        minimum: int,
        maximum: int,
        critical_max: int,
        high_max: int,
        user_id: str,
        api_key: str,
    ) -> None:
        selected: list[dict] = []
        examined = 0
        error = ""
        stopped_at_end = False
        next_page = start_page
        try:
            block_start = start_page
            while not selected and not self.tagging_stop_event.is_set():
                for block_index in range(pages):
                    if self.tagging_stop_event.is_set():
                        break
                    current_page = block_start + block_index
                    params = {
                        "page": "dapi",
                        "s": "post",
                        "q": "index",
                        "json": "1",
                        "limit": "100",
                        "pid": str(current_page - 1),
                        "tags": query,
                        "user_id": user_id,
                        "api_key": api_key,
                    }
                    url = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(params)
                    request = urllib.request.Request(
                        url,
                        headers={
                            "User-Agent": "artist-by-tag/1.0",
                            "Referer": "https://gelbooru.com/",
                        },
                    )
                    with urllib.request.urlopen(request, timeout=30) as response:
                        payload = json.loads(
                            response.read().decode("utf-8", errors="replace")
                        )
                    posts = gelbooru_posts_from_payload(payload)
                    for post in posts:
                        count = len(gelbooru_post_tags(post))
                        examined += 1
                        if minimum <= count <= maximum:
                            item = dict(post)
                            item["tag_count"] = count
                            item["priority"] = tagging_priority(
                                count, critical_max, high_max
                            )
                            selected.append(item)
                    next_page = current_page + 1
                    self.events.put(
                        (
                            "tagging_progress",
                            (
                                generation,
                                block_index + 1,
                                pages,
                                current_page,
                                examined,
                                len(selected),
                            ),
                        )
                    )
                    if len(posts) < 100:
                        stopped_at_end = True
                        break
                if selected or stopped_at_end or self.tagging_stop_event.is_set():
                    break
                block_start += pages
                self.events.put(
                    ("tagging_continue", (generation, block_start, pages, examined))
                )
        except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
            error = str(exc)
        self.events.put(
            (
                "tagging_done",
                (
                    generation,
                    selected,
                    examined,
                    self.tagging_stop_event.is_set(),
                    stopped_at_end,
                    next_page,
                    error,
                ),
            )
        )

    def _show_tagging_results(self, generation: int, posts: list[dict]) -> None:
        if generation != self.tagging_generation:
            return
        grouped = {
            priority: [post for post in posts if post.get("priority") == priority]
            for priority in ("Critique", "Haute", "Faible")
        }
        card_width = 170
        for priority, items in grouped.items():
            section = ttk.LabelFrame(
                self.tagging_results,
                text=f"{priority} — {len(items)} image(s)",
                padding=8,
            )
            section.pack(fill=X, pady=(0, 8))
            for column in range(5):
                section.columnconfigure(column, weight=1, uniform="tagging")
            for index, post in enumerate(items):
                post_id = int(post.get("id", 0))
                count = int(post.get("tag_count", 0))
                button = ttk.Button(
                    section,
                    text=f"Aperçu\n#{post_id} · {count} tags",
                    width=22,
                    command=lambda value=post_id: webbrowser.open(
                        "https://gelbooru.com/index.php?page=post&s=view&id="
                        + str(value)
                    ),
                )
                button.grid(
                    row=index // 5,
                    column=index % 5,
                    padx=4,
                    pady=4,
                    sticky="nsew",
                    ipadx=2,
                    ipady=20,
                )
                self.tagging_cards[post_id] = button
        if Image is not None and ImageTk is not None:
            threading.Thread(
                target=self._load_tagging_thumbnails,
                args=(generation, posts, card_width),
                daemon=True,
            ).start()

    def _load_tagging_thumbnails(
        self, generation: int, posts: list[dict], card_width: int
    ) -> None:
        for post in posts:
            if generation != self.tagging_generation or self.tagging_stop_event.is_set():
                return
            url = str(post.get("preview_url") or "")
            if not url:
                continue
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "artist-by-tag/1.0",
                        "Referer": "https://gelbooru.com/",
                    },
                )
                with urllib.request.urlopen(request, timeout=20) as response:
                    content = response.read()
                assert Image is not None
                picture = Image.open(BytesIO(content))
                picture.thumbnail((card_width - 20, 145))
                self.events.put(
                    (
                        "tagging_thumbnail",
                        (
                            generation,
                            int(post.get("id", 0)),
                            picture.copy(),
                            int(post.get("tag_count", 0)),
                        ),
                    )
                )
            except (OSError, ValueError, urllib.error.URLError):
                continue

    def _build_cleanup_tab(self) -> None:
        frame = ttk.LabelFrame(
            self.cleanup_tab,
            text="Auditer les téléchargements avec la blacklist Grabber",
            padding=10,
        )
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(
            frame,
           text=(
                "Un dossier par ligne. L’analyse combine les artistes du nom "
                "de fichier et les tags présents dans le chemin. Les règles "
                "composées et width/height sont ignorées."
            ),
            wraplength=850,
        ).pack(anchor="w")
        self.cleanup_folders_text = ScrolledText(frame, height=7, wrap="none")
        self.cleanup_folders_text.pack(fill=X, pady=(8, 6))
        buttons = ttk.Frame(frame)
        buttons.pack(fill=X)
        ttk.Button(
            buttons,
            text="Ajouter un dossier…",
            command=self.add_cleanup_folder,
        ).pack(side=LEFT)
        ttk.Button(
            buttons,
            text="Vider la liste",
            command=lambda: self.cleanup_folders_text.delete("1.0", END),
        ).pack(side=LEFT, padx=(6, 0))
        self.cleanup_scan_button = ttk.Button(
            buttons,
            text="Analyser (lecture seule)",
            command=self.start_cleanup_scan,
        )
        self.cleanup_scan_button.pack(side=RIGHT)
        self.cleanup_recycle_button = ttk.Button(
            buttons,
            text="Envoyer les correspondances à la Corbeille",
            command=self.recycle_cleanup_matches,
            state="disabled",
        )
        self.cleanup_recycle_button.pack(side=RIGHT, padx=(0, 8))
        self.cleanup_status_var = StringVar(value="Aucune analyse effectuée.")
        ttk.Label(frame, textvariable=self.cleanup_status_var).pack(
            fill=X, pady=(8, 4)
        )
        self.cleanup_log = ScrolledText(
            frame, height=20, state="disabled", wrap="word"
        )
        self.cleanup_log.pack(fill=BOTH, expand=True)

    def cleanup_log_line(self, text: str) -> None:
        self.cleanup_log.configure(state="normal")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.cleanup_log.insert(END, f"[{stamp}] {text.rstrip()}\n")
        self.cleanup_log.see(END)
        self.cleanup_log.configure(state="disabled")

    def add_cleanup_folder(self) -> None:
        value = filedialog.askdirectory(title="Ajouter un dossier à analyser")
        if not value:
            return
        
        current = {
            line.strip()
            for line in self.cleanup_folders_text.get("1.0", END).splitlines()
            if line.strip()
        }
        if value not in current:
            self.cleanup_folders_text.insert(END, value + "\n")

    def start_cleanup_scan(self) -> None:
        if self.cleanup_running:
            return
        roots = [
            Path(line.strip().strip('"'))
            for line in self.cleanup_folders_text.get("1.0", END).splitlines()
            if line.strip()
        ]
        missing = [str(path) for path in roots if not path.exists()]
        if not roots:
            messagebox.showwarning("Aucun dossier", "Ajoute au moins un dossier.")
            return
        if missing:
            messagebox.showerror(
                "Dossier introuvable", "\n".join(missing[:10])
            )
            return
        self.cleanup_running = True
        self.cleanup_matches = []
        self.cleanup_report = None
        self.cleanup_scan_button.configure(state="disabled")
        self.cleanup_recycle_button.configure(state="disabled")
        self.cleanup_status_var.set("Analyse en lecture seule en cours…")
        self.cleanup_log_line(
            f"Début de l’analyse de {len(roots)} dossier(s). Aucune suppression."
        )
        threading.Thread(
            target=self._run_cleanup_scan, args=(roots,), daemon=True
        ).start()

    def _run_cleanup_scan(self, roots: list[Path]) -> None:
        try:
            blacklist = self.grabber_dir / "blacklist.txt"
            parsed = parse_cleanup_blacklist(
                blacklist.read_text(
                    encoding="utf-8-sig", errors="replace"
                ).splitlines()
            )
            matches: list[CleanupMatch] = []
            count = 0
            for count, path in enumerate(iter_image_files(roots), start=1):
                matches.extend(cleanup_match_file(path, parsed, "all"))
                if count == 1 or count % 500 == 0:
                    self.events.put(("cleanup_progress", (count, len(matches))))
            report = (
                Path(self.output_var.get())
                / "retro_cleanup"
                / f"audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            )
            write_cleanup_report(report, matches)
            self.events.put(
                (
                    "cleanup_done",
                    (count, matches, report, parsed.ignored_compound, parsed.ignored_non_tag),
                )
            )
        except Exception as exc:
            self.events.put(("cleanup_error", str(exc)))

    def recycle_cleanup_matches(self) -> None:
        files = sorted({match.path for match in self.cleanup_matches})
        if not files:
            return
        total_size = sum(
            path.stat().st_size for path in files if path.is_file()
        )
        if not messagebox.askyesno(
            "Confirmer l’envoi à la Corbeille",
            f"Envoyer {len(files)} fichier(s), soit "
            f"{total_size / (1024 * 1024):.1f} Mio, à la Corbeille ?\n\n"
            f"Le journal est conservé ici :\n{self.cleanup_report}",
        ):
            return
        self.cleanup_recycle_button.configure(state="disabled")
        self.cleanup_scan_button.configure(state="disabled")
        self.cleanup_status_var.set(
            f"Envoi de {len(files)} fichier(s) à la Corbeille Windows…"
        )
        self.cleanup_log_line(
            f"Corbeille : préparation de {len(files)} fichier(s)."
        )
        threading.Thread(
            target=self._run_cleanup_recycle, args=(files,), daemon=True
        ).start()

    def _run_cleanup_recycle(self, files: list[Path]) -> None:
        ok, message = send_to_recycle_bin(files)
        self.events.put(("cleanup_recycle_done", (ok, message)))

    def _path_row(self, parent, row, label, variable, command) -> None:
        ttk.Label(parent, text=label, width=18).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=5, pady=2
        )
        ttk.Button(parent, text="…", width=3, command=command).grid(row=row, column=2)
        parent.columnconfigure(1, weight=1)

    def _build_tag_list_tab(self) -> None:
        info = ttk.LabelFrame(
            self.tag_list_tab, text="Créer directement des onglets Grabber", padding=10
        )
        info.pack(fill=BOTH, expand=True)
        ttk.Label(
            info,
            text=(
                "Colle une liste préparée manuellement ou par famille de tags. "
                "Une ligne = un onglet ; les doublons sont retirés."
            ),
            wraplength=820,
        ).pack(anchor="w")
        self.manual_tag_text = ScrolledText(info, height=20, wrap="none")
        self.manual_tag_text.pack(fill=BOTH, expand=True, pady=(8, 8))
        controls = ttk.Frame(info)
        controls.pack(fill=X)
        ttk.Button(
            controls,
            text="Charger un fichier TXT…",
            command=self.load_manual_tag_file,
        ).pack(side=LEFT)
        ttk.Label(controls, text="Onglets par lot").pack(side=LEFT, padx=(15, 4))
        ttk.Spinbox(
            controls,
            from_=1,
            to=200,
            textvariable=self.batch_size_var,
            width=6,
        ).pack(side=LEFT)
        ttk.Label(controls, text="Images par onglet").pack(
            side=LEFT, padx=(15, 4)
        )
        ttk.Spinbox(
            controls,
            from_=1,
            to=100,
            textvariable=self.images_per_tab_var,
            width=6,
        ).pack(side=LEFT)
        self.manual_generate_button = ttk.Button(
            controls,
            text="Créer la session Grabber",
            command=self.generate_manual_tag_session,
        )
        self.manual_generate_button.pack(side=RIGHT)
        modifiers = ttk.Frame(info)
        modifiers.pack(fill=X, pady=(8, 0))
        ttk.Label(modifiers, text="Avant chaque tag :").pack(side=LEFT)
        ttk.Entry(modifiers, textvariable=self.tab_prefix_var, width=30).pack(
            side=LEFT, padx=(5, 15)
        )
        ttk.Label(modifiers, text="Après chaque tag :").pack(side=LEFT)
        ttk.Entry(modifiers, textvariable=self.tab_suffix_var, width=30).pack(
            side=LEFT, padx=(5, 0)
        )
        self.manual_tag_text.bind("<<Modified>>", self._on_manual_tags_modified)

    def _build_database_tab(self) -> None:
        frame = ttk.LabelFrame(
            self.database_tab, text="Mettre à jour les bases locales", padding=10
        )
        frame.pack(fill=BOTH, expand=True)
        ttk.Checkbutton(
            frame,
            text="Gelbooru — collecte API reprenable",
            variable=self.update_gelbooru_var,
        ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Label(frame, textvariable=self.gel_db_name_var).grid(
            row=1, column=0, sticky="w", padx=(24, 20)
        )
        ttk.Checkbutton(
            frame,
            text="e621 — dernier export officiel",
            variable=self.update_e621_var,
        ).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(frame, textvariable=self.e621_db_name_var).grid(
            row=1, column=1, sticky="w", padx=(24, 0)
        )
        frame.columnconfigure(1, weight=1)
        ttk.Label(
            frame,
            text=(
                "Une sauvegarde datée est créée avant de modifier une base existante. "
                "Les deux sites restent dans des fichiers séparés."
            ),
            wraplength=800,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 6))
        self.database_log = ScrolledText(frame, height=20, state="disabled", wrap="word")
        self.database_log.grid(row=3, column=0, columnspan=2, sticky="nsew")
        frame.rowconfigure(3, weight=1)
        bottom = ttk.Frame(frame)
        bottom.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(bottom, textvariable=self.database_status_var).pack(side=LEFT)
        self.database_update_button = ttk.Button(
            bottom, text="Mettre à jour les bases cochées", command=self.update_databases
        )
        self.database_update_button.pack(side=RIGHT)

    def _build_options_tab(self) -> None:

        locations = ttk.LabelFrame(
            self.options_tab, text="Emplacements utilisés", padding=12
        )
        locations.pack(fill=X)
        locations.columnconfigure(0, weight=1)
        locations.columnconfigure(1, weight=1)
        self._option_location(
            locations, 0, 0, "Base Gelbooru", self.gel_db_name_var,
            self._choose_gelbooru_db,
        )
        self._option_location(
            locations, 0, 1, "Base e621", self.e621_db_name_var,
            self._choose_e621_db,
        )
        self._option_location(
            locations, 1, 0, "Dossier des résultats", self.output_name_var,
            self._choose_output,
        )
        self._option_location(
            locations, 1, 1, "Dossier Grabber", self.grabber_name_var,
            self._choose_grabber,
        )

        cache = ttk.LabelFrame(self.options_tab, text="Cache", padding=12)
        cache.pack(fill=X, pady=(10, 0))
        ttk.Label(cache, text="Validité des compteurs locaux :").pack(side=LEFT)
        ttk.Spinbox(
            cache,
            from_=0,
            to=3650,
            textvariable=self.cache_days_var,
            width=7,
        ).pack(side=LEFT, padx=(6, 4))
        ttk.Label(cache, text="jours (0 = toujours actualiser)").pack(side=LEFT)

        ttk.Label(
            self.options_tab,
            text=(
                "Les chemins complets restent enregistrés dans la configuration, "
                "mais seuls les noms utiles sont affichés ici pour alléger l’interface."
            ),
            wraplength=850,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 0))

    def _build_tag_organizer_tab(self) -> None:
        self.organizer_path: list[str] = []
        self.organizer_selected: tuple[int, str, bool] | None = None
        self.organizer_lists: list[Listbox] = []
        self.organizer_pointer_state: dict | None = None
        self.organizer_clipboard: dict | None = None
        self.organizer_context: dict | None = None
        self.organizer_wiki_update_running = False

        top = ttk.Frame(self.tag_organizer_tab)
        top.pack(fill=X)
        ttk.Label(top, text="Board :").pack(side=LEFT)
        board_box = ttk.Combobox(
            top,
            state="readonly",
            width=18,
            textvariable=self.organizer_board_var,
            values=sorted(self.tag_organization["boards"]),
        )
        board_box.pack(side=LEFT, padx=(5, 12))
        board_box.bind("<<ComboboxSelected>>", self._organizer_board_changed)
        ttk.Button(top, text="Ajouter un board…", command=self._organizer_add_board).pack(
            side=LEFT
        )
        self.organizer_update_button = ttk.Button(
            top, text="Mettre à jour depuis les wikis", command=self._start_wiki_update
        )
        self.organizer_update_button.pack(side=RIGHT)
        self.organizer_update_progress = ttk.Progressbar(
            top, mode="indeterminate", length=100
        )
        self.organizer_update_progress.pack(side=RIGHT, padx=(0, 7))
        ttk.Label(
            top,
            text=(
                "Profondeur libre · Ctrl/Maj : sélection multiple · "
                "Clic droit : couper/copier/coller · Bleu : contient des sous-catégories."
            ),
            foreground="#555555",
        ).pack(side=LEFT, padx=(16, 0))

        actions = ttk.Frame(self.tag_organizer_tab)
        actions.pack(fill=X, pady=(8, 6))
        ttk.Button(
            actions,
            text="Ajouter au niveau 1…",
            command=lambda: self._organizer_add_category(at_root=True),
        ).pack(side=LEFT)
        ttk.Button(
            actions,
            text="Ajouter une sous-catégorie…",
            command=self._organizer_add_category,
        ).pack(side=LEFT, padx=(6, 0))
        ttk.Button(actions, text="Ajouter des tags…", command=self._organizer_add_tags).pack(side=LEFT, padx=(6, 0))
        ttk.Button(
            actions,
            text="Importer une page/liste…",
            command=self._organizer_import_list,
        ).pack(side=LEFT, padx=(6, 0))
        ttk.Button(actions, text="Renommer…", command=self._organizer_rename).pack(side=LEFT, padx=(6, 0))
        ttk.Button(actions, text="Supprimer", command=self._organizer_delete).pack(side=LEFT, padx=(6, 0))
        self.organizer_undo_button = ttk.Button(
            actions, text="Annuler (0)", command=self._organizer_undo, state="disabled"
        )
        self.organizer_undo_button.pack(side=RIGHT)
        selection_actions = ttk.Frame(self.tag_organizer_tab)
        selection_actions.pack(fill=X, pady=(0, 6))
        ttk.Button(
            selection_actions, text="Ajouter toute la branche au panier",
            command=self._organizer_add_branch_to_basket,
        ).pack(side=LEFT)
        ttk.Button(
            selection_actions, text="Ajouter la sélection au panier",
            command=self._organizer_add_visible_selection_to_basket,
        ).pack(side=LEFT, padx=(6, 0))
        ttk.Button(
            selection_actions, text="Ajouter ce niveau au panier",
            command=self._organizer_add_current_level_to_basket,
        ).pack(side=LEFT, padx=(6, 0))
        ttk.Button(
            selection_actions,
            text="Envoyer la sélection vers Revue depuis une liste",
            command=self._organizer_send_tags,
        ).pack(side=RIGHT)

        ttk.Label(
            self.tag_organizer_tab, textvariable=self.organizer_update_var
        ).pack(fill=X, pady=(0, 5))
        holder = ttk.Frame(self.tag_organizer_tab)
        holder.pack(fill=BOTH, expand=True)
        search_panel = ttk.LabelFrame(holder, text="Recherche globale", padding=6)
        search_panel.pack(side=LEFT, fill="y", padx=(0, 8))
        ttk.Entry(search_panel, textvariable=self.organizer_search_var, width=32).pack(fill=X)
        ttk.Label(
            search_panel,
            text="* et ? sont acceptés (ex. *_skirt)",
            foreground="#555555",
        ).pack(anchor="w", pady=(3, 5))
        self.organizer_search_results = Listbox(
            search_panel, width=38, selectmode=EXTENDED, exportselection=False
        )
        self.organizer_search_results.pack(fill=BOTH, expand=True)
        self.organizer_search_results.bind(
            "<<ListboxSelect>>", self._organizer_search_selection
        )
        self.organizer_search_results.bind(
            "<Double-1>", self._organizer_open_search_result
        )
        ttk.Button(
            search_panel,
            text="Ajouter au panier",
            command=self._organizer_add_search_to_basket,
        ).pack(fill=X, pady=(6, 0))
        self.organizer_basket_var = StringVar(value="Panier : 0 tag")
        ttk.Label(search_panel, textvariable=self.organizer_basket_var).pack(anchor="w", pady=(5, 0))
        ttk.Button(
            search_panel,
            text="Envoyer le panier vers la revue",
            command=self._organizer_send_basket,
        ).pack(fill=X, pady=(4, 0))

        browser_panel = ttk.Frame(holder)
        browser_panel.pack(side=LEFT, fill=BOTH, expand=True)
        self.organizer_root_drop = ttk.Label(
            browser_panel,
            text="Niveau 1 — clic droit ici pour coller à la racine",
            anchor="center",
            padding=6,
            relief="groove",
            foreground="#555555",
        )
        self.organizer_root_drop.pack(fill=X, pady=(0, 5))
        self.organizer_root_drop.bind("<Button-3>", self._organizer_root_context_menu)
        self.organizer_context_menu = Menu(self.root, tearoff=False)
        self.organizer_context_menu.add_command(
            label="Couper", command=lambda: self._organizer_copy_or_cut(True)
        )
        self.organizer_context_menu.add_command(
            label="Copier", command=lambda: self._organizer_copy_or_cut(False)
        )
        self.organizer_context_menu.add_command(
            label="Supprimer", command=lambda: self._organizer_delete(confirm=False)
        )
        self.organizer_context_menu.add_separator()
        self.organizer_context_menu.add_command(
            label="Coller", command=self._organizer_paste
        )
        self.organizer_canvas = Canvas(browser_panel, highlightthickness=0)
        self.organizer_canvas.pack(fill=BOTH, expand=True, side="top")
        scrollbar = ttk.Scrollbar(
            browser_panel, orient="horizontal", command=self.organizer_canvas.xview
        )
        scrollbar.pack(fill=X, side="bottom")
        self.organizer_canvas.configure(xscrollcommand=scrollbar.set)
        self.organizer_columns = ttk.Frame(self.organizer_canvas)
        self.organizer_canvas_window = self.organizer_canvas.create_window(
            (0, 0), window=self.organizer_columns, anchor="nw"
        )
        self.organizer_columns.bind(
            "<Configure>",
            lambda _event: self.organizer_canvas.configure(
                scrollregion=self.organizer_canvas.bbox("all")
            ),
        )
        self.organizer_canvas.bind(
            "<Configure>",
            lambda event: self.organizer_canvas.itemconfigure(
                self.organizer_canvas_window,
                height=max(1, event.height),
            ),
        )
        details = ttk.LabelFrame(
            self.tag_organizer_tab, text="Définition du tag", padding=6
        )
        details.pack(fill=X, pady=(8, 0))
        self.organizer_definition_text = ScrolledText(
            details, height=7, wrap="word", state="disabled"
        )
        self.organizer_definition_text.pack(fill=X)
        ttk.Label(
            details, textvariable=self.organizer_source_var, foreground="#555555"
        ).pack(anchor="w", pady=(3, 0))
        self.organizer_search_var.trace_add("write", self._organizer_refresh_search)
        self._organizer_render()
        self._organizer_refresh_search()

    def _organizer_board_root(self):
        return self.tag_organization["boards"].setdefault(
            self.organizer_board_var.get(), {}
        )

    def _organizer_push_undo(self, label: str) -> None:
        snapshot = json.dumps(
            self.tag_organization, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.organizer_undo_history.append((label, zlib.compress(snapshot, level=3)))
        del self.organizer_undo_history[:-20]
        self._organizer_update_undo_button()

    def _organizer_update_undo_button(self) -> None:
        if not hasattr(self, "organizer_undo_button"):
            return
        count = len(self.organizer_undo_history)
        self.organizer_undo_button.configure(
            text=f"Annuler ({count})", state="normal" if count else "disabled"
        )

    def _organizer_undo(self) -> None:
        if not self.organizer_undo_history:
            return
        label, compressed = self.organizer_undo_history.pop()
        self.tag_organization = json.loads(zlib.decompress(compressed).decode("utf-8"))
        self.organizer_path = []
        self.organizer_selected = None
        self._save_tag_organization()
        self._organizer_render()
        self._organizer_refresh_search()
        self._organizer_update_undo_button()
        self.organizer_update_var.set(f"Action annulée : {label}.")

    def _organizer_node(self, path: list[str] | None = None):
        node = self._organizer_board_root()
        for name in self.organizer_path if path is None else path:
            node = node[name]
        return node

    def _organizer_render(self) -> None:
        for child in self.organizer_columns.winfo_children():
            child.destroy()
        self.organizer_lists = []
        node = self._organizer_board_root()
        depth = 0
        while isinstance(node, dict):
            children = sorted(
                key for key in node
                if key not in {"__tag__", "__tags__", "__manual__"}
            )
            if not children:
                break
            branches = {
                key
                for key in children
                if isinstance(node.get(key), dict)
                and any(
                    child not in {"__tag__", "__tags__", "__manual__"}
                    for child in node[key]
                )
            }
            self._organizer_column(
                depth, f"Niveau {depth + 1}", children, False, branches
            )
            if depth >= len(self.organizer_path):
                break
            selected = self.organizer_path[depth]
            if selected not in node:
                self.organizer_path = self.organizer_path[:depth]
                break
            box = self.organizer_lists[-1]
            position = children.index(selected)
            box.selection_set(position)
            box.selection_anchor(position)
            box.activate(position)
            box.see(position)
            node = node[selected]
            depth += 1
        if isinstance(node, list):
            self._organizer_column(depth, "Tags", sorted(node), True)
        elif isinstance(node, dict) and node.get("__tags__"):
            self._organizer_column(
                depth, "Tags de ce groupe", sorted(node["__tags__"]), True
            )

    def _organizer_column(
        self,
        depth: int,
        title: str,
        values: list[str],
        is_tags: bool,
        branches: set[str] | None = None,
    ) -> None:
        column = ttk.LabelFrame(self.organizer_columns, text=title, padding=6)
        column.pack(side=LEFT, fill="y", padx=(0, 7))
        box = Listbox(
            column,
            width=28,
            selectmode=EXTENDED,
            exportselection=False,
        )
        box.pack(fill=BOTH, expand=True)
        for index, value in enumerate(values):
            box.insert(END, value)
            if branches and value in branches:
                box.itemconfigure(
                    index,
                    foreground="#075a9c",
                    background="#e8f3ff",
                    selectforeground="#ffffff",
                    selectbackground="#176b9c",
                )
        box.organizer_depth = depth
        box.organizer_is_tags = is_tags
        list_index = len(self.organizer_lists)
        box.bind(
            "<<ListboxSelect>>",
            lambda event, i=list_index, d=depth, tags=is_tags: self._organizer_select(event, i, d, tags),
        )
        box.bind(
            "<ButtonPress-1>",
            lambda event, i=list_index, d=depth, tags=is_tags: self._organizer_pointer_press(
                event, i, d, tags
            ),
        )
        box.bind("<ButtonRelease-1>", self._organizer_pointer_release)
        box.bind(
            "<Button-3>",
            lambda event, i=list_index, d=depth, tags=is_tags: self._organizer_list_context_menu(
                event, i, d, tags
            ),
        )
        box.bind("<Delete>", self._organizer_delete_key)
        self.organizer_lists.append(box)

    def _organizer_select(self, event, list_index: int, depth: int, is_tags: bool) -> None:
        box = self.organizer_lists[list_index]
        selection = box.curselection()
        if not selection:
            return
        active = int(box.index("active"))
        chosen = active if active in selection else selection[-1]
        name = str(box.get(chosen))
        self.organizer_selected = (depth, name, is_tags)
        self.organizer_selected_list_index = list_index
        modified_selection = len(selection) > 1 or bool(
            getattr(event, "state", 0) & 0x0005
        )
        if not is_tags and not modified_selection:
            self.organizer_path = self.organizer_path[:depth] + [name]
            self._organizer_render()
        elif is_tags:
            self._organizer_load_definition(name)

    def _organizer_open_context_menu(self, event, can_copy: bool) -> str:
        board_matches = bool(
            self.organizer_clipboard
            and self.organizer_clipboard.get("board") == self.organizer_board_var.get()
        )
        self.organizer_context_menu.entryconfigure(
            0, state="normal" if can_copy else "disabled"
        )
        self.organizer_context_menu.entryconfigure(
            1, state="normal" if can_copy else "disabled"
        )
        self.organizer_context_menu.entryconfigure(
            2, state="normal" if can_copy else "disabled"
        )
        paste_label = "Coller"
        if board_matches:
            count = len(self.organizer_clipboard.get("names", []))
            paste_label = f"Coller ({count})"
        self.organizer_context_menu.entryconfigure(
            4,
            label=paste_label,
            state="normal" if board_matches else "disabled",
        )
        try:
            self.organizer_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.organizer_context_menu.grab_release()
        return "break"

    def _organizer_list_context_menu(
        self, event, list_index: int, depth: int, is_tags: bool
    ) -> str:
        box = self.organizer_lists[list_index]
        source_path = list(self.organizer_path[:depth])
        clicked_name: str | None = None
        if box.size():
            index = int(box.nearest(event.y))
            bounds = box.bbox(index)
            if bounds and bounds[1] <= event.y < bounds[1] + bounds[3]:
                clicked_name = str(box.get(index))
                if not box.selection_includes(index):
                    box.selection_clear(0, END)
                    box.selection_set(index)
                    box.selection_anchor(index)
                box.activate(index)
        names = [str(box.get(index)) for index in box.curselection()]
        destination_path = list(source_path)
        if clicked_name is not None and not is_tags:
            destination_path.append(clicked_name)
        self.organizer_context = {
            "board": self.organizer_board_var.get(),
            "source_path": source_path,
            "destination_path": destination_path,
            "names": names,
            "is_tags": is_tags,
        }
        if clicked_name is not None:
            self.organizer_selected = (depth, clicked_name, is_tags)
            self.organizer_selected_list_index = list_index
        return self._organizer_open_context_menu(event, bool(names))

    def _organizer_root_context_menu(self, event) -> str:
        self.organizer_context = {
            "board": self.organizer_board_var.get(),
            "source_path": [],
            "destination_path": [],
            "names": [],
            "is_tags": False,
        }
        return self._organizer_open_context_menu(event, False)

    def _organizer_copy_or_cut(self, cut: bool) -> None:
        context = self.organizer_context
        if not context or not context.get("names"):
            return
        names = list(context["names"])
        try:
            source_node = self._organizer_node(context["source_path"])
        except (KeyError, TypeError):
            self.organizer_update_var.set("Copie annulée : la source n’existe plus.")
            return
        if context["is_tags"]:
            source_tags = (
                source_node if isinstance(source_node, list)
                else source_node.get("__tags__", []) if isinstance(source_node, dict)
                else []
            )
            if any(name not in source_tags for name in names):
                self.organizer_update_var.set("Copie annulée : un tag source n’existe plus.")
                return
            payload = list(names)
            kind = "tag(s)"
        else:
            if not isinstance(source_node, dict) or any(
                name not in source_node for name in names
            ):
                self.organizer_update_var.set(
                    "Copie annulée : une catégorie source n’existe plus."
                )
                return
            payload = {name: copy.deepcopy(source_node[name]) for name in names}
            kind = "catégorie(s)"
        self.organizer_clipboard = {
            "board": context["board"],
            "source_path": list(context["source_path"]),
            "names": names,
            "is_tags": bool(context["is_tags"]),
            "cut": cut,
            "payload": payload,
        }
        action = "coupé(s)" if cut else "copié(s)"
        self.organizer_update_var.set(
            f"{len(names)} {kind} {action} — clic droit sur la destination puis Coller."
        )

    def _organizer_paste(self) -> None:
        clipboard = self.organizer_clipboard
        context = self.organizer_context
        if not clipboard or not context:
            return
        if clipboard["board"] != self.organizer_board_var.get():
            self.organizer_update_var.set(
                "Collage annulé : le presse-papiers appartient à une autre board."
            )
            return
        destination_path = list(context["destination_path"])
        if clipboard["cut"]:
            before = json.dumps(
                self.tag_organization, ensure_ascii=False, sort_keys=True
            )
            self._organizer_move_items(
                list(clipboard["source_path"]),
                list(clipboard["names"]),
                bool(clipboard["is_tags"]),
                destination_path,
            )
            after = json.dumps(
                self.tag_organization, ensure_ascii=False, sort_keys=True
            )
            if before != after:
                self.organizer_clipboard = None
            return
        self._organizer_copy_items(clipboard, destination_path)

    def _organizer_copy_items(
        self, clipboard: dict, destination_path: list[str]
    ) -> None:
        try:
            destination_node = self._organizer_node(destination_path)
        except (KeyError, TypeError):
            self.organizer_update_var.set("Collage annulé : la destination n’existe plus.")
            return
        names = list(clipboard["names"])
        if clipboard["is_tags"]:
            if not destination_path:
                self.organizer_update_var.set(
                    "Un tag doit être collé dans une catégorie, pas directement au niveau 1."
                )
                return
            destination_tags = (
                destination_node if isinstance(destination_node, list)
                else destination_node.get("__tags__", []) if isinstance(destination_node, dict)
                else []
            )
            additions = [name for name in names if name not in destination_tags]
            if not additions:
                self.organizer_update_var.set(
                    "Collage inutile : ces tags sont déjà présents à destination."
                )
                return
            self._organizer_push_undo(f"copie de {len(additions)} tag(s)")
            if isinstance(destination_node, dict):
                destination_tags = destination_node.setdefault("__tags__", [])
            destination_tags.extend(additions)
            destination_tags.sort()
            label = f"{len(additions)} tag(s) copié(s)"
        else:
            for name in names:
                source_path = list(clipboard["source_path"]) + [name]
                if destination_path[:len(source_path)] == source_path:
                    self.organizer_update_var.set(
                        f"Collage annulé : « {name} » ne peut pas être copié dans sa propre branche."
                    )
                    return
            if isinstance(destination_node, dict):
                duplicates = [name for name in names if name in destination_node]
                if duplicates:
                    self.organizer_update_var.set(
                        "Collage annulé : nom déjà présent à destination — "
                        + ", ".join(duplicates[:3])
                    )
                    return
            self._organizer_push_undo(f"copie de {len(names)} catégorie(s)")
            destination_dict = self._organizer_ensure_dict(destination_path)
            for name, node in clipboard["payload"].items():
                destination_dict[name] = copy.deepcopy(node)
            label = f"{len(names)} catégorie(s) copiée(s)"
        self.organizer_path = list(destination_path)
        self.organizer_selected = None
        self._save_tag_organization()
        self._organizer_render()
        self._organizer_refresh_search()
        self.organizer_update_var.set(f"{label}.")

    def _organizer_pointer_press(
        self, event, list_index: int, depth: int, is_tags: bool
    ):
        box = self.organizer_lists[list_index]
        if box.size() == 0:
            return "break"
        index = int(box.nearest(event.y))
        bounds = box.bbox(index)
        if not bounds or not (bounds[1] <= event.y < bounds[1] + bounds[3]):
            return "break"
        shift = bool(event.state & 0x0001)
        control = bool(event.state & 0x0004)
        preserved_multi = not shift and not control and box.selection_includes(index)
        if shift:
            try:
                anchor = int(box.index("anchor"))
            except Exception:
                anchor = index
            box.selection_clear(0, END)
            box.selection_set(min(anchor, index), max(anchor, index))
        elif control:
            if box.selection_includes(index):
                box.selection_clear(index)
            else:
                box.selection_set(index)
                box.selection_anchor(index)
        elif not preserved_multi:
            box.selection_clear(0, END)
            box.selection_set(index)
            box.selection_anchor(index)
        box.activate(index)
        selection = box.curselection()
        if selection:
            chosen = index if index in selection else selection[-1]
            self.organizer_selected = (depth, str(box.get(chosen)), is_tags)
            self.organizer_selected_list_index = list_index
        self.organizer_pointer_state = {
            "box": box,
            "depth": depth,
            "is_tags": is_tags,
            "index": index,
            "shift": shift,
            "control": control,
            "preserved_multi": preserved_multi,
            "source_path": list(self.organizer_path[:depth]),
        }
        return "break"

    def _organizer_pointer_release(self, _event):
        click = self.organizer_pointer_state
        self.organizer_pointer_state = None
        if not click:
            return "break"
        box = click["box"]
        index = click["index"]
        if click["preserved_multi"]:
            box.selection_clear(0, END)
            box.selection_set(index)
            box.selection_anchor(index)
        if click["shift"] or click["control"]:
            return "break"
        name = str(box.get(index))
        if click["is_tags"]:
            self._organizer_load_definition(name)
        else:
            self.organizer_path = click["source_path"] + [name]
            self._organizer_render()
        return "break"

    def _organizer_ensure_dict(self, path: list[str]) -> dict:
        node = self._organizer_board_root()
        if not path:
            return node
        parent = node
        for name in path:
            child = parent[name]
            if isinstance(child, list):
                child = {"__tags__": list(child)}
                parent[name] = child
            if not isinstance(child, dict):
                raise ValueError("Cette destination ne peut pas recevoir de catégorie.")
            parent = child
        return parent

    def _organizer_move_items(
        self,
        source_path: list[str],
        names: list[str],
        source_is_tags: bool,
        destination_path: list[str],
    ) -> None:
        if not names:
            return
        if source_path == destination_path:
            self.organizer_update_var.set("Déplacement inutile : la destination est inchangée.")
            return
        try:
            source_node = self._organizer_node(source_path)
            destination_node = self._organizer_node(destination_path)
        except (KeyError, TypeError):
            self.organizer_update_var.set("Déplacement annulé : une branche n’existe plus.")
            return
        if source_is_tags:
            if not destination_path:
                self.organizer_update_var.set(
                    "Un tag doit être collé dans une catégorie, pas directement au niveau 1."
                )
                return
            source_tags = (
                source_node if isinstance(source_node, list)
                else source_node.get("__tags__", []) if isinstance(source_node, dict)
                else []
            )
            destination_tags = (
                destination_node if isinstance(destination_node, list)
                else destination_node.get("__tags__", []) if isinstance(destination_node, dict)
                else []
            )
            duplicates = [name for name in names if name in destination_tags]
            if duplicates:
                self.organizer_update_var.set(
                    "Déplacement annulé : déjà présent à destination — "
                    + ", ".join(duplicates[:3])
                )
                return
            if any(name not in source_tags for name in names):
                self.organizer_update_var.set("Déplacement annulé : tag source introuvable.")
                return
            self._organizer_push_undo(f"déplacement de {len(names)} tag(s)")
            if isinstance(destination_node, dict):
                destination_tags = destination_node.setdefault("__tags__", [])
            for name in names:
                source_tags.remove(name)
                destination_tags.append(name)
            destination_tags.sort()
            label = f"{len(names)} tag(s) déplacé(s)"
        else:
            if not isinstance(source_node, dict):
                self.organizer_update_var.set("Déplacement annulé : catégorie source invalide.")
                return
            for name in names:
                moved_path = source_path + [name]
                if destination_path[:len(moved_path)] == moved_path:
                    self.organizer_update_var.set(
                        f"Déplacement annulé : « {name} » ne peut pas contenir sa propre branche."
                    )
                    return
                if name not in source_node:
                    self.organizer_update_var.set(
                        f"Déplacement annulé : catégorie « {name} » introuvable."
                    )
                    return
            if isinstance(destination_node, dict):
                duplicates = [name for name in names if name in destination_node]
                if duplicates:
                    self.organizer_update_var.set(
                        "Déplacement annulé : nom déjà présent à destination — "
                        + ", ".join(duplicates[:3])
                    )
                    return
            self._organizer_push_undo(f"déplacement de {len(names)} catégorie(s)")
            try:
                destination_dict = self._organizer_ensure_dict(destination_path)
            except ValueError as exc:
                self.organizer_undo_history.pop()
                self._organizer_update_undo_button()
                self.organizer_update_var.set(f"Déplacement annulé : {exc}")
                return
            moved = [(name, source_node.pop(name)) for name in names]
            destination_dict.update(moved)
            label = f"{len(names)} catégorie(s) déplacée(s)"
        self.organizer_path = list(destination_path)
        self.organizer_selected = None
        self._save_tag_organization()
        self._organizer_render()
        self._organizer_refresh_search()
        self.organizer_update_var.set(f"{label}.")

    def _organizer_board_changed(self, _event=None) -> None:
        self.organizer_path = []
        self.organizer_selected = None
        self._organizer_render()
        self._organizer_refresh_search()

    def _organizer_add_board(self) -> None:
        name = simpledialog.askstring("Nouveau board", "Nom du board :", parent=self.root)
        if not name or not name.strip():
            return
        key = name.strip().casefold()
        if key not in self.tag_organization["boards"]:
            self._organizer_push_undo(f"ajout du board {key}")
            self.tag_organization["boards"][key] = {}
        self.organizer_board_var.set(key)
        self._save_tag_organization()
        self._organizer_board_changed()

    def _organizer_add_category(self, at_root: bool = False) -> None:
        destination = "niveau 1" if at_root else "groupe ouvert"
        name = simpledialog.askstring(
            "Nouvelle catégorie",
            f"Nom de la catégorie ({destination}) :",
            parent=self.root,
        )
        if not name or not name.strip():
            return
        name = name.strip()
        current = self._organizer_board_root() if at_root else self._organizer_node()
        if isinstance(current, dict) and name in current:
            messagebox.showinfo(
                "Catégorie existante",
                f"La catégorie « {name} » existe déjà à cet emplacement.",
                parent=self.root,
            )
            return
        self._organizer_push_undo(f"ajout de la catégorie {name}")
        node = current
        if isinstance(node, list):
            replacement = {"__tags__": list(node)}
            parent = self._organizer_node(self.organizer_path[:-1])
            parent[self.organizer_path[-1]] = replacement
            node = replacement
        node[name] = {"__manual__": True}
        if at_root:
            self.organizer_path = []
            self.organizer_selected = None
        self._save_tag_organization()
        self._organizer_render()

    def _organizer_multiline_dialog(self, title: str, prompt: str) -> str:
        dialog = Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("1180x680")
        dialog.minsize(820, 480)
        ttk.Label(dialog, text=prompt, wraplength=1120, justify="left").pack(
            fill=X, padx=12, pady=(12, 7)
        )
        paned = ttk.Panedwindow(dialog, orient="horizontal")
        paned.pack(fill=BOTH, expand=True, padx=12)
        editor_panel = ttk.LabelFrame(paned, text="Texte avec tabulations", padding=6)
        preview_panel = ttk.LabelFrame(paned, text="Aperçu repliable", padding=6)
        paned.add(editor_panel, weight=3)
        paned.add(preview_panel, weight=2)

        editor_toolbar = ttk.Frame(editor_panel)
        editor_toolbar.pack(fill=X, pady=(0, 5))
        search_bar = ttk.Frame(editor_panel)
        search_var = StringVar()
        search_status = StringVar()
        ttk.Label(search_bar, text="Rechercher :").pack(side=LEFT)
        search_entry = ttk.Entry(search_bar, textvariable=search_var)
        search_entry.pack(side=LEFT, fill=X, expand=True, padx=(5, 5))
        editor_holder = ttk.Frame(editor_panel)
        editor_holder.pack(fill=BOTH, expand=True)
        line_numbers = Text(
            editor_holder, width=6, padx=5, takefocus=False, wrap="none",
            state="disabled", cursor="arrow", relief="flat",
            background="#ececec", foreground="#666666", font="TkFixedFont",
        )
        line_numbers.pack(side=LEFT, fill="y")
        indent_guides = Text(
            editor_holder, width=8, padx=4, takefocus=False, wrap="none",
            state="disabled", cursor="arrow", relief="flat",
            background="#f5f5f5", foreground="#6a86a8", font="TkFixedFont",
        )
        indent_guides.pack(side=LEFT, fill="y")
        editor = ScrolledText(
            editor_holder, wrap="none", undo=True, font="TkFixedFont"
        )
        editor.pack(side=LEFT, fill=BOTH, expand=True)
        ttk.Button(
            editor_toolbar, text="Coller", command=lambda: editor.event_generate("<<Paste>>")
        ).pack(side=LEFT)
        ttk.Button(
            editor_toolbar, text="Copier", command=lambda: editor.event_generate("<<Copy>>")
        ).pack(side=LEFT, padx=(5, 0))
        ttk.Button(
            editor_toolbar, text="Couper", command=lambda: editor.event_generate("<<Cut>>")
        ).pack(side=LEFT, padx=(5, 0))
        ttk.Label(
            editor_toolbar, text="→ = un niveau", foreground="#6a86a8"
        ).pack(side=LEFT, padx=(10, 0))
        ttk.Label(
            editor_toolbar,
            text="Tab : ajouter un niveau · Shift+Tab : retirer un niveau",
            foreground="#555555",
        ).pack(side=RIGHT)
        preview_toolbar = ttk.Frame(preview_panel)
        preview_toolbar.pack(fill=X, pady=(0, 5))
        tree_holder = ttk.Frame(preview_panel)
        tree_holder.pack(fill=BOTH, expand=True)
        preview_tree = ttk.Treeview(tree_holder, show="tree", selectmode="browse")
        preview_scroll_y = ttk.Scrollbar(
            tree_holder, orient="vertical", command=preview_tree.yview
        )
        preview_scroll_x = ttk.Scrollbar(
            tree_holder, orient="horizontal", command=preview_tree.xview
        )
        preview_tree.configure(
            yscrollcommand=preview_scroll_y.set,
            xscrollcommand=preview_scroll_x.set,
        )
        preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_scroll_y.grid(row=0, column=1, sticky="ns")
        preview_scroll_x.grid(row=1, column=0, sticky="ew")
        tree_holder.rowconfigure(0, weight=1)
        tree_holder.columnconfigure(0, weight=1)
        preview_status = StringVar(value="Colle une liste pour construire l’aperçu.")
        preview_path = StringVar(value="Chemin : —")
        status_label = ttk.Label(preview_panel, textvariable=preview_status)
        status_label.pack(fill=X, pady=(5, 0))
        ttk.Label(
            preview_panel, textvariable=preview_path, wraplength=430,
            foreground="#555555",
        ).pack(fill=X, pady=(3, 0))
        result: list[str] = []
        preview_paths: dict[str, list[str]] = {}
        preview_item_lines: dict[str, int] = {}
        preview_error_lines: set[int] = set()
        refresh_job = None

        def highlight_line_numbers(_event=None) -> None:
            current = int(editor.index("insert").split(".")[0])
            for gutter in (line_numbers, indent_guides):
                gutter.tag_remove("current", "1.0", END)
                gutter.tag_remove("error", "1.0", END)
                gutter.tag_add("current", f"{current}.0", f"{current}.end")
                for number in preview_error_lines:
                    gutter.tag_add("error", f"{number}.0", f"{number}.end")
                gutter.tag_raise("error")

        def refresh_line_numbers() -> None:
            lines = editor.get("1.0", "end-1c").split("\n")
            count = len(lines)
            first = editor.yview()[0]
            line_numbers.configure(state="normal")
            line_numbers.delete("1.0", END)
            line_numbers.insert("1.0", "\n".join(str(number) for number in range(1, count + 1)))
            line_numbers.configure(state="disabled")
            guides: list[str] = []
            max_depth = 0
            for value in lines:
                leading = value[:len(value) - len(value.lstrip(" \t"))]
                depth = len(leading.expandtabs(4)) // 4
                max_depth = max(max_depth, depth)
                guides.append("→ " * depth)
            indent_guides.configure(
                state="normal", width=max(4, min(24, max_depth * 2 + 1))
            )
            indent_guides.delete("1.0", END)
            indent_guides.insert("1.0", "\n".join(guides))
            indent_guides.configure(state="disabled")
            line_numbers.yview_moveto(first)
            indent_guides.yview_moveto(first)
            highlight_line_numbers()

        def sync_editor_scroll(first: str, last: str) -> None:
            editor.vbar.set(first, last)
            line_numbers.yview_moveto(first)
            indent_guides.yview_moveto(first)

        def scroll_from_gutter(event):
            steps = -1 * int(event.delta / 120) if event.delta else 0
            editor.yview_scroll(steps, "units")
            return "break"

        for gutter in (line_numbers, indent_guides):
            gutter.tag_configure("current", background="#dfe8f5")
            gutter.tag_configure(
                "error", background="#c83f3f", foreground="#ffffff"
            )
        editor.configure(yscrollcommand=sync_editor_scroll)
        line_numbers.bind("<MouseWheel>", scroll_from_gutter)
        indent_guides.bind("<MouseWheel>", scroll_from_gutter)

        editor.tag_configure("find_match", background="#fff1a8")
        editor.tag_configure("find_current", background="#ffbd69")
        current_search = {"query": "", "position": None}

        def refresh_search_highlights(*_args) -> list[str]:
            editor.tag_remove("find_match", "1.0", END)
            editor.tag_remove("find_current", "1.0", END)
            query = search_var.get()
            if query != current_search["query"]:
                current_search["query"] = query
                current_search["position"] = None
            if not query:
                search_status.set("")
                return []
            matches: list[str] = []
            position = "1.0"
            while True:
                position = editor.search(
                    query, position, stopindex="end-1c", nocase=True
                )
                if not position:
                    break
                end = f"{position}+{len(query)}c"
                matches.append(position)
                editor.tag_add("find_match", position, end)
                position = end
            current = current_search["position"]
            if current in matches:
                editor.tag_add(
                    "find_current", current, f"{current}+{len(query)}c"
                )
            search_status.set(f"{len(matches)} résultat(s)")
            return matches

        def find_match(backwards: bool = False, _event=None):
            matches = refresh_search_highlights()
            if not matches:
                return "break"
            cursor = editor.index("insert")
            current = current_search["position"]
            if current in matches:
                index = matches.index(current)
                target = matches[(index - 1) % len(matches)] if backwards else matches[(index + 1) % len(matches)]
            elif backwards:
                candidates = [pos for pos in matches if editor.compare(pos, "<", cursor)]
                target = candidates[-1] if candidates else matches[-1]
            else:
                candidates = [pos for pos in matches if editor.compare(pos, ">=", cursor)]
                target = candidates[0] if candidates else matches[0]
            end = f"{target}+{len(search_var.get())}c"
            editor.tag_remove("find_current", "1.0", END)
            editor.tag_add("find_current", target, end)
            current_search["position"] = target
            editor.mark_set("insert", target)
            editor.see(target)
            search_status.set(f"{matches.index(target) + 1}/{len(matches)}")
            highlight_line_numbers()
            return "break"

        def show_search(_event=None):
            if not search_bar.winfo_ismapped():
                search_bar.pack(fill=X, pady=(0, 5), before=editor_holder)
            search_entry.focus_set()
            search_entry.selection_range(0, END)
            refresh_search_highlights()
            return "break"

        def hide_search(_event=None):
            search_bar.pack_forget()
            editor.tag_remove("find_match", "1.0", END)
            editor.tag_remove("find_current", "1.0", END)
            current_search["position"] = None
            editor.focus_set()
            return "break"

        ttk.Button(
            search_bar, text="Précédent", command=lambda: find_match(True)
        ).pack(side=LEFT)
        ttk.Button(
            search_bar, text="Suivant", command=lambda: find_match(False)
        ).pack(side=LEFT, padx=(5, 0))
        ttk.Label(search_bar, textvariable=search_status, width=14).pack(
            side=LEFT, padx=(7, 0)
        )
        ttk.Button(search_bar, text="Fermer", command=hide_search).pack(side=RIGHT)
        search_var.trace_add("write", refresh_search_highlights)

        def set_open(item: str, mode: str, depth: int = 0) -> None:
            if mode == "collapse":
                preview_tree.item(item, open=False)
            else:
                preview_tree.item(item, open=depth < 2)
            for child in preview_tree.get_children(item):
                set_open(child, mode, depth + 1)

        def collapse_all() -> None:
            for item in preview_tree.get_children(""):
                set_open(item, "collapse")

        def expand_two_levels() -> None:
            for item in preview_tree.get_children(""):
                set_open(item, "expand", 0)

        def refresh_preview() -> None:
            nonlocal refresh_job
            refresh_job = None
            text = editor.get("1.0", END)
            preview_tree.delete(*preview_tree.get_children(""))
            preview_paths.clear()
            preview_item_lines.clear()
            preview_error_lines.clear()
            if not text.strip():
                preview_status.set("Colle une liste pour construire l’aperçu.")
                status_label.configure(foreground="#555555")
                accept_button.configure(state="disabled")
                highlight_line_numbers()
                return
            tree, audit = analyze_pasted_tag_list(text)

            source_lines: dict[str, list[int]] = {}
            used_source_lines: set[int] = set()

            def source_keys(value: str) -> set[str]:
                value = html.unescape(value).strip()
                value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
                value = re.sub(
                    r"\[/?(?:b|i|u|s|url)(?:=[^\]]*)?\]",
                    "",
                    value,
                    flags=re.I,
                )
                value = re.sub(r"^[*:\-–—]+\s*", "", value).strip()
                variants = {value, value.split("//", 1)[0].strip()}
                variants.update(part.strip() for part in value.split(" / "))
                keys: set[str] = set()
                for variant in variants:
                    variant = variant.rstrip(":").strip()
                    key = re.sub(r"[\s_-]+", "_", variant).strip("_").casefold()
                    if key:
                        keys.add(key)
                return keys

            for line_number, raw_line in enumerate(text.splitlines(), 1):
                for key in source_keys(raw_line):
                    source_lines.setdefault(key, []).append(line_number)

            def source_line_for(name: str) -> int | None:
                for key in source_keys(name):
                    for line_number in source_lines.get(key, []):
                        if line_number not in used_source_lines:
                            used_source_lines.add(line_number)
                            return line_number
                return None

            def insert(parent: str, node, path: list[str], depth: int) -> None:
                if isinstance(node, list):
                    for tag in node:
                        item = preview_tree.insert(parent, END, text=str(tag))
                        preview_paths[item] = path + [str(tag)]
                        line_number = source_line_for(str(tag))
                        if line_number is not None:
                            preview_item_lines[item] = line_number
                    return
                if not isinstance(node, dict):
                    return
                for name, child in node.items():
                    if name in {"__tag__", "__tags__", "__manual__"}:
                        continue
                    children = (
                        [key for key in child if key not in {"__tag__", "__tags__", "__manual__"}]
                        if isinstance(child, dict) else []
                    )
                    if isinstance(child, dict):
                        descendant_tags = set(str(tag) for tag in child.get("__tags__", []))
                        for child_name, grandchild in child.items():
                            if child_name not in {"__tag__", "__tags__", "__manual__"}:
                                descendant_tags.update(str(tag) for tag in iter_tags(grandchild))
                    elif isinstance(child, list):
                        descendant_tags = set(str(tag) for tag in child)
                    else:
                        descendant_tags = set()
                    count = len(descendant_tags)
                    label = f"{name}  ({count})" if children and count else str(name)
                    item = preview_tree.insert(
                        parent, END, text=label, open=depth < 1
                    )
                    preview_paths[item] = path + [str(name)]
                    line_number = source_line_for(str(name))
                    if line_number is not None:
                        preview_item_lines[item] = line_number
                    insert(item, child, path + [str(name)], depth + 1)

            insert("", tree, [], 0)
            jumps = audit["jumps"]
            if jumps:
                preview_error_lines.update(int(jump["line"]) for jump in jumps)
                first = jumps[0]
                preview_status.set(
                    f"⚠ {len(jumps)} saut(s) d’indentation — premier à la ligne "
                    f"{first['line']} : {first['text']}"
                )
                status_label.configure(foreground="#a23b00")
                accept_button.configure(state="disabled")
            else:
                difference = audit["nonempty_lines"] - audit["node_count"]
                suffix = (
                    f" · {difference} ligne(s) fusionnée(s) ou ignorée(s)"
                    if difference else ""
                )
                preview_status.set(
                    f"{audit['node_count']} élément(s) · profondeur {audit['max_depth']}"
                    f" · indentation valide{suffix}"
                )
                status_label.configure(foreground="#236b2b")
                accept_button.configure(state="normal")
            highlight_line_numbers()

        def schedule_preview(_event=None) -> None:
            nonlocal refresh_job
            if editor.edit_modified():
                editor.edit_modified(False)
            if refresh_job is not None:
                dialog.after_cancel(refresh_job)
            refresh_line_numbers()
            if search_var.get():
                refresh_search_highlights()
            refresh_job = dialog.after(450, refresh_preview)

        def show_preview_path(_event=None) -> None:
            selection = preview_tree.selection()
            if selection:
                preview_path.set(
                    "Chemin : " + " → ".join(preview_paths.get(selection[0], []))
                )

        def selected_line_numbers() -> tuple[int, int, bool]:
            try:
                start = int(editor.index("sel.first").split(".")[0])
                end_index = editor.index("sel.last")
                end_line, end_column = (int(value) for value in end_index.split("."))
                if end_column == 0 and end_line > start:
                    end_line -= 1
                return start, end_line, True
            except Exception:
                line = int(editor.index("insert").split(".")[0])
                return line, line, False

        def jump_to_preview_line(event=None):
            if event is not None:
                clicked = preview_tree.identify_row(event.y)
                if clicked:
                    preview_tree.selection_set(clicked)
            selection = preview_tree.selection()
            if not selection:
                return "break"
            line_number = preview_item_lines.get(selection[0])
            if line_number is None:
                return "break"
            editor.tag_remove("sel", "1.0", END)
            editor.tag_add("sel", f"{line_number}.0", f"{line_number}.end")
            editor.mark_set("insert", f"{line_number}.0")
            editor.see(f"{line_number}.0")
            editor.focus_set()
            highlight_line_numbers()
            return "break"

        def indent_lines(_event=None):
            start, end, selected = selected_line_numbers()
            if not selected:
                editor.insert("insert", "\t")
            else:
                for line in range(start, end + 1):
                    editor.insert(f"{line}.0", "\t")
                editor.tag_remove("sel", "1.0", END)
                editor.tag_add("sel", f"{start}.0", f"{end}.end")
            schedule_preview()
            return "break"

        def unindent_lines(_event=None):
            start, end, selected = selected_line_numbers()
            for line in range(start, end + 1):
                value = editor.get(f"{line}.0", f"{line}.end")
                if value.startswith("\t"):
                    editor.delete(f"{line}.0", f"{line}.1")
                else:
                    spaces = len(value) - len(value.lstrip(" "))
                    if spaces:
                        editor.delete(f"{line}.0", f"{line}.{min(4, spaces)}")
            if selected:
                editor.tag_remove("sel", "1.0", END)
                editor.tag_add("sel", f"{start}.0", f"{end}.end")
            schedule_preview()
            return "break"

        def select_all_text(_event=None):
            editor.tag_add("sel", "1.0", "end-1c")
            editor.mark_set("insert", "1.0")
            editor.see("insert")
            return "break"

        def accept() -> None:
            result.append(editor.get("1.0", END))
            dialog.destroy()

        ttk.Button(
            preview_toolbar, text="Actualiser", command=refresh_preview
        ).pack(side=LEFT)
        ttk.Button(
            preview_toolbar, text="Replier tout", command=collapse_all
        ).pack(side=LEFT, padx=(5, 0))
        ttk.Button(
            preview_toolbar, text="Ouvrir 2 niveaux", command=expand_two_levels
        ).pack(side=LEFT, padx=(5, 0))
        buttons = ttk.Frame(dialog)
        buttons.pack(fill=X, padx=12, pady=12)
        ttk.Button(buttons, text="Annuler", command=dialog.destroy).pack(side=RIGHT)
        accept_button = ttk.Button(
            buttons, text="Importer", command=accept, state="disabled"
        )
        accept_button.pack(
            side=RIGHT, padx=(0, 7)
        )
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        editor.bind("<<Modified>>", schedule_preview)
        editor.bind("<Tab>", indent_lines)
        editor.bind("<Shift-Tab>", unindent_lines)
        editor.bind("<ISO_Left_Tab>", unindent_lines)
        editor.bind("<Control-a>", select_all_text)
        editor.bind("<Control-f>", show_search)
        editor.bind("<KeyRelease>", highlight_line_numbers, add="+")
        editor.bind("<ButtonRelease-1>", highlight_line_numbers, add="+")
        search_entry.bind("<Return>", lambda event: find_match(False, event))
        search_entry.bind("<Shift-Return>", lambda event: find_match(True, event))
        search_entry.bind("<Escape>", hide_search)
        preview_tree.bind("<<TreeviewSelect>>", show_preview_path)
        preview_tree.bind("<Double-1>", jump_to_preview_line)
        refresh_line_numbers()
        editor.focus_set()
        self.root.wait_window(dialog)
        return result[0] if result else ""

    def _organizer_tag_list(self, create: bool = False) -> list[str] | None:
        node = self._organizer_node()
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            if create:
                return node.setdefault("__tags__", [])
            tags = node.get("__tags__")
            return tags if isinstance(tags, list) else None
        return None

    def _organizer_add_tags(self) -> None:
        if not self.organizer_path:
            messagebox.showinfo("Choisir un groupe", "Sélectionne le groupe qui recevra les tags.")
            return
        value = self._organizer_multiline_dialog(
            "Ajouter des tags",
            "Colle un ou plusieurs tags exacts, un par ligne ou séparés par des points-virgules. "
            "Une tabulation devant une ligne en fait l’enfant de la ligne précédente; "
            "plusieurs tabulations créent des niveaux supplémentaires.",
        )
        if not value:
            return
        if any(line.startswith(("\t", "    ")) for line in value.splitlines()):
            tree = parse_pasted_tag_list(value)
            if not tree:
                messagebox.showwarning("Liste non reconnue", "Aucune hiérarchie valide n’a été détectée.")
                return
            self._organizer_apply_import(
                self.organizer_board_var.get(), list(self.organizer_path), tree
            )
            return
        existing = self._organizer_tag_list() or []
        additions = [tag for tag in unique_lines(value) if tag not in existing]
        if not additions:
            return
        self._organizer_push_undo(f"ajout de {len(additions)} tag(s)")
        node = self._organizer_tag_list(create=True)
        if node is None:
            return
        node.extend(additions)
        node.sort()
        self._save_tag_organization()
        self._organizer_render()
        self._organizer_refresh_search()

    def _organizer_merge_imported_tree(self, target: dict, incoming: dict) -> None:
        for key, child in incoming.items():
            if key in {"__tag__", "__tags__"}:
                if key == "__tags__":
                    tags = target.setdefault(key, [])
                    tags.extend(tag for tag in child if tag not in tags)
                else:
                    target[key] = child
                continue
            if key not in target:
                if isinstance(child, dict):
                    child["__manual__"] = True
                target[key] = child
            elif isinstance(target[key], dict) and isinstance(child, dict):
                self._organizer_merge_imported_tree(target[key], child)

    def _organizer_apply_import(self, board: str, path: list[str], tree: dict) -> None:
        node = self.tag_organization["boards"].setdefault(board, {})
        for name in path:
            if not isinstance(node, dict) or name not in node:
                raise ValueError("Le groupe de destination n’existe plus.")
            node = node[name]
        if not isinstance(node, (dict, list)):
            raise ValueError("La destination ne peut pas recevoir cette arborescence.")
        count = len(set(iter_tags(tree)))
        self._organizer_push_undo(f"import de {count} tag(s)")
        if isinstance(node, list):
            replacement = {"__tags__": list(node)}
            parent = self.tag_organization["boards"][board]
            for name in path[:-1]:
                parent = parent[name]
            parent[path[-1]] = replacement
            node = replacement
        if not isinstance(node, dict):
            raise ValueError("La destination ne peut pas recevoir cette arborescence.")
        self._organizer_merge_imported_tree(node, tree)
        self._save_tag_organization()
        if board == self.organizer_board_var.get():
            self._organizer_render()
            self._organizer_refresh_search()
        self.organizer_update_var.set(f"Import manuel terminé : {count} tag(s) ajoutés ou fusionnés.")

    def _organizer_import_list(self) -> None:
        if not self.organizer_path:
            messagebox.showinfo("Choisir un groupe", "Sélectionne d’abord le groupe parent de la liste.")
            return
        value = self._organizer_multiline_dialog(
            "Importer une page ou une liste wiki",
            "Colle l’URL d’une page wiki Gelbooru (ou son identifiant), ou colle directement la liste. "
            "Les titres terminés par ':' deviennent des catégories; *, -, — et : indiquent des enfants.",
        ).strip()
        if not value:
            return
        board = self.organizer_board_var.get()
        path = list(self.organizer_path)
        if "\n" not in value and (value.isdigit() or value.startswith(("http://", "https://"))):
            if board != "gelbooru":
                messagebox.showwarning(
                    "Board incompatible",
                    "L’import direct par URL est actuellement réservé aux pages wiki Gelbooru.",
                )
                return
            self.organizer_update_var.set("Téléchargement et analyse de la page wiki…")

            def fetch() -> None:
                try:
                    tree = gelbooru_page_tree(value)
                    self.events.put(("organizer_manual_import", (board, path, tree)))
                except Exception as exc:
                    self.events.put(("organizer_update_error", str(exc)))

            threading.Thread(target=fetch, daemon=True).start()
            return
        tree = parse_pasted_tag_list(value)
        if not tree:
            messagebox.showwarning("Liste non reconnue", "Aucun tag structuré n’a été détecté.")
            return
        self._organizer_apply_import(board, path, tree)

    def _organizer_rename(self) -> None:
        if not self.organizer_selected:
            return
        depth, old, is_tag = self.organizer_selected
        new = simpledialog.askstring("Renommer", "Nouveau nom :", initialvalue=old, parent=self.root)
        if not new or not new.strip() or new.strip() == old:
            return
        new = new.strip()
        self._organizer_push_undo(f"renommage de {old}")
        if is_tag:
            node = self._organizer_tag_list()
            if node is None:
                return
            node[node.index(old)] = new
            node.sort()
            board = self.organizer_board_var.get()
            metadata = self.tag_organization.setdefault("metadata", {}).setdefault(board, {})
            if old in metadata:
                metadata[new] = metadata.pop(old)
            if "Depuis le wiki" in self.organizer_path:
                excluded = self.tag_organization.setdefault(
                    "excluded_imported_tags", {}
                ).setdefault(board, [])
                if old not in excluded:
                    excluded.append(old)
        else:
            parent = self._organizer_node(self.organizer_path[:depth])
            parent[new] = parent.pop(old)
            self.organizer_path[depth] = new
        self.organizer_selected = None
        self._save_tag_organization()
        self._organizer_render()

    def _organizer_delete_key(self, _event=None):
        self._organizer_delete(confirm=False)
        return "break"

    def _organizer_delete(self, confirm: bool = True) -> None:
        if not self.organizer_selected:
            return
        depth, name, is_tag = self.organizer_selected
        names = [name]
        list_index = getattr(self, "organizer_selected_list_index", depth)
        if list_index < len(self.organizer_lists):
            box = self.organizer_lists[list_index]
            names = [str(box.get(index)) for index in box.curselection()] or names
        kind = "tag(s)" if is_tag else "catégorie(s)"
        if confirm and not messagebox.askyesno(
            "Confirmer", f"Supprimer {len(names)} {kind} ?"
        ):
            return
        self._organizer_push_undo(f"suppression de {len(names)} {kind}")
        if is_tag:
            node = self._organizer_tag_list()
            if node is None:
                self.organizer_undo_history.pop()
                self._organizer_update_undo_button()
                return
            for value in names:
                if value in node:
                    node.remove(value)
            if "Depuis le wiki" in self.organizer_path:
                excluded = self.tag_organization.setdefault(
                    "excluded_imported_tags", {}
                ).setdefault(self.organizer_board_var.get(), [])
                excluded.extend(value for value in names if value not in excluded)
        else:
            parent = self._organizer_node(self.organizer_path[:depth])
            removed_tags = []
            for value in names:
                removed_node = parent.get(value, {})
                removed_tags.extend(iter_tags(removed_node))
                parent.pop(value, None)
            if removed_tags:
                excluded = self.tag_organization.setdefault(
                    "excluded_imported_tags", {}
                ).setdefault(self.organizer_board_var.get(), [])
                excluded.extend(tag for tag in removed_tags if tag not in excluded)
            self.organizer_path = self.organizer_path[:depth]
        self.organizer_selected = None
        self._save_tag_organization()
        self._organizer_render()
        self._organizer_refresh_search()

    def _organizer_send_tags(self) -> None:
        if not self.organizer_lists:
            return
        box = self.organizer_lists[-1]
        node = self._organizer_tag_list()
        if node is None:
            messagebox.showinfo("Aucun tag", "Ouvre d’abord une branche contenant des tags.")
            return
        indexes = box.curselection()
        if not indexes:
            messagebox.showinfo("Aucune sélection", "Sélectionne un ou plusieurs tags.")
            return
        tags = [str(box.get(index)) for index in indexes]
        existing = unique_lines(self.manual_tag_text.get("1.0", END))
        merged = existing + [tag for tag in tags if tag not in existing]
        self.manual_tag_text.delete("1.0", END)
        self.manual_tag_text.insert("1.0", "\n".join(merged) + "\n")
        self.notebook.select(self.tag_list_tab)
        self._refresh_buttons()
    def _organizer_tag_paths(self) -> dict[str, list[list[str]]]:
        result: dict[str, list[list[str]]] = {}
        def walk(node, path: list[str]) -> None:
            if isinstance(node, list):
                for tag in node:
                    if self._organizer_is_usable_tag(str(tag)):
                        result.setdefault(str(tag), []).append(list(path))
            elif isinstance(node, dict):
                if "__tag__" in node:
                    tag = str(node["__tag__"])
                    result.setdefault(tag, []).append(list(path))
                for tag in node.get("__tags__", []):
                    if self._organizer_is_usable_tag(str(tag)):
                        result.setdefault(str(tag), []).append(list(path))
                for name, child in node.items():
                    if name not in {"__tag__", "__tags__", "__manual__"}:
                        walk(child, path + [str(name)])
        walk(self._organizer_board_root(), [])
        return result

    def _organizer_refresh_search(self, *_args) -> None:
        if not hasattr(self, "organizer_search_results"):
            return
        pattern = self.organizer_search_var.get().strip().casefold()
        self.organizer_search_results.delete(0, END)
        self.organizer_search_map: list[str] = []
        self.organizer_search_path_map: list[list[str]] = []
        if not pattern:
            return
        wildcard = pattern if any(char in pattern for char in "*?") else f"*{pattern}*"
        board = self.organizer_board_var.get()
        metadata = self.tag_organization.get("metadata", {}).get(board, {})
        for tag, paths in sorted(self._organizer_tag_paths().items(), key=lambda item: item[0].casefold()):
            aliases = metadata.get(tag, {}).get("aliases", [])
            matching_aliases = [
                str(alias) for alias in aliases
                if fnmatch.fnmatchcase(str(alias).casefold(), wildcard)
            ]
            if fnmatch.fnmatchcase(tag.casefold(), wildcard) or matching_aliases:
                self.organizer_search_map.append(tag)
                self.organizer_search_path_map.append(list(paths[0]) if paths else [])
                alias_suffix = (
                    f"  [alias: {', '.join(matching_aliases[:3])}]"
                    if matching_aliases else ""
                )
                self.organizer_search_results.insert(
                    END,
                    f"{tag}{alias_suffix}  —  "
                    + " | ".join(" → ".join(path) for path in paths[:2]),
                )

    def _organizer_search_selection(self, _event=None) -> None:
        indexes = self.organizer_search_results.curselection()
        if indexes:
            self._organizer_load_definition(self.organizer_search_map[indexes[0]])

    def _organizer_open_search_result(self, _event=None) -> None:
        indexes = self.organizer_search_results.curselection()
        if not indexes:
            return
        index = indexes[0]
        if index >= len(self.organizer_search_path_map):
            return
        self.organizer_path = list(self.organizer_search_path_map[index])
        self.organizer_selected = None
        self._organizer_render()

    def _organizer_add_search_to_basket(self) -> None:
        for index in self.organizer_search_results.curselection():
            value = self.organizer_search_map[index]
            if self._organizer_is_usable_tag(value):
                self.organizer_basket.add(value)
        self.organizer_basket_var.set(f"Panier : {len(self.organizer_basket)} tag(s)")

    def _organizer_add_branch_to_basket(self) -> None:
        self.organizer_basket.update(
            str(tag)
            for tag in iter_tags(self._organizer_node())
            if self._organizer_is_usable_tag(str(tag))
        )
        self.organizer_basket_var.set(f"Panier : {len(self.organizer_basket)} tag(s)")

    def _organizer_add_visible_selection_to_basket(self) -> None:
        if not self.organizer_selected or not self.organizer_lists:
            return
        depth, _name, is_tag = self.organizer_selected
        list_index = getattr(self, "organizer_selected_list_index", depth)
        box = self.organizer_lists[list_index]
        selected = [str(box.get(index)) for index in box.curselection()]
        if is_tag:
            tags = selected
        else:
            parent = self._organizer_node(self.organizer_path[:depth])
            tags = [
                str(tag)
                for name in selected
                for tag in iter_tags(parent.get(name, {}))
                if self._organizer_is_usable_tag(str(tag))
            ]
        self.organizer_basket.update(tags)
        self.organizer_basket_var.set(
            f"Panier : {len(self.organizer_basket)} tag(s)"
        )

    def _organizer_add_current_level_to_basket(self) -> None:
        if not self.organizer_path:
            return
        node = self._organizer_node()
        if isinstance(node, dict) and node.get("__tag__"):
            value = str(node["__tag__"])
        else:
            label = self.organizer_path[-1]
            value = re.sub(r"\s*:\s*\[#[^]]+]\s*$", "", label).strip(" :")
            value = re.sub(r"\s+", "_", value).casefold()
        if value and self._organizer_is_usable_tag(value) and value not in {
            "from_wiki", "visual_characteristics", "real_world",
            "attire_and_body_accessories",
        }:
            self.organizer_basket.add(value)
            self.organizer_basket_var.set(
                f"Panier : {len(self.organizer_basket)} tag(s)"
            )
            self._organizer_load_definition(value)

    @staticmethod
    def _organizer_is_usable_tag(value: str) -> bool:
        lowered = value.casefold()
        return not lowered.startswith(("tag_group:", "list_of_"))

    def _organizer_send_basket(self) -> None:
        if not self.organizer_basket:
            return
        existing = unique_lines(self.manual_tag_text.get("1.0", END))
        merged = existing + sorted(self.organizer_basket - set(existing), key=str.casefold)
        self.manual_tag_text.delete("1.0", END)
        self.manual_tag_text.insert("1.0", "\n".join(merged) + "\n")
        self.organizer_basket.clear()
        self.organizer_basket_var.set("Panier : 0 tag")
        self.notebook.select(self.tag_list_tab)
        self._refresh_buttons()

    def _organizer_load_definition(self, tag: str) -> None:
        board = self.organizer_board_var.get()
        self.organizer_definition_requested = (board, tag)
        cached = self.tag_organization.get("metadata", {}).get(board, {}).get(tag, {})
        if cached.get("definition"):
            definition = self._organizer_definition_with_relations(
                cached["definition"], cached
            )
            self._show_organizer_definition(tag, definition, cached.get("wiki_url", ""))
            return
        self._show_organizer_definition(tag, "Chargement de la définition…", cached.get("wiki_url", ""))
        threading.Thread(
            target=self._fetch_organizer_definition, args=(board, tag), daemon=True
        ).start()

    @staticmethod
    def _organizer_definition_with_relations(definition: str, metadata: dict) -> str:
        lines = []
        for key, label in (
            ("aliases", "Aliases to this tag"),
            ("implicates", "This tag implicates"),
            ("implicated_by", "Tags implicating this tag"),
        ):
            values = metadata.get(key, [])
            if values:
                lines.append(f"{label}: {', '.join(map(str, values))}")
        return definition + (("\n\n" + "\n".join(lines)) if lines else "")

    def _fetch_organizer_definition(self, board: str, tag: str) -> None:
        try:
            definition, url = tag_definition(board, tag)
            if not definition.strip():
                definition = "Aucune définition wiki disponible pour ce tag."
            self.events.put(("organizer_definition", (board, tag, definition, url)))
        except Exception as exc:
            self.events.put(("organizer_definition", (board, tag, f"Aucune définition disponible ({exc}).", "")))

    def _show_organizer_definition(self, tag: str, definition: str, url: str) -> None:
        self.organizer_definition_text.configure(state="normal")
        self.organizer_definition_text.delete("1.0", END)
        self.organizer_definition_text.insert("1.0", f"{tag}\n\n{definition}")
        self.organizer_definition_text.configure(state="disabled")
        self.organizer_source_var.set(url)

    def _start_wiki_update(self) -> None:
        if self.organizer_wiki_update_running:
            return
        self._set_wiki_update_running(True)
        self.organizer_update_var.set(
            "e621 : départ depuis l’index wiki 1671 (tag_group:index)…"
        )
        threading.Thread(target=self._run_wiki_update, daemon=True).start()

    def _set_wiki_update_running(self, running: bool) -> None:
        self.organizer_wiki_update_running = running
        if not hasattr(self, "organizer_update_button"):
            return
        self.organizer_update_button.configure(
            state="disabled" if running else "normal"
        )
        if running:
            self.organizer_update_progress.start(12)
        else:
            self.organizer_update_progress.stop()

    def _run_wiki_update(self) -> None:
        try:
            imported = import_catalogues(
                lambda message: self.events.put(
                    ("organizer_update_progress", message)
                )
            )
            preview = json.loads(json.dumps(self.tag_organization))
            summary = merge_catalogues(preview, imported)
            self.events.put(("organizer_update_preview", (preview, summary)))
        except Exception as exc:
            self.events.put(("organizer_update_error", str(exc)))

    def _apply_wiki_update(self, preview: dict, summary: dict) -> None:
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            if self.tag_organization_path.is_file():
                shutil.copy2(
                    self.tag_organization_path,
                    self.tag_organization_path.with_name(
                        f"tag_organization.backup-{stamp}.json"
                    ),
                )
            for board, database in self.taxonomy_databases.items():
                if database.path.is_file():
                    shutil.copy2(
                        database.path,
                        database.path.with_name(
                            f"tag_organization_{board}.backup-{stamp}.sqlite"
                        ),
                    )
                worker_database = TaxonomyDatabase(database.path, board)
                try:
                    worker_database.sync_from_document(
                        preview.get("boards", {}).get(board, {}),
                        preview.get("metadata", {}).get(board, {}),
                        preview.get("excluded_imported_tags", {}).get(board, []),
                        preview.get("sources", []),
                    )
                    if worker_database.integrity().casefold() != "ok":
                        raise RuntimeError(
                            f"Intégrité SQLite invalide après mise à jour : {board}"
                        )
                finally:
                    worker_database.close()
            temporary = self.tag_organization_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(preview, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.tag_organization_path)
            self.events.put(("organizer_update_applied", (preview, summary)))
        except Exception as exc:
            self.events.put(("organizer_update_apply_error", str(exc)))

    def _option_location(
        self, parent, row: int, column: int, title: str,
                name_variable: StringVar, command,
    ) -> None:
        card = ttk.Frame(parent, padding=(8, 7))
        card.grid(row=row, column=column, sticky="ew", padx=4, pady=4)
        ttk.Label(card, text=title).pack(anchor="w")
        line = ttk.Frame(card)
        line.pack(fill=X, pady=(4, 0))
        ttk.Label(line, textvariable=name_variable).pack(side=LEFT)
        ttk.Button(line, text="Changer…", command=command).pack(side=RIGHT)

    def load_manual_tag_file(self) -> None:
        value = filedialog.askopenfilename(
            title="Charger une liste de tags",
            filetypes=[("Listes texte", "*.txt"), ("Tous les fichiers", "*.*")],
        )
        if not value:
            return
        text = Path(value).read_text(encoding="utf-8-sig", errors="replace")
        self.manual_tag_text.delete("1.0", END)
        self.manual_tag_text.insert("1.0", text)
        self.manual_tag_text.edit_modified(False)
        self._refresh_buttons()

    def _on_manual_tags_modified(self, _event=None) -> None:
        if self.manual_tag_text.edit_modified():
            self.manual_tag_text.edit_modified(False)
            self._refresh_buttons()

    def generate_manual_tag_session(self) -> None:
        tags = unique_lines(self.manual_tag_text.get("1.0", END))
        if not tags:
            messagebox.showwarning("Liste vide", "Colle ou charge au moins un tag.")
            return
        source = Path(self.output_var.get()) / "liste_tags_manuelle.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("\n".join(tags) + "\n", encoding="utf-8")
        self._generate_batches(source)

    def database_log_line(self, text: str) -> None:
        self.database_log.configure(state="normal")
        self.database_log.insert(END, text.rstrip() + "\n")
        self.database_log.see(END)
        self.database_log.configure(state="disabled")

    def update_databases(self) -> None:
        if self.database_process is not None:
            return
        jobs: list[tuple[str, list[str], dict[str, str]]] = []
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        if self.update_gelbooru_var.get():
            target = Path(self.local_gel_db_var.get())
            if target.is_file():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = target.with_name(
                    f"{target.stem}.backup-{stamp}{target.suffix}"
                )
                shutil.copy2(target, backup)
                self.database_log_line(f"Sauvegarde Gelbooru : {backup}")
            user_id, api_key = find_grabber_credentials(self.grabber_dir)
            gel_env = child_env.copy()
            gel_env["GELBOORU_TAG_DB"] = str(target)
            gel_env["GELBOORU_USER_ID"] = user_id
            gel_env["GELBOORU_API_KEY"] = api_key
            bundled_python = Path(
                r"C:\Users\Yami\.cache\codex-runtimes\codex-primary-runtime"
                r"\dependencies\python\python.exe"
            )
            python_exe = str(bundled_python if bundled_python.is_file() else Path(sys.executable))
            jobs.append(
                (
                    "Gelbooru",
                    [python_exe, "-u", str(LEGACY_DIR / "gelbooru_tags_updater.py")],
                    gel_env,
                )
            )
        if self.update_e621_var.get():
            jobs.append(
                (
                    "e621",
                    [
                        sys.executable,
                        "-u",
                        str(LEGACY_DIR / "e621_tags_importer.py"),
                        "--db",
                        self.local_e621_db_var.get(),
                        "--cache-dir",
                        str(DATA_DIR / "imports" / "e621_exports"),
                    ],
                    child_env.copy(),
                )
            )
        if not jobs:
            messagebox.showwarning("Aucun site", "Coche au moins un site à mettre à jour.")
            return
        self.database_update_button.configure(state="disabled")
        self.database_status_var.set("Mise à jour en cours…")
        threading.Thread(
            target=self._run_database_jobs, args=(jobs,), daemon=True
        ).start()

    def _run_database_jobs(
        self, jobs: list[tuple[str, list[str], dict[str, str]]]
    ) -> None:
        final_code = 0
        try:
            for site, command, environment in jobs:
                self.events.put(("db_output", f"=== {site} ===\n"))
                self.database_process = subprocess.Popen(
                    command,
                    cwd=APP_DIR,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                assert self.database_process.stdout is not None
                for line in self.database_process.stdout:
                    self.events.put(("db_output", line))
                final_code = self.database_process.wait()
                if final_code:
                    break
        except Exception as exc:
            final_code = 1
            self.events.put(("db_output", f"Erreur : {exc}\n"))
        finally:
            self.database_process = None
            self.events.put(("db_done", final_code))

    def _spin(self, parent, label, variable, start, end) -> None:
        frame = ttk.Frame(parent)
        frame.pack(side=LEFT, padx=(0, 12))
        ttk.Label(frame, text=label).pack(side=LEFT)
        ttk.Spinbox(
            frame, from_=start, to=end, textvariable=variable, width=7
        ).pack(side=LEFT, padx=(4, 0))

    def _choose_gelbooru_db(self) -> None:
        value = filedialog.askopenfilename(
            title="Choisir la base Gelbooru",
            filetypes=[("Bases SQLite", "*.db *.sqlite"), ("Tous les fichiers", "*.*")],
        )
        if value:
            self.local_gel_db_var.set(value)
            self._refresh_path_labels()

    def _choose_e621_db(self) -> None:
        value = filedialog.askopenfilename(
            title="Choisir la base e621",
            filetypes=[("Bases SQLite", "*.db *.sqlite"), ("Tous les fichiers", "*.*")],
        )
        if value:
            self.local_e621_db_var.set(value)
            self._refresh_path_labels()

    def _choose_output(self) -> None:
        value = filedialog.askdirectory(title="Choisir le dossier de résultats")
        if value:
            self.output_var.set(value)
            self._refresh_path_labels()
            self._refresh_buttons()

    def _choose_grabber(self) -> None:
        value = filedialog.askdirectory(title="Choisir le dossier de Grabber")
        if value:
            self.grabber_var.set(value)
            self._refresh_path_labels()
            self._load_existing_state(silent=True)
            self._refresh_buttons()

    def _refresh_path_labels(self) -> None:
        def short_name(value: str) -> str:
            path = Path(value.strip())
            return path.name or str(path)

        self.gel_db_name_var.set(short_name(self.local_gel_db_var.get()))
        self.e621_db_name_var.set(short_name(self.local_e621_db_var.get()))
        self.output_name_var.set(short_name(self.output_var.get()))
        self.grabber_name_var.set(short_name(self.grabber_var.get()))

    def _update_percentage_explanation(self, *_args) -> None:
        try:
            minimum_catalogue = max(0, int(self.min_posts_var.get()))
            percentage = max(0, int(self.min_percent_var.get()))
        except (TypeError, ValueError):
            return
        example_total = minimum_catalogue or 100
        required = math.ceil(example_total * percentage / 100)
        self.percentage_explanation.set(
            f"Exemple : avec {example_total} résultats au total, {percentage} % "
            f"demande au moins {required} résultat(s) correspondant à la recherche."
        )


    def _toggle_query_count_status(self, *_args) -> None:
        if self.query_count_status.get().strip():
            if not self.query_count_label.winfo_manager():
                self.query_count_label.pack(
                    fill=X,
                    anchor="w",
                    pady=(5, 0),
                    before=self.scan_progress_frame,
                )
        elif self.query_count_label.winfo_manager():
            self.query_count_label.pack_forget()

    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        stamp = datetime.now().strftime("%H:%M:%S")
        lines = text.rstrip().splitlines()
        if not lines:
            self.log_text.insert(END, "\n")
        else:
            for line in lines:
                if line.strip():
                    self.log_text.insert(END, f"[{stamp}] {line}\n")
                else:
                    self.log_text.insert(END, "\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", END)
        self.log_text.configure(state="disabled")

    def _query_signature(self) -> str:
        return self.query_text.get("1.0", END).strip()

    def _scan_signature(self) -> str:
        return "|".join(
            (
                self.entity_type_var.get(),
                str(self.search_gelbooru_var.get()),
                str(self.search_e621_var.get()),
                str(Path(self.local_gel_db_var.get())),
                str(Path(self.local_e621_db_var.get())),
                str(self.min_hits_var.get()),
                str(self.min_posts_var.get()),
                str(self.max_posts_var.get()),
                str(self.min_percent_var.get()),
                self._query_signature(),
            )
        )

    def _on_mode_changed(self, _event=None) -> None:
        if self.entity_type_var.get() == "species":
            self.search_gelbooru_var.set(False)
            self.search_e621_var.set(True)
        self.start_page_var.set(1)
        self.last_successful_query_signature = None
        self._save_settings()
        self._hide_query_autocomplete()
        self.query_count_status.set("")
        self._refresh_buttons()

    def _entity(self):
        return entity_type(self.entity_type_var.get())

    def _candidate_path(self, root: Path) -> Path:
        return root / self._entity().candidate_filename

    def _sites_path(self, root: Path) -> Path:
        return root / self._entity().sites_filename

    def _on_query_text_modified(self, _event=None) -> None:
        if not self.query_text.edit_modified():
            return
        self.query_text.edit_modified(False)
        self.query_count_status.set("")
        signature = self._scan_signature()
        if (
            self.last_successful_query_signature is not None
            and signature != self.last_successful_query_signature
        ):
            self.start_page_var.set(1)
            self.last_successful_query_signature = None
            self.last_successful_start_page = 0
            self.last_successful_page_count = 0
            self.scan_progress_text.set(
                "Contenu modifié — la prochaine recherche repartira de la page 1."
            )
            self._save_settings()

    def _on_autocomplete_site_changed(self) -> None:
        self._hide_query_autocomplete()
        self._schedule_query_autocomplete()
        self.query_count_status.set("")
        self._refresh_buttons()

    def _on_query_autocomplete_key(self, event=None) -> None:
        if event is not None and event.keysym in {
            "Up", "Down", "Return", "Tab", "Escape", "Shift_L", "Shift_R",
            "Control_L", "Control_R", "Alt_L", "Alt_R",
        }:
            return
        self._schedule_query_autocomplete()

    def _schedule_query_autocomplete(self) -> None:
        if self.query_autocomplete_after is not None:
            self.root.after_cancel(self.query_autocomplete_after)
        self.query_autocomplete_after = self.root.after(
            180, self._start_query_autocomplete
        )

    def _current_query_token(self) -> str:
        before_cursor = self.query_text.get("1.0", "insert")
        match = re.search(r"([^\s;]+)$", before_cursor)
        return "" if match is None else match.group(1)

    def _autocomplete_databases(self) -> list[tuple[str, Path]]:
        databases: list[tuple[str, Path]] = []
        if self.search_gelbooru_var.get():
            databases.append(("Gelbooru", Path(self.local_gel_db_var.get())))
        if self.search_e621_var.get():
            databases.append(("e621", Path(self.local_e621_db_var.get())))
        return [(site, path) for site, path in databases if path.is_file()]

    def _start_query_autocomplete(self) -> None:
        self.query_autocomplete_after = None
        raw_token = self._current_query_token()
        token = raw_token.lstrip("-").casefold()
        if len(token) < 3 or ":" in token:
            self._hide_query_autocomplete()
            return
        databases = self._autocomplete_databases()
        if not databases:
            self._hide_query_autocomplete()
            return
        self.query_autocomplete_generation += 1
        generation = self.query_autocomplete_generation
        threading.Thread(
            target=self._query_autocomplete_worker,
            args=(generation, raw_token, token, databases),
            daemon=True,
        ).start()

    def _query_autocomplete_worker(
        self,
        generation: int,
        raw_token: str,
        token: str,
        databases: list[tuple[str, Path]],
    ) -> None:
        merged: dict[str, dict[str, object]] = {}
        for site, path in databases:
            try:
                connection = sqlite3.connect(
                    f"file:{path.resolve().as_posix()}?mode=ro", uri=True
                )
                try:
                    rows = connection.execute(
                        """
                        SELECT name,post_count FROM tags INDEXED BY idx_tags_name
                        WHERE name>=? AND name<?
                        ORDER BY post_count DESC
                        LIMIT 20
                        """,
                        (token, token + "\uffff"),
                    ).fetchall()
                finally:
                    connection.close()
            except (OSError, sqlite3.Error):
                continue
            for name, count in rows:
                name = str(name)
                item = merged.setdefault(
                    name, {"count": 0, "sites": []}
                )
                item["count"] = max(int(item["count"]), int(count))
                item["sites"].append(site)
        ranked = sorted(
            merged.items(),
            key=lambda item: (-int(item[1]["count"]), item[0]),
        )[:12]
        self.events.put(
            ("autocomplete_results", (generation, raw_token, ranked))
        )

    def _show_query_autocomplete(
        self,
        generation: int,
        raw_token: str,
        ranked: list[tuple[str, dict[str, object]]],
    ) -> None:
        if generation != self.query_autocomplete_generation:
            return
        if raw_token != self._current_query_token() or not ranked:
            self._hide_query_autocomplete()
            return
        self.query_autocomplete_values = [name for name, _data in ranked]
        self.query_autocomplete_list.delete(0, END)
        for name, data in ranked:
            count = f"{int(data['count']):,}".replace(",", " ")
            sites = " + ".join(str(site) for site in data["sites"])
            self.query_autocomplete_list.insert(
                END, f"{name} ({count})   ·   {sites}"
            )
        selected_sites = " + ".join(site for site, _path in self._autocomplete_databases())
        self.query_autocomplete_source.set(
            f"Suggestions locales — {selected_sites}"
        )
        if not self.query_autocomplete_frame.winfo_manager():
            self.query_autocomplete_frame.pack(
                fill=X, pady=(0, 6), after=self.query_text
            )

    def _focus_query_autocomplete(self, _event=None):
        if not self.query_autocomplete_frame.winfo_manager():
            return None
        self.query_autocomplete_list.focus_set()
        if self.query_autocomplete_list.size():
            self.query_autocomplete_list.selection_clear(0, END)
            self.query_autocomplete_list.selection_set(0)
            self.query_autocomplete_list.activate(0)
        return "break"

    def _accept_query_autocomplete(self, _event=None):
        if not self.query_autocomplete_frame.winfo_manager():
            return None
        selection = self.query_autocomplete_list.curselection()
        index = int(selection[0]) if selection else 0
        if not 0 <= index < len(self.query_autocomplete_values):
            return "break"
        raw_token = self._current_query_token()
        replacement = self.query_autocomplete_values[index]
        if raw_token.startswith("-"):
            replacement = "-" + replacement
        self.query_text.delete(f"insert-{len(raw_token)}c", "insert")
        self.query_text.insert("insert", replacement)
        self.query_text.focus_set()
        self._hide_query_autocomplete()
        return "break"

    def _return_from_query_autocomplete(self, _event=None):
        self.query_text.focus_set()
        self._hide_query_autocomplete()
        return "break"

    def _hide_query_autocomplete(self, _event=None):
        self.query_autocomplete_generation += 1
        if self.query_autocomplete_after is not None:
            self.root.after_cancel(self.query_autocomplete_after)
            self.query_autocomplete_after = None
        if hasattr(self, "query_autocomplete_frame"):
            self.query_autocomplete_frame.pack_forget()
        return None

    def stop_auto_continue(self) -> None:
        self.auto_continue_cancelled = True
        self.status_var.set("Arrêt automatique demandé après le bloc courant.")

    def start_query_count(self) -> None:
        queries = unique_lines(self.query_text.get("1.0", END))
        if not queries:
            messagebox.showwarning("Recherches manquantes", "Entre au moins un tag.")
            return
        if not self.search_gelbooru_var.get():
            messagebox.showinfo(
                "Comptage Gelbooru",
                "Le comptage exact préalable est disponible lorsque Gelbooru "
                "est coché.",
            )
            return
        user_id, api_key = find_grabber_credentials(self.grabber_dir)
        if not user_id or not api_key:
            messagebox.showerror(
                "Authentification absente",
                "Impossible de compter sans les identifiants Gelbooru du profil Grabber.",
            )
            return
        self.query_count_running = True
        self.query_count_status.set(
            f"Comptage Gelbooru en cours… 0/{len(queries)}"
        )
        self._refresh_buttons()
        threading.Thread(
            target=self._query_count_worker,
            args=(queries, user_id, api_key),
            daemon=True,
        ).start()

    def _query_count_worker(
        self,
        queries: list[str],
        user_id: str,
        api_key: str,
    ) -> None:
        results: list[tuple[str, int]] = []
        errors: list[tuple[str, str]] = []
        for index, query in enumerate(queries, start=1):
            try:
                count, _posts = fetch_result_count(query, user_id, api_key)
                results.append((query, count))
            except Exception as exc:
                errors.append((query, str(exc)))
            self.events.put(
                ("count_progress", (index, len(queries), query))
            )
        self.events.put(("count_done", (results, errors)))

    def stop_scan(self) -> None:
        if not self.scan_running:
            return
        self.auto_continue_cancelled = True
        self.scan_stop_requested = True
        self.status_var.set("Arrêt de la recherche demandé…")
        self.scan_progress_text.set("Arrêt en cours…")
        self.log("Arrêt manuel demandé ; interruption du moteur en cours.")
        process = self.scan_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError as exc:
                self.log(f"Impossible d’arrêter immédiatement le moteur : {exc}")
        self.stop_scan_button.configure(state="disabled")

    def start_scan(self, automatic: bool = False) -> None:
        queries = unique_lines(self.query_text.get("1.0", END))
        gelbooru_db = Path(self.local_gel_db_var.get())
        grabber = self.grabber_dir
        if not queries:
            messagebox.showwarning("Recherches manquantes", "Entre au moins un tag.")
            return
        if not self.search_gelbooru_var.get() and not self.search_e621_var.get():
            messagebox.showwarning("Aucun site", "Coche Gelbooru, e621 ou les deux.")
            return
        if self.entity_type_var.get() == "species" and self.search_gelbooru_var.get():
            messagebox.showerror(
                "Mode incompatible",
                "La catégorie species est propre à e621. Décoche Gelbooru.",
            )
            return
        if self.search_gelbooru_var.get() and (
            not SCANNER.is_file() or not gelbooru_db.is_file()
        ):
            messagebox.showerror(
                "Base Gelbooru absente",
                f"Base Gelbooru introuvable :\n{gelbooru_db}",
            )
            return
        e621_db = Path(self.local_e621_db_var.get())
        if self.search_e621_var.get() and not e621_db.is_file():
            messagebox.showerror("Base e621 absente", f"Base introuvable :\n{e621_db}")
            return
        if not self._valid_grabber():
            return
        if not automatic:
            self.auto_continue_cancelled = False

        signature = self._scan_signature()
        if not automatic:
            if signature == self.last_successful_query_signature:
                self.start_page_var.set(
                    self.last_successful_start_page
                    + self.last_successful_page_count
                )
            elif self.last_successful_query_signature is not None:
                self.start_page_var.set(1)
                self.last_successful_query_signature = None
                self.last_successful_start_page = 0
                self.last_successful_page_count = 0
        scan_start_page = self.start_page_var.get()
        scan_page_count = self.pages_var.get()
        common = [
            "--pages", str(scan_page_count),
            "--page-debut", str(scan_start_page),
            "--min-artist-posts", str(self.min_posts_var.get()),
            "--max-artist-posts", str(self.max_posts_var.get()),
            "--min-match-percent", str(self.min_percent_var.get()),
            "--cache-days", str(self.cache_days_var.get()),
            "--blacklist", str(grabber / "blacklist.txt"),
            "--ignore", str(grabber / "ignore.txt"),
            "--autoriser-requetes-ignorees",
            "--entity-type", self.entity_type_var.get(),
        ]
        commands: list[tuple[str, list[str], Path]] = []
        output_root = Path(self.output_var.get())
        if self.search_gelbooru_var.get():
            gel_output = output_root / self.entity_type_var.get() / "gelbooru"
            command = [
                sys.executable, "-u", str(SCANNER), str(gelbooru_db), *queries,
                *common, "--min-hits", "1", "--sortie", str(gel_output),
            ]
            if self.remember_queries_var.get():
                command.append("--memoriser-requetes")
            user_id, api_key = find_grabber_credentials(grabber)
            if user_id:
                command.extend(["--user-id", user_id])
            if api_key:
                command.extend(["--api-key", api_key])
            commands.append(("Gelbooru", command, gel_output))
        if self.search_e621_var.get():
            e621_output = output_root / self.entity_type_var.get() / "e621"
            command = [
                sys.executable, "-u", str(LEGACY_DIR / "e621_artistes_par_tags.py"),
                str(e621_db), *queries, *common, "--sortie", str(e621_output),
            ]
            commands.append(("e621", command, e621_output))

        self.status_var.set("Recherche en cours…")
        self.scan_running = True
        self.scan_stop_requested = False
        self.scan_query_total = len(queries)
        self.scan_query_current = 0
        self.scan_pages_total = scan_page_count
        self.scan_total_pages = 0
        self.scan_fetched_pages = False
        self.scan_reported_next_page = None
        self.e621_reached_end = False
        self.active_scan_signature = signature
        self.active_scan_start_page = scan_start_page
        self.scan_progress_value.set(0)
        self.scan_progress_text.set(
            f"Démarrage — 0/{self.scan_query_total} recherche(s)."
        )
        self.log("=== Nouvelle recherche ===")
        self.log(
            f"Bloc demandé : pages {scan_start_page} à "
            f"{scan_start_page + scan_page_count - 1}."
        )
        self.scan_button.configure(state="disabled")
        self.stop_scan_button.configure(state="normal")
        threading.Thread(
            target=self._run_scan, args=(commands, output_root), daemon=True
        ).start()

    def _run_scan(
        self,
        commands: list[tuple[str, list[str], Path]],
        output_root: Path,
    ) -> None:
        try:
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"
            code = 0
            candidate_paths: list[Path] = []
            source_entries: list[tuple[str, str]] = []
            for site, command, site_output in commands:
                if self.scan_stop_requested:
                    code = 130
                    break
                self.events.put(("scan_output", f"\n=== Moteur {site} ===\n"))
                self.scan_process = subprocess.Popen(
                    command, cwd=APP_DIR, env=child_env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if self.scan_stop_requested and self.scan_process.poll() is None:
                    self.scan_process.terminate()
                assert self.scan_process.stdout is not None
                for line in self.scan_process.stdout:
                    self.events.put(("scan_output", line))
                code = self.scan_process.wait()
                if code:
                    break
                candidate_path = self._candidate_path(site_output)
                candidate_paths.append(candidate_path)
                if candidate_path.is_file():
                    source_entries.extend(
                        (site.casefold(), artist)
                        for artist in unique_lines(
                            candidate_path.read_text(
                                encoding="utf-8-sig", errors="replace"
                            )
                        )
                    )
            if code == 0:
                merged: list[str] = []
                seen: set[str] = set()
                for path in candidate_paths:
                    if not path.is_file():
                        continue
                    for artist in unique_lines(
                        path.read_text(encoding="utf-8-sig", errors="replace")
                    ):
                        if artist not in seen:
                            seen.add(artist)
                            merged.append(artist)
                output_root.mkdir(parents=True, exist_ok=True)
                mode_root = output_root / self.entity_type_var.get()
                mode_root.mkdir(parents=True, exist_ok=True)
                self._candidate_path(mode_root).write_text(
                    "\n".join(merged) + ("\n" if merged else ""), encoding="utf-8"
                )
                self._sites_path(mode_root).write_text(
                    "\n".join(f"{site}\t{artist}" for site, artist in source_entries)
                    + ("\n" if source_entries else ""),
                    encoding="utf-8",
                )
            self.events.put(("scan_done", code))
        except Exception as exc:
            self.events.put(("error", f"Impossible de lancer la recherche : {exc}"))
        finally:
            self.scan_process = None

    def import_tag_list(self) -> None:
        value = filedialog.askopenfilename(
            title="Choisir une liste de tags",
            filetypes=[("Listes texte", "*.txt"), ("Tous les fichiers", "*.*")],
        )
        if value:
            self._generate_batches(Path(value))

    def generate_from_results(self) -> None:
        self._generate_batches(
            self._candidate_path(
                Path(self.output_var.get()) / self.entity_type_var.get()
            )
        )

    def _generate_batches(self, source: Path) -> None:
        if not source.is_file():
            messagebox.showerror("Liste introuvable", f"Fichier absent :\n{source}")
            return
        if not self._valid_grabber():
            return
        try:
            size = int(self.batch_size_var.get())
            images_per_tab = int(self.images_per_tab_var.get())
        except (TypeError, ValueError):
            size = 0
            images_per_tab = 0
        if size < 1:
            messagebox.showerror("Taille invalide", "La taille d’un lot doit être positive.")
            return
        if images_per_tab < 1:
            messagebox.showerror(
                "Taille invalide",
                "Le nombre d’images par onglet doit être positif.",
            )
            return

        tags = unique_lines(source.read_text(encoding="utf-8-sig", errors="replace"))
        entries: list[tuple[str, str]] = [("gelbooru", tag) for tag in tags]
        site_map = source.with_name(self._entity().sites_filename)
        if source.name == self._entity().candidate_filename and site_map.is_file():
            entries = []
            seen_entries: set[tuple[str, str]] = set()
            for line in site_map.read_text(
                encoding="utf-8-sig", errors="replace"
            ).splitlines():
                if "\t" not in line:
                    continue
                site, tag = line.split("\t", 1)
                entry = (site.strip().casefold(), tag.strip())
                if entry[1] and entry not in seen_entries:
                    seen_entries.add(entry)
                    entries.append(entry)
        blacklist = read_nonempty_lines(self.grabber_dir / "blacklist.txt")
        ignored = read_nonempty_lines(self.grabber_dir / "ignore.txt")
        filtered = [
            entry
            for entry in entries
            if entry[1] not in blacklist and entry[1] not in ignored
        ]
        skipped = len(entries) - len(filtered)
        if not filtered:
            messagebox.showinfo(
                "Aucun onglet",
                "Tous les tags de cette liste sont déjà blacklistés ou ignorés.",
            )
            return

        existing = self._active_or_pending_session()
        if existing and not messagebox.askyesno(
            "Session déjà présente",
            "Une session de lots semble encore active.\n\n"
            "Créer une nouvelle session sans supprimer l’ancienne ?",
        ):
            return

        user_id, api_key = find_grabber_credentials(self.grabber_dir)
        needs_gelbooru = any(site != "e621" for site, _tag in filtered)
        if needs_gelbooru and (not user_id or not api_key):
            messagebox.showerror(
                "Authentification introuvable",
                "Aucun ancien onglet contenant user_id et api_key n’a été trouvé.\n\n"
                "Lance Grabber manuellement, ouvre un onglet Gelbooru, ferme-le, "
                "puis recommence la création des lots.",
            )
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_dir = self.grabber_dir / SESSION_DIR_NAME / stamp
        session_dir.mkdir(parents=True, exist_ok=False)
        files: list[str] = []
        for index in range(0, len(filtered), size):
            chunk = filtered[index : index + size]
            data = {
                "current": 0,
                "tabs": [
                    build_tab(
                        tag,
                        user_id,
                        api_key,
                        site=site,
                        images_per_tab=images_per_tab,
                        prefix=self.tab_prefix_var.get(),
                        suffix=self.tab_suffix_var.get(),
                    )
                    for site, tag in chunk
                ],
                "version": 2,
            }
            path = session_dir / f"tabs_{index // size + 1:04d}.json"
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=4),
                encoding="utf-8",
            )
            files.append(str(path))

        (session_dir / "tags_source.txt").write_text(
            "\n".join(f"{site}\t{tag}" for site, tag in filtered) + "\n",
            encoding="utf-8",
        )
        self.session = {
            "version": 1,
            "created": datetime.now().isoformat(timespec="seconds"),
            "source": str(source),
            "session_dir": str(session_dir),
            "files": files,
            "current": 0,
            "completed": [],
            "total_tags": len(filtered),
            "tabs_per_batch": size,
            "images_per_tab": images_per_tab,
            "tab_prefix": self.tab_prefix_var.get(),
            "tab_suffix": self.tab_suffix_var.get(),
        }
        self._save_state()
        self._activate_current_batch()
        self.log(
            f"Session créée : {len(filtered)} tags, {len(files)} lots, "
            f"{size} onglets maximum par lot, {images_per_tab} images par onglet"
            f" ({skipped} déjà blacklistés/ignorés ont été retirés)."
        )
        self.status_var.set("Lots créés. Le premier lot est prêt.")
        self._refresh_progress()
        self._refresh_buttons()

    def _valid_grabber(self) -> bool:
        if not (self.grabber_dir / "Grabber.exe").is_file():
            messagebox.showerror(
                "Grabber introuvable",
                f"Grabber.exe est absent de :\n{self.grabber_dir}",
            )
            return False
        return True

    def _active_or_pending_session(self) -> bool:
        if self.state_path.is_file():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                return state.get("current", 0) < len(state.get("files", []))
            except (OSError, ValueError):
                return True
        return bool(self._legacy_batch_files())

    def _legacy_batch_files(self) -> list[Path]:
        active = self.grabber_dir / "tabs.json"
        numbered = sorted(self.grabber_dir.glob("tabs_[0-9][0-9][0-9][0-9].json"))
        return ([active] if active.is_file() else []) + numbered

    def _import_legacy_session(self) -> bool:
        legacy = self._legacy_batch_files()
        if not legacy:
            return False
        if not messagebox.askyesno(
            "File existante détectée",
            f"{len(legacy)} lot(s) existant(s) ont été détectés dans Grabber.\n\n"
            "Les importer dans le pilote sans supprimer les originaux ?",
        ):
            return False
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_dir = self.grabber_dir / SESSION_DIR_NAME / f"{stamp}-importe"
        session_dir.mkdir(parents=True, exist_ok=False)
        files: list[str] = []
        total_tags = 0
        for index, source in enumerate(legacy, start=1):
            destination = session_dir / f"tabs_{index:04d}.json"
            shutil.copy2(source, destination)
            files.append(str(destination))
            try:
                data = json.loads(source.read_text(encoding="utf-8-sig"))
                total_tags += len(data.get("tabs", []))
            except (OSError, ValueError, TypeError):
                pass
        self.session = {
            "version": 1,
            "created": datetime.now().isoformat(timespec="seconds"),
            "source": "file existante de Grabber",
            "session_dir": str(session_dir),
            "files": files,
            "current": 0,
            "completed": [],
            "total_tags": total_tags,
        }
        self._save_state()
        self.log(
            f"File existante importée : {len(files)} lots, {total_tags} onglets. "
            "Les fichiers originaux ont été conservés."
        )
        self.status_var.set("File existante importée. Le lot courant est prêt.")
        self._refresh_progress()
        self._refresh_buttons()
        return True

    def _save_state(self) -> None:
        assert self.session is not None
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.session, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.state_path)

    def _activate_current_batch(self) -> None:
        if not self.session:
            return
        current = int(self.session["current"])
        files = self.session["files"]
        if current >= len(files):
            return
        source = Path(files[current])
        destination = self.grabber_dir / "tabs.json"
        backup_dir = Path(self.session["session_dir"]) / "active_backups"
        backup_dir.mkdir(exist_ok=True)
        if destination.is_file():
            backup = backup_dir / (
                datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-tabs.json"
            )
            shutil.copy2(destination, backup)
        temporary = destination.with_suffix(".json.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)

    def load_existing_state(self) -> None:
        self._load_existing_state(silent=False)

    def _load_existing_state(self, silent: bool) -> None:
        path = self.state_path
        if not path.is_file():
            if not silent and self._import_legacy_session():
                return
            if not silent:
                messagebox.showinfo("Aucune session", "Aucune session pilotée trouvée.")
            self.session = None
            self._refresh_progress()
            self._refresh_buttons()
            return
        try:
            self.session = json.loads(path.read_text(encoding="utf-8"))
            self._refresh_progress()
            self._refresh_buttons()
            if not silent:
                self.log(f"Session reprise : {self.session.get('session_dir', '')}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Session illisible", str(exc))

    def launch_grabber(self) -> None:
        if self.grabber_process is not None:
            return
        if not self.session:
            messagebox.showwarning("Aucune session", "Crée ou reprends d’abord une session.")
            return
        current = int(self.session["current"])
        if current >= len(self.session["files"]):
            messagebox.showinfo("Terminé", "Tous les lots ont été traités.")
            return
        user_id, api_key = find_grabber_credentials(self.grabber_dir)
        if not user_id or not api_key:
            messagebox.showerror(
                "Authentification introuvable",
                "Le pilote refuse de lancer un lot sans user_id et api_key.",
            )
            return
        repaired = 0
        for filename in self.session["files"][current:]:
            try:
                repaired += repair_tabs_auth(Path(filename), user_id, api_key)
            except (OSError, ValueError, TypeError) as exc:
                messagebox.showerror(
                    "Lot illisible",
                    f"Impossible de vérifier l’authentification de :\n{filename}\n\n{exc}",
                )
                return
        if repaired:
            self.log(
                f"Authentification restaurée dans {repaired} onglet(s) "
                "avant le lancement."
            )
        self._activate_current_batch()
        self.before_blacklist = read_nonempty_lines(self.grabber_dir / "blacklist.txt")
        self.before_ignore = read_nonempty_lines(self.grabber_dir / "ignore.txt")
        try:
            self.grabber_process = subprocess.Popen(
                [str(self.grabber_dir / "Grabber.exe")],
                cwd=self.grabber_dir,
            )
        except OSError as exc:
            self.grabber_process = None
            messagebox.showerror("Lancement impossible", str(exc))
            return
        self.status_var.set("Grabber est ouvert. Ferme-le après ton tri.")
        self.log(f"Grabber lancé pour le lot {current + 1}.")
        self._refresh_buttons()
        threading.Thread(target=self._wait_for_grabber, daemon=True).start()

    def _wait_for_grabber(self) -> None:
        assert self.grabber_process is not None
        code = self.grabber_process.wait()
        self.grabber_process = None
        self.events.put(("grabber_done", code))

    def _after_grabber_closed(self, code: int) -> None:
        if not self.session:
            return
        after_blacklist = read_nonempty_lines(self.grabber_dir / "blacklist.txt")
        after_ignore = read_nonempty_lines(self.grabber_dir / "ignore.txt")
        added_blacklist = len(after_blacklist - self.before_blacklist)
        added_ignore = len(after_ignore - self.before_ignore)
        current = int(self.session["current"])
        if code != 0:
            self.status_var.set(
                "Grabber s’est arrêté anormalement. Le même lot reste prêt."
            )
            self.log(
                f"Arrêt anormal du lot {current + 1} (code {code}) — "
                f"+{added_blacklist} blacklist, +{added_ignore} ignore. "
                "La progression n’a pas été avancée."
            )
            self._refresh_progress()
            self._refresh_buttons()
            return
        active_tabs_path = self.grabber_dir / "tabs.json"
        try:
            active_data = json.loads(
                active_tabs_path.read_text(encoding="utf-8-sig")
            )
            remaining_tabs = remaining_review_tabs(active_data)
        except (OSError, ValueError, TypeError) as exc:
            self.status_var.set(
                "Impossible de vérifier les onglets restants. Aucun lot relancé."
            )
            self.log(
                f"Fermeture du lot {current + 1}, mais tabs.json est illisible : "
                f"{exc}. La progression reste inchangée."
            )
            self._refresh_progress()
            self._refresh_buttons()
            return
        if remaining_tabs:
            # Conserve le lot partiellement traité afin qu'une reprise ne restaure
            # pas les onglets déjà fermés par l'utilisateur.
            active_data["tabs"] = remaining_tabs
            active_data["current"] = min(
                int(active_data.get("current", 0)), len(remaining_tabs) - 1
            )
            partial_path = Path(self.session["files"][current])
            temporary = partial_path.with_suffix(".partial.tmp")
            temporary.write_text(
                json.dumps(active_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, partial_path)
            self.status_var.set(
                f"Pause demandée : {len(remaining_tabs)} onglet(s) restent ouverts."
            )
            self.log(
                f"Lot {current + 1} mis en pause avec {len(remaining_tabs)} "
                "onglet(s) restant(s). Aucun lot suivant n’a été lancé."
            )
            self._refresh_progress()
            self._refresh_buttons()
            return
        completed = self.session.setdefault("completed", [])
        if current not in completed:
            completed.append(current)
        self.session["current"] = current + 1
        self._save_state()
        if self.session["current"] < len(self.session["files"]):
            self._activate_current_batch()
            self.status_var.set("Lot terminé. Lancement automatique du suivant…")
        else:
            self.status_var.set("Session terminée.")
        self.log(
            f"Lot {current + 1} terminé — +{added_blacklist} blacklist, "
            f"+{added_ignore} ignore (code de sortie {code})."
        )
        self._refresh_progress()
        self._refresh_buttons()
        if self.session["current"] < len(self.session["files"]):
            self.root.after(750, self.launch_grabber)

    def previous_batch(self) -> None:
        if not self.session or self.grabber_process is not None:
            return
        current = int(self.session["current"])
        if current <= 0:
            return
        self.session["current"] = current - 1
        self._save_state()
        self._activate_current_batch()
        self.log(f"Retour au lot {current}.")
        self.status_var.set("Lot précédent restauré.")
        self._refresh_progress()
        self._refresh_buttons()

    def _refresh_progress(self) -> None:
        if not self.session:
            self.progress_var.set("Aucune session chargée.")
            return
        total = len(self.session.get("files", []))
        current = int(self.session.get("current", 0))
        if current >= total:
            self.progress_var.set(
                f"Session terminée — {total}/{total} lots, "
                f"{self.session.get('total_tags', 0)} tags."
            )
        else:
            configuration = ""
            if self.session.get("tabs_per_batch") and self.session.get(
                "images_per_tab"
            ):
                configuration = (
                    f" — {self.session['tabs_per_batch']} onglets/lot, "
                    f"{self.session['images_per_tab']} images/onglet"
                )
            self.progress_var.set(
                f"Lot prêt : {current + 1}/{total} — "
                f"{self.session.get('total_tags', 0)} tags dans la session"
                f"{configuration}."
            )

    def _refresh_buttons(self) -> None:
        scanning = self.scan_running
        running = self.grabber_process is not None
        self.scan_button.configure(
            state="disabled"
            if scanning or self.query_count_running
            else "normal"
        )
        self.count_button.configure(
            state="normal"
            if (
                not scanning
                and not self.query_count_running
                and self.search_gelbooru_var.get()
            )
            else "disabled"
        )
        self.stop_scan_button.configure(state="normal" if scanning else "disabled")
        results_source = self._candidate_path(
            Path(self.output_var.get()) / self.entity_type_var.get()
        )
        can_generate_results = self._source_has_available_tags(results_source)
        self.generate_button.configure(
            state="normal" if can_generate_results and not running else "disabled"
        )
        self.import_button.configure(state="disabled" if running else "normal")
        manual_tags = unique_lines(self.manual_tag_text.get("1.0", END))
        unavailable = (
            read_nonempty_lines(self.grabber_dir / "blacklist.txt")
            | read_nonempty_lines(self.grabber_dir / "ignore.txt")
        )
        self.manual_generate_button.configure(
            state="normal"
            if manual_tags and any(tag not in unavailable for tag in manual_tags) and not running
            else "disabled"
        )
        can_launch = (
            self.session is not None
            and int(self.session.get("current", 0))
            < len(self.session.get("files", []))
            and not running
        )
        self.launch_button.configure(state="normal" if can_launch else "disabled")
        can_previous = (
            self.session is not None
            and int(self.session.get("current", 0)) > 0
            and not running
        )
        self.previous_button.configure(
            state="normal" if can_previous else "disabled"
        )

    def _source_has_available_tags(self, source: Path) -> bool:
        if not source.is_file():
            return False
        tags = unique_lines(source.read_text(encoding="utf-8-sig", errors="replace"))
        unavailable = (
            read_nonempty_lines(self.grabber_dir / "blacklist.txt")
            | read_nonempty_lines(self.grabber_dir / "ignore.txt")
        )
        return any(tag not in unavailable for tag in tags)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log(str(payload))
                elif kind == "scan_output":
                    self._handle_scan_output(str(payload))
                elif kind == "autocomplete_results":
                    generation, raw_token, ranked = payload
                    self._show_query_autocomplete(
                        int(generation), str(raw_token), list(ranked)
                    )
                elif kind == "count_progress":
                    current, total, query = payload
                    self.query_count_status.set(
                        f"Comptage Gelbooru… {int(current)}/{int(total)} — {query}"
                    )
                elif kind == "count_done":
                    results, errors = payload
                    self.query_count_running = False
                    for query, count in results:
                        formatted = f"{int(count):,}".replace(",", " ")
                        self.log(f"Comptage : {query} → {formatted} résultat(s).")
                    if len(results) == 1 and not errors:
                        query, count = results[0]
                        formatted = f"{int(count):,}".replace(",", " ")
                        self.query_count_status.set(
                            f"{query} → {formatted} résultat(s) Gelbooru."
                        )
                    elif results:
                        raw_total = sum(int(count) for _query, count in results)
                        formatted = f"{raw_total:,}".replace(",", " ")
                        self.query_count_status.set(
                            f"{len(results)} requête(s) comptée(s) — "
                            f"total brut {formatted}, sans dédoublonnage."
                        )
                    if errors:
                        for query, error in errors:
                            self.log(f"Comptage impossible pour {query} : {error}")
                        if not results:
                            self.query_count_status.set(
                                "Comptage Gelbooru impossible ; consulte le journal."
                            )
                    self._refresh_buttons()
                elif kind == "tagging_progress":
                    generation, current, total, gelbooru_page, examined, retained = payload
                    if int(generation) == self.tagging_generation:
                        self.tagging_progress_var.set(
                            round(int(current) * 100 / max(1, int(total)))
                        )
                        self.tagging_current_page_var.set(
                            f"Page actuelle : {int(gelbooru_page)}"
                        )
                        self.tagging_status_var.set(
                            f"Bloc : {int(current)}/{int(total)} — "
                            f"{int(examined)} post(s) examiné(s), "
                            f"{int(retained)} retenu(s)."
                        )
                elif kind == "tagging_continue":
                    generation, block_start, pages, examined = payload
                    if int(generation) == self.tagging_generation:
                        self.tagging_progress_var.set(0)
                        self.tagging_current_page_var.set(
                            f"Page actuelle : {int(block_start)}"
                        )
                        self.tagging_status_var.set(
                            f"Aucun résultat après {int(examined)} post(s) ; "
                            f"poursuite automatique pages {int(block_start)}–"
                            f"{int(block_start) + int(pages) - 1}..."
                        )
                elif kind == "tagging_done":
                    generation, posts, examined, stopped, reached_end, next_page, error = payload
                    if int(generation) == self.tagging_generation:
                        self.tagging_running = False
                        self.tagging_start_button.configure(state="normal")
                        self.tagging_stop_button.configure(state="disabled")
                        self._show_tagging_results(int(generation), list(posts))
                        self.tagging_start_page_var.set(max(1, int(next_page)))
                        self._save_settings()
                        if error:
                            self.tagging_status_var.set(
                                f"Recherche interrompue après {int(examined)} post(s) : {error}"
                            )
                        elif stopped:
                            self.tagging_status_var.set(
                                f"Arrêté — {int(examined)} post(s) examiné(s), "
                                f"{len(posts)} retenu(s)."
                            )
                        elif reached_end and not posts:
                            self.tagging_progress_var.set(100)
                            self.tagging_status_var.set(
                                f"Terminé — fin des résultats atteinte après "
                                f"{int(examined)} post(s), aucun post retenu."
                            )
                        else:
                            self.tagging_progress_var.set(100)
                            self.tagging_status_var.set(
                                f"Terminé — {int(examined)} post(s) examiné(s), "
                                f"{len(posts)} retenu(s). Clique une image pour l'ouvrir."
                            )
                elif kind == "tagging_thumbnail":
                    generation, post_id, picture, count = payload
                    if (
                        int(generation) == self.tagging_generation
                        and int(post_id) in self.tagging_cards
                        and ImageTk is not None
                    ):
                        photo = ImageTk.PhotoImage(picture)
                        self.tagging_images[int(post_id)] = photo
                        self.tagging_cards[int(post_id)].configure(
                            image=photo,
                            text=f"#{int(post_id)} · {int(count)} tags",
                            compound="top",
                        )
                elif kind == "scan_done":
                    code = int(payload)
                    self.scan_running = False
                    if code == 0:
                        current_signature = self._scan_signature()
                        next_page: int | None = None
                        if current_signature == self.active_scan_signature:
                            self.last_successful_query_signature = (
                                self.active_scan_signature
                            )
                            self.last_successful_start_page = (
                                self.active_scan_start_page
                            )
                            self.last_successful_page_count = self.scan_pages_total
                            next_page = self.scan_reported_next_page
                            if next_page is None:
                                next_page = (
                                    self.active_scan_start_page
                                    + self.scan_pages_total
                                    if self.scan_fetched_pages
                                    else self.active_scan_start_page
                                )
                            if (
                                self.search_gelbooru_var.get()
                                and not self.search_e621_var.get()
                                and self.scan_total_pages
                                and next_page > self.scan_total_pages
                            ):
                                next_page = None
                            if next_page is not None:
                                self.start_page_var.set(next_page)
                        self.scan_progress_value.set(100)
                        if next_page is not None:
                            self.scan_progress_text.set(
                                f"Terminé — prochain bloc à partir de la page "
                                f"{next_page}."
                            )
                            self.log(
                                f"Prochaine relance inchangée : départ automatique "
                                f"page {next_page}."
                            )
                            self._save_settings()
                        else:
                            if (
                                self.scan_total_pages
                                and self.active_scan_start_page > self.scan_total_pages
                            ):
                                self.scan_progress_text.set(
                                    "Terminé — aucune page supplémentaire disponible."
                                )
                            else:
                                self.scan_progress_text.set(
                                    "Terminé — toutes les pages disponibles ont été parcourues."
                                )
                            self.log(
                                "Arrêt : toutes les pages disponibles ont été parcourues ; "
                                "aucune relance supplémentaire n’est proposée."
                            )
                        self.log("Recherche terminée avec succès.")
                        candidates_path = (
                            Path(self.output_var.get())
                            / self.entity_type_var.get()
                            / self._entity().candidate_filename
                        )
                        has_candidates = bool(
                            candidates_path.is_file()
                            and candidates_path.read_text(
                                encoding="utf-8-sig", errors="replace"
                            ).strip()
                        )
                        if has_candidates:
                            self.status_var.set(
                                "Recherche terminée. Tu peux maintenant créer "
                                "les lots."
                            )
                        else:
                            self.status_var.set(
                                "Recherche terminée : aucune entrée retenue. "
                                "Le bouton des lots reste désactivé."
                            )
                            self.log(
                                "Aucun résultat disponible pour les lots ; "
                                "vérifie la recherche et les critères actifs."
                            )
                        if (
                            not has_candidates
                            and self.auto_continue_var.get()
                            and not self.auto_continue_cancelled
                            and next_page is not None
                            and (
                                (
                                    self.search_gelbooru_var.get()
                                    and next_page <= self.scan_total_pages
                                )
                                or (
                                    self.search_e621_var.get()
                                    and not self.e621_reached_end
                                )
                            )
                        ):
                            self.status_var.set(
                                f"Aucune entrée retenue. Poursuite automatique "
                                f"à la page {next_page}…"
                            )
                            self.log(
                                f"Aucune entrée ne satisfait les critères ; "
                                f"poursuite automatique page {next_page}."
                            )
                            self.root.after(
                                700, lambda: self.start_scan(automatic=True)
                            )
                        elif (
                            not has_candidates
                            and self.scan_total_pages
                            and (
                                next_page is None
                                or next_page > self.scan_total_pages
                            )
                        ):
                            self.status_var.set(
                                "Fin réelle des résultats Gelbooru atteinte."
                            )
                            self.log(
                                "Arrêt : toutes les pages disponibles ont été "
                                "parcourues."
                            )
                    else:
                        self.scan_progress_text.set(
                            f"Recherche interrompue (code {code})."
                        )
                        self.status_var.set(f"La recherche a échoué (code {code}).")
                    self._refresh_buttons()
                elif kind == "cleanup_progress":
                    files, matches = payload
                    self.cleanup_status_var.set(
                        f"Analyse en cours — {files} image(s), "
                        f"{matches} correspondance(s)."
                    )
                    self.cleanup_log_line(
                        f"Progression : {files} image(s), "
                        f"{matches} correspondance(s)."
                    )
                elif kind == "cleanup_done":
                    files, matches, report, ignored_compound, ignored_non_tag = payload
                    self.cleanup_running = False
                    self.cleanup_matches = list(matches)
                    self.cleanup_report = Path(report)
                    unique = sorted({match.path for match in self.cleanup_matches})
                    self.cleanup_status_var.set(
                        f"Terminé — {files} image(s), "
                        f"{len(unique)} fichier(s) correspondant(s)."
                    )
                    self.cleanup_log_line(
                        f"Terminé : {len(unique)} fichier(s) correspondent ; "
                        f"{ignored_compound} règle(s) composée(s) et "
                        f"{ignored_non_tag} non-tag(s) ignorés."
                    )
                    for match in self.cleanup_matches[:1000]:
                        self.cleanup_log_line(
                            f"{match.path} ← {match.tag} "
                            f"[{match.mode}, {match.detected_site or 'site inconnu'}]"
                        )
                    if len(self.cleanup_matches) > 1000:
                        self.cleanup_log_line(
                            "Affichage limité à 1000 correspondances ; "
                            "le rapport CSV est complet."
                        )
                    self.cleanup_log_line(f"Rapport CSV : {report}")
                    self.cleanup_scan_button.configure(state="normal")
                    self.cleanup_recycle_button.configure(
                        state="normal" if unique else "disabled"
                    )
                elif kind == "cleanup_error":
                    self.cleanup_running = False
                    self.cleanup_scan_button.configure(state="normal")
                    self.cleanup_recycle_button.configure(state="disabled")
                    self.cleanup_status_var.set("Échec de l’analyse.")
                    self.cleanup_log_line(f"Erreur : {payload}")
                    messagebox.showerror("Analyse impossible", str(payload))
                elif kind == "cleanup_recycle_done":
                    ok, message = payload
                    self.cleanup_log_line(str(message))
                    self.cleanup_scan_button.configure(state="normal")
                    if ok:
                        self.cleanup_matches = []
                        self.cleanup_status_var.set(
                            str(message) + " Journal CSV conservé."
                        )
                        messagebox.showinfo("Nettoyage terminé", str(message))
                    else:
                        self.cleanup_status_var.set(
                            "Envoi incomplet — relance une analyse avant de continuer."
                        )
                        self.cleanup_recycle_button.configure(state="disabled")
                        messagebox.showerror("Nettoyage interrompu", str(message))

                elif kind == "grabber_done":
                    self._after_grabber_closed(int(payload))
                elif kind == "db_output":
                    self.database_log_line(str(payload))
                elif kind == "db_done":
                    code = int(payload)
                    self.database_update_button.configure(state="normal")
                    if code == 0:
                        self.database_status_var.set(
                            "Mise à jour terminée et validée."
                        )
                    else:
                        self.database_status_var.set(
                            f"Mise à jour interrompue (code {code})."
                        )
                elif kind == "organizer_definition":
                    board, tag, definition, url = payload
                    metadata = self.tag_organization.setdefault("metadata", {}).setdefault(str(board), {})
                    metadata.setdefault(str(tag), {}).update(
                        {"definition": str(definition), "wiki_url": str(url)}
                    )
                    self._save_tag_organization()
                    if getattr(self, "organizer_definition_requested", None) == (board, tag):
                        self._show_organizer_definition(str(tag), str(definition), str(url))
                elif kind == "organizer_manual_import":
                    board, path, tree = payload
                    try:
                        self._organizer_apply_import(str(board), list(path), dict(tree))
                    except Exception as exc:
                        self.organizer_update_var.set(f"Échec de l’import manuel : {exc}")
                elif kind == "organizer_update_preview":
                    preview, summary = payload
                    if messagebox.askyesno(
                        "Mise à jour des wikis",
                        f"Import analysé : {summary['total']} tags uniques, "
                        f"{summary['added']} ajout(s), {summary['removed']} retrait(s).\n\n"
                        "Appliquer cette mise à jour ? Les suppressions locales restent exclues.",
                    ):
                        self._organizer_push_undo("mise à jour depuis les wikis")
                        self.organizer_update_var.set(
                            "Application en arrière-plan : sauvegardes, JSON et SQLite…"
                        )
                        threading.Thread(
                            target=self._apply_wiki_update,
                            args=(preview, summary),
                            daemon=True,
                        ).start()
                    else:
                        self._set_wiki_update_running(False)
                        self.organizer_update_var.set("Mise à jour analysée puis annulée.")
                elif kind == "organizer_update_applied":
                    preview, summary = payload
                    self.tag_organization = preview
                    self.organizer_path = []
                    self.organizer_selected = None
                    self.organizer_search_var.set("")
                    self._organizer_render()
                    self._set_wiki_update_running(False)
                    self.organizer_update_var.set(
                        f"Wikis à jour depuis l’index e621 1671 : "
                        f"{summary['total']} tags uniques."
                    )
                elif kind == "organizer_update_apply_error":
                    self._set_wiki_update_running(False)
                    self.organizer_update_var.set(
                        f"Échec pendant l’application : {payload}"
                    )
                    messagebox.showerror("Mise à jour non appliquée", str(payload))
                elif kind == "organizer_update_error":
                    self._set_wiki_update_running(False)
                    self.organizer_update_var.set(f"Échec de la mise à jour : {payload}")
                    messagebox.showerror("Mise à jour impossible", str(payload))
                elif kind == "organizer_update_progress":
                    self.organizer_update_var.set(str(payload))
                elif kind == "error":
                    self.scan_running = False
                    self.status_var.set(str(payload))
                    self.log(str(payload))
                    messagebox.showerror("Erreur", str(payload))
                    self._refresh_buttons()
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def _handle_scan_output(self, line: str) -> None:
        total_match = re.search(
            r"Total Gelbooru\s*:.*?,\s*(\d+)\s+page",
            line,
        )
        if total_match:
            previous_total = self.scan_total_pages
            self.scan_total_pages = max(
                self.scan_total_pages, int(total_match.group(1))
            )
            if self.scan_total_pages != previous_total:
                self.log(
                    f"Repère de progression : {self.scan_total_pages} page(s) "
                    "disponible(s) au total."
                )

        journal_line = line.rstrip("\r\n")
        journal_page_match = re.search(
            r"Page Gelbooru\s+(\d+)\s+\((\d+)/(\d+) du bloc\)", line
        )
        if journal_page_match and self.scan_total_pages:
            current_page = int(journal_page_match.group(1))
            journal_line = (
                f"{journal_line} — repère global : page {current_page}/"
                f"{self.scan_total_pages}"
            )
        self.log(journal_line)
        next_page_match = re.search(
            r"Progression cumulative enregistr.e\s*:\s*prochain d.part page\s+(\d+)",
            line,
        )
        if next_page_match:
            self.scan_reported_next_page = int(next_page_match.group(1))
        if "Fin réelle des résultats e621 atteinte" in line:
            self.e621_reached_end = True
        query_match = re.match(r"^===\s+(.+?)\s+===", line.strip())
        if query_match and not query_match.group(1).startswith("Moteur "):
            self.scan_query_current = min(
                self.scan_query_current + 1, self.scan_query_total
            )
            completed_before = max(0, self.scan_query_current - 1)
            percent = (
                completed_before * 100 / self.scan_query_total
                if self.scan_query_total
                else 0
            )
            self.scan_progress_value.set(round(percent))
            self.scan_progress_text.set(
                f"Recherche {self.scan_query_current}/{self.scan_query_total} — "
                f"{query_match.group(1)}"
            )
            return

        counter_match = re.search(
            r"Compteurs (?:artiste|entree|totaux|correspondants)\s+(\d+)/(\d+)",
            line,
        )
        if counter_match:
            current = int(counter_match.group(1))
            total = max(1, int(counter_match.group(2)))
            self.scan_progress_text.set(
                f"Vérification des catalogues Gelbooru — {current}/{total}"
            )
            return

        cache_match = re.search(
            r"(?:Cache compteurs|Nouvelles réponses mises en cache — compteurs) "
            r"(totaux|correspondants)\s+(\d+)/(\d+)",
            line,
        )
        if cache_match:
            current = int(cache_match.group(2))
            total = max(1, int(cache_match.group(3)))
            self.scan_progress_text.set(
                f"Enregistrement local des compteurs {cache_match.group(1)} "
                f"— {current}/{total}"
            )
            return

        phase_messages = {
            "Lecture locale des": "Lecture du cache local…",
            "Filtrage sur le total": "Filtrage des compteurs totaux…",
            "Application finale des critères": "Application finale des critères…",
        }
        for marker, message in phase_messages.items():
            if marker in line:
                self.scan_progress_text.set(message)
                return

        page_match = re.search(
            r"Page Gelbooru\s+(\d+)\s+\((\d+)/(\d+) du bloc\)", line
        )
        if not page_match:
            page_match = re.search(
                r"Page e621\s+(\d+)\s+\((\d+)/(\d+) du bloc\)", line
            )
        if page_match and self.scan_query_total:
            self.scan_fetched_pages = True
            gelbooru_page = int(page_match.group(1))
            block_page = int(page_match.group(2))
            pages = max(1, int(page_match.group(3)))
            completed = max(0, self.scan_query_current - 1) + block_page / pages
            percent = min(99, completed * 100 / self.scan_query_total)
            self.scan_progress_value.set(round(percent))
            self.scan_progress_text.set(
                f"Recherche {self.scan_query_current}/{self.scan_query_total} — "
                f"page {gelbooru_page} "
                f"({block_page}/{pages} du bloc)"
                + (
                    f" — page {gelbooru_page}/{self.scan_total_pages} disponible(s)"
                    if self.scan_total_pages
                    else ""
                )
            )


def main() -> int:
    root = Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
