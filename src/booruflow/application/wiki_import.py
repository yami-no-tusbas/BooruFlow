"""Parse Danbooru DText and convert it into an inspectable Gelbooru draft."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from booruflow.application.tagging import normalize_booru_tag
from booruflow.infrastructure.gelbooru_aliases import resolve_gelbooru_alias_with_diagnostic


@dataclass(frozen=True, slots=True)
class DTextBlock:
    kind: str
    text: str
    level: int = 0


@dataclass(frozen=True, slots=True)
class DTextDocument:
    blocks: tuple[DTextBlock, ...]


@dataclass(frozen=True, slots=True)
class TagConversion:
    source: str
    target: str
    reason: str


@dataclass(frozen=True, slots=True)
class WikiImportResult:
    text: str
    source_url: str = ""
    warnings: tuple[str, ...] = ()
    tag_conversions: tuple[TagConversion, ...] = ()


@dataclass(slots=True)
class DanbooruWikiParser:
    """A small line parser; inline markup is intentionally left for the converter."""

    def parse(self, source: str) -> DTextDocument:
        blocks: list[DTextBlock] = []
        paragraph: list[str] = []

        def flush_paragraph() -> None:
            if paragraph:
                blocks.append(DTextBlock("paragraph", "\n".join(paragraph)))
                paragraph.clear()

        for raw_line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.rstrip()
            heading = re.fullmatch(r"\s*h([1-6])\.\s+(.+?)\s*", line, re.IGNORECASE)
            bullet = re.fullmatch(r"\s*(\*+)\s+(.+?)\s*", line)
            if heading:
                flush_paragraph()
                blocks.append(DTextBlock("heading", heading.group(2), int(heading.group(1))))
            elif bullet:
                flush_paragraph()
                blocks.append(DTextBlock("list_item", bullet.group(2), len(bullet.group(1))))
            elif not line.strip():
                flush_paragraph()
                blocks.append(DTextBlock("blank", ""))
            else:
                paragraph.append(line)
        flush_paragraph()
        return DTextDocument(tuple(blocks))


@dataclass(slots=True)
class GelbooruTagResolver:
    """Resolve exact local tags and active Gelbooru aliases, never fuzzy matches."""

    tag_database: Path | None
    alias_database: Path | None = None
    crossboard_mappings: dict[str, str] = field(default_factory=dict)

    def resolve(self, source: str) -> TagConversion:
        normalized = normalize_booru_tag(source)
        mapped = self.crossboard_mappings.get(normalized)
        if mapped:
            return TagConversion(source, mapped, "known_crossboard_mapping")
        if self.alias_database and self.alias_database.is_file():
            canonical, diagnostic = resolve_gelbooru_alias_with_diagnostic(
                normalized, self.alias_database
            )
            if diagnostic is None and canonical != normalized and self._tag_exists(canonical):
                return TagConversion(source, canonical, "gelbooru_alias")
        if self._tag_exists(normalized):
            reason = "same_name" if source == normalized else "normalized_syntax"
            return TagConversion(source, normalized, reason)
        return TagConversion(source, normalized or source, "unresolved")

    def _tag_exists(self, name: str) -> bool:
        if not self.tag_database or not self.tag_database.is_file() or not name:
            return False
        connection: sqlite3.Connection | None = None
        try:
            uri = f"file:{self.tag_database.resolve().as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            return connection.execute(
                "SELECT 1 FROM tags WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)
            ).fetchone() is not None
        except sqlite3.Error:
            return False
        finally:
            if connection is not None:
                connection.close()


class DanbooruToGelbooruConverter:
    """Convert supported DText constructs while preserving unsupported content."""

    _WIKI_LINK = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?]]")
    _MARKDOWN_LINK = re.compile(r"\[([^\]]+)]\((https?://[^\s)]+)\)")
    _DTEXT_LINK = re.compile(r'"([^"\n]+)":(https?://[^\s]+)')

    def __init__(self, resolver: GelbooruTagResolver) -> None:
        self.resolver = resolver

    def convert(
        self, document: DTextDocument, tag_name: str, *, source_url: str = ""
    ) -> WikiImportResult:
        warnings: list[str] = []
        conversions: list[TagConversion] = []
        lines = [f"{{{{{normalize_booru_tag(tag_name)}}}}}"]
        section_depth = 0
        for block in document.blocks:
            if block.kind == "blank":
                if lines and lines[-1] and not lines[-1].startswith("[h2]"):
                    lines.append("")
                continue
            converted = self._convert_inline(block.text, conversions, warnings)
            if block.kind == "heading":
                lines.append(f"[h2]{converted}[/h2]")
            elif block.kind == "list_item":
                lines.append(f"* {converted}")
                if block.level > 1:
                    warnings.append(
                        "Nested Danbooru list depth was flattened to Gelbooru '* item' syntax."
                    )
            else:
                lines.extend(converted.splitlines())
            section_depth += converted.casefold().count("[section")
            section_depth -= converted.casefold().count("[/section]")
        if section_depth:
            warnings.append("Unbalanced Danbooru [section] markup was preserved for review.")
        text = self._normalize_lines(lines)
        return WikiImportResult(
            text=text,
            source_url=source_url,
            warnings=tuple(dict.fromkeys(warnings)),
            tag_conversions=tuple(self._deduplicate_conversions(conversions)),
        )

    def _convert_inline(
        self,
        value: str,
        conversions: list[TagConversion],
        warnings: list[str],
    ) -> str:
        def wiki_link(match: re.Match[str]) -> str:
            source = match.group(1).strip()
            label = (match.group(2) or "").strip()
            conversion = self.resolver.resolve(source)
            conversions.append(conversion)
            if conversion.reason == "unresolved":
                warnings.append(
                    f"{source} — not found in local Gelbooru database; original preserved"
                )
                target = normalize_booru_tag(source) or source
            else:
                target = conversion.target
            if label and normalize_booru_tag(label) != target:
                warnings.append(
                    f"Link label '{label}' has no safe Gelbooru equivalent; tag link preserved."
                )
            return f"[[{target}]]"

        value = self._WIKI_LINK.sub(wiki_link, value)
        value = self._MARKDOWN_LINK.sub(lambda match: f"{match.group(1)} ({match.group(2)})", value)
        value = self._DTEXT_LINK.sub(lambda match: f"{match.group(1)} ({match.group(2)})", value)
        value = re.sub(r"\[u](.*?)\[/u]", r"\1", value, flags=re.IGNORECASE | re.DOTALL)
        value = re.sub(r"\[s](.*?)\[/s]", r"\1", value, flags=re.IGNORECASE | re.DOTALL)
        for token in re.findall(r"\[(/?)([a-z][\w-]*)(?:=[^\]]*)?]", value, re.IGNORECASE):
            name = token[1].casefold()
            if name not in {"b", "i", "quote", "spoiler"}:
                warnings.append(f"Unsupported Danbooru [{name}] markup was preserved for review.")
        return value

    @staticmethod
    def _normalize_lines(lines: list[str]) -> str:
        normalized: list[str] = []
        for line in lines:
            if not line and (not normalized or not normalized[-1] or normalized[-1].startswith("[h2]")):
                continue
            normalized.append(line.rstrip())
        while normalized and not normalized[-1]:
            normalized.pop()
        return "\n".join(normalized) + "\n"

    @staticmethod
    def _deduplicate_conversions(values: list[TagConversion]) -> list[TagConversion]:
        result: list[TagConversion] = []
        seen: set[tuple[str, str, str]] = set()
        for value in values:
            key = (value.source, value.target, value.reason)
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result
