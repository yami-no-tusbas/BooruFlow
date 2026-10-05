from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QCompleter

from booruflow.application.tag_autocomplete import TagSuggestion
from booruflow.presentation.pyside6.tag_presentation import (
    CANONICAL_TAG_ROLE,
    TAG_ALIAS_SOURCE_ROLE,
    TAG_CATEGORY_ROLE,
    TAG_COMPLETER_POPUP_STYLE,
    TAG_NAME_ROLE,
    TAG_POST_COUNT_ROLE,
    TagSuggestionListModel,
    chip_display_text,
    configure_tag_completer,
    tag_visual_style,
)


def _palette(window: str) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(window))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff" if window == "#ffffff" else "#303134"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#202124" if window == "#ffffff" else "#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#777777"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#1a73e8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return palette


def test_shared_truncation_contract() -> None:
    assert chip_display_text("123456789012345") == "123456789012345"
    assert chip_display_text("1234567890123456") == "123456789012345..."


def test_gelbooru_exact_category_colors_and_neutral_general() -> None:
    palette = _palette("#ffffff")
    assert tag_visual_style("gelbooru", "character", palette).color(palette).name() == "#00aa00"
    assert tag_visual_style("gelbooru", "copyright", palette).color(palette).name() == "#dd00dd"
    assert tag_visual_style("gelbooru", "artist", palette).color(palette).name() == "#cc0000"
    assert tag_visual_style("gelbooru", "general", palette).color(palette).name() == "#202124"


def test_e621_normal_and_alternate_profiles() -> None:
    light = _palette("#ffffff")
    dark = _palette("#101010")
    expected = {
        "general": ("#202124", "#f1f3f4"),
        "artist": ("#f2ac08", "#fbd67f"),
        "contributor": ("#c0c0c0", "#71706e"),
        "copyright": ("#dd00dd", "#ff5eff"),
        "character": ("#00aa00", "#2bff2b"),
        "species": ("#ed5d1f", "#f6b295"),
        "invalid": ("#ff3d3d", "#ff8a8a"),
        "meta": ("#ffffff", "#666666"),
        "lore": ("#228822", "#5fdb5f"),
        "pool": ("#f5deb3", "#d0b27a"),
    }
    for category, (normal, alternate) in expected.items():
        assert tag_visual_style("e621", category, light).color(light).name() == normal
        assert tag_visual_style("e621", category, dark).color(dark).name() == alternate


def test_structured_suggestion_roles_separate_popup_text_from_completion_value() -> None:
    app = QApplication.instance() or QApplication([])
    model = TagSuggestionListModel(detailed=True)
    suggestion = TagSuggestion(
        "gelbooru", "pokemon", "pokemon", "copyright", 533_357, "pocket_monsters"
    )
    model.set_suggestions([suggestion], _palette("#ffffff"))
    index = model.index(0, 0)

    assert model.stringList() == [
        "pocket_monsters → pokemon — copyright · 533,357 posts"
    ]
    assert index.data(TAG_NAME_ROLE) == "pokemon"
    assert index.data(CANONICAL_TAG_ROLE) == "pokemon"
    assert index.data(TAG_CATEGORY_ROLE) == "copyright"
    assert index.data(TAG_POST_COUNT_ROLE) == 533_357
    assert index.data(TAG_ALIAS_SOURCE_ROLE) == "pocket_monsters"
    assert index.data(Qt.ItemDataRole.ForegroundRole).color().name() == "#dd00dd"

    completer = QCompleter(model)
    configure_tag_completer(completer)
    assert completer.completionRole() == Qt.ItemDataRole.DisplayRole
    assert app is not None


def test_neutral_color_refresh_and_selected_text_follow_live_palette() -> None:
    model = TagSuggestionListModel()
    light = _palette("#ffffff")
    dark = _palette("#101010")
    model.set_suggestions(
        [TagSuggestion("gelbooru", "solo", "solo", "general", 100)], light
    )
    index = model.index(0, 0)
    assert index.data(Qt.ItemDataRole.ForegroundRole).color() == light.color(
        QPalette.ColorRole.Text
    )
    model.refresh_palette(dark)
    assert index.data(Qt.ItemDataRole.ForegroundRole).color() == dark.color(
        QPalette.ColorRole.Text
    )

    completer = QCompleter(model)
    configure_tag_completer(completer)
    assert "palette(highlighted-text)" in TAG_COMPLETER_POPUP_STYLE
    assert "palette(highlight)" in TAG_COMPLETER_POPUP_STYLE
