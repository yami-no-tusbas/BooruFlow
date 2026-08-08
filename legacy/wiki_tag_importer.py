"""Importation prudente des groupes de tags depuis les wikis e621/Gelbooru."""

from __future__ import annotations

import copy
import html
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser

USER_AGENT = "ArtistByTag/1.0 (personal taxonomy organizer)"

E621_GROUPS = {
    "Species": "tag_group:species",
    "Professions": "tag_group:professions",
    "Weapons": "tag_group:weapons",
    "Clothes": "tag_group:clothes",
    "Vehicle": "vehicle",
}
GELBOORU_GROUPS = {
    "Creatures|Tag_group:Dogs": "Tag_group:Dogs",
    "Creatures|Tag_group:Cats": "Tag_group:Cats",
    "Real world|Tag_group:Jobs": "Tag_group:Jobs",
    "Objects|List_of_weapons": "List_of_weapons",
    "Attire and body accessories|Tag_group:Attire": "Tag_group:Attire",
}


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def _e621_page(title: str) -> dict:
    query = urllib.parse.urlencode({"search[title]": title, "limit": 1})
    data = json.loads(_get(f"https://e621.net/wiki_pages.json?{query}"))
    exact = next(
        (page for page in data if str(page.get("title", "")).casefold() == title.casefold()),
        None,
    )
    if exact is None:
        raise RuntimeError(f"Page e621 introuvable : {title}")
    return exact


def _e621_page_with_retry(title: str, attempts: int = 3) -> dict:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _e621_page(title)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.6 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _e621_relationships(
    tag: str, relation_types: set[str] | None = None
) -> dict[str, list[str]]:
    """Retourne uniquement les alias/implications approuvés d'un tag e621."""
    endpoints = {
        "aliases": ("tag_aliases.json", "consequent_name", "antecedent_name"),
        "implicates": ("tag_implications.json", "antecedent_name", "consequent_name"),
        "implicated_by": ("tag_implications.json", "consequent_name", "antecedent_name"),
    }
    result: dict[str, list[str]] = {}
    for label, (endpoint, search_field, value_field) in endpoints.items():
        if relation_types is not None and label not in relation_types:
            continue
        query = urllib.parse.urlencode(
            {
                f"search[{search_field}]": tag,
                "search[status]": "active",
                "limit": 320,
            }
        )
        rows = json.loads(_get(f"https://e621.net/{endpoint}?{query}"))
        values = {
            str(row.get(value_field, "")).strip().casefold()
            for row in rows
            if str(row.get("status", "")).casefold() == "active"
            and str(row.get(value_field, "")).strip()
        }
        result[label] = sorted(values, key=str.casefold)
    return result


def _clean_heading(value: str) -> str:
    value = re.sub(r"\[\[[^]|]+\|([^]]+)]]", r"\1", value)
    value = re.sub(r"\[\[([^]]+)]]", r"\1", value)
    value = re.sub(r"\[/?(?:b|i|u|s)]", "", value, flags=re.I)
    value = re.sub(r"\s*\[#[^]]+]", "", value)
    value = value.split(":", 1)[0]
    return value.strip(" :^\t")


def _wiki_links(line: str) -> list[str]:
    result: list[str] = []
    for match in re.finditer(r"\[\[([^]|#]+)(?:#[^]|]+)?(?:\|[^]]+)?]]", line):
        tag = match.group(1).strip().replace(" ", "_").casefold()
        if tag and not tag.startswith(("help:", "howto:")):
            result.append(tag)
    return result


def parse_e621_group(body: str) -> dict:
    """Transforme le DText e621 en arbre sans limiter sa profondeur."""
    tree: dict = {}
    heading_nodes: dict[int, dict] = {}
    section_stack: list[tuple[dict, dict]] = []
    base_node: dict = tree
    container_node: dict = tree
    heading_containers: dict[int, dict] = {}
    last_at_depth: dict[int, dict] = {}
    last_bullet_node: dict | None = None
    last_bullet_depth = 1
    skip_heading_level: int | None = None
    just_opened_heading = False

    def heading_name(value: str) -> tuple[str, str | None]:
        link = re.search(r"\[\[([^]|#]+)(?:\|([^]]+))?]]", value)
        tag = None
        if link:
            tag = link.group(1).strip().replace(" ", "_").casefold()
            label = (link.group(2) or link.group(1)).strip()
            if label and label != "^":
                return label, tag
        return _clean_heading(value), tag

    def add_tags(tags: list[str], depth: int) -> None:
        nonlocal last_bullet_node, last_bullet_depth, just_opened_heading
        if not tags:
            return
        parent = last_at_depth.get(depth - 1)
        if parent is None:
            lower = [level for level in last_at_depth if level < depth]
            parent = last_at_depth[max(lower)] if lower else base_node
        nodes: list[dict] = []
        for tag in tags:
            node = parent.setdefault(tag, {})
            if isinstance(node, dict):
                node["__tag__"] = tag
                nodes.append(node)
        if not nodes:
            return
        # Si une ligne contient plusieurs tags séparés par des virgules, ils
        # sont frères. Un éventuel niveau suivant dépend du premier nom cité.
        last_at_depth[depth] = nodes[0]
        for deeper in [level for level in last_at_depth if level > depth]:
            del last_at_depth[deeper]
        last_bullet_node = nodes[0]
        last_bullet_depth = depth
        just_opened_heading = False

    def plain_tags(value: str) -> list[str]:
        value = re.sub(r"\[/?[a-z][^]]*]", "", value, flags=re.I)
        value = re.split(r"\s+-\s+", value, maxsplit=1)[0].strip()
        result: list[str] = []
        for part in value.split(","):
            part = re.sub(r"\s+\([^)]*\)\s*$", "", part).strip(" *:;`|")
            if not part or part.casefold().startswith("see also"):
                continue
            words = part.split()
            if len(words) > 4 or any(char in part for char in ".;!?"):
                continue
            candidate = "_".join(words).casefold()
            if re.fullmatch(r"[\w'()+:$^/\-]+", candidate, re.UNICODE):
                result.append(candidate)
        return list(dict.fromkeys(result))

    def bullet_tags(value: str) -> list[str]:
        if value.strip().casefold().startswith("see also"):
            return []
        links = _wiki_links(value)
        if not links:
            return plain_tags(value)
        result = [links[0]]
        # Certains wikis mélangent un lien et d'autres tags en texte brut :
        # « [[Liger]], tigon ». Seule la partie introduite par une virgule
        # après le dernier lien doit alors être ajoutée.
        tail = value.rsplit("]]", 1)[-1]
        if tail.lstrip().startswith(","):
            result.extend(plain_tags(tail.lstrip()[1:]))
        return list(dict.fromkeys(result))

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"h([1-6])\.\s*(.+)", line, re.I)
        if heading:
            level = int(heading.group(1))
            name, tag = heading_name(heading.group(2))
            folded = name.strip(" :").casefold()
            if skip_heading_level is not None and level > skip_heading_level:
                continue
            if skip_heading_level is not None and level <= skip_heading_level:
                skip_heading_level = None
            if folded in {"navigation", "see also"}:
                skip_heading_level = level
                heading_nodes = {
                    old_level: node
                    for old_level, node in heading_nodes.items()
                    if old_level < level
                }
                continue
            if not name:
                continue
            lower_headings = [
                old_level
                for old_level in heading_nodes
                if old_level < level
                and heading_containers.get(old_level) is container_node
            ]
            parent = (
                heading_nodes[max(lower_headings)]
                if lower_headings
                else container_node
            )
            node = parent.setdefault(name.strip(" :"), {})
            if tag and isinstance(node, dict):
                node["__tag__"] = tag
            heading_nodes[level] = node
            heading_containers[level] = container_node
            for deeper in [old_level for old_level in heading_nodes if old_level > level]:
                del heading_nodes[deeper]
                heading_containers.pop(deeper, None)
            base_node = node
            last_at_depth.clear()
            last_bullet_node = None
            just_opened_heading = True
            continue
        if skip_heading_level is not None:
            continue
        section = re.match(
            r"\[section(?:(?:,expanded)?=([^]]+))?]", line, re.I
        )
        if section:
            section_stack.append((base_node, container_node))
            if not just_opened_heading and last_bullet_node is not None:
                base_node = last_bullet_node
            elif not just_opened_heading and section.group(1):
                label = section.group(1).strip(" :")
                if label:
                    section_node = container_node.setdefault(label, {})
                    if isinstance(section_node, dict):
                        container_node = section_node
                        base_node = section_node
            last_at_depth.clear()
            last_bullet_node = None
            just_opened_heading = False
            continue
        if line.casefold().startswith("[/section"):
            if section_stack:
                base_node, container_node = section_stack.pop()
            last_at_depth.clear()
            last_bullet_node = None
            just_opened_heading = False
            continue
        bullet = re.match(r"^(\*+)\s*(.+)", line)
        if bullet:
            add_tags(bullet_tags(bullet.group(2)), len(bullet.group(1)))
            continue
        # Quelques grandes pages e621 contiennent exceptionnellement des
        # liens sans astérisque au milieu d'une liste. Ils restent frères du
        # dernier élément au lieu d'être perdus.
        if line.startswith("[[") and last_bullet_node is not None:
            links = _wiki_links(line)
            if links:
                add_tags([links[0]], last_bullet_depth)

    return tree


class _GelbooruWikiParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_content = False
        self.seen_title = False
        self.in_h2 = False
        self.current_href = ""
        self.links: list[str] = []
        self.text: list[str] = []
        self.lines: list[dict] = []
        self.line_text: list[str] = []
        self.line_links: list[str] = []
        self.bold_depth = 0
        self.line_has_bold = False
        self.blank_lines = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        if tag == "h2":
            self.in_h2 = True
        if tag == "b":
            self.bold_depth += 1
        if self.in_content and tag == "a":
            self.current_href = html.unescape(attributes.get("href", ""))
        if self.in_content and tag == "br":
            self._flush_line()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.current_href = ""
        if tag == "h2":
            self.in_h2 = False
        if tag == "b" and self.bold_depth:
            self.bold_depth -= 1
        if self.in_content and tag == "td" and self.seen_title:
            self.in_content = False

    def handle_data(self, data: str) -> None:
        if self.in_h2 and "Now Viewing:" in data:
            self.seen_title = True
            self.in_content = True
        if not self.in_content:
            return
        value = data.strip()
        if value:
            self.text.append(value)
            self.line_text.append(value)
            if self.bold_depth:
                self.line_has_bold = True
        if self.current_href and "page=wiki" in self.current_href and "search=" in self.current_href:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.current_href).query)
            candidate = query.get("search", [""])[0].split("|", 1)[0].strip()
            if candidate and candidate.casefold() not in {"tag_groups"}:
                self.links.append(candidate)
                self.line_links.append(candidate)

    def _flush_line(self) -> None:
        if self.line_text or self.line_links:
            self.lines.append(
                {
                    "text": list(self.line_text),
                    "links": list(self.line_links),
                    "bold": self.line_has_bold,
                    "blank_before": self.blank_lines,
                }
            )
            self.blank_lines = 0
        else:
            self.blank_lines += 1
        self.line_text.clear()
        self.line_links.clear()
        self.line_has_bold = False

    def close(self) -> None:
        self._flush_line()
        super().close()


def parse_gelbooru_group(source: str) -> tuple[dict, str]:
    marker = re.search(r"<h2[^>]*>\s*Now Viewing:", source, re.I)
    if not marker:
        return {}, ""
    end = source.find("</td>", marker.start())
    source = source[marker.start() : end if end >= 0 else len(source)]
    parser = _GelbooruWikiParser()
    parser.in_content = True
    parser.seen_title = True
    parser.feed(source)
    parser.close()
    tree: dict = {}
    section = tree
    last_at_depth: dict[int, dict] = {}
    started = False
    for line in parser.lines:
        text = " ".join(line["text"]).strip()
        compact_text = "".join(line["text"]).strip()
        links = line["links"]
        wrapped_header = compact_text.startswith("-") and compact_text.endswith("-")
        header_candidate = (
            wrapped_header
            or (not links and bool(line.get("bold")))
            or (not links and int(line.get("blank_before", 0)) > 0)
        )
        if header_candidate:
            header = (compact_text if wrapped_header else text).strip(" :*-—–\t")
            if (
                header
                and not header.casefold().startswith(("now viewing", "tag type", "see "))
                and not header.startswith("[")
                and not header.startswith("(")
                and header.casefold() not in {"see also", "other wiki information"}
                and (wrapped_header or "." not in header)
                and len(header) < 100
            ):
                section = tree.setdefault(header, {})
                last_at_depth.clear()
                started = True
            continue
        if not links:
            continue
        if not started:
            continue
        tag = links[0]
        prefix = text.split(tag, 1)[0] if tag in text else text
        markers = re.match(r"^\s*([*:\-—–]+)", prefix)
        depth = len(markers.group(1)) if markers else 0
        parent = last_at_depth.get(depth - 1, section) if depth else section
        node = parent.setdefault(tag, {})
        node["__tag__"] = tag
        last_at_depth[depth] = node
        for deeper in [value for value in last_at_depth if value > depth]:
            del last_at_depth[deeper]
    definition = "\n".join(parser.text)
    return tree, definition


def parse_pasted_tag_list(source: str) -> dict:
    """Convertit une liste copiée d'un wiki en arbre de catégories et de tags."""
    source = source.strip("\r\n")
    if len(source) >= 2 and source[0] == source[-1] and source[0] in {'"', "'"}:
        source = source[1:-1]
    raw_lines = source.splitlines()

    def indentation_depth(raw: str) -> int:
        decoded = html.unescape(raw.rstrip())
        leading = decoded[: len(decoded) - len(decoded.lstrip(" \t"))]
        return len(leading.expandtabs(4)) // 4

    nonempty_depths = [
        indentation_depth(raw) for raw in raw_lines if raw.strip()
    ]
    base_depth = min(nonempty_depths, default=0)
    indentation_mode = any(depth > base_depth for depth in nonempty_depths)
    effective_depths = [
        max(0, indentation_depth(raw) - base_depth) for raw in raw_lines
    ]
    following_depths: list[int | None] = [None] * len(raw_lines)
    following: int | None = None
    for index in range(len(raw_lines) - 1, -1, -1):
        following_depths[index] = following
        if raw_lines[index].strip():
            following = effective_depths[index]
    tree: dict = {}
    section = tree
    section_stack: list[dict] = []
    last_at_depth: dict[int, dict] = {}
    started = False
    tags_since_heading = False
    for line_index, raw in enumerate(raw_lines):
        decoded = html.unescape(raw.rstrip())
        tab_depth = effective_depths[line_index]
        following_depth = following_depths[line_index]
        has_children = following_depth is not None and following_depth > tab_depth
        line = decoded.strip()
        if not line:
            continue
        line = re.sub(r"\[([^]]+)]\(https?://[^)]+\)", r"\1", line)
        line = line.strip().strip("`|")
        markdown_heading = line.startswith("**") and line.endswith("**")
        if markdown_heading:
            line = line[2:-2].strip()
        plain = line.strip(" *_")
        if plain.casefold().startswith("other wiki information"):
            break

        wrapped_header = line.startswith("-") and line.endswith("-")
        marker_match = None if wrapped_header else re.match(r"^\s*([*:\-—–]+)\s*", line)
        marker = marker_match.group(1) if marker_match else ""
        content = line[marker_match.end() :] if marker_match else line
        content = content.strip()
        content = re.sub(r"\[/?[a-z][^]]*]", "", content, flags=re.I).strip()
        content = content.split("//", 1)[0].strip()
        if not has_children:
            content = re.split(r"\s+/\s+", content, maxsplit=1)[0].strip()
        is_heading = not marker and (
            wrapped_header
            or content.endswith(":")
            or markdown_heading
        )
        if is_heading:
            heading = content.strip(" :*-—–_\t")
            if heading and len(heading) < 100:
                if not section_stack:
                    parent = tree
                    section_stack = []
                elif not tags_since_heading:
                    # Deux titres consécutifs : le second précise le premier
                    # (Rifle -> Bolt-action).
                    parent = section_stack[-1]
                else:
                    # Après une liste de tags, le titre suivant est le frère du
                    # précédent (Bolt-action -> Semi-automatic).
                    parent = section_stack[-2] if len(section_stack) > 1 else tree
                    section_stack = section_stack[:-1]
                section = parent.setdefault(heading, {})
                section_stack.append(section)
                last_at_depth.clear()
                started = True
                tags_since_heading = False
            continue

        # Les annotations entre parenthèses et les descriptions après une
        # tabulation ne font pas partie du tag copié.
        if has_children:
            candidate = re.split(r"\t+", content, maxsplit=1)[0].strip()
        else:
            candidate = re.split(r"\s+\(|\t+", content, maxsplit=1)[0].strip()
        candidate = candidate.strip("[]` ")
        if not candidate or candidate.startswith(("http://", "https://")):
            continue
        if indentation_mode and " " in candidate and (tab_depth == 0 or has_children):
            # Dans un bloc structuré par tabulations, une ligne non indentée
            # est nécessairement un nouveau parent. Elle peut être un libellé
            # humain long et ne doit pas être rejetée comme phrase descriptive.
            category = re.sub(r"\s+", " ", candidate).strip()
            depth = tab_depth
            parent = last_at_depth.get(depth - 1, section) if depth else section
            node = parent.setdefault(category, {})
            last_at_depth[depth] = node
            for deeper in [value for value in last_at_depth if value > depth]:
                del last_at_depth[deeper]
            started = True
            tags_since_heading = True
            continue
        if " " in candidate:
            # Une phrase explicative n'est pas un tag. Les libellés courts sont
            # normalisés comme le ferait un booru.
            words = candidate.split()
            if len(words) > 4 or any(char in candidate for char in ".,;!"):
                continue
            candidate = "_".join(words)
        if not re.fullmatch(r"[\w'()+!?.:$^/\-]+", candidate, re.UNICODE):
            continue
        if not started:
            started = True

        depth = tab_depth if tab_depth else len(marker)
        parent = last_at_depth.get(depth - 1, section) if depth else section
        node = parent.setdefault(candidate, {})
        if isinstance(node, dict):
            node["__tag__"] = candidate
        last_at_depth[depth] = node
        tags_since_heading = True
        for deeper in [value for value in last_at_depth if value > depth]:
            del last_at_depth[deeper]
    return tree


def analyze_pasted_tag_list(source: str) -> tuple[dict, dict]:
    """Construit l'aperçu d'un collage et mesure ses risques d'indentation."""
    tree = parse_pasted_tag_list(source)
    raw_lines = source.splitlines()

    def indentation_depth(raw: str) -> int:
        leading = raw[: len(raw) - len(raw.lstrip(" \t"))]
        return len(leading.expandtabs(4)) // 4

    rows = [
        (number, indentation_depth(raw), raw.strip())
        for number, raw in enumerate(raw_lines, 1)
        if raw.strip()
    ]
    base_depth = min((depth for _number, depth, _text in rows), default=0)
    normalized = [
        (number, max(0, depth - base_depth), text)
        for number, depth, text in rows
    ]
    jumps = []
    for previous, current in zip(normalized, normalized[1:]):
        if current[1] > previous[1] + 1:
            jumps.append(
                {
                    "line": current[0],
                    "from_depth": previous[1],
                    "to_depth": current[1],
                    "text": current[2],
                }
            )

    node_count = 0
    tree_depth = 0

    def walk(node, depth: int) -> None:
        nonlocal node_count, tree_depth
        if isinstance(node, list):
            node_count += len(node)
            tree_depth = max(tree_depth, depth)
        elif isinstance(node, dict):
            for key, child in node.items():
                if key in {"__tag__", "__tags__", "__manual__"}:
                    continue
                node_count += 1
                tree_depth = max(tree_depth, depth)
                walk(child, depth + 1)

    walk(tree, 0)
    return tree, {
        "nonempty_lines": len(rows),
        "node_count": node_count,
        "max_depth": max((depth for _number, depth, _text in normalized), default=0),
        "tree_depth": tree_depth,
        "jumps": jumps,
    }


def gelbooru_page_tree(value: str) -> dict:
    """Télécharge une page wiki Gelbooru explicite et renvoie sa taxonomie."""
    value = value.strip()
    if value.isdigit():
        url = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(
            {"page": "wiki", "s": "view", "id": value}
        )
    else:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "gelbooru.com", "www.gelbooru.com"
        }:
            raise ValueError("Utilise une URL de page wiki Gelbooru ou son identifiant numérique.")
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("page") != ["wiki"] or query.get("s") != ["view"]:
            raise ValueError("Cette URL ne désigne pas une page wiki Gelbooru.")
        url = value
    tree, _definition = parse_gelbooru_group(_get(url))
    if not tree:
        raise ValueError("La page ne contient aucune liste structurée détectable.")
    return tree


def import_catalogues(progress=None) -> dict:
    boards: dict[str, dict] = {"e621": {}, "gelbooru": {}}
    metadata: dict[str, dict] = {"e621": {}, "gelbooru": {}}
    sources: list[dict] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def find_tag_nodes(node, wanted: str):
        found = []
        if isinstance(node, dict):
            if node.get("__tag__", "").casefold() == wanted.casefold():
                found.append(node)
            for key, child in node.items():
                if key not in {"__tag__", "__tags__", "__manual__"}:
                    found.extend(find_tag_nodes(child, wanted))
        return found

    def merge_tree(target: dict, incoming: dict) -> None:
        for key, child in incoming.items():
            if key in target and isinstance(target[key], dict) and isinstance(child, dict):
                merge_tree(target[key], child)
            elif key not in target:
                target[key] = copy.deepcopy(child)

    index_title = "tag_group:index"
    index_page = _e621_page_with_retry(index_title)
    boards["e621"] = parse_e621_group(str(index_page.get("body", "")))
    sources.append(
        {
            "board": "e621",
            "label": index_title,
            "url": f"https://e621.net/wiki_pages/{index_page['id']}",
            "updated_at": now,
        }
    )
    page_trees: dict[str, dict] = {}
    queue = sorted(
        {
            tag
            for tag in iter_tags(boards["e621"])
            if tag.casefold().startswith("tag_group:")
        },
        key=str.casefold,
    )
    visited = {index_title.casefold()}
    while queue and len(visited) < 500:
        batch: list[str] = []
        while queue and len(batch) < 12 and len(visited) + len(batch) < 500:
            title = queue.pop(0)
            normalized = title.casefold()
            if normalized in visited:
                continue
            visited.add(normalized)
            batch.append(title)
        if not batch:
            continue
        downloaded: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_e621_page_with_retry, title): title
                for title in batch
            }
            for future in as_completed(futures):
                title = futures[future]
                try:
                    downloaded[title] = future.result()
                except Exception:
                    if progress:
                        progress(f"e621 ignoré (indisponible) : {title}")
        for title in batch:
            page = downloaded.get(title)
            if page is None:
                continue
            group_tree = parse_e621_group(str(page.get("body", "")))
            page_trees[title.casefold()] = group_tree
            for tag in iter_tags(group_tree):
                normalized = tag.casefold()
                if normalized.startswith("tag_group:") and normalized not in visited:
                    queue.append(tag)
            sources.append(
                {
                    "board": "e621",
                    "label": title,
                    "url": f"https://e621.net/wiki_pages/{page['id']}",
                    "updated_at": now,
                }
            )
            if progress:
                progress(f"e621 {len(visited)} : {title}")

    def expand_e621_groups(node, ancestry: frozenset[str]) -> None:
        if not isinstance(node, dict):
            return
        tag = str(node.get("__tag__", ""))
        normalized = tag.casefold()
        branch_ancestry = ancestry
        if normalized.startswith("tag_group:"):
            if normalized in page_trees and normalized not in ancestry:
                merge_tree(node, page_trees[normalized])
                branch_ancestry = ancestry | {normalized}
        for key, child in list(node.items()):
            if key not in {"__tag__", "__tags__", "__manual__"}:
                expand_e621_groups(child, branch_ancestry)

    expand_e621_groups(boards["e621"], frozenset({index_title.casefold()}))

    # Les pages tag_group ne sont pas les seules listes structurées d'e621.
    # Par exemple la page ordinaire `feline` (wiki 293) contient plusieurs
    # niveaux taxonomiques absents du résumé de tag_group:species. On développe
    # donc les tags qui sont déjà manifestement des parents, sans interroger
    # chacune des milliers de feuilles de l'index.
    ordinary_pages: dict[str, dict] = {}
    ordinary_visited: set[str] = set()
    wiki_referenced_tags: set[str] = set()

    def structural_tags(node) -> set[str]:
        result: set[str] = set()
        if not isinstance(node, dict):
            return result
        tag = str(node.get("__tag__", "")).strip()
        children = [
            child
            for key, child in node.items()
            if key not in {"__tag__", "__tags__", "__manual__"}
            and isinstance(child, (dict, list))
        ]
        if tag and children and not tag.casefold().startswith("tag_group:"):
            result.add(tag)
        for child in children:
            result.update(structural_tags(child))
        return result

    def tag_subtree(tree: dict, wanted: str) -> dict | None:
        matches = find_tag_nodes(tree, wanted)
        if not matches:
            return None
        return {
            key: copy.deepcopy(value)
            for key, value in matches[0].items()
            if key not in {"__tag__", "__tags__", "__manual__"}
        }

    structural_pending = structural_tags(boards["e621"])
    priority = {"mammal": 0, "felid": 1, "feline": 2, "cat": 3}
    ordinary_queue = sorted(
        structural_pending,
        key=lambda value: (priority.get(value.casefold(), 10), value.casefold()),
    )
    implication_queue: list[str] = []
    while (implication_queue or ordinary_queue) and len(ordinary_visited) < 400:
        batch: list[tuple[str, bool]] = []
        while (implication_queue or ordinary_queue) and len(batch) < 8 and len(ordinary_visited) + len(batch) < 400:
            title = (
                implication_queue.pop(0)
                if implication_queue
                else ordinary_queue.pop(0)
            )
            normalized = title.casefold()
            if normalized in ordinary_visited:
                continue
            ordinary_visited.add(normalized)
            batch.append((title, normalized in {tag.casefold() for tag in structural_pending}))
        if not batch:
            continue

        def download_ordinary(
            title: str, fetch_page: bool
        ) -> tuple[dict | None, dict[str, list[str]]]:
            page = None
            relations: dict[str, list[str]] = {}
            if fetch_page:
                try:
                    page = _e621_page_with_retry(title, attempts=2)
                except Exception:
                    pass
            try:
                relations = _e621_relationships(
                    title,
                    None if fetch_page else {"implicated_by"},
                )
            except Exception:
                pass
            return page, relations

        downloaded: dict[str, tuple[dict | None, dict[str, list[str]]]] = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(download_ordinary, title, fetch_page): title
                for title, fetch_page in batch
            }
            for future in as_completed(futures):
                downloaded[futures[future]] = future.result()

        for title, _fetch_page in batch:
            page, relations = downloaded.get(title, (None, {}))
            normalized = title.casefold()
            targets = find_tag_nodes(boards["e621"], title)
            if page is not None:
                parsed = parse_e621_group(str(page.get("body", "")))
                wiki_referenced_tags.update(
                    tag.casefold() for tag in iter_tags(parsed)
                )
                subtree = tag_subtree(parsed, title)
                if subtree:
                    ordinary_pages[normalized] = subtree
                    for target in targets:
                        merge_tree(target, subtree)
                    sources.append(
                        {
                            "board": "e621",
                            "label": title,
                            "url": f"https://e621.net/wiki_pages/{page['id']}",
                            "updated_at": now,
                        }
                    )
            if relations:
                entry = metadata["e621"].setdefault(title, {})
                entry.update(relations)
                # A -> B signifie que A est plus spécifique que B. Les tags
                # qui impliquent le parent sont donc des enfants valides.
                for target in targets:
                    for child_tag in relations.get("implicated_by", []):
                        child = target.setdefault(child_tag, {})
                        if isinstance(child, dict):
                            child["__tag__"] = child_tag
                for child_tag in relations.get("implicated_by", []):
                    child_entry = metadata["e621"].setdefault(child_tag, {})
                    parents = child_entry.setdefault("implicates", [])
                    if title not in parents:
                        parents.append(title)
                    if child_tag.casefold() not in ordinary_visited:
                        implication_queue.append(child_tag)
            if progress:
                progress(
                    f"e621 wiki/implications {len(ordinary_visited)}/400 : {title}"
                )
        new_structural = structural_tags(boards["e621"])
        structural_pending.update(new_structural)
        for candidate in new_structural:
            if candidate.casefold() not in ordinary_visited:
                ordinary_queue.append(candidate)
        implication_queue = sorted(
            dict.fromkeys(implication_queue),
            key=lambda value: (
                0 if value.casefold() in wiki_referenced_tags else 1,
                value.casefold(),
            ),
        )
        ordinary_queue = list(dict.fromkeys(ordinary_queue))

    # Rejoue les données connues dans chaque occurrence. C'est nécessaire si
    # un parent (felid) est découvert après que sa branche enfant (feline) a
    # déjà été développée ailleurs dans le wiki.
    def expand_e621_details(node, ancestry: frozenset[str]) -> None:
        if not isinstance(node, dict):
            return
        tag = str(node.get("__tag__", "")).strip()
        normalized = tag.casefold()
        branch_ancestry = ancestry
        if tag and normalized not in ancestry:
            branch_ancestry = ancestry | {normalized}
            if normalized in ordinary_pages:
                merge_tree(node, ordinary_pages[normalized])
            relations = metadata["e621"].get(tag, {})
            for child_tag in relations.get("implicated_by", []):
                child = node.setdefault(child_tag, {})
                if isinstance(child, dict):
                    child["__tag__"] = child_tag
        for key, child in list(node.items()):
            if key not in {"__tag__", "__tags__", "__manual__"}:
                child_tag = (
                    str(child.get("__tag__", "")).casefold()
                    if isinstance(child, dict)
                    else ""
                )
                if not child_tag or child_tag not in branch_ancestry:
                    expand_e621_details(child, branch_ancestry)

    expand_e621_details(boards["e621"], frozenset())
    if (implication_queue or ordinary_queue) and progress:
        progress(
            "e621 : limite de sécurité de 400 pages/relations atteinte ; "
            "la taxonomie importée peut encore être incomplète."
        )

    # La page Vehicle est une liste utile mais n'est pas un tag_group de
    # l'index principal. Elle reste donc une racine complémentaire.
    vehicle_page = _e621_page_with_retry("vehicle")
    vehicle_tree = parse_e621_group(str(vehicle_page.get("body", "")))
    if vehicle_tree:
        boards["e621"]["Vehicle"] = vehicle_tree
        sources.append(
            {
                "board": "e621",
                "label": "vehicle",
                "url": f"https://e621.net/wiki_pages/{vehicle_page['id']}",
                "updated_at": now,
            }
        )

    for tag in iter_tags(boards["e621"]):
        entry = metadata["e621"].setdefault(tag, {})
        entry.setdefault(
            "wiki_url",
            "https://e621.net/wiki_pages/show_or_new?title="
            f"{urllib.parse.quote(tag)}",
        )
    if progress:
        progress(
            f"e621 : {len(page_trees)} groupe(s), "
            f"{len(ordinary_pages)} liste(s) détaillée(s) développée(s)"
        )

    master_url = "https://gelbooru.com/index.php?page=wiki&s=view&id=6682"
    gelbooru_tree, _definition = parse_gelbooru_group(_get(master_url))
    boards["gelbooru"] = gelbooru_tree
    sources.append(
        {"board": "gelbooru", "label": "tag_groups", "url": master_url, "updated_at": now}
    )

    queue = sorted(
        {
            tag
            for tag in iter_tags(gelbooru_tree)
            if tag.casefold().startswith(("tag_group:", "list_of_"))
        },
        key=str.casefold,
    )
    visited: set[str] = set()
    while queue and len(visited) < 350:
        batch: list[tuple[str, str]] = []
        while queue and len(batch) < 16 and len(visited) + len(batch) < 350:
            title = queue.pop(0)
            normalized_title = title.casefold()
            if normalized_title in visited or normalized_title == "tag_groups":
                continue
            visited.add(normalized_title)
            url = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(
                {"page": "wiki", "s": "list", "search": title}
            )
            batch.append((title, url))
        if not batch:
            continue
        downloaded: dict[str, tuple[str, str]] = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_get, url): (title, url) for title, url in batch}
            for future in as_completed(futures):
                title, url = futures[future]
                try:
                    downloaded[title] = (url, future.result())
                except Exception:
                    if progress:
                        progress(f"Gelbooru ignoré (indisponible) : {title}")
        for title, _url in batch:
            if title not in downloaded:
                continue
            url, source = downloaded[title]
            group_tree, _definition = parse_gelbooru_group(source)
            targets = find_tag_nodes(gelbooru_tree, title)
            if group_tree and not targets:
                gelbooru_tree[title] = {"__tag__": title, **group_tree}
            elif group_tree:
                # Un groupe peut être référencé depuis plusieurs branches du wiki.
                # Chaque occurrence doit exposer la même descendance : sinon le
                # premier doublon rencontré capte seul les détails du groupe.
                for target in targets:
                    target.update(copy.deepcopy(group_tree))
            for tag in iter_tags(group_tree):
                metadata["gelbooru"].setdefault(
                    tag,
                    {"wiki_url": "https://gelbooru.com/index.php?" + urllib.parse.urlencode({"page": "wiki", "s": "list", "search": tag})},
                )
                if tag.casefold().startswith(("tag_group:", "list_of_")) and tag.casefold() not in visited:
                    queue.append(tag)
            sources.append({"board": "gelbooru", "label": title, "url": url, "updated_at": now})
            if progress:
                progress(f"Gelbooru {len(visited)} : {title}")

    return {"boards": boards, "metadata": metadata, "sources": sources}


def iter_tags(node):
    if isinstance(node, list):
        yield from node
    elif isinstance(node, dict):
        if "__tag__" in node:
            yield str(node["__tag__"])
        yield from node.get("__tags__", [])
        for key, child in node.items():
            if key not in {"__tag__", "__tags__", "__manual__"}:
                yield from iter_tags(child)


def merge_catalogues(document: dict, imported: dict) -> dict[str, int]:
    document.setdefault("metadata", {})
    document.setdefault("sources", [])
    document.setdefault("excluded_imported_tags", {})
    before = {
        board: set(iter_tags(document.get("boards", {}).get(board, {})))
        for board in imported["boards"]
    }
    after: dict[str, set[str]] = {}
    for board, groups in imported["boards"].items():
        excluded = set(document["excluded_imported_tags"].get(board, []))
        def filter_excluded(node):
            if isinstance(node, list):
                return [tag for tag in node if tag not in excluded]
            if isinstance(node, dict):
                result = {}
                for key, child in node.items():
                    if key == "__tag__":
                        if child not in excluded:
                            result[key] = child
                    else:
                        result[key] = filter_excluded(child)
                return result
            return node
        clean_groups = filter_excluded(groups)
        board_tree = document.setdefault("boards", {}).setdefault(board, {})

        def preserve_manual(existing, refreshed):
            if not isinstance(existing, dict) or not isinstance(refreshed, dict):
                return
            manual_tags = existing.get("__tags__", [])
            if manual_tags:
                refreshed["__tags__"] = list(dict.fromkeys(
                    list(refreshed.get("__tags__", [])) + list(manual_tags)
                ))
            for key, child in existing.items():
                if key in {"__tag__", "__tags__", "__manual__"}:
                    continue
                if isinstance(child, dict) and child.get("__manual__"):
                    refreshed[key] = child
                elif key in refreshed:
                    preserve_manual(child, refreshed[key])

        for root_name, refreshed_root in clean_groups.items():
            if root_name in board_tree:
                preserve_manual(board_tree[root_name], refreshed_root)
        board_tree.pop("Depuis le wiki", None)
        board_tree.pop("From wiki", None)
        if board == "e621":
            for legacy in (
                "Espèces",
                "Métiers",
                "Armes",
                "Vêtements",
                "Species",
                "Professions",
                "Weapons",
                "Clothes",
            ):
                board_tree.pop(legacy, None)
        else:
            for legacy in ("Chiens", "Chats", "Métiers", "Armes", "Vêtements"):
                board_tree.pop(legacy, None)
        board_tree.update(clean_groups)
        document["metadata"].setdefault(board, {}).update(imported["metadata"][board])
        after[board] = set(iter_tags(board_tree))
    document["sources"] = imported["sources"]
    document["version"] = 2
    return {
        "added": sum(len(after[b] - before[b]) for b in after),
        "removed": sum(len(before[b] - after[b]) for b in after),
        "total": sum(len(tags) for tags in after.values()),
    }


def tag_definition_details(board: str, tag: str, wiki_url: str = "") -> tuple[str, str, list[str]]:
    if board == "e621":
        page_id = re.search(r"/wiki_pages/(\d+)", wiki_url)
        page = json.loads(_get(f"https://e621.net/wiki_pages/{page_id.group(1)}.json")) if page_id else _e621_page(tag)
        raw_body = str(page.get("body", ""))
        referenced_tags = list(dict.fromkeys(_wiki_links(raw_body)))
        body = raw_body
        body = re.sub(r"\[\[[^]|]+\|([^]]+)]]", r"\1", body)
        body = re.sub(r"\[\[([^]]+)]]", r"\1", body)
        body = re.sub(r"\[/?[^]]+]", "", body)
        body = re.sub(r"(?m)^h\d\.\s*", "", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        try:
            relations = _e621_relationships(tag)
        except Exception:
            relations = {}
        relation_lines = []
        for label, heading in (
            ("aliases", "Aliases to this tag"),
            ("implicates", "This tag implicates"),
            ("implicated_by", "Tags implicating this tag"),
        ):
            values = relations.get(label, [])
            if values:
                relation_lines.append(f"{heading}: {', '.join(values)}")
        if relation_lines:
            body = f"{body}\n\n" + "\n".join(relation_lines)
        return body[:6000], f"https://e621.net/wiki_pages/{page['id']}", referenced_tags
    url = wiki_url or "https://gelbooru.com/index.php?" + urllib.parse.urlencode(
        {"page": "wiki", "s": "list", "search": tag}
    )
    source = _get(url)
    _tags, definition = parse_gelbooru_group(source)
    parser = _GelbooruWikiParser(); parser.feed(source); parser.close()
    referenced_tags = list(dict.fromkeys(parser.links))
    return definition[:6000], url, referenced_tags


def tag_definition(board: str, tag: str) -> tuple[str, str]:
    definition, url, _referenced_tags = tag_definition_details(board, tag)
    return definition, url
