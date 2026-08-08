"""Analyse conservative d'images déjà téléchargées avec une blacklist Grabber."""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import ctypes
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".jxl",
}
SITE_ALIASES = {
    "e621.net": "e621",
    "e621": "e621",
    "gelbooru.com": "gelbooru",
    "gelbooru": "gelbooru",
}
GRABBER_FILENAME = re.compile(
    r"^(?P<artists>.+?)\s+-\s+(?P<id>\d+)\s+-\s+"
    r"(?P<rating>[^-]+?)\s+-\s+(?P<md5>[0-9a-fA-F]{32})$"
)


@dataclass(frozen=True)
class BlacklistRule:
    tag: str
    site: str | None
    source_line: str


@dataclass(frozen=True)
class Match:
    path: Path
    mode: str
    tag: str
    detected_site: str | None
    rule: BlacklistRule


@dataclass(frozen=True)
class ParsedBlacklist:
    rules: tuple[BlacklistRule, ...]
    ignored_compound: int
    ignored_non_tag: int


def decode_html(value: str) -> str:
    while (decoded := html.unescape(value)) != value:
        value = decoded
    return value


def normalize_tag(value: str) -> str:
    return decode_html(value.strip()).casefold()


def parse_blacklist(lines: Iterable[str]) -> ParsedBlacklist:
    """Ne conserve que `tag` et `website:site tag`."""
    rules: list[BlacklistRule] = []
    seen: set[tuple[str | None, str]] = set()
    ignored_compound = 0
    ignored_non_tag = 0

    for raw in lines:
        line = decode_html(raw.strip())
        if not line or line.startswith(("#", "//")):
            continue
        parts = line.split()
        first = parts[0].casefold()
        if first.startswith(("width:", "height:")):
            ignored_non_tag += 1
            continue

        site: str | None = None
        if first.startswith("website:"):
            if len(parts) != 2:
                ignored_compound += 1
                continue
            site_name = first.partition(":")[2]
            site = SITE_ALIASES.get(site_name, site_name)
            tag = normalize_tag(parts[1])
        elif len(parts) == 1:
            tag = normalize_tag(parts[0])
        else:
            ignored_compound += 1
            continue

        key = (site, tag)
        if tag and key not in seen:
            seen.add(key)
            rules.append(BlacklistRule(tag, site, line))

    return ParsedBlacklist(tuple(rules), ignored_compound, ignored_non_tag)


def detect_site(path: Path) -> str | None:
    text = str(path).casefold()
    e621 = bool(re.search(r"(?:\(|\b)e621(?:\.net)?(?:\)|\b)", text))
    gelbooru = bool(
        re.search(r"(?:\(|\b)gelbooru(?:\.com)?(?:\)|\b)", text)
    )
    if e621 and not gelbooru:
        return "e621"
    if gelbooru and not e621:
        return "gelbooru"
    return None


def filename_artist_tags(path: Path) -> set[str]:
    """Extrait `%artist%` du modèle Grabber artiste - id - rating - md5."""
    match = GRABBER_FILENAME.match(path.stem)
    if not match:
        return set()
    # Grabber sépare plusieurs artistes par des espaces dans `%artist%`.
    return {
        normalize_tag(tag)
        for tag in match.group("artists").split()
        if normalize_tag(tag)
    }


def path_tags(path: Path) -> set[str]:
    """Extrait les tags exacts présents dans les noms des dossiers parents."""
    tags: set[str] = set()
    for part in path.parts[:-1]:
        decoded = normalize_tag(part)
        if not decoded:
            continue
        tags.add(decoded)
        for token in decoded.split():
            token = token.removeprefix("n_").removeprefix("a_")
            if token:
                tags.add(token)
    return tags


def rule_applies(rule: BlacklistRule, detected_site: str | None) -> bool:
    # Si le chemin ne donne aucun site, le préfixe website est volontairement
    # ignoré et le tag reste applicable.
    return rule.site is None or detected_site is None or rule.site == detected_site


def match_file(
    path: Path,
    parsed: ParsedBlacklist,
    mode: str,
) -> list[Match]:
    if path.suffix.casefold() not in IMAGE_EXTENSIONS:
        return []
    if mode == "all":
        artist_tags = filename_artist_tags(path)
        parent_tags = path_tags(path)
        available = artist_tags | parent_tags
    elif mode == "artists":
        available = filename_artist_tags(path)
    elif mode in {"copyrights", "characters", "species"}:
        available = path_tags(path)
    else:
        raise ValueError(f"Mode inconnu : {mode}")

    site = detect_site(path)
    matches: list[Match] = []
    for rule in parsed.rules:
        if rule.tag not in available or not rule_applies(rule, site):
            continue
        source = mode
        if mode == "all":
            source = "filename" if rule.tag in artist_tags else "path"
        matches.append(Match(path, source, rule.tag, site, rule))
    return matches


def scan_paths(
    roots: Iterable[Path],
    parsed: ParsedBlacklist,
    mode: str,
) -> list[Match]:
    matches: list[Match] = []
    for root in roots:
        files = [root] if root.is_file() else root.rglob("*")
        for path in files:
            if path.is_file():
                matches.extend(match_file(path, parsed, mode))
    return matches


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit en lecture seule des images correspondant à une blacklist "
            "Grabber. Aucun fichier n'est supprimé ou déplacé."
        )
    )
    parser.add_argument(
        "dossiers",
        nargs="+",
        type=Path,
        help="Un ou plusieurs dossiers ou fichiers à analyser.",
    )
    parser.add_argument(
        "--blacklist",
        type=Path,
        default=Path(r"D:\0ZGrabber_blacklist\blacklist.txt"),
        help="Blacklist Grabber à utiliser.",
    )
    parser.add_argument(
        "--mode",
        choices=("all", "artists", "copyrights", "characters", "species"),
        default="all",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--report",
        type=Path,
        help=(
            "Chemin du rapport CSV. Par défaut : "
            "results/audit-retroactif-AAAAMMJJ-HHMMSS.csv"
        ),
    )
    return parser.parse_args()


def iter_image_files(roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for root in roots:
        if root.is_file():
            candidates: Iterable[Path] = (root,)
        elif root.is_dir():
            candidates = root.rglob("*")
        else:
            print(f"Dossier introuvable, ignoré : {root}", file=sys.stderr)
            continue
        for path in candidates:
            if (
                path.is_file()
                and path.suffix.casefold() in IMAGE_EXTENSIONS
                and path not in seen
            ):
                seen.add(path)
                yield path


def write_report(path: Path, matches: Iterable[Match]) -> int:
    rows = list(matches)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(
            ("fichier", "mode", "site_detecte", "tag", "regle_blacklist")
        )
        for match in rows:
            writer.writerow(
                (
                    str(match.path),
                    match.mode,
                    match.detected_site or "inconnu",
                    match.tag,
                    match.rule.source_line,
                )
            )
    return len(rows)


def main() -> int:
    args = parse_args()
    if not args.blacklist.is_file():
        print(f"Blacklist introuvable : {args.blacklist}", file=sys.stderr)
        return 2

    parsed = parse_blacklist(
        args.blacklist.read_text(
            encoding="utf-8-sig", errors="replace"
        ).splitlines()
    )
    print(
        f"Blacklist : {len(parsed.rules)} règle(s) de tag ; "
        f"{parsed.ignored_compound} composée(s) ignorée(s) ; "
        f"{parsed.ignored_non_tag} non-tag(s) ignoré(s)."
    )
    print("Analyse combinée du nom et du chemin, strictement en lecture seule.")

    matches: list[Match] = []
    file_count = 0
    for file_count, path in enumerate(iter_image_files(args.dossiers), start=1):
        file_matches = match_file(path, parsed, args.mode)
        matches.extend(file_matches)
        if file_count == 1 or file_count % 500 == 0:
            print(
                f"Fichiers analysés : {file_count} ; "
                f"correspondances : {len(matches)}",
                flush=True,
            )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = args.report or Path("results") / f"audit-retroactif-{stamp}.csv"
    write_report(report, matches)
    unique_files = {match.path for match in matches}
    sites = Counter(match.detected_site or "inconnu" for match in matches)
    print(f"Terminé : {file_count} image(s) analysée(s).")
    print(
        f"Résultat : {len(unique_files)} fichier(s), "
        f"{len(matches)} correspondance(s)."
    )
    if matches:
        print("Sites : " + ", ".join(f"{k}={v}" for k, v in sorted(sites.items())))
    print(f"Rapport CSV : {report.resolve()}")
    print("Aucun fichier n'a été supprimé ou déplacé.")
    return 0


def send_to_recycle_bin(paths: Iterable[Path]) -> tuple[bool, str]:
    """Envoie des fichiers à la Corbeille avec l'API Shell moderne."""
    unique = sorted({str(path.resolve()) for path in paths})
    if not unique:
        return True, "Aucun fichier à traiter."
    if sys.platform != "win32":
        return False, "La Corbeille n'est prise en charge que sous Windows."

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_text(cls, value: str) -> "GUID":
            raw = uuid.UUID(value).bytes_le
            return cls.from_buffer_copy(raw)

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = (
        ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    ole32.CoCreateInstance.restype = ctypes.c_long
    shell32.SHCreateItemFromParsingName.argtypes = (
        ctypes.c_wchar_p, ctypes.c_void_p,
        ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long

    clsid_file_operation = GUID.from_text(
        "3ad05575-8857-4850-9277-11b85bdb8e09"
    )
    iid_file_operation = GUID.from_text(
        "947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8"
    )
    iid_shell_item = GUID.from_text(
        "43826d1e-e718-42ee-bc55-a1e261c37bfe"
    )

    initialized = ole32.CoInitializeEx(None, 2) >= 0
    operation = ctypes.c_void_p()
    failed: list[str] = []
    queued = 0

    def method(pointer: ctypes.c_void_p, index: int, restype, *argtypes):
        table = ctypes.cast(
            pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])

    try:
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid_file_operation), None, 1,
            ctypes.byref(iid_file_operation), ctypes.byref(operation),
        )
        if hr < 0:
            return False, f"Impossible d'ouvrir IFileOperation (0x{hr & 0xffffffff:08X})."

        set_flags = method(operation, 5, ctypes.c_long, ctypes.c_ulong)
        delete_item = method(
            operation, 18, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p
        )
        perform = method(operation, 21, ctypes.c_long)
        aborted = method(
            operation, 22, ctypes.c_long, ctypes.POINTER(ctypes.c_int)
        )
        release_operation = method(operation, 2, ctypes.c_ulong)

        # Recyclage explicite, sans deuxième boîte de dialogue du Shell.
        set_flags(operation, 0x00080000 | 0x0010 | 0x0004 | 0x0400)
        for value in unique:
            item = ctypes.c_void_p()
            hr = shell32.SHCreateItemFromParsingName(
                value, None, ctypes.byref(iid_shell_item), ctypes.byref(item)
            )
            if hr < 0:
                failed.append(value)
                continue
            try:
                hr = delete_item(operation, item, None)
                if hr < 0:
                    failed.append(value)
                else:
                    queued += 1
            finally:
                method(item, 2, ctypes.c_ulong)(item)

        if queued:
            hr = perform(operation)
            was_aborted = ctypes.c_int()
            aborted(operation, ctypes.byref(was_aborted))
            if hr < 0 or was_aborted.value:
                return False, (
                    f"Opération Shell interrompue (0x{hr & 0xffffffff:08X}) ; "
                    "relance l'analyse pour contrôler les fichiers restants."
                )
        release_operation(operation)
        operation = ctypes.c_void_p()
    finally:
        if operation:
            method(operation, 2, ctypes.c_ulong)(operation)
        if initialized:
            ole32.CoUninitialize()

    if failed:
        preview = " ; ".join(failed[:3])
        return False, (
            f"{queued} fichier(s) recyclé(s), {len(failed)} chemin(s) refusé(s) "
            f"par Windows : {preview}"
        )
    return True, f"{queued} fichier(s) envoyé(s) à la Corbeille."


if __name__ == "__main__":
    raise SystemExit(main())
