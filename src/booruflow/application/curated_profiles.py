"""Load JSON packs once, validating exclusively against read-only local catalogues."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from booruflow.application.database_paths import gelbooru_alias_database, gelbooru_tag_database
from booruflow.application.tag_policy import is_deprecated
from booruflow.application.tagging import normalize_booru_tag
from booruflow.domain.curated_profiles import (
    CuratedGroup,
    CuratedProfile,
    CuratedProfileIndex,
    CuratedTag,
)
from booruflow.infrastructure.tag_browser import exact_tags

logger = logging.getLogger(__name__)


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
        raise ValueError("tag/id must be a non-empty string without whitespace")
    if value.startswith(("group:", "curated:")):
        raise ValueError("virtual identifiers cannot be tags")
    return normalize_booru_tag(value)


def load_curated_profiles(directories, tag_database, alias_database=None, *,
                          min_post_count=25, top_count=5, warn=None):
    """Later directories/files override settings/labels and union canonical groups.

    Valid siblings survive malformed files, profiles, groups and individual tags.
    All names are resolved once and queried in batches, never during navigation.
    """
    warn = warn or logger.warning
    try:
        minimum = _integer(min_post_count, "min_post_count")
        count = _integer(top_count, "top_count")
    except ValueError as exc:
        warn(f"Curated Profiles: {exc}; using defaults")
        minimum, count = 25, 5
    raw_profiles = []
    for directory in directories:
        try:
            files = sorted(Path(directory).glob("*.json"), key=lambda p: p.name)
        except OSError as exc:
            warn(f"Curated Profiles: {directory}: {exc}")
            continue
        for path in files:
            try:
                pack = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(pack, dict) or type(pack.get("format_version")) is not int:
                    raise ValueError("format_version must be 1")
                if pack["format_version"] != 1:
                    raise ValueError(f"unsupported format_version {pack['format_version']}")
                pack_id = _name(pack["pack"]["id"])
                if not isinstance(pack.get("profiles"), list):
                    raise TypeError("profiles must be a list")
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
                warn(f"Curated Profiles: {path}: {exc}")
                continue
            for index, profile in enumerate(pack["profiles"]):
                location = f"{path}: profiles[{index}]"
                try:
                    trigger = _name(profile["trigger"])
                    threshold = _integer(profile.get("min_post_count", minimum), "min_post_count")
                    top = _integer(profile.get("top_count", count), "top_count")
                    if not isinstance(profile.get("groups"), list):
                        raise TypeError("groups must be a list")
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    warn(f"Curated Profiles: {location}: {exc}")
                    continue
                groups = []
                for group in profile["groups"]:
                    try:
                        group_id = _name(group["id"])
                        labels = group.get("label", {})
                        if (not isinstance(labels, dict) or any(not isinstance(k, str)
                                or not isinstance(v, str) for k, v in labels.items())):
                            raise ValueError("label must map locales to strings")
                        if "groups" in group or not isinstance(group.get("tags"), list):
                            raise ValueError("groups must contain tags only; nesting is unsupported")
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        warn(f"Curated Profiles: {location}: {exc}")
                        continue
                    names = []
                    for value in group["tags"]:
                        try:
                            names.append(_name(value))
                        except ValueError as exc:
                            warn(f"Curated Profiles: {location}: {value!r}: {exc}")
                    groups.append((group_id, labels, names))
                raw_profiles.append((trigger, pack_id, threshold, top, groups, location))
    if not raw_profiles:
        return {}
    names = {p[0] for p in raw_profiles}
    names.update(n for p in raw_profiles for g in p[4] for n in g[2])
    pack_names = set(names)
    aliases = {}
    if alias_database is not None and Path(alias_database).is_file():
        try:
            with closing(sqlite3.connect(
                    f"file:{Path(alias_database).resolve().as_posix()}?mode=ro", uri=True)) as connection:
                aliases = {normalize_booru_tag(a): normalize_booru_tag(b) for a, b in
                           connection.execute("SELECT source_name,target_name FROM "
                                              "gelbooru_aliases WHERE status='active'")}
        except (OSError, sqlite3.Error) as exc:
            warn(f"Curated Profiles: alias catalogue unavailable: {exc}")
    canonical = {}
    # Resolve only pack names and active alias sources; the rest of the tag DB is never scanned.
    names.update(aliases)
    for name in sorted(names):
        try:
            target, seen = name, set()
            while target in aliases:
                if target in seen or len(seen) >= 16:
                    raise RuntimeError("unsafe alias chain/cycle")
                seen.add(target)
                target = aliases[target]
            canonical[name] = normalize_booru_tag(target)
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            warn(f"Curated Profiles: alias {name}: {exc}; ignored")
    try:
        rows = {normalize_booru_tag(row.name): row for row in
                exact_tags(Path(tag_database), list({canonical[n] for n in pack_names
                                                    if n in canonical}))}
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        warn(f"Curated Profiles: local Gelbooru catalogue unavailable: {exc}")
        return CuratedProfileIndex()
    valid = {}
    for name in sorted(pack_names & canonical.keys()):
        target = canonical[name]
        row = rows.get(target)
        if row is None or is_deprecated("gelbooru", row.category):
            warn(f"Curated Profiles: unknown/deprecated tag {name} (canonical {target}); ignored")
        else:
            valid[name] = target
    merged = {}
    for trigger, pack_id, threshold, top, groups, location in raw_profiles:
        if trigger not in valid:
            warn(f"Curated Profiles: {location}: unusable trigger {trigger}")
            continue
        trigger = valid[trigger]
        entry = merged.setdefault(trigger, {"packs": set(), "groups": {}})
        entry.update(minimum=threshold, top=top)
        entry["packs"].add(pack_id)
        for group_id, labels, tags in groups:
            group = entry["groups"].setdefault(group_id, {"labels": {}, "tags": defaultdict(set)})
            group["labels"].update(labels)
            for name in tags:
                if name in valid and valid[name] != trigger:
                    group["tags"][valid[name]].add(pack_id)
    result = {}
    for trigger, entry in merged.items():
        provenance = defaultdict(set)
        for group in entry["groups"].values():
            for name, packs in group["tags"].items():
                provenance[name].update(packs)
        tags = {name: CuratedTag(name, rows[name].category, rows[name].post_count,
                               tuple(sorted(packs))) for name, packs in provenance.items()
                if rows[name].post_count >= entry["minimum"]}
        groups = tuple(CuratedGroup(group_id, group["labels"],
                       tuple(tags[n] for n in sorted(group["tags"],
                             key=lambda n: (-rows[n].post_count, n)) if n in tags))
                       for group_id, group in entry["groups"].items()
                       if any(n in tags for n in group["tags"]))
        if groups:
            result[trigger] = CuratedProfile(trigger, groups, entry["top"], entry["minimum"],
                                             tuple(sorted(entry["packs"])))
    return CuratedProfileIndex(result, {n: t for n, t in canonical.items() if t in rows})


def configured_curated_profiles(root, resources, settings, *, warn=None):
    directories = [Path(resources) / "resources/curated_tag_profiles",
                   Path(root) / "config/curated_tag_profiles"]
    external = settings.get("curated_tag_profile_directories", [])
    if isinstance(external, list):
        for value in external:
            if isinstance(value, str) and value.strip():
                path = Path(value)
                directories.append(path if path.is_absolute() else Path(root) / path)
    return load_curated_profiles(
        list(dict.fromkeys(directories)), gelbooru_tag_database(settings),
        gelbooru_alias_database(settings),
        min_post_count=settings.get("curated_tag_profiles_min_post_count", 25),
        top_count=settings.get("curated_tag_profiles_top_count", 5), warn=warn)
