"""Gelbooru wiki draft templates, validation and safe local preview."""

from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path


TEMPLATES = {
    "character": """[b]Description:[/b]
<Short description of the character and their role.>

[b]Copyright:[/b]
* [[copyright_tag]]

[b]Appearance:[/b]
<Distinctive physical traits, clothing or equipment.>

[b]See also:[/b]
* [[related_character]]

[b]External links:[/b]
https://example.com/
""",
    "copyright": """[b]Description:[/b]
<Type of work, creator and a concise description.>

[b]Alternative titles:[/b]
* <Alternative title>

[b]Characters:[/b]
* [[character_tag]]

[b]External links:[/b]
https://official.example.com/
""",
    "artist": """[b]Artist:[/b]
<Publicly used name and a concise description.>

[b]Aliases:[/b]
* <Public alias>

[b]External links:[/b]
https://portfolio.example.com/
https://social.example.com/

[b]See also:[/b]
* [[related_artist_tag]]
""",
    "general": """[b]Definition:[/b]
<What the tag objectively describes.>

[b]Usage:[/b]
<When this tag should be used.>

[b]Do not use for:[/b]
<Important exclusions or easily confused tags.>

[b]See also:[/b]
* [[related_tag]]
""",
}


def referenced_tags(source: str) -> list[str]:
    return list(dict.fromkeys(
        match.group(1).strip()
        for match in re.finditer(r"\[\[([^]]+)]]", source)
        if match.group(1).strip()
    ))


def validate_wiki_source(source: str) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    for tag in referenced_tags(source):
        if "|" in tag:
            issues.append(("alias", tag))
        if " " in tag:
            issues.append(("spaces", tag))
    for match in re.finditer(r"\{\{([^}]+)}}", source):
        if " " in match.group(1).strip():
            issues.append(("search_spaces", match.group(1).strip()))
    if re.search(r"(?mi)^h[1-6]\.\s+", source):
        issues.append(("heading", ""))
    for token in ("b", "i", "quote", "spoiler", "post", "h1", "h2", "h3", "h4", "h5"):
        if len(re.findall(fr"\[{token}]", source, re.I)) != len(re.findall(fr"\[/{token}]", source, re.I)):
            issues.append(("unbalanced", token))
    return issues


def missing_local_tags(database_path: Path | None, tags: list[str]) -> list[str]:
    candidates = [tag for tag in tags if tag and " " not in tag and "|" not in tag]
    if not database_path or not database_path.is_file() or not candidates:
        return []
    placeholders = ",".join("?" for _value in candidates)
    connection = None
    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        found = {str(row[0]).casefold() for row in connection.execute(
            f"SELECT name FROM tags WHERE name COLLATE NOCASE IN ({placeholders})", candidates
        )}
    except sqlite3.Error:
        return []
    finally:
        if connection is not None: connection.close()
    return [tag for tag in candidates if tag.casefold() not in found]


def render_wiki_preview(source: str) -> str:
    value = html.escape(source)
    value = re.sub(
        r"(?:https?://|(?:www\.)?gelbooru\.com/)[^\s<]+",
        lambda match: f'<a href="{match.group(0) if "://" in match.group(0) else "https://" + match.group(0)}">{match.group(0)}</a>',
        value,
    )
    value = re.sub(
        r"\[\[([^]|]+)]]",
        lambda match: f'<a href="booruflow-tag:{match.group(1)}">{match.group(1)}</a>',
        value,
    )
    value = re.sub(
        r"\{\{([^}]+)}}",
        lambda match: f'<a href="https://gelbooru.com/index.php?page=post&amp;s=list&amp;tags={match.group(1)}">{match.group(1)}</a>',
        value,
    )
    value = re.sub(r"\[b](.*?)\[/b]", r"<b>\1</b>", value, flags=re.I | re.S)
    value = re.sub(r"\[i](.*?)\[/i]", r"<i>\1</i>", value, flags=re.I | re.S)
    for level in range(1, 6):
        value = re.sub(
            fr"\[h{level}](.*?)\[/h{level}]",
            fr"<h{level}>\1</h{level}>", value, flags=re.I | re.S,
        )
    value = re.sub(r"\[quote](.*?)\[/quote]", r"<blockquote>\1</blockquote>", value, flags=re.I | re.S)
    value = re.sub(r"\[spoiler](.*?)\[/spoiler]", r"<span style='background:#555;color:#555'>\1</span>", value, flags=re.I | re.S)
    value = re.sub(
        r"\[post](\d+)\[/post]",
        r'<a href="https://gelbooru.com/index.php?page=post&amp;s=view&amp;id=\1">post #\1</a>',
        value,
        flags=re.I,
    )
    return value.replace("\n", "<br>")
