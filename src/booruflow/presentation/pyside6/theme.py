"""Live palette refresh helpers for already-instantiated Qt widgets."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

# Windows 11's native item style can keep Text when painting a selected cell.
# Pair these roles explicitly, including selection without keyboard focus.
ITEM_VIEW_SELECTION_STYLE = (
    "QAbstractItemView {color: palette(text); background-color: palette(base);"
    "alternate-background-color: palette(alternate-base);}"
    "QAbstractItemView::item:selected {"
    "color: palette(highlighted-text); background-color: palette(highlight);"
    "}"
)


def style_item_view(view) -> None:
    if ITEM_VIEW_SELECTION_STYLE not in view.styleSheet():
        view.setStyleSheet(view.styleSheet() + ITEM_VIEW_SELECTION_STYLE)


def refresh_widget_tree(root: QWidget) -> None:
    """Re-resolve local QSS and custom palette assets without rebuilding pages."""
    widgets = (root, *root.findChildren(QWidget))
    for widget in widgets:
        # Qt updates ordinary widget palettes automatically, but palette(...)
        # values inside an already-polished local stylesheet remain cached.
        if widget.styleSheet():
            stylesheet = widget.styleSheet()
            widget.setStyleSheet("")
            widget.setStyleSheet(stylesheet)
    # Rebuild palette-derived assets after every parent/child QSS palette has
    # settled; retain all existing widgets, models, selections and scrollbars.
    for widget in widgets:
        refresh = getattr(widget, "refresh_theme", None)
        if widget is not root and callable(refresh):
            refresh()
        widget.update()
