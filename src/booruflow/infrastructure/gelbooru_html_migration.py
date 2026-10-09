"""Transactional, one-pass repair of legacy Gelbooru names with lossless archives."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime

from booruflow.domain.booru_sites import category_name
from booruflow.domain.entity_profiles import tag_kind
from booruflow.infrastructure.gelbooru_normalization import decode_tag, preferred_category
from booruflow.infrastructure.tag_browser import exact_tags


def _rows(connection, table, where="", parameters=()):
    cursor = connection.execute(f"SELECT * FROM {table} {where}", parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def _insert(connection, table, row):
    names = list(row)
    connection.execute(f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})",
                       tuple(row[name] for name in names))


def _prepare(connection, name):
    connection.execute("""CREATE TABLE IF NOT EXISTS gelbooru_html_migrations(
        name TEXT PRIMARY KEY, completed_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS gelbooru_html_archive(
        migration TEXT NOT NULL, table_name TEXT NOT NULL, row_json TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS gelbooru_html_conflicts(
        migration TEXT NOT NULL, scope TEXT NOT NULL, tag TEXT NOT NULL,
        choices_json TEXT NOT NULL, resolution TEXT NOT NULL)""")
    return connection.execute("SELECT 1 FROM gelbooru_html_migrations WHERE name=?", (name,)).fetchone()


def _archive(connection, name, table, rows):
    connection.executemany("INSERT INTO gelbooru_html_archive VALUES(?,?,?)",
                           ((name, table, json.dumps(row, ensure_ascii=False)) for row in rows))


def _complete(connection, name):
    connection.execute("INSERT INTO gelbooru_html_migrations VALUES(?,?)",
                       (name, datetime.now(UTC).isoformat()))


def _decision(connection, name, scope, tag, values, priority):
    choices = sorted(set(values))
    selected = max(choices, key=priority.index)
    if len(choices) > 1:
        connection.execute("INSERT INTO gelbooru_html_conflicts VALUES(?,?,?,?,?)",
                           (name, str(scope), tag, json.dumps(choices), selected))
    return selected


def _categories(database, names):
    if database is None or not database.is_file():
        return {}
    return {row.name: category_name("gelbooru", row.category) for row in exact_tags(database, list(names))}


def migrate_entity_profiles(connection, tag_database=None):
    """Union witnesses, rebuild counts, and preserve every original decision in an archive."""
    name = "entity_profiles_html_v1"
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if _prepare(connection, name):
            return
        profiles = _rows(connection, "profiles")
        witnesses = _rows(connection, "profile_post_tags")
        recurring = _rows(connection, "recurring_tags")
        groups = defaultdict(list)
        affected = {row["profile_id"] for row in witnesses + recurring if decode_tag(row["tag"]) != row["tag"]}
        for profile in profiles:
            key = (decode_tag(profile["entity_tag"]), decode_tag(profile["query"]))
            groups[key].append(profile)
            if key != (profile["entity_tag"], profile["query"]):
                affected.add(profile["id"])
        mapping = {}
        rebuilt = {}
        for (entity, query), members in groups.items():
            if not any(member["id"] in affected for member in members):
                continue
            affected.update(member["id"] for member in members)
            chosen = min(members, key=lambda p: (p["entity_tag"] != entity or p["query"] != query, p["id"]))
            latest = max(members, key=lambda p: (p["updated_at"], p["id"]))
            rebuilt[chosen["id"]] = dict(chosen, entity_tag=entity, query=query,
                created_at=min(p["created_at"] for p in members), updated_at=latest["updated_at"],
                last_run_state=latest["last_run_state"], last_run_new=latest["last_run_new"])
            mapping.update((p["id"], chosen["id"]) for p in members)
        source_rows = {"profiles": [p for p in profiles if p["id"] in affected],
            "profile_posts": [p for p in _rows(connection, "profile_posts") if p["profile_id"] in affected],
            "profile_post_tags": [p for p in witnesses if p["profile_id"] in affected],
            "recurring_tags": [p for p in recurring if p["profile_id"] in affected]}
        # Refuse to fabricate counts if an old dataset has lost its witness evidence.
        old_counts = Counter((p["profile_id"], p["tag"]) for p in source_rows["profile_post_tags"])
        if any(old_counts[p["profile_id"], p["tag"]] != p["occurrences"]
               for p in source_rows["recurring_tags"]):
            raise RuntimeError("Entity HTML repair requires complete matching-post witnesses")
        for table, rows in source_rows.items():
            _archive(connection, name, table, rows)
        posts = {}
        for row in source_rows["profile_posts"]:
            key = (mapping[row["profile_id"]], row["post_id"])
            if key not in posts or not posts[key]["preview_url"]:
                posts[key] = dict(row, profile_id=key[0])
        tags = {(mapping[row["profile_id"]], row["post_id"], decode_tag(row["tag"]))
                for row in source_rows["profile_post_tags"]}
        counts = Counter((pid, tag) for pid, _post, tag in tags)
        merged = defaultdict(list)
        for row in source_rows["recurring_tags"]:
            merged[mapping[row["profile_id"]], decode_tag(row["tag"])].append(row)
        local = _categories(tag_database, {decode_tag(row["tag"]) for row in source_rows["recurring_tags"]
                                          if decode_tag(row["tag"]) != row["tag"]})
        for pid in affected:
            connection.execute("DELETE FROM profiles WHERE id=?", (pid,))
        for pid, profile in rebuilt.items():
            profile["sample_count"] = sum(key[0] == pid for key in posts)
            profile["last_run_new"] = min(profile["last_run_new"], profile["sample_count"])
            _insert(connection, "profiles", profile)
        for row in posts.values():
            _insert(connection, "profile_posts", row)
        connection.executemany("INSERT INTO profile_post_tags VALUES(?,?,?)", sorted(tags))
        for (pid, tag), rows in merged.items():
            status = _decision(connection, name, pid, tag, [row["status"] for row in rows],
                               ["review", "keep", "ignore"])
            if tag == rebuilt[pid]["entity_tag"]:
                continue
            category = local.get(tag, preferred_category([row["category"] for row in rows]))
            _insert(connection, "recurring_tags", dict(rows[0], profile_id=pid, tag=tag,
                category=category, kind=tag_kind(category), occurrences=counts[pid, tag], status=status))
        exclusions = _rows(connection, "global_exclusions")
        if any(decode_tag(row["tag"]) != row["tag"] for row in exclusions):
            _archive(connection, name, "global_exclusions", exclusions)
            connection.execute("DELETE FROM global_exclusions")
            connection.executemany("INSERT INTO global_exclusions VALUES(?)",
                                   ((tag,) for tag in sorted({decode_tag(row["tag"]) for row in exclusions})))
        decisions = _rows(connection, "tagging_profile_decisions")
        if any(decode_tag(row["tag"]) != row["tag"] for row in decisions):
            _archive(connection, name, "tagging_profile_decisions", decisions)
            connection.execute("DELETE FROM tagging_profile_decisions")
            groups = defaultdict(list)
            for row in decisions:
                groups[row["item_id"], decode_tag(row["tag"])].append(row)
            for (item, tag), rows in groups.items():
                decision = _decision(connection, name, f"item:{item}", tag,
                                     [row["decision"] for row in rows], ["unreviewed", "accepted", "rejected"])
                chosen = next(row for row in rows if row["decision"] == decision)
                _insert(connection, "tagging_profile_decisions", dict(chosen, tag=tag))
        _complete(connection, name)
        connection.execute("PRAGMA user_version=2")


def migrate_metadata_cache(connection, tag_database=None):
    """Repair the Gelbooru portion of the shared cache, leaving e621 untouched."""
    name = "metadata_cache_html_v1"
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if _prepare(connection, name):
            return
        rows = _rows(connection, "remote_post_tags", "WHERE site='gelbooru'")
        changed_posts = {row["post_id"] for row in rows if decode_tag(row["tag"]) != row["tag"]}
        affected = [row for row in rows if row["post_id"] in changed_posts]
        _archive(connection, name, "remote_post_tags", affected)
        groups = defaultdict(list)
        for row in affected:
            groups[row["post_id"], decode_tag(row["tag"])].append(row)
        local = _categories(tag_database, {decode_tag(row["tag"]) for row in affected
                                          if decode_tag(row["tag"]) != row["tag"]})
        for post in changed_posts:
            connection.execute("DELETE FROM remote_post_tags WHERE site='gelbooru' AND post_id=?", (post,))
        for (_post, tag), members in groups.items():
            category = local.get(tag, preferred_category([row["category"] for row in members]))
            _insert(connection, "remote_post_tags", dict(members[0], tag=tag, category=category))
        _complete(connection, name)


def migrate_image_source_cache(connection, tag_database=None):
    """Explicit repair of Gelbooru source metadata; no observations or WD14 vectors."""
    name = "image_source_html_v1"
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if _prepare(connection, name):
            return
        candidates = _rows(connection, "source_tags", "WHERE site='gelbooru' AND tag_name LIKE '%&%'")
        items = sorted({row["item_id"] for row in candidates if decode_tag(row["tag_name"]) != row["tag_name"]})
        rows = []
        for offset in range(0, len(items), 500):
            chunk = items[offset:offset + 500]
            rows.extend(_rows(connection, "source_tags",
                f"WHERE site='gelbooru' AND item_id IN ({','.join('?' for _ in chunk)})", chunk))
        _archive(connection, name, "source_tags", rows)
        groups = defaultdict(list)
        renamed = {}
        for row in rows:
            decoded = decode_tag(row["tag_name"])
            groups[row["item_id"], decoded].append(row)
            if decoded != row["tag_name"]:
                renamed[row["item_id"], row["tag_name"]] = decoded
        local = _categories(tag_database, set(renamed.values()))
        for item in items:
            connection.execute("DELETE FROM source_tags WHERE site='gelbooru' AND item_id=?", (item,))
        for (_item, tag), members in groups.items():
            category = local.get(tag, preferred_category([row["category"] for row in members]))
            _insert(connection, "source_tags", dict(members[0], tag_name=tag, category=category,
                fetched_at=max(row["fetched_at"] for row in members)))
        # Existing-tag decisions are site-neutral, so rename only names actually
        # encountered in that item's Gelbooru source, never arbitrary manual/WD14 text.
        decisions = _rows(connection, "tag_review_entries", "WHERE origin='existing'")
        changed = {(row["item_id"], renamed[row["item_id"], row["tag_name"]])
                   for row in decisions if (row["item_id"], row["tag_name"]) in renamed}
        groups = defaultdict(list)
        for row in decisions:
            tag = renamed.get((row["item_id"], row["tag_name"]), row["tag_name"])
            if (row["item_id"], tag) in changed:
                groups[row["item_id"], tag].append(row)
        for (item, tag), members in groups.items():
            _archive(connection, name, "tag_review_entries", members)
            decision = _decision(connection, name, f"item:{item}", tag,
                                 [row["decision"] for row in members], ["keep", "remove"])
            for row in members:
                connection.execute("DELETE FROM tag_review_entries WHERE item_id=? AND tag_name=? AND origin='existing'",
                                   (item, row["tag_name"]))
            chosen = next(row for row in members if row["decision"] == decision)
            _insert(connection, "tag_review_entries", dict(chosen, tag_name=tag))
        cached = _rows(connection, "post_metadata_cache",
                       "WHERE site='gelbooru' AND (tags_json LIKE '%&%' OR artist_tags_json LIKE '%&%')")
        for row in cached:
            raw = json.loads(row["tags_json"])
            artists = json.loads(row["artist_tags_json"])
            if not (any(decode_tag(tag["name"]) != tag["name"] for tag in raw)
                    or any(decode_tag(tag) != tag for tag in artists)):
                continue
            _archive(connection, name, "post_metadata_cache", [row])
            groups = defaultdict(list)
            for tag in raw:
                groups[decode_tag(tag["name"])].append(tag.get("category"))
            local = _categories(tag_database, {decode_tag(tag["name"]) for tag in raw
                                              if decode_tag(tag["name"]) != tag["name"]})
            tags = [{"name": tag, "category": local.get(tag, preferred_category(categories))}
                    for tag, categories in groups.items()]
            artists = list(dict.fromkeys(decode_tag(tag) for tag in artists))
            connection.execute("UPDATE post_metadata_cache SET tags_json=?,artist_tags_json=? WHERE site='gelbooru' AND post_id=?",
                (json.dumps(tags, ensure_ascii=False), json.dumps(artists, ensure_ascii=False), row["post_id"]))
        _complete(connection, name)
