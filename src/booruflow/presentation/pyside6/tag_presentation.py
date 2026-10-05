"""Shared, site-aware visual semantics for tags and autocomplete rows."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QStringListModel, Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import QCompleter

from booruflow.application.tag_autocomplete import TagSuggestion
from booruflow.domain.booru_sites import category_name
from booruflow.presentation.pyside6.theme import ITEM_VIEW_SELECTION_STYLE

CHIP_TEXT_LIMIT = 15
TAG_NAME_ROLE = Qt.ItemDataRole.UserRole + 100
CANONICAL_TAG_ROLE = Qt.ItemDataRole.UserRole + 101
TAG_CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 102
TAG_POST_COUNT_ROLE = Qt.ItemDataRole.UserRole + 103
TAG_ALIAS_SOURCE_ROLE = Qt.ItemDataRole.UserRole + 104
TAG_SITE_ROLE = Qt.ItemDataRole.UserRole + 105
TAG_COMPLETER_POPUP_STYLE = ITEM_VIEW_SELECTION_STYLE


def chip_display_text(value: str) -> str:
    return value if len(value) <= CHIP_TEXT_LIMIT else f"{value[:CHIP_TEXT_LIMIT]}..."


def normalized_tag_category(category: int | str | None, site: str) -> str:
    value = category_name(site, category)
    return {
        "meta": "metadata",
        "invalid / unknown": "invalid",
        "deprecated": "invalid",
    }.get(value, value)


@dataclass(frozen=True, slots=True)
class TagVisualStyle:
    normal: QColor
    alternate: QColor

    def color(self, palette: QPalette) -> QColor:
        return self.alternate if palette.color(QPalette.ColorRole.Window).lightness() < 128 else self.normal


@dataclass(frozen=True, slots=True)
class TagPresentation:
    site: str
    name: str
    category: str
    display_text: str
    tooltip: str
    style: TagVisualStyle


_E621 = {
    "general": ("#B4C7D9", "#2E76B4"),
    "artist": ("#F2AC08", "#FBD67F"),
    "contributor": ("#C0C0C0", "#71706E"),
    "copyright": ("#DD00DD", "#FF5EFF"),
    "character": ("#00AA00", "#2BFF2B"),
    "species": ("#ED5D1F", "#F6B295"),
    "invalid": ("#FF3D3D", "#FF8A8A"),
    "metadata": ("#FFFFFF", "#666666"),
    "lore": ("#228822", "#5FDB5F"),
    "pool": ("#F5DEB3", "#D0B27A"),
}

_GELBOORU = {
    "artist": ("#CC0000", "#CC0000"),
    "copyright": ("#DD00DD", "#DD00DD"),
    "character": ("#00AA00", "#00AA00"),
    "metadata": ("#B45309", "#FFB55A"),
    "invalid": ("#8A6D8F", "#BFA7C5"),
}


def tag_visual_style(site: str, category: int | str | None, palette: QPalette) -> TagVisualStyle:
    semantic = normalized_tag_category(category, site)
    if semantic in {"general", "unknown"}:
        neutral = palette.color(QPalette.ColorRole.Text)
        return TagVisualStyle(neutral, neutral)
    profile = (_E621 if site.casefold() == "e621" else _GELBOORU).get(semantic)
    if profile is None:
        neutral = palette.color(QPalette.ColorRole.Text)
        return TagVisualStyle(neutral, neutral)
    return TagVisualStyle(QColor(profile[0]), QColor(profile[1]))


def present_tag(
    site: str, name: str, category: int | str | None, palette: QPalette
) -> TagPresentation:
    semantic = normalized_tag_category(category, site)
    return TagPresentation(
        site=site,
        name=name,
        category=semantic,
        display_text=chip_display_text(name),
        tooltip=name,
        style=tag_visual_style(site, semantic, palette),
    )


def populate_suggestion_model(
    model: QStringListModel,
    suggestions: list[TagSuggestion],
    palette: QPalette,
    *,
    detailed: bool = False,
) -> None:
    labels = [suggestion_display_text(item, detailed=detailed) for item in suggestions]
    model.setStringList(labels)
    for row, item in enumerate(suggestions):
        index = model.index(row, 0)
        model.setData(index, item.name, TAG_NAME_ROLE)
        model.setData(index, item.canonical_name, CANONICAL_TAG_ROLE)
        model.setData(index, item.category, TAG_CATEGORY_ROLE)
        model.setData(index, item.post_count, TAG_POST_COUNT_ROLE)
        model.setData(index, item.alias_source, TAG_ALIAS_SOURCE_ROLE)
        model.setData(index, item.site, TAG_SITE_ROLE)
        tooltip = [item.canonical_name, item.category, f"{item.post_count:,} posts"]
        if item.alias_source:
            tooltip.insert(0, f"{item.alias_source} → {item.canonical_name}")
        model.setData(index, " · ".join(tooltip), Qt.ItemDataRole.ToolTipRole)
    refresh_suggestion_model(model, palette)


def suggestion_display_text(item: TagSuggestion, *, detailed: bool = False) -> str:
    value = (
        f"{item.alias_source} → {item.canonical_name}"
        if item.alias_source
        else item.canonical_name
    )
    if detailed:
        value += f" — {item.category} · {item.post_count:,} posts"
    return value


def refresh_suggestion_model(model: QStringListModel, palette: QPalette) -> None:
    """Refresh stored brushes from structured roles after a live palette change."""
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        site = str(index.data(TAG_SITE_ROLE) or "gelbooru")
        category = index.data(TAG_CATEGORY_ROLE)
        presentation = present_tag(site, str(index.data(CANONICAL_TAG_ROLE) or ""), category, palette)
        model.setData(
            index,
            QBrush(presentation.style.color(palette)),
            Qt.ItemDataRole.ForegroundRole,
        )


def configure_tag_completer(completer: QCompleter) -> None:
    """Keep rich presentation and category colours in the popup."""
    completer.setCompletionRole(Qt.ItemDataRole.DisplayRole)


def show_tag_completer(completer: QCompleter) -> None:
    """Create and style the popup only when suggestions are actually shown."""
    popup = completer.popup()
    popup.setStyleSheet(TAG_COMPLETER_POPUP_STYLE)
    receiver = getattr(completer, "_context_menu_receiver", None)
    if receiver is not None:
        receiver.bind_popup(popup)
    completer.complete()


def semantic_chip_stylesheet(palette: QPalette, site: str = "gelbooru") -> str:
    base = palette.color(QPalette.ColorRole.Base)
    text = palette.color(QPalette.ColorRole.Text)
    muted = palette.color(QPalette.ColorRole.PlaceholderText)
    dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
    categories = (
        "general", "artist", "contributor", "copyright", "character", "species",
        "invalid", "metadata", "lore", "pool",
    )
    rules: list[str] = []
    for category in categories:
        accent = tag_visual_style(site, category, palette).color(palette)
        blend = 0.16 if category == "general" else (0.24 if dark else 0.13)
        active = _mix(base, accent, blend)
        inactive = _mix(base, muted, 0.08)
        hover = _mix(base, accent, 0.28 if dark else 0.19)
        rules.extend((
            f"QFrame[tagChip='true'][chipCategory='{category}'][chipActive='true'] {{ background:{active.name()}; color:{text.name()}; border:2px solid {accent.name()}; border-radius:8px; font-weight:600; }}",
            f"QFrame[tagChip='true'][chipCategory='{category}'][chipActive='false'] {{ background:{inactive.name()}; color:{muted.name()}; border:1px dashed {muted.name()}; border-radius:8px; }}",
            f"QFrame[tagChip='true'][chipCategory='{category}']:hover {{ background:{hover.name()}; }}",
        ))
    rules.append("QFrame[tagChip='true']:disabled { border-style:dotted; color:palette(mid); }")
    return "\n".join(rules)


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    return QColor(
        round(first.red() * (1.0 - amount) + second.red() * amount),
        round(first.green() * (1.0 - amount) + second.green() * amount),
        round(first.blue() * (1.0 - amount) + second.blue() * amount),
    )


class TagSuggestionListModel(QStringListModel):
    """A completer-compatible string model backed by structured suggestions."""

    def __init__(self, parent=None, *, detailed: bool = False) -> None:
        super().__init__(parent)
        self._suggestions: list[TagSuggestion] = []
        self._palette = QPalette()
        self.detailed = detailed
        self._role_values: list[dict[int, object]] = []

    def setStringList(self, strings: list[str]) -> None:
        super().setStringList(strings)
        self._role_values = [{} for _value in strings]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if (
            role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}
            and index.isValid()
            and 0 <= index.row() < len(self._role_values)
        ):
            return self._role_values[index.row()].get(int(role))
        return super().data(index, role)

    def setData(
        self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if (
            role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}
            and index.isValid()
            and 0 <= index.row() < len(self._role_values)
        ):
            self._role_values[index.row()][int(role)] = value
            self.dataChanged.emit(index, index, [int(role)])
            return True
        return super().setData(index, value, role)

    def set_suggestions(self, suggestions: list[TagSuggestion], palette: QPalette) -> None:
        self._suggestions = list(suggestions)
        self._palette = QPalette(palette)
        populate_suggestion_model(
            self, suggestions, self._palette, detailed=self.detailed
        )

    def suggestion(self, row: int) -> TagSuggestion | None:
        return self._suggestions[row] if 0 <= row < len(self._suggestions) else None

    def suggestion_for_completion(self, value: str) -> TagSuggestion | None:
        return next(
            (item for item in self._suggestions if item.canonical_name == value), None
        )

    def refresh_palette(self, palette: QPalette) -> None:
        self._palette = QPalette(palette)
        refresh_suggestion_model(self, self._palette)
