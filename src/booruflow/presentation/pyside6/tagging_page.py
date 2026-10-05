"""Primary Tagging presentation entry point.

The current implementation deliberately inherits the proven review screen while
the new workflow is introduced here incrementally.  The frozen implementation
itself lives in :mod:`tagging_legacy_page` and remains available in navigation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from textwrap import wrap
from time import perf_counter

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.batch_publisher import MAX_PUBLISH_ATTEMPTS
from booruflow.application.tag_autocomplete import TagSuggestion, parse_autocomplete_text
from booruflow.application.tag_lookup import AUTOCOMPLETE_MIN_CHARS
from booruflow.application.tagging import (
    TaggingRequest,
    canonical_tag_value,
    parse_review_row_token,
)
from booruflow.application.targeted_wd14 import CONFIDENCE_BUCKETS, confidence_bucket
from booruflow.domain.booru_sites import site_definition
from booruflow.domain.publication_retry import can_retry_publication
from booruflow.presentation.pyside6.context_actions import (
    add_action,
    bind_tag_popup,
    exec_menu,
    post_actions,
    tag_actions,
)
from booruflow.presentation.pyside6.process_memory import format_bytes
from booruflow.presentation.pyside6.status_bar import PageStatus
from booruflow.presentation.pyside6.tag_presentation import (
    CANONICAL_TAG_ROLE,
    CHIP_TEXT_LIMIT,
    TAG_CATEGORY_ROLE,
    TagSuggestionListModel,
    chip_display_text,
    configure_tag_completer,
    normalized_tag_category,
    populate_suggestion_model,
    refresh_suggestion_model,
    semantic_chip_stylesheet,
    show_tag_completer,
    suggestion_display_text,
)
from booruflow.presentation.pyside6.tagging_legacy_page import TaggingLegacyPage
from booruflow.presentation.pyside6.tagging_suggestions import SuggestionTable
from booruflow.presentation.pyside6.thumbnail_cache import MAX_ADAPTIVE_THUMBNAIL_CACHE_BYTES
from booruflow.presentation.pyside6.ui_components import DataTable


def compact_result_card_text(
    post_id: int,
    tag_count: int,
    status: str = "",
    addition_count: int = 0,
    removal_count: int = 0,
) -> tuple[str, str]:
    first = f"{status} · #{post_id}" if status else f"#{post_id}"
    deltas = []
    if addition_count:
        deltas.append(f"+{addition_count}")
    if removal_count:
        deltas.append(f"-{removal_count}")
    second = f"{tag_count} tags"
    if deltas:
        second += " · " + " / ".join(deltas)
    return first, second


def queued_result_entry(entry: dict[str, object]) -> bool:
    return bool(entry.get("post_id"))


def natural_text_key(value: object) -> tuple[tuple[int, object], ...]:
    """Case-insensitive natural key whose components always remain comparable."""
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", str(value or ""))
    )


def post_artist_names(post: dict) -> tuple[str, ...]:
    """Return only artist information already present on a search result."""
    raw = (
        post.get("artist_tags")
        or post.get("tag_string_artist")
        or post.get("artists")
        or post.get("artist")
        or ()
    )
    if isinstance(raw, str):
        artists = raw.replace(",", " ").split()
    else:
        artists = [str(value) for value in raw]
    if not artists:
        categories = post.get("_tag_categories", {})
        if isinstance(categories, dict):
            artists = [
                str(tag) for tag, category in categories.items()
                if category in {1, "1", "artist"}
            ]
    return tuple(sorted(dict.fromkeys(artists), key=natural_text_key))


def post_tag_names(post: dict) -> tuple[str, ...]:
    raw = post.get("tags", post.get("tag_string", "")) or ""
    values = raw.split() if isinstance(raw, str) else [str(value) for value in raw]
    return tuple(sorted(dict.fromkeys(values), key=natural_text_key))


def existing_tags_tooltip(post: dict, *, max_tags: int = 40, max_chars: int = 700) -> str:
    """Present only tags already included in the search result."""
    raw = post.get("tags") or ""
    tags = raw.split() if isinstance(raw, str) else list(raw)
    shown: list[str] = []
    for tag in tags:
        candidate = ", ".join((*shown, str(tag)))
        if len(shown) >= max_tags or len(candidate) > max_chars:
            break
        shown.append(str(tag))
    content = ", ".join(shown)
    if len(shown) < len(tags):
        content += ", …" if content else "…"
    return "\n".join(wrap(content, width=80, break_long_words=False))


def derived_tagging_thresholds(minimum: int, maximum: int) -> tuple[int, int]:
    """Derive inclusive presentation bands inside the selected tag-count range."""
    minimum = max(0, int(minimum))
    maximum = max(minimum, int(maximum))
    critical = min(maximum, max(minimum, 5, round(maximum * 0.01)))
    high = min(maximum, max(critical, 8, round(maximum * 0.02)))
    return critical, high


def _setting_int(
    settings: dict[str, object], key: str, default: int, lower: int, upper: int
) -> int:
    try:
        value = int(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    return min(upper, max(lower, value))


def _normalized_chip_category(category: str | None, site: str = "gelbooru") -> str:
    """Compatibility wrapper while widgets migrate to the shared presenter."""
    return normalized_tag_category(category, site)


class TokenLineEdit(QLineEdit):
    autocomplete_commit_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            completer = self.completer()
            if completer is not None and completer.popup().isVisible():
                self.autocomplete_commit_requested.emit()
                event.accept()
                return
        if event.key() == Qt.Key.Key_Backspace and not self.text():
            # Backspace edits this field only. Chips have explicit remove buttons.
            event.accept()
            return
        super().keyPressEvent(event)


class FlowLayout(QLayout):
    """Small height-for-width layout for responsive tag-chip rows."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, max(0, width), 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = max(0, self.spacing())
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if line_height and next_x > effective.right() + 1:
                x = effective.x()
                y += line_height + spacing
                next_x = x + hint.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x + spacing
            line_height = max(line_height, hint.height())
        return max(0, y + line_height - rect.y() + margins.bottom())


class ChipFlowWidget(QWidget):
    """Expose a flow layout's wrapped height to a containing scroll area."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.single_line = False
        self._extent_timer = QTimer(self)
        self._extent_timer.setSingleShot(True)
        self._extent_timer.timeout.connect(self._update_scroll_extent)

    def schedule_scroll_extent(self) -> None:
        # Wait until newly inserted chips have been shown and laid out by Qt.
        self._extent_timer.start(0)

    def hasHeightForWidth(self) -> bool:
        return not self.single_line

    def heightForWidth(self, width: int) -> int:
        layout = self.layout()
        if self.single_line:
            return layout.sizeHint().height()
        return layout.heightForWidth(max(1, width)) if layout is not None else 0

    def sizeHint(self) -> QSize:
        if self.single_line:
            return self.layout().sizeHint()
        width = max(1, self.width())
        return QSize(0, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _update_scroll_extent(self) -> None:
        if self.single_line:
            self.setMinimumWidth(self.layout().sizeHint().width())
        # QScrollArea otherwise keeps the resizable content at viewport height.
        height = self.heightForWidth(max(1, self.width()))
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scroll_extent()

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:
            self.schedule_scroll_extent()
        return result


class TagTokenEditor(QWidget):
    """Compact autocomplete entry that commits validated text as removable chips."""

    tags_changed = Signal()
    lookup_requested = Signal(str)
    tag_added = Signal(str)
    analysis_requested = Signal(str)
    analysis_toggled = Signal(str, bool)
    CHIP_TEXT_LIMIT = CHIP_TEXT_LIMIT
    CHIP_LABEL_MAX_WIDTH = 110

    def __init__(
        self,
        parent=None,
        *,
        confidence_actions: bool = False,
        chip_role: str = "user",
        site: str = "gelbooru",
        mixed_operations: bool = False,
        expand_chips: bool = False,
        active_tooltip: str = "Inclure ce tag dans le classement WD14",
        single_line: bool = False,
    ) -> None:
        super().__init__(parent)
        self.site = site
        self.confidence_actions = confidence_actions
        self.chip_role = chip_role
        self.mixed_operations = mixed_operations
        self.expand_chips = expand_chips
        self.single_line = single_line
        self.active_tooltip = active_tooltip
        self._tags: list[str] = []
        self._operations: dict[str, str] = {}
        self._chips: dict[str, QPushButton] = {}
        self._suggestion_values: dict[str, str] = {}
        self._suggestion_categories: dict[str, str] = {}
        self._all_suggestions: list[str | tuple[str, str | None] | tuple[str, str | None, str]] = []
        self.editor_layout = QVBoxLayout(self)
        self.editor_layout.setContentsMargins(4, 2, 4, 2)
        self.editor_layout.setSpacing(4)
        self.chips = ChipFlowWidget()
        self.chips.single_line = single_line
        self.chips.setMinimumWidth(0)
        self.chips.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.chips_layout = QHBoxLayout(self.chips) if single_line else FlowLayout(self.chips)
        if single_line:
            self.chips_layout.setContentsMargins(0, 0, 0, 0)
            self.chips_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.chips.setVisible(expand_chips)
        self.chips_scroll = QScrollArea()
        self.chips_scroll.setWidgetResizable(True)
        self.chips_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chips_scroll.setMinimumHeight(46)
        if not expand_chips:
            self.chips_scroll.setMaximumHeight(150)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.chips_scroll.setWidget(self.chips)
        self.chips.setVisible(expand_chips)
        self.chips_scroll.setVisible(expand_chips)
        self.editor_layout.addWidget(self.chips_scroll, 1)
        if single_line:
            self.setFixedHeight(self.fontMetrics().height() + 38)
            self.chips_scroll.setMinimumHeight(0)
            self.chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.chips_scroll.show()
            self.chips.show()
        self.input = TokenLineEdit()
        self.input.setMinimumWidth(120)
        self.editor_layout.addWidget(self.input)
        self.model = TagSuggestionListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        configure_tag_completer(self.completer)
        self.input.setCompleter(self.completer)
        self.lookup_timer = QTimer(self)
        self.lookup_timer.setSingleShot(True)
        self.lookup_timer.setInterval(200)
        self.lookup_timer.timeout.connect(self._emit_lookup)
        self.input.textEdited.connect(self._schedule_lookup)
        self.input.returnPressed.connect(self.commit_input)
        self.input.autocomplete_commit_requested.connect(self._commit_current_suggestion)
        self.completer.activated[QModelIndex].connect(self._commit_suggestion_index)
        self.refresh_theme()

    def tags(self) -> list[str]:
        return list(self._tags)

    def set_context_actions(self, catalog, launcher, entity):
        self._context_catalog, self._context_launcher, self._context_entity = catalog, launcher, entity
        for tag, chip in self._chips.items():
            self._bind_chip_menu(tag, chip)

    def _bind_chip_menu(self, tag, chip):
        if not hasattr(self, "_context_catalog") or chip.property("tagMenuBound"):
            return
        chip.setProperty("tagMenuBound", True)
        def show(position, target):
            menu = QMenu(chip)
            handlers = tag_actions(menu, self._context_catalog, tag, site=self.site,
                                   category=chip.property("chipCategory"), launcher=self._context_launcher,
                                   entity=self._context_entity)
            menu.addSeparator()
            add_action(menu, self._context_catalog.text("context.remove_chip"),
                       lambda: self.remove_tag(tag), handlers)
            exec_menu(menu, target.mapToGlobal(position), handlers)
        for target in (chip, *chip.findChildren(QWidget)):
            target.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            target.customContextMenuRequested.connect(lambda pos, target=target: show(pos, target))

    def add_tag(
        self,
        value: str,
        category: str | None = None,
        *,
        operation: str | None = None,
        active: bool = True,
        emit: bool = True,
    ) -> bool:
        normalized = canonical_tag_value(value).replace(" ", "_")
        if not normalized or normalized in self._tags:
            return False
        operation = "remove" if operation in {"-", "remove"} else "add"
        self._tags.append(normalized)
        self._operations[normalized] = operation
        chip = QFrame()
        chip.setProperty("tagChip", True)
        chip.setProperty(
            "chipOperation", operation if self.mixed_operations else self.chip_role
        )
        chip.setProperty("chipCategory", _normalized_chip_category(category, self.site))
        chip.setProperty("chipActive", True)
        chip.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        chip_row = QHBoxLayout(chip); chip_row.setContentsMargins(3, 0, 3, 0); chip_row.setSpacing(2)
        chip.label = QLabel(chip)
        # Restored chips already exist when ScrollablePageHost adapts the page.
        # Keep the same label sizing policy as chips added after installation.
        chip.label.setProperty("preserveHorizontalSize", True)
        chip.label.setMaximumWidth(self.CHIP_LABEL_MAX_WIDTH)
        self._hydrate_chip_presentation(chip, normalized)
        chip.active = QCheckBox()
        chip.active.setChecked(bool(active))
        chip.active.setToolTip(self.active_tooltip)
        if not self.mixed_operations:
            chip.active.toggled.connect(
                lambda checked, tag=normalized: self.analysis_toggled.emit(tag, checked)
            )
        chip.active.toggled.connect(
            lambda checked, value=chip: self._refresh_chip_state(value, checked)
        )
        chip.active.toggled.connect(lambda _checked: self.tags_changed.emit())
        chip_row.addWidget(chip.active)
        chip.operation = QToolButton()
        chip.operation.setAutoRaise(True)
        chip.operation.setFixedWidth(24)
        chip.operation.setVisible(self.mixed_operations)
        chip.operation.clicked.connect(
            lambda _checked=False, tag=normalized: self.toggle_operation(tag)
        )
        chip_row.addWidget(chip.operation)
        chip_row.addWidget(chip.label)
        chip.analysis = QPushButton("?"); chip.analysis.setFlat(True); chip.analysis.setFixedWidth(22)
        chip.analysis.setVisible(self.confidence_actions)
        chip.analysis.clicked.connect(
            lambda _checked=False, tag=normalized: self.analysis_requested.emit(tag)
        )
        chip_row.addWidget(chip.analysis)
        chip.remove = QPushButton("×"); chip.remove.setFlat(True); chip.remove.setFixedWidth(22)
        chip.remove.clicked.connect(lambda _checked=False, tag=normalized: self.remove_tag(tag))
        chip.click = chip.remove.click
        chip_row.addWidget(chip.remove)
        self._chips[normalized] = chip
        self._bind_chip_menu(normalized, chip)
        chip_row.activate()
        self.chips_layout.addWidget(chip)
        self._refresh_chip_presentation(normalized)
        self._refresh_operation_chip(normalized)
        self._refresh_chip_state(chip, bool(active))
        chip_row.activate()
        chip.adjustSize()
        chip.ensurePolished()
        chip.show()
        self.chips_scroll.show()
        self.chips.show()
        self.chips_layout.invalidate()
        self.chips.schedule_scroll_extent()
        self.chips.updateGeometry()
        self.updateGeometry()
        self.input.clear(); self._refresh_suggestions()
        if emit:
            self.tag_added.emit(normalized); self.tags_changed.emit()
        return True

    def _refresh_chip_presentation(self, tag: str) -> None:
        """Hydrate visible chip text after the chip belongs to the flow layout."""
        chip = self._chips.get(tag)
        if chip is None:
            return
        self._hydrate_chip_presentation(chip, tag)

    def _hydrate_chip_presentation(self, chip: QWidget, tag: str) -> None:
        """Apply canonical presentation and invalidate every cached layout hint."""
        chip.label.setText(chip_display_text(tag))
        chip.label.setToolTip(tag)
        chip.label.setMinimumWidth(
            min(chip.label.sizeHint().width(), self.CHIP_LABEL_MAX_WIDTH)
        )
        chip.label.setVisible(True)
        chip.label.ensurePolished()
        chip.label.updateGeometry()
        layout = chip.layout()
        if layout is not None:
            layout.invalidate()
        chip.updateGeometry()
        self.chips_layout.invalidate()
        self.chips.updateGeometry()

    def _refresh_operation_chip(self, tag: str) -> None:
        chip = self._chips.get(tag)
        if chip is None:
            return
        operation = self._operations.get(tag, "add")
        chip.operation.setText("+" if operation == "add" else "−")
        chip.operation.setToolTip(
            "Ajouter ce tag" if operation == "add" else "Retirer ce tag"
        )
        chip.setProperty(
            "chipOperation", operation if self.mixed_operations else self.chip_role
        )
        chip.analysis.setVisible(self.confidence_actions and operation == "add")
        chip.style().unpolish(chip)
        chip.style().polish(chip)

    def toggle_operation(self, tag: str) -> None:
        if tag not in self._operations:
            return
        self._operations[tag] = "remove" if self._operations[tag] == "add" else "add"
        self._refresh_operation_chip(tag)
        self.tags_changed.emit()

    def operation(self, tag: str) -> str:
        return self._operations.get(tag, "add")

    def entries(self) -> list[dict[str, object]]:
        return [
            {
                "tag": tag,
                "mode": "+" if self.operation(tag) == "add" else "-",
                "checked": self._chips[tag].active.isChecked(),
                "category": str(self._chips[tag].property("chipCategory") or "unknown"),
            }
            for tag in self._tags
        ]

    def _refresh_chip_state(self, chip: QWidget, active: bool) -> None:
        chip.setProperty("chipActive", bool(active))
        chip.style().unpolish(chip)
        chip.style().polish(chip)
        chip.update()

    def refresh_theme(self) -> None:
        self.setStyleSheet(semantic_chip_stylesheet(self.palette(), self.site))
        for tag in self._tags:
            self._refresh_chip_presentation(tag)
        refresh_suggestion_model(self.model, self.palette())

    def set_site(self, site: str) -> None:
        if site == self.site:
            return
        self.site = site
        self.set_suggestions([])
        for chip in self._chips.values():
            chip.setProperty("chipCategory", "invalid")
            chip.style().unpolish(chip)
            chip.style().polish(chip)
        self.refresh_theme()

    def set_analysis_state(self, tag: str, state: str) -> None:
        chip = self._chips.get(tag)
        if chip is None or not self.confidence_actions:
            return
        symbols = {"idle": "?", "running": "⏳", "complete": "✓", "failed": "!", "cancelled": "—"}
        chip.analysis.setText(symbols.get(state, "?"))
        chip.analysis.setEnabled(state != "running")

    def active_tags(self, operation: str | None = None) -> list[str]:
        normalized_operation = (
            "remove" if operation in {"-", "remove"} else "add"
            if operation is not None
            else None
        )
        return [
            tag for tag in self._tags
            if self._chips[tag].active.isChecked()
            and (normalized_operation is None or self.operation(tag) == normalized_operation)
        ]

    def set_tag_active(self, tag: str, active: bool) -> None:
        chip = self._chips.get(tag)
        if chip is not None and chip.active.isChecked() != bool(active):
            chip.active.setChecked(bool(active))

    def remove_tag(self, value: str) -> bool:
        if value not in self._tags:
            return False
        self._tags.remove(value)
        self._operations.pop(value, None)
        chip = self._chips.pop(value)
        self.chips_layout.removeWidget(chip)
        chip.hide()
        chip.setParent(None)
        chip.deleteLater()
        self.chips_scroll.setVisible(self.single_line or self.expand_chips or bool(self._tags))
        self.chips.setVisible(self.single_line or self.expand_chips or bool(self._tags))
        self.chips_layout.invalidate()
        self.chips.schedule_scroll_extent()
        self.chips.updateGeometry()
        self.updateGeometry()
        self._refresh_suggestions()
        self.tags_changed.emit()
        return True

    def remove_last(self) -> None:
        if self._tags:
            self.remove_tag(self._tags[-1])

    def commit_input(self) -> None:
        self._commit_suggestion(self.input.text())

    def _commit_current_suggestion(self) -> None:
        index = self.completer.popup().currentIndex()
        value = (
            str(index.data(CANONICAL_TAG_ROLE) or "")
            if index.isValid()
            else self.completer.currentCompletion()
        )
        if value:
            self._commit_suggestion(value)
        else:
            self.commit_input()

    def _commit_suggestion_index(self, index: QModelIndex) -> None:
        value = str(index.data(CANONICAL_TAG_ROLE) or "")
        if value:
            self._suggestion_values[value] = value
            self._suggestion_categories[value] = _normalized_chip_category(
                index.data(TAG_CATEGORY_ROLE), self.site
            )
            self._commit_suggestion(value)

    def set_suggestions(
        self,
        suggestions: list[
            TagSuggestion | str | tuple[str, str | None] | tuple[str, str | None, str]
        ],
    ) -> None:
        self._all_suggestions = list(suggestions)
        self._refresh_suggestions()

    def _refresh_suggestions(self) -> None:
        labels: list[str] = []
        structured: list[TagSuggestion] = []
        self._suggestion_values = {}
        self._suggestion_categories = {}
        present = {canonical_tag_value(tag).replace(" ", "_") for tag in self._tags}
        for suggestion in self._all_suggestions:
            if isinstance(suggestion, TagSuggestion):
                value = suggestion.canonical_name
                alias = suggestion.alias_source
                category = suggestion.category
                structured_item = suggestion
            elif isinstance(suggestion, str):
                value, alias, category = suggestion, None, "unknown"
                structured_item = TagSuggestion(self.site, value, value, category, 0)
            elif len(suggestion) == 2:
                value, alias = suggestion
                category = "unknown"
                structured_item = TagSuggestion(self.site, value, value, category, 0, alias)
            else:
                value, alias, category = suggestion
                structured_item = TagSuggestion(self.site, value, value, category, 0, alias)
            canonical = canonical_tag_value(value).replace(" ", "_")
            if canonical in present:
                continue
            label = suggestion_display_text(structured_item)
            labels.append(label)
            structured.append(structured_item)
            self._suggestion_values[label] = value
            self._suggestion_values[value] = value
            if alias:
                self._suggestion_values[f"{value} ← {alias}"] = value
            self._suggestion_categories[label] = _normalized_chip_category(category, self.site)
            self._suggestion_categories[value] = _normalized_chip_category(category, self.site)
            if alias:
                self._suggestion_categories[f"{value} ← {alias}"] = _normalized_chip_category(
                    category, self.site
                )
        populate_suggestion_model(self.model, structured, self.palette())
        if labels and self.input.hasFocus():
            show_tag_completer(self.completer)

    def _commit_suggestion(self, label: str) -> None:
        value = self._suggestion_values.get(label, label)
        category = self._suggestion_categories.get(label)
        if category is None:
            canonical = canonical_tag_value(value).replace(" ", "_")
            for suggestion_label, suggestion_value in self._suggestion_values.items():
                if canonical_tag_value(suggestion_value).replace(" ", "_") == canonical:
                    category = self._suggestion_categories.get(suggestion_label)
                    break
        if self.add_tag(value, category):
            self.completer.popup().hide()
            # QLineEdit may insert a clicked completion after activated is emitted.
            QTimer.singleShot(0, self.input.clear)

    def _schedule_lookup(self, value: str) -> None:
        self.lookup_timer.stop()
        if len(parse_autocomplete_text(value).text) < AUTOCOMPLETE_MIN_CHARS:
            self.model.setStringList([])
            return
        self.lookup_timer.start()

    def _emit_lookup(self) -> None:
        value = self.input.text().strip()
        if len(parse_autocomplete_text(value).text) >= AUTOCOMPLETE_MIN_CHARS:
            self.lookup_requested.emit(value)


@dataclass(slots=True)
class SearchToken:
    """One ordered search fragment, kept lossless until a local exact match exists."""

    token_id: int
    kind: str
    value: str
    negative: bool = False
    category: str = "unknown"

    def serialize(self) -> str:
        if self.kind == "known_tag":
            return f"{'-' if self.negative else ''}{self.value}"
        return self.value


class SearchEntryLineEdit(TokenLineEdit):
    """Line edit that lets the token editor own multi-token paste and separators."""

    paste_requested = Signal(str)
    separator_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Paste):
            value = QApplication.clipboard().text()
            if any(character.isspace() for character in value):
                self.paste_requested.emit(value)
                event.accept()
                return
        completer = self.completer()
        if (
            event.key() == Qt.Key.Key_Space
            and (completer is None or not completer.popup().isVisible())
        ):
            self.separator_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SearchQueryEditor(QWidget):
    """Bounded hybrid editor for ordered known-tag chips and editable raw tokens."""

    TOKEN_VIEW_HEIGHT = 58
    AUTOCOMPLETE_MIN_WIDTH = 280

    lookup_requested = Signal(str)
    resolve_requested = Signal(int, object)
    query_changed = Signal(str)
    entity_profile_requested = Signal(str, str)

    def __init__(
        self, catalog, value: str = "", parent=None, *, site: str = "gelbooru"
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.site = site
        self._tokens: list[SearchToken] = []
        self._next_token_id = 1
        self._resolution_generation = 0
        self._suggestion_values: dict[str, str] = {}
        self._suggestion_categories: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("search_query_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setMinimumHeight(0)
        self.scroll.setMaximumHeight(self.TOKEN_VIEW_HEIGHT)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.flow_widget = ChipFlowWidget()
        self.flow_widget.setMinimumWidth(0)
        self.flow_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )
        self.flow_layout = FlowLayout(self.flow_widget, spacing=4)
        self.scroll.setWidget(self.flow_widget)
        root.addWidget(self.scroll)

        self.input = SearchEntryLineEdit()
        self.input.setMinimumWidth(150)
        self.input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(self.input)
        self.model = TagSuggestionListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        configure_tag_completer(self.completer)
        self.input.setCompleter(self.completer)
        self.lookup_timer = QTimer(self)
        self.lookup_timer.setSingleShot(True)
        self.lookup_timer.setInterval(200)
        self.lookup_timer.timeout.connect(self._emit_lookup)
        self.input.textEdited.connect(self._schedule_lookup)
        self.input.separator_requested.connect(self.commit_raw_input)
        self.input.returnPressed.connect(self.commit_raw_input)
        self.input.autocomplete_commit_requested.connect(self._commit_current_suggestion)
        self.input.paste_requested.connect(self.paste_query)
        self.completer.activated[QModelIndex].connect(self._commit_suggestion_index)
        self.setFocusProxy(self.input)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_query_menu)
        self.input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.input.customContextMenuRequested.connect(
            lambda position: self._show_input_menu(self.input, position)
        )
        self.setText(value)
        self.retranslate()
        self.refresh_theme()

    def tokens(self) -> list[SearchToken]:
        return [
            SearchToken(token.token_id, token.kind, token.value, token.negative, token.category)
            for token in self._tokens
        ]

    def text(self) -> str:
        return self.serialize()

    def serialize(self) -> str:
        values = [token.serialize().strip() for token in self._tokens]
        values.extend(self.input.text().split())
        return " ".join(value for value in values if value)

    def setText(self, value: str) -> None:
        self._tokens = [self._raw_token(token) for token in str(value).split()]
        self.input.clear()
        self._rebuild()
        self.request_resolution()
        self._emit_changed()

    def clear(self) -> None:
        self.setText("")

    def lookup_text(self) -> str:
        value = self.input.text().strip()
        return value.removeprefix("-")

    def request_resolution(self) -> None:
        self._resolution_generation += 1
        candidates = []
        for token in self._tokens:
            candidate = self._candidate(token.value)
            if token.kind == "raw" and candidate:
                candidates.append(candidate)
        self.resolve_requested.emit(self._resolution_generation, list(dict.fromkeys(candidates)))

    def apply_known_tokens(
        self, generation: int, known: dict[str, tuple[str, str]]
    ) -> None:
        if generation != self._resolution_generation:
            return
        changed = False
        normalized_known = {str(key).casefold(): value for key, value in known.items()}
        for token in self._tokens:
            if token.kind != "raw":
                continue
            candidate = self._candidate(token.value)
            match = normalized_known.get(candidate.casefold()) if candidate else None
            if match is None:
                continue
            negative = token.value.startswith("-") and not token.value.startswith("--")
            name, category = match
            token.kind = "known_tag"
            token.value = name
            token.negative = negative
            token.category = _normalized_chip_category(category, self.site)
            changed = True
        if changed:
            self._rebuild()
            self._emit_changed()

    def _raw_token(self, value: str) -> SearchToken:
        token = SearchToken(self._next_token_id, "raw", value)
        self._next_token_id += 1
        return token

    @staticmethod
    def _candidate(raw: str) -> str:
        value = raw[1:] if raw.startswith("-") and not raw.startswith("--") else raw
        if not value or any(character in value for character in ":<>*?"):
            return ""
        return value

    def paste_query(self, value: str) -> None:
        prefix = self.input.text()
        combined = f"{prefix}{value}"
        self.input.clear()
        self._tokens.extend(self._raw_token(token) for token in combined.split())
        self._rebuild()
        self.request_resolution()
        self._emit_changed()

    def commit_raw_input(self) -> None:
        value = self.input.text().strip()
        if not value:
            return
        self._tokens.append(self._raw_token(value))
        self.input.clear()
        self._rebuild()
        self.request_resolution()
        self._emit_changed()

    def set_suggestions(
        self,
        suggestions: list[
            TagSuggestion | str | tuple[str, str | None] | tuple[str, str | None, str]
        ],
    ) -> None:
        labels: list[str] = []
        structured: list[TagSuggestion] = []
        self._suggestion_values = {}
        self._suggestion_categories = {}
        for suggestion in suggestions:
            if isinstance(suggestion, TagSuggestion):
                value = suggestion.canonical_name
                alias = suggestion.alias_source
                category = suggestion.category
                structured_item = suggestion
            elif isinstance(suggestion, str):
                value, alias, category = suggestion, None, "unknown"
                structured_item = TagSuggestion(self.site, value, value, category, 0)
            elif len(suggestion) == 2:
                value, alias = suggestion
                category = "unknown"
                structured_item = TagSuggestion(self.site, value, value, category, 0, alias)
            else:
                value, alias, category = suggestion
                structured_item = TagSuggestion(self.site, value, value, category, 0, alias)
            label = suggestion_display_text(structured_item)
            labels.append(label)
            structured.append(structured_item)
            self._suggestion_values[label] = value
            self._suggestion_values[value] = value
            if alias:
                self._suggestion_values[f"{value} ← {alias}"] = value
            self._suggestion_categories[label] = _normalized_chip_category(category, self.site)
            self._suggestion_categories[value] = _normalized_chip_category(category, self.site)
            if alias:
                self._suggestion_categories[f"{value} ← {alias}"] = _normalized_chip_category(
                    category, self.site
                )
        populate_suggestion_model(self.model, structured, self.palette())
        if labels and self.input.hasFocus():
            self._update_autocomplete_width()
            show_tag_completer(self.completer)

    def _commit_current_suggestion(self) -> None:
        index = self.completer.popup().currentIndex()
        value = (
            str(index.data(CANONICAL_TAG_ROLE) or "")
            if index.isValid()
            else self.completer.currentCompletion()
        )
        if value:
            self._commit_suggestion(value)
        else:
            self.commit_raw_input()

    def _commit_suggestion_index(self, index: QModelIndex) -> None:
        value = str(index.data(CANONICAL_TAG_ROLE) or "")
        if not value:
            return
        self._suggestion_values[value] = value
        self._suggestion_categories[value] = _normalized_chip_category(
            index.data(TAG_CATEGORY_ROLE), self.site
        )
        self._commit_suggestion(value)

    def _commit_suggestion(self, label: str) -> None:
        value = self._suggestion_values.get(label)
        if value is None:
            self.commit_raw_input()
            return
        negative = self.input.text().strip().startswith("-")
        token = SearchToken(
            self._next_token_id,
            "known_tag",
            value,
            negative,
            self._suggestion_categories.get(label, "unknown"),
        )
        self._next_token_id += 1
        self._tokens.append(token)
        self.input.clear()
        self.completer.popup().hide()
        self._rebuild()
        self._emit_changed()
        QTimer.singleShot(0, self.input.clear)

    def _schedule_lookup(self, _value: str) -> None:
        self.lookup_timer.stop()
        value = self.lookup_text()
        if len(parse_autocomplete_text(value).text) < AUTOCOMPLETE_MIN_CHARS:
            self.model.setStringList([])
            return
        self.lookup_timer.start()

    def _emit_lookup(self) -> None:
        value = self.lookup_text()
        if len(parse_autocomplete_text(value).text) >= AUTOCOMPLETE_MIN_CHARS:
            self.lookup_requested.emit(value)

    def toggle_token(self, token_id: int) -> None:
        token = next((item for item in self._tokens if item.token_id == token_id), None)
        if token is None or token.kind != "known_tag":
            return
        token.negative = not token.negative
        self._rebuild()
        self._emit_changed()

    def remove_token(self, token_id: int) -> None:
        self._tokens = [token for token in self._tokens if token.token_id != token_id]
        self._rebuild()
        self._emit_changed()

    def copy_tag(self, token_id: int) -> None:
        token = next((item for item in self._tokens if item.token_id == token_id), None)
        if token is not None and token.kind == "known_tag":
            QApplication.clipboard().setText(token.value)

    def copy_full_query(self) -> None:
        QApplication.clipboard().setText(self.serialize())

    def _show_query_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        action = menu.addAction(self.catalog.text("tagging.search.copy_full_query"))
        action.triggered.connect(self.copy_full_query)
        menu.exec(self.mapToGlobal(position))

    def _show_input_menu(self, editor: QLineEdit, position: QPoint) -> None:
        menu = editor.createStandardContextMenu()
        menu.addSeparator()
        action = menu.addAction(self.catalog.text("tagging.search.copy_full_query"))
        action.triggered.connect(self.copy_full_query)
        menu.exec(editor.mapToGlobal(position))

    def _show_chip_menu(self, token_id: int, chip: QWidget, position: QPoint) -> None:
        menu = QMenu(chip)
        token = next((token for token in self._tokens if token.token_id == token_id), None)
        if token is None:
            return
        handlers = tag_actions(menu, self.catalog, token.value, site=self.site,
                               category=token.category, launcher=getattr(self, "browser_launcher", None),
                               entity=self.entity_profile_requested.emit)
        exec_menu(menu, chip.mapToGlobal(position), handlers)

    def _raw_edited(self, token: SearchToken, editor: QLineEdit, value: str) -> None:
        token.value = value
        self._resize_raw_editor(editor, value)
        self._emit_changed()

    def _resize_raw_editor(self, editor: QLineEdit, value: str) -> None:
        width = editor.fontMetrics().horizontalAdvance(value or " ") + 20
        editor.setFixedWidth(max(42, min(260, width)))

    def _clear_flow(self) -> None:
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is None or widget is self.input:
                continue
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()

    def _rebuild(self) -> None:
        self._clear_flow()
        for token in self._tokens:
            if token.kind == "known_tag":
                widget = self._build_chip(token)
            else:
                widget = SearchEntryLineEdit()
                widget.setText(token.value)
                widget.setToolTip(token.value)
                self._resize_raw_editor(widget, token.value)
                widget.textChanged.connect(
                    lambda value, item=token, editor=widget: self._raw_edited(
                        item, editor, value
                    )
                )
                widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                widget.customContextMenuRequested.connect(
                    lambda position, editor=widget: self._show_input_menu(editor, position)
                )
            self.flow_layout.addWidget(widget)
        self.flow_layout.invalidate()
        self.flow_widget.updateGeometry()
        self.updateGeometry()
        QTimer.singleShot(0, self._update_flow_height)

    def _update_flow_height(self) -> None:
        width = max(1, self.scroll.viewport().width())
        content_height = self.flow_layout.heightForWidth(width)
        self.flow_widget.setMinimumHeight(content_height)
        self.scroll.setMaximumHeight(
            self.TOKEN_VIEW_HEIGHT if self.isVisible() else 0
        )
        self.scroll.updateGeometry()

    def _update_autocomplete_width(self) -> None:
        self.completer.popup().setMinimumWidth(
            max(self.AUTOCOMPLETE_MIN_WIDTH, self.input.width())
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_flow_height)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.scroll.setMaximumHeight(self.TOKEN_VIEW_HEIGHT)
        self._invalidate_ancestor_layouts()
        QTimer.singleShot(0, self._update_flow_height)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.scroll.setMaximumHeight(0)
        self.updateGeometry()
        self._invalidate_ancestor_layouts()

    def _invalidate_ancestor_layouts(self) -> None:
        ancestor = self.parentWidget()
        while ancestor is not None:
            layout = ancestor.layout()
            if layout is not None:
                layout.invalidate()
            ancestor.updateGeometry()
            if isinstance(ancestor, QStackedWidget):
                break
            ancestor = ancestor.parentWidget()

    def _build_chip(self, token: SearchToken) -> QFrame:
        chip = QFrame()
        chip.setProperty("tagChip", True)
        chip.setProperty("searchTokenId", token.token_id)
        chip.setProperty("chipCategory", _normalized_chip_category(token.category, self.site))
        chip.setProperty("chipActive", True)
        chip.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        row = QHBoxLayout(chip)
        row.setContentsMargins(3, 0, 3, 0)
        row.setSpacing(2)
        polarity = QToolButton()
        polarity.setText("−" if token.negative else "+")
        polarity.setAutoRaise(True)
        polarity.setFixedWidth(22)
        polarity.clicked.connect(lambda: self.toggle_token(token.token_id))
        label = QLabel(chip_display_text(token.value))
        label.setToolTip(token.value)
        label.setMaximumWidth(TagTokenEditor.CHIP_LABEL_MAX_WIDTH)
        remove = QToolButton()
        remove.setText("×")
        remove.setAutoRaise(True)
        remove.setFixedWidth(22)
        remove.clicked.connect(lambda: self.remove_token(token.token_id))
        row.addWidget(polarity)
        row.addWidget(label)
        row.addWidget(remove)
        chip.polarity = polarity
        chip.label = label
        chip.remove = remove
        for context_target in (chip, polarity, label, remove):
            context_target.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            context_target.customContextMenuRequested.connect(
                lambda position, source=context_target: self._show_chip_menu(
                    token.token_id, source, position
                )
            )
        return chip

    def _emit_changed(self) -> None:
        self.query_changed.emit(self.serialize())

    def retranslate(self) -> None:
        self.input.setPlaceholderText(self.catalog.text("tagging.search.placeholder"))
        self.setToolTip(self.catalog.text("tagging.search.editor_tip"))

    def refresh_theme(self) -> None:
        self.setStyleSheet(semantic_chip_stylesheet(self.palette(), self.site))
        refresh_suggestion_model(self.model, self.palette())

    def set_site(self, site: str) -> None:
        if site == self.site:
            return
        self.site = site
        self.set_suggestions([])
        self.refresh_theme()



class TaggingPage(TaggingLegacyPage):
    suggestion_table_type = SuggestionTable
    """Transition entry point for the new Tagging workflow."""

    BULK_MIN_WIDTH = 220
    BULK_MAX_WIDTH = 480
    BULK_DEFAULT_WIDTH = 300
    BULK_COLLAPSED_WIDTH = 30

    undo_requested = Signal()
    redo_requested = Signal()
    manual_lookup_requested = Signal(str)
    manual_add_requested = Signal(str)
    review_validation_requested = Signal()
    batch_refresh_requested = Signal()
    batch_review_requested = Signal(int)
    batch_remove_requested = Signal(list)
    batch_remove_completed_requested = Signal()
    batch_open_requested = Signal(int)
    batch_publish_requested = Signal()
    batch_retry_requested = Signal(list)
    batch_session_test_requested = Signal()
    batch_cancel_requested = Signal()
    reanalyze_requested = Signal()
    site_changed = Signal(str)
    bulk_apply_requested = Signal(list, list, list)
    bulk_lookup_requested = Signal(str, str)
    search_lookup_requested = Signal(str)
    search_resolve_requested = Signal(int, object)
    targeted_wd14_requested = Signal(str, list)
    targeted_wd14_cancel_requested = Signal()
    navigation_changed = Signal()
    search_settings_saved = Signal(object)
    entity_profile_requested = Signal(str, str)

    def _build_search(self) -> None:
        layout = QVBoxLayout(self.search_view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.group = QGroupBox()
        controls = QGridLayout(self.group)
        controls.setContentsMargins(8, 6, 8, 6)
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(4)
        controls.setColumnStretch(3, 1)

        self.query_label = QLabel()
        self.query = SearchQueryEditor(
            self.catalog,
            str(self.settings.get("tagging_query", "rating:general")),
            site=str(self.settings.get("tagging_site", "gelbooru")),
        )
        self.query.lookup_requested.connect(self.search_lookup_requested)
        self.query.resolve_requested.connect(self.search_resolve_requested)
        self.start_button = QPushButton()
        self.stop_button = QPushButton()
        self.stop_button.setEnabled(False)
        controls.addWidget(self.query_label, 0, 2)
        controls.addWidget(self.query, 0, 3)
        controls.addWidget(self.start_button, 0, 4)
        controls.addWidget(self.stop_button, 0, 5)

        defaults = {"pages": 10, "start": 1, "minimum": 0, "maximum": 12}
        ranges = {
            "pages": (1, 1_000),
            "start": (1, 1_000_000),
            "minimum": (0, 1_000),
            "maximum": (0, 1_000),
        }
        self.spins = {}
        self.spin_labels = {}
        self.parameter_row = QWidget()
        self.parameter_layout = QHBoxLayout(self.parameter_row)
        self.parameter_layout.setContentsMargins(0, 0, 0, 0)
        self.parameter_layout.setSpacing(8)
        for key, default in defaults.items():
            lower, upper = ranges[key]
            label = QLabel()
            spin = QSpinBox()
            spin.setRange(lower, upper)
            spin.setValue(
                _setting_int(self.settings, f"tagging_{key}", default, lower, upper)
            )
            self.spins[key] = spin
            self.spin_labels[key] = label
            label.setProperty("preserveHorizontalSize", True)
            spin.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            if self.parameter_layout.count():
                self.parameter_layout.addStretch(1)
            self.parameter_layout.addWidget(label)
            self.parameter_layout.addWidget(spin)
        controls.addWidget(self.parameter_row, 1, 0, 1, 8)
        self.spins["maximum"].setMinimum(self.spins["minimum"].value())
        self.spins["minimum"].valueChanged.connect(self._minimum_changed)
        self.spins["maximum"].valueChanged.connect(self._update_threshold_summary)
        self.threshold_summary = QLabel()
        self.threshold_summary.setProperty("preserveHorizontalSize", True)
        self.parameter_layout.addStretch(1)
        self.parameter_layout.addWidget(self.threshold_summary)
        self._update_threshold_summary()
        layout.addWidget(self.group)

        self.progress = QProgressBar()
        self.progress.setMaximumHeight(22)
        self.progress.hide()
        layout.addWidget(self.progress)
        # Compatibility target for controller/task text; execution feedback is
        # presented by PageStatus instead of consuming another layout row.
        self.state = QLabel()
        self.state.hide()

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results = QWidget()
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.results_scroll.setWidget(self.results)
        layout.addWidget(self.results_scroll, 1)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.page_status = PageStatus("tagging", self)
        self.query.entity_profile_requested.connect(self.entity_profile_requested)
        self.query.browser_launcher = self.browser_launcher
        bind_tag_popup(self.query.completer, self.catalog, launcher=self.browser_launcher,
                       entity=self.entity_profile_requested.emit)
        self.suggestions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.suggestions.customContextMenuRequested.connect(self._entity_suggestion_menu)
        self.suggestions.itemDoubleClicked.disconnect()
        self.suggestions.itemDoubleClicked.connect(
            lambda item: self.tag_search_requested.emit(self.suggestions.item(item.row(), 0).text())
            if item.row() in self.suggestions.tag_rows() else None)
        self.preview.enable_preparation()
        self.thumbnail_state_changed.connect(self._thumbnail_state_for_footer)
        self._displayed_post_id: int | None = None
        self.mode_stack.currentChanged.connect(self._update_counter)
        self.open_button.clicked.disconnect()
        self.open_button.clicked.connect(self._open_current_post)
        configured_site = str(self.settings.get("tagging_site", "gelbooru"))
        self.active_site = configured_site if configured_site in {"gelbooru", "e621"} else "gelbooru"
        self.query.set_site(self.active_site)
        self._all_search_results: list[dict] = []
        self._last_searched_query = self.query.text().strip()
        self._batch_entries_by_post: dict[int, dict[str, object]] = {}
        self._confidence_tag = ""
        self._confidence_scores: dict[int, float] = {}
        self._confidence_failed: set[int] = set()
        self._confidence_scores_by_tag: dict[str, dict[int, float]] = {}
        self._confidence_failed_by_tag: dict[str, set[int]] = {}
        self._confidence_tags: list[str] = []
        self._running_confidence_tag = ""
        configured_sort = str(self.settings.get("tagging_result_sort", "id"))
        self.result_sort_mode = configured_sort if configured_sort in {"id", "artist", "tags"} else "id"
        self._build_site_selector()
        self._build_result_filter()
        self._build_bulk_editor()
        self._reviewed_post_ids: set[int] = set()
        self._suggestion_id_column = 5
        self.suggestions.setColumnCount(7)
        self.suggestions.verticalHeader().hide()
        self.suggestions.horizontalHeader().moveSection(6, 0)
        self.suggestions.setColumnWidth(6, 38)
        self.suggestions.setHorizontalHeaderLabels(
            (
                "Tag",
                "Confidence",
                "Origin / match",
                "Category",
                "Decision",
                "ID",
                "? / ✓ / ✕",
            )
        )
        self.suggestions.setColumnHidden(5, True)
        self.review.layout().setSpacing(0)
        # Widget-scoped shortcuts leave editable fields and their completers in
        # control of Ctrl+Z/Ctrl+Shift+Z.
        self.accept_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.reject_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self.suggestions)
        self.redo_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Z"), self.suggestions)
        self.redo_alias_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self.suggestions)
        self.undo_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.redo_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.redo_alias_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.undo_shortcut.activated.connect(self.undo_requested)
        self.redo_shortcut.activated.connect(self.redo_requested)
        self.redo_alias_shortcut.activated.connect(self.redo_requested)
        self._build_manual_entry()
        bind_tag_popup(self.manual_completer, self.catalog, launcher=self.browser_launcher,
                       entity=self.entity_profile_requested.emit)
        self._build_reanalyze_action()
        self._build_batch_view()
        self._thumbnail_levels = (96, 128, 160, 192, 256, 320)
        configured_size = int(self.settings.get("tagging_thumbnail_size", 160))
        self._thumbnail_size = min(self._thumbnail_levels, key=lambda value: abs(value - configured_size))
        self.results_scroll.viewport().installEventFilter(self)
        self.results_scroll.viewport().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.results_scroll.viewport().customContextMenuRequested.connect(
            self._show_results_menu
        )
        self.select_all_shortcut = QShortcut(QKeySequence.StandardKey.SelectAll, self.results_scroll)
        self.select_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.select_all_shortcut.activated.connect(self.select_all_results)
        self.retranslate()

    def _show_results_menu(self, position: QPoint, post=None) -> None:
        menu = QMenu(self)
        handlers = {}
        if post is not None:
            handlers = post_actions(menu, self.catalog, post["id"], site=self.active_site,
                                    source=post.get("source"), launcher=self.browser_launcher,
                                    opener=lambda: self._open_post(int(post["id"])))
            menu.addSeparator()
        sort_menu = menu.addMenu(self.catalog.text("tagging.sort.title"))
        group = QActionGroup(sort_menu)
        group.setExclusive(True)
        for mode, key in (
            ("id", "tagging.sort.id"),
            ("artist", "tagging.sort.artist"),
            ("tags", "tagging.sort.tags"),
        ):
            action = sort_menu.addAction(self.catalog.text(key))
            action.setCheckable(True)
            action.setChecked(mode == self.result_sort_mode)
            action.setData(mode)
            action.triggered.connect(
                lambda _checked=False, value=mode: self.set_result_sort(value)
            )
            group.addAction(action)
        exec_menu(menu, self.results_scroll.viewport().mapToGlobal(position), handlers)

    def set_result_sort(self, mode: str) -> None:
        if mode not in {"id", "artist", "tags"} or mode == self.result_sort_mode:
            return
        self.result_sort_mode = mode
        self.settings["tagging_result_sort"] = mode
        self.search_settings_saved.emit({"tagging_result_sort": mode})
        if self._all_search_results:
            self._apply_result_filter(presentation_only=True)

    @staticmethod
    def _post_id(post: dict) -> int:
        try:
            return int(post.get("id", 0))
        except (TypeError, ValueError):
            return 0

    def _sort_result_posts(self, posts: list[dict]) -> list[dict]:
        if self.result_sort_mode == "artist":
            def artist_key(post: dict):
                artists = post_artist_names(post)
                return (
                    1 if not artists else 0,
                    tuple(natural_text_key(value) for value in artists),
                    self._post_id(post),
                )
            return sorted(posts, key=artist_key)
        if self.result_sort_mode == "tags":
            return sorted(
                posts,
                key=lambda post: (
                    tuple(natural_text_key(value) for value in post_tag_names(post)),
                    1 if not post_artist_names(post) else 0,
                    tuple(natural_text_key(value) for value in post_artist_names(post)),
                    self._post_id(post),
                ),
            )
        return sorted(posts, key=self._post_id)

    def _thumbnail_state_for_footer(self, state: dict[str, int]) -> None:
        loaded = int(state.get("loaded", 0))
        total = int(state.get("total", 0))
        failed = int(state.get("failed", 0))
        cache = self.thumbnail_cache
        tooltip = "\n".join((
            "Thumbnail images:",
            f"Used: {format_bytes(cache.byte_count)}",
            f"Target: {format_bytes(cache.target_bytes)}",
            f"Maximum: {format_bytes(MAX_ADAPTIVE_THUMBNAIL_CACHE_BYTES)}",
            f"Items: {len(cache)}",
            f"Average: {format_bytes(cache.average_image_bytes)}/image",
            f"Evictions: {cache.evictions}",
        ))
        self.page_status.set_thumbnail_state(loaded, total, failed, tooltip)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._reflow_results_to_viewport)

    def _reflow_results_to_viewport(self) -> None:
        if not hasattr(self, "results_scroll"):
            return
        available = max(1, self.results_scroll.viewport().width() - 8)
        for group in self.result_groups:
            group.setMaximumWidth(available)
            group.reflow(available)

    def _minimum_changed(self, minimum: int) -> None:
        self.spins["maximum"].setMinimum(minimum)
        self._update_threshold_summary()

    def _thresholds(self) -> tuple[int, int]:
        return derived_tagging_thresholds(
            self.spins["minimum"].value(), self.spins["maximum"].value()
        )

    def _update_threshold_summary(self, *_args) -> None:
        critical, high = self._thresholds()
        self.threshold_summary.setText(
            self.catalog.text("tagging.thresholds", critical=critical, high=high)
        )

    def _search_settings(self) -> dict[str, object]:
        return {
            "tagging_site": self.active_site,
            "tagging_query": self.query.text().strip(),
            **{
                f"tagging_{key}": self.spins[key].value()
                for key in ("pages", "start", "minimum", "maximum")
            },
        }

    def _build_result_filter(self) -> None:
        self.hide_queued_results = QCheckBox()
        self.hide_queued_results.setChecked(
            bool(self.settings.get("tagging_hide_queued_results", True))
        )
        self.result_filter_counts = QLabel()
        self.hide_queued_results.toggled.connect(self._result_filter_toggled)
        self.checkbox_selection = QCheckBox()
        self.checkbox_selection.setChecked(bool(self.settings.get("tagging_checkbox_selection", False)))
        self.checkbox_selection.toggled.connect(self._checkbox_mode_changed)

    def _checkbox_mode_changed(self, checked: bool) -> None:
        self.settings["tagging_checkbox_selection"] = bool(checked)
        self.search_settings_saved.emit({"tagging_checkbox_selection": bool(checked)})

    def _result_filter_toggled(self, checked: bool) -> None:
        self.settings["tagging_hide_queued_results"] = bool(checked)
        self._apply_result_filter()

    def _build_bulk_editor(self) -> None:
        self.bulk_group = QGroupBox()
        layout = QVBoxLayout(self.bulk_group)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        self.bulk_collapse = QToolButton()
        self.bulk_collapse.setAutoRaise(True)
        self.bulk_collapse.setText("<")
        self.bulk_collapse.clicked.connect(lambda: self.set_bulk_expanded(False))
        header = QHBoxLayout()
        header.addWidget(self.result_filter_counts, 1)
        header.addWidget(self.bulk_collapse)
        layout.addLayout(header)
        options = QHBoxLayout()
        options.addWidget(self.hide_queued_results)
        options.addWidget(self.checkbox_selection)
        options.addStretch(1)
        layout.addLayout(options)
        self.bulk_palette_label = QLabel()
        self.bulk_palette_label.setProperty("preserveHorizontalSize", True)
        self.bulk_palette = TagTokenEditor(
            confidence_actions=True,
            chip_role="add",
            site=self.active_site,
            mixed_operations=True,
            expand_chips=True,
            active_tooltip=self.catalog.text("tagging.bulk.checkbox_tip"),
        )
        # Compatibility alias for callers that only need the former add editor.
        self.bulk_add = self.bulk_palette
        self.bulk_apply = QPushButton()
        layout.addWidget(self.bulk_palette_label)
        layout.addWidget(self.bulk_palette, 1)
        self.bulk_intent_help = QLabel()
        self.bulk_intent_help.setWordWrap(True)
        self.wd14_cancel_button = QPushButton()
        self.wd14_cancel_button.setVisible(False)
        self.wd14_cancel_button.clicked.connect(self.clear_targeted_wd14)
        layout.addWidget(self.wd14_cancel_button, 0, Qt.AlignmentFlag.AlignRight)
        self.bulk_action_separator = QFrame()
        self.bulk_action_separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(self.bulk_action_separator)
        layout.addWidget(self.bulk_apply)
        layout.addWidget(self.bulk_intent_help)
        self.bulk_apply.clicked.connect(self._emit_bulk_apply)
        search_layout = self.search_view.layout()
        search_layout.removeWidget(self.results_scroll)
        self.bulk_reopen = QToolButton()
        self.bulk_reopen.setAutoRaise(True)
        self.bulk_reopen.setText(">")
        self.bulk_reopen.clicked.connect(lambda: self.set_bulk_expanded(True))
        self.bulk_panel = QWidget()
        bulk_panel_layout = QVBoxLayout(self.bulk_panel)
        bulk_panel_layout.setContentsMargins(0, 0, 0, 0)
        bulk_panel_layout.addWidget(self.bulk_reopen)
        bulk_panel_layout.addWidget(self.bulk_group, 1)
        self.bulk_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.bulk_splitter.setChildrenCollapsible(False)
        self.bulk_splitter.setHandleWidth(6)
        self.bulk_splitter.addWidget(self.bulk_panel)
        self.bulk_splitter.addWidget(self.results_scroll)
        self.bulk_splitter.setStretchFactor(0, 0)
        self.bulk_splitter.setStretchFactor(1, 1)
        self.bulk_splitter.splitterMoved.connect(self._bulk_splitter_moved)
        self.search_workspace = self.bulk_splitter
        search_layout.addWidget(self.bulk_splitter, 1)
        self.set_bulk_expanded(
            bool(self.settings.get("tagging_bulk_expanded", True)), persist=False
        )
        self._restore_bulk_palette()
        self.bulk_palette.tags_changed.connect(self._update_bulk_action)
        self.bulk_palette.tags_changed.connect(self._persist_bulk_palette)
        self.bulk_palette.lookup_requested.connect(
            lambda text: self.bulk_lookup_requested.emit("bulk", text)
        )
        self.bulk_palette.analysis_requested.connect(self._request_targeted_wd14)
        self.analysis_palette = TagTokenEditor(
            confidence_actions=True, site=self.active_site,
            active_tooltip=self.catalog.text("tagging.wd14.active_tip"),
            single_line=True,
        )
        self.analysis_palette.input.hide()
        self.active_filters_label = QLabel(self.catalog.text("tagging.active_filters"))
        self.active_filters = QWidget()
        active_layout = QHBoxLayout(self.active_filters)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.addWidget(self.active_filters_label)
        active_layout.addWidget(self.analysis_palette, 1)
        self.active_filters.setFixedHeight(self.analysis_palette.height())
        search_layout.insertWidget(0, self.active_filters)
        for editor in (self.bulk_palette, self.analysis_palette):
            editor.set_context_actions(self.catalog, self.browser_launcher, self.entity_profile_requested.emit)
            bind_tag_popup(editor.completer, self.catalog, launcher=self.browser_launcher,
                           entity=self.entity_profile_requested.emit)
        self.analysis_palette.analysis_toggled.connect(self._toggle_targeted_wd14)
        self.analysis_palette.tags_changed.connect(self._sync_active_analyses)
        self.analysis_palette.analysis_requested.connect(self._request_targeted_wd14)
        self._update_bulk_action()

    def _sync_active_analyses(self) -> None:
        active = self.analysis_palette.active_tags()
        if active != self._confidence_tags:
            self._confidence_tags = active
            self._apply_result_filter(presentation_only=True)

    def _toggle_targeted_wd14(self, tag: str, checked: bool) -> None:
        self._sync_active_analyses()

    def _set_targeted_analysis_state(self, tag: str, state: str) -> None:
        self.bulk_add.set_analysis_state(tag, state)
        self.analysis_palette.set_analysis_state(tag, state)

    def _restore_bulk_palette(self) -> None:
        entries = self.settings.get("tagging_bulk_palette", [])
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            self.bulk_palette.add_tag(
                str(entry.get("tag", "")),
                str(entry.get("category", "unknown")),
                operation=str(entry.get("mode", "+")),
                active=bool(entry.get("checked", True)),
                emit=False,
            )

    def _persist_bulk_palette(self) -> None:
        entries = self.bulk_palette.entries()
        self.settings["tagging_bulk_palette"] = entries
        self.search_settings_saved.emit({"tagging_bulk_palette": entries})

    def set_bulk_expanded(self, expanded: bool, *, persist: bool = True) -> None:
        expanded = bool(expanded)
        width = _setting_int(
            self.settings,
            "tagging_bulk_width",
            self.BULK_DEFAULT_WIDTH,
            self.BULK_MIN_WIDTH,
            self.BULK_MAX_WIDTH,
        )
        self.bulk_group.setVisible(expanded)
        self.bulk_reopen.setVisible(not expanded)
        self.bulk_panel.setMinimumWidth(
            self.BULK_MIN_WIDTH if expanded else self.BULK_COLLAPSED_WIDTH
        )
        self.bulk_panel.setMaximumWidth(
            self.BULK_MAX_WIDTH if expanded else self.BULK_COLLAPSED_WIDTH
        )
        self.bulk_splitter.blockSignals(True)
        self.bulk_splitter.setSizes([
            width if expanded else self.BULK_COLLAPSED_WIDTH,
            max(1, self.bulk_splitter.width() - width),
        ])
        self.bulk_splitter.blockSignals(False)
        action = self.catalog.text(
            "tagging.bulk.collapse" if expanded else "tagging.bulk.expand"
        )
        button = self.bulk_collapse if expanded else self.bulk_reopen
        button.setToolTip(action)
        button.setAccessibleName(action)
        self.settings["tagging_bulk_expanded"] = expanded
        if persist:
            self.search_settings_saved.emit({"tagging_bulk_expanded": expanded})
        QTimer.singleShot(0, self._reflow_results_to_viewport)

    def _bulk_splitter_moved(self, position: int, _index: int) -> None:
        if self.bulk_group.isHidden():
            return
        width = max(self.BULK_MIN_WIDTH, min(self.BULK_MAX_WIDTH, position))
        self.settings["tagging_bulk_width"] = width
        self.search_settings_saved.emit({"tagging_bulk_width": width})
        QTimer.singleShot(0, self._reflow_results_to_viewport)

    def _request_targeted_wd14(self, tag: str) -> None:
        posts = list(self.result_posts)
        if not posts or self._running_confidence_tag:
            return
        self.analysis_palette.add_tag(tag, emit=False)
        self.analysis_palette.show()
        self.analysis_palette.set_tag_active(tag, True)
        self._confidence_tag = tag
        if tag not in self._confidence_tags:
            self._confidence_tags.append(tag)
        available = self._confidence_scores_by_tag.get(tag, {})
        if all(self._post_id(post) in available for post in posts):
            self._set_targeted_analysis_state(tag, "complete")
            self._apply_result_filter(presentation_only=True)
            return
        self._running_confidence_tag = tag
        self._set_targeted_analysis_state(tag, "running")
        self.wd14_cancel_button.setVisible(True)
        self.page_status.set_state("analyzing")
        self.page_status.show_message(
            self.catalog.text("tagging.wd14.starting", tag=tag), timeout_ms=0
        )
        self.page_status.set_progress(
            0,
            len(posts),
            accessible_text=self.catalog.text("tagging.wd14.progress_accessible"),
        )
        self.page_status.set_analysis_state(0, len(posts), 0, 0, 0, 0)
        self.targeted_wd14_requested.emit(tag, posts)

    def targeted_wd14_active_tags(self) -> list[str]:
        return list(self._confidence_tags)

    def clear_targeted_wd14(self) -> None:
        previous = self.analysis_palette.tags()
        self._running_confidence_tag = ""
        self._confidence_tag = ""; self._confidence_scores = {}; self._confidence_failed = set()
        self._confidence_scores_by_tag = {}; self._confidence_failed_by_tag = {}
        self._confidence_tags = []
        for tag in previous:
            self._set_targeted_analysis_state(tag, "idle")
        self.analysis_palette.blockSignals(True)
        for tag in previous:
            self.analysis_palette.remove_tag(tag)
        self.analysis_palette.blockSignals(False)
        self.wd14_cancel_button.setVisible(False)
        self.page_status.clear_progress()
        self.page_status.clear_analysis_state()
        self.page_status.clear_message()
        self.page_status.set_state("ready")
        self.targeted_wd14_cancel_requested.emit()
        if self._all_search_results:
            self._apply_result_filter()

    def show_targeted_wd14_unavailable(self, tag: str, message: str) -> None:
        self._running_confidence_tag = ""
        self._set_targeted_analysis_state(tag, "failed")
        self.wd14_cancel_button.setVisible(False)
        self.page_status.clear_progress()
        self.page_status.set_analysis_state(0, 0, 0, 0, 1, 0, message)
        self.page_status.set_state("ready")
        self.page_status.show_message(message, timeout_ms=6_000, log=True)
        if self._confidence_tag == tag:
            self._confidence_tag = ""
            self._confidence_tags = [value for value in self._confidence_tags if value != tag]
            self._confidence_scores = {}; self._confidence_failed = set()
            self._confidence_scores_by_tag.pop(tag, None)
            self._confidence_failed_by_tag.pop(tag, None)
            if self._all_search_results:
                self._apply_result_filter()

    def set_targeted_wd14_progress(self, progress) -> None:
        self.page_status.set_state("analyzing")
        self.page_status.set_progress(
            progress.completed,
            progress.total,
            accessible_text=self.catalog.text("tagging.wd14.progress_accessible"),
        )
        self.page_status.show_message(
            self.catalog.text(
                "tagging.wd14.status.progress",
                reused=progress.reused,
                analyzed=progress.analyzed,
                failed=progress.failed,
            ),
            timeout_ms=0,
        )
        self.page_status.set_analysis_state(
            progress.completed,
            progress.total,
            progress.reused,
            progress.analyzed,
            progress.failed,
            getattr(progress, "retrying", 0),
        )

    def show_targeted_wd14_result(self, result) -> None:
        self._running_confidence_tag = ""
        scores_by_tag = result.scores_by_tag or {result.tag: dict(result.scores)}
        for tag, scores in scores_by_tag.items():
            if tag not in self.analysis_palette.tags():
                self.analysis_palette.add_tag(tag, emit=False)
                self._confidence_tags.append(tag)
            self._confidence_scores_by_tag[tag] = dict(scores)
            self._confidence_failed_by_tag[tag] = set(result.failed_post_ids)
            state = "failed" if result.failed_post_ids else "complete"
            self._set_targeted_analysis_state(tag, state)
            chip = getattr(self.bulk_add, "_chips", {}).get(tag)
            if chip is not None:
                chip.analysis.setToolTip(
                    f"{result.progress.analyzed} analyzed · {result.progress.failed} failed"
                )
        self._confidence_scores = dict(self._confidence_scores_by_tag.get(
            self._confidence_tag, result.scores
        ))
        self._confidence_failed = set(self._confidence_failed_by_tag.get(
            self._confidence_tag, result.failed_post_ids
        ))
        self.wd14_cancel_button.setVisible(False)
        self.analysis_palette.show()
        self.set_targeted_wd14_progress(result.progress)
        self.page_status.clear_progress()
        self.page_status.set_state("ready")
        self.page_status.show_message(
            self.catalog.text(
                "tagging.wd14.status.complete",
                total=result.progress.total,
                reused=result.progress.reused,
                analyzed=result.progress.analyzed,
                failed=result.progress.failed,
            ),
            timeout_ms=8_000,
            log=True,
        )
        self._apply_result_filter()

    def show_targeted_wd14_cancelled(self, result) -> None:
        if result.tag != self._confidence_tag:
            return
        self.set_targeted_wd14_progress(result.progress)
        self._running_confidence_tag = ""
        self._set_targeted_analysis_state(result.tag, "cancelled")
        self.wd14_cancel_button.setVisible(False)
        self.page_status.clear_progress()
        self.page_status.set_state("ready")
        self.page_status.show_message(
            self.catalog.text(
                "tagging.wd14.status.cancelled",
                completed=result.progress.completed,
                total=result.progress.total,
            ),
            timeout_ms=8_000,
            log=True,
        )
        self._apply_result_filter()

    def _result_sections(self, posts: list[dict]) -> list[tuple[str, list[dict]]]:
        active_tags = [
            tag for tag in self._confidence_tags
            if tag in self._confidence_scores_by_tag or tag in self._confidence_failed_by_tag
        ]
        if not active_tags:
            if self.result_sort_mode == "tags":
                return [(
                    self.catalog.text("tagging.sort.all", count=len(posts)),
                    self._sort_result_posts(posts),
                )]
            return [
                (title, self._sort_result_posts(values))
                for title, values in super()._result_sections(posts)
            ]
        grouped = {key: [] for key in CONFIDENCE_BUCKETS}
        for post in posts:
            post_id = int(post.get("id", 0))
            scores = [self._confidence_scores_by_tag.get(tag, {}).get(post_id) for tag in active_tags]
            failed = any(post_id in self._confidence_failed_by_tag.get(tag, set()) for tag in active_tags)
            combined = min(scores) if all(score is not None for score in scores) else None
            key = confidence_bucket(combined, failed=failed)
            grouped[key].append(post)
        return [
            (
                self.catalog.text(f"tagging.wd14.bucket.{key}", count=len(grouped[key])),
                self._sort_result_posts(grouped[key]),
            )
            for key in CONFIDENCE_BUCKETS if grouped[key]
        ]

    def _emit_bulk_apply(self) -> None:
        posts = self.selected_posts()
        additions, removals = self._selected_bulk_changes()
        if not posts or not (additions or removals):
            return
        sections = [self.catalog.text("tagging.bulk.confirm_title", count=len(posts))]
        if additions:
            sections.append(self.catalog.text("tagging.bulk.confirm_add", tags=", ".join(additions)))
        if removals:
            sections.append(self.catalog.text("tagging.bulk.confirm_remove", tags=", ".join(removals)))
        sections.append(self.catalog.text("tagging.bulk.confirm_help"))
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.catalog.text("tagging.bulk.confirm_window"))
        dialog.setText("\n\n".join(sections))
        dialog.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok)
        dialog.button(QMessageBox.StandardButton.Ok).setText(self.catalog.text("tagging.bulk.confirm_accept"))
        if dialog.exec() == QMessageBox.StandardButton.Ok:
            self.bulk_apply_requested.emit(posts, additions, removals)

    def _selected_bulk_changes(self) -> tuple[list[str], list[str]]:
        """Return the checked chips used by both confirmation and persistence."""
        return (
            self.bulk_palette.active_tags("add"),
            self.bulk_palette.active_tags("remove"),
        )

    def _result_clicked(self, post: dict, checked: bool) -> None:
        if self.checkbox_selection.isChecked():
            self._selection_anchor = self.result_posts.index(post)
            self._selection_changed()
            return
        super()._result_clicked(post, checked)

    def selection_changed(self) -> None:
        self._update_bulk_action()

    def _update_bulk_action(self) -> None:
        count = len(self.selected_posts()) if hasattr(self, "result_buttons") else 0
        has_delta = bool(any(self._selected_bulk_changes())) if hasattr(self, "bulk_add") else False
        self.bulk_apply.setEnabled(count > 0 and has_delta)
        self.bulk_apply.setText(self.catalog.text("tagging.bulk.apply", count=count))

    def set_bulk_suggestions(
        self,
        target: str,
        suggestions: list[
            str | tuple[str, str | None] | tuple[str, str | None, str]
        ],
    ) -> None:
        if target == "search":
            self.query.set_suggestions(suggestions)
            return
        self.bulk_palette.set_suggestions(suggestions)

    def bulk_lookup_text(self, target: str) -> str:
        if target == "search":
            return self.query.lookup_text()
        return self.bulk_palette.input.text().strip()

    def show_bulk_pending(self, post_id: int, additions: list[str], removals: list[str]) -> None:
        previous_queued = self._queued_result_ids()
        button = self.result_buttons.get(int(post_id))
        self._batch_entries_by_post[int(post_id)] = {
            "site": self.active_site, "post_id": str(post_id),
            "additions": list(additions), "removals": list(removals),
            "publish_state": "pending_publish",
        }
        if previous_queued != self._queued_result_ids() and self._all_search_results:
            self._apply_result_filter()
        elif button is not None:
            self._render_reviewed_checks()

    def eventFilter(self, watched, event) -> bool:
        if (hasattr(self, "batch_button") and watched is self.batch_button
                and event.type() == QEvent.Type.PaletteChange):
            self._batch_glow.setColor(self.batch_button.palette().highlight().color())
        if watched is self.results_scroll.viewport() and event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            direction = 1 if event.angleDelta().y() > 0 else -1
            index = self._thumbnail_levels.index(self._thumbnail_size)
            next_index = max(0, min(len(self._thumbnail_levels) - 1, index + direction))
            if next_index != index:
                bar = self.results_scroll.verticalScrollBar(); old_max = max(1, bar.maximum()); ratio = bar.value() / old_max
                self._thumbnail_size = self._thumbnail_levels[next_index]
                self.settings["tagging_thumbnail_size"] = self._thumbnail_size
                self.set_thumbnail_size(self._thumbnail_size)
                QTimer.singleShot(0, lambda: bar.setValue(round(ratio * bar.maximum())))
            event.accept(); return True
        return super().eventFilter(watched, event)

    def _build_site_selector(self) -> None:
        self.site_label = QLabel()
        self.site_selector = QComboBox()
        self.site_selector.addItem("Gelbooru", "gelbooru")
        self.site_selector.addItem("e621", "e621")
        index = self.site_selector.findData(self.active_site)
        self.site_selector.setCurrentIndex(max(0, index))
        controls = self.group.layout()
        controls.addWidget(self.site_label, 0, 0)
        controls.addWidget(self.site_selector, 0, 1)
        controls.addWidget(self.query_label, 0, 2)
        controls.addWidget(self.query, 0, 3, 1, 2)
        controls.addWidget(self.start_button, 0, 5)
        controls.addWidget(self.stop_button, 0, 6)
        self.site_selector.currentIndexChanged.connect(self._site_selected)

    def _site_selected(self) -> None:
        site = str(self.site_selector.currentData())
        if site == self.active_site:
            return
        if self._confidence_tag:
            self.clear_targeted_wd14()
        self.active_site = site
        self.query.set_site(site)
        self.bulk_palette.set_site(site)
        self.set_manual_suggestions([])
        self.settings["tagging_site"] = site
        self.search_settings_saved.emit(self._search_settings())
        self._clear_results()
        self._all_search_results = []
        self._batch_entries_by_post = {}
        self.processed_in_session.clear()
        self._reviewed_post_ids.clear()
        self.current_post_id = None
        self._displayed_post_id = None
        self.current_post = {}
        self.state.setText(self.catalog.text("tagging.ready"))
        self.group.setTitle(
            self.catalog.text(
                "tagging.group_site", site=site_definition(site).display_name
            )
        )
        if hasattr(self, "batch_status") and site == "e621":
            self.batch_status.setText(self.catalog.text("tagging.publish.e621_unavailable"))
        if hasattr(self, "batch_session_test_button"):
            self._update_batch_actions()
        self.query.setText(self.query.text())
        self.site_changed.emit(site)

    def _start(self) -> None:
        try:
            query = self.query.text().strip()
            if query != self._last_searched_query:
                self.spins["start"].setValue(1)
            critical, high = self._thresholds()
            request = TaggingRequest(
                self.query.text().strip(), self.spins["pages"].value(),
                self.spins["start"].value(), self.spins["minimum"].value(),
                self.spins["maximum"].value(), critical, high, self.active_site,
            )
        except ValueError as exc:
            self.state.setText(self.catalog.text("tagging.invalid", error=exc))
            return
        self.processed_in_session.clear()
        self._last_searched_query = query
        if self._confidence_tag:
            self.clear_targeted_wd14()
        saved = self._search_settings()
        self.settings.update(saved)
        self.search_settings_saved.emit(saved)
        self.start_requested.emit(request)

    def set_running(self, running: bool) -> None:
        super().set_running(running)
        self.progress.setVisible(running)
        self.page_status.set_state("searching" if running else "ready")
        if not running:
            self.page_status.clear_progress()

    def set_progress(
        self, page: int, current: int, total: int, examined: int, retained: int
    ) -> None:
        super().set_progress(page, current, total, examined, retained)
        self.progress.show()
        self.page_status.set_state("searching")
        self.page_status.show_message(
            self.catalog.text("tagging.search.summary", retained=retained), timeout_ms=0
        )

    def _open_result(self, index: int, fallback: dict | None = None) -> None:
        started = perf_counter()
        self._initial_review_tags = None
        post = self.result_posts[index % len(self.result_posts)] if self.result_posts else fallback
        if post is not None:
            # Set the guard before post_selected invokes the controller synchronously.
            self._displayed_post_id = int(post.get("id", 0))
        super()._open_result(index, fallback)
        if self.current_post_id is not None:
            self._displayed_post_id = int(self.current_post_id)
            definition = site_definition(self.active_site)
            self.review_title.setText(f"{definition.display_name} #{self.current_post_id}")
        self._perf_log("post_selection", started)

    def _update_counter(self) -> None:
        if self.mode_stack.currentWidget() is self.review:
            self.current_result_index = next((
                index for index, post in enumerate(self.result_posts)
                if self._post_id(post) == self.current_post_id
            ), -1)
        super()._update_counter()
        if hasattr(self, "page_status"):
            current = self.current_result_index + 1 if (
                self.mode_stack.currentWidget() is self.review
                and not getattr(self, "_batch_local_item_id", None)
            ) else 0
            self.page_status.set_position(current, len(self.result_posts))
            self.navigation_changed.emit()

    def _open_current_post(self) -> None:
        if self._displayed_post_id is not None:
            self._open_post(self._displayed_post_id)

    def _entity_suggestion_menu(self, position) -> None:
        row = self.suggestions.rowAt(position.y())
        if row not in self.suggestions.tag_rows():
            return
        item = self.suggestions.item(row, 0)
        from booruflow.presentation.pyside6.tagging_suggestions import CATEGORY_ROLE, INITIAL_ROLE
        menu = QMenu(self)
        from booruflow.presentation.pyside6.context_actions import select_context_row
        select_context_row(self.suggestions, row)
        handlers = tag_actions(menu, self.catalog, item.text(), site=self.active_site,
                               category=item.data(CATEGORY_ROLE), launcher=self.browser_launcher,
                               entity=self.entity_profile_requested.emit)
        menu.addSeparator()
        add_action(menu, self.catalog.text("context.search_tag"),
                   lambda: self.tag_search_requested.emit(item.text()), handlers)
        reject_key = "tagging.review.decision.remove" if item.data(INITIAL_ROLE) else "entity.ignore"
        for decision, key, shortcut in (("accepted", "entity.keep", "A"), ("rejected", reject_key, "R")):
            action = add_action(menu, self.catalog.text(key) + f" ({shortcut})",
                                lambda value=decision: self._emit_decision(value), handlers)
            action.setEnabled(self.accept_button.isEnabled())
        exec_menu(menu, self.suggestions.viewport().mapToGlobal(position), handlers)

    def _perf_log(self, step: str, started: float) -> None:
        post_id = self._displayed_post_id or self.current_post_id or "-"
        self.activity_logged.emit(
            "TaggingPerf",
            f"site={self.active_site} post={post_id} step={step} "
            f"elapsed_ms={(perf_counter() - started) * 1000:.1f} gui_thread=true",
        )

    def _open_post(self, post_id: int) -> None:
        definition = site_definition(self.active_site)
        self.activity_logged.emit("Open", f"{definition.display_name} #{post_id}")
        url = definition.post_url(post_id)
        from booruflow.presentation.pyside6.context_actions import open_url
        open_url(url, self.browser_launcher)

    def _build_reanalyze_action(self) -> None:
        validation = self.action_bar.layout().itemAt(0).layout()
        self.reanalyze_button = QPushButton()
        self.reanalyze_button.clicked.connect(self.reanalyze_requested)
        validation.insertWidget(1, self.reanalyze_button)
        self.reanalyze_button.hide()

    def set_reanalyze_available(self, available: bool, busy: bool = False) -> None:
        self.reanalyze_button.setVisible(available)
        self.reanalyze_button.setEnabled(available and not busy)

    def _render_suggestions(self, *_args) -> None:
        started = perf_counter()
        decision = str(self.decision_filter.currentData())
        visible = {"accepted": {"accepted", "keep"}, "rejected": {"rejected", "remove"}}.get(
            decision, {decision}
        )
        rows = [
            row
            for row in self._all_suggestion_rows
            if decision == "all" or row["decision"] in visible
        ]
        self.suggestions.render_rows(
            rows, self.catalog, getattr(self, "active_site", "gelbooru"),
            self._sort_column, self._sort_order,
            next_token=self._pending_next_id, advance=self._pending_fallback_row is not None,
        )
        if not getattr(self, "_suggestion_columns_sized", False):
            self.suggestions.resizeColumnsToContents()
            self._suggestion_columns_sized = True
        if self.suggestions.tag_rows():
            self.suggestions.setFocus()
        self._pending_next_id = None
        self._pending_fallback_row = None
        self._update_review_action_states()
        self._perf_log("proposal_table_rebuild", started)

    def _sort_changed(self, column, order) -> None:
        self._sort_column, self._sort_order = column, order
        if self.suggestions.columnCount() == 7:
            self._render_suggestions()

    def _emit_decision(self, decision: str) -> None:
        tokens = self.suggestions.selected_tokens()
        if not tokens or not self.accept_button.isEnabled():
            return
        rows = self.suggestions.tag_rows()
        current = self.suggestions.currentRow()
        target = next((row for row in rows if row > current
                       and self.suggestions.item(row, 5).text() not in tokens), None)
        self._pending_fallback_row = current
        self._pending_next_id = self.suggestions.item(target, 5).text() if target is not None else None
        if len(tokens) == 1:
            kind, value = parse_review_row_token(tokens[0])
            self.decision_requested.emit(value if kind == "observation" else tokens[0], decision)
        else:
            self.decision_requested.emit(tokens, decision)

    def _update_review_action_states(self) -> None:
        super()._update_review_action_states()
        if isinstance(self.suggestions, SuggestionTable) and self.suggestions.columnCount() == 7:
            selected = bool(self.suggestions.selected_tokens())
            self.accept_button.setEnabled(self._review_ready and selected)
            self.reject_button.setEnabled(self._review_ready and selected)
            token = self._selected_observation_id()
            self.map_button.setEnabled(self._review_ready and token is not None
                                       and not str(token).startswith("entity:"))

    def _set_preview_media(self, path, *, preview_data: bytes | None = None) -> None:
        started = perf_counter()
        super()._set_preview_media(path, preview_data=preview_data)
        self._perf_log("image_decode_scale", started)

    def show_results(self, posts: list[dict]) -> None:
        self._all_search_results = list(posts)
        self._apply_result_filter()

    def _set_card_tooltips(self) -> None:
        for post in self.result_posts:
            card = self.result_buttons.get(int(post.get("id", 0)))
            if card is not None:
                card.setToolTip(existing_tags_tooltip(post))
                if not card.property("sortContextBound"):
                    card.setProperty("sortContextBound", True)
                    card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                    card.customContextMenuRequested.connect(
                        lambda position, source=card: self._show_results_menu(
                            self.results_scroll.viewport().mapFromGlobal(
                                source.mapToGlobal(position)
                            ), source.post
                        )
                    )

    def _thumbnail_failed(self, key, reason: str) -> None:
        super()._thumbnail_failed(key, reason)
        target = self._thumbnail_targets.get(key)
        if target is not None:
            card, generation = target
            if generation == self.result_generation:
                try:
                    card.setToolTip(existing_tags_tooltip(card.post))
                except RuntimeError:
                    pass

    def set_batch_queue_entries(self, entries: list[dict[str, object]]) -> dict[str, object]:
        started = perf_counter()
        previous_queued = self._queued_result_ids()
        batch_entries_by_post = {
            int(entry["post_id"]): entry
            for entry in entries
            if entry.get("site") == self.active_site and entry.get("post_id")
        }
        if batch_entries_by_post == self._batch_entries_by_post:
            self._render_reviewed_checks()
            return {"filter_ms": 0.0, "grid_rebuilt": False, "removed": 0}
        self._batch_entries_by_post = batch_entries_by_post
        new_queued = self._queued_result_ids()
        newly_hidden = new_queued - previous_queued
        newly_visible = previous_queued - new_queued
        incremental = bool(
            self._all_search_results and self.hide_queued_results.isChecked()
            and newly_hidden and not newly_visible
        )
        rebuilt = bool(
            self._all_search_results and self.hide_queued_results.isChecked()
            and previous_queued != new_queued and not incremental
        )
        removed = self._remove_result_cards(newly_hidden) if incremental else 0
        if rebuilt:
            self._apply_result_filter()
        elif not incremental:
            self._render_reviewed_checks()
        return {
            "filter_ms": (perf_counter() - started) * 1000,
            "grid_rebuilt": rebuilt,
            "removed": removed,
        }

    def _remove_result_cards(self, post_ids: set[int]) -> int:
        removed = 0
        for post_id in post_ids:
            button = self.result_buttons.pop(post_id, None)
            if button is None:
                continue
            removed += 1
            button.setChecked(False)
            button.deleteLater()
            for key, (target, _generation) in list(self._thumbnail_targets.items()):
                if target is button:
                    self._thumbnail_targets.pop(key, None)
        self.result_posts = [
            post for post in self.result_posts if int(post.get("id", 0)) not in post_ids
        ]
        for group in self.result_groups:
            group.cards = [card for card in group.cards if int(card.post.get("id", 0)) not in post_ids]
            group.reflow()
            group.update_selection_label()
        self._selection_changed()
        self._update_result_filter_counts()
        self._update_counter()
        return removed

    def _queued_result_ids(self) -> set[int]:
        return {
            post_id
            for post_id, entry in self._batch_entries_by_post.items()
            if queued_result_entry(entry)
        }

    def _apply_result_filter(self, *, presentation_only: bool = False) -> None:
        current_view = self.mode_stack.currentWidget()
        selected = {
            post_id for post_id, button in self.result_buttons.items() if button.isChecked()
        }
        expanded = {
            group.title: group.toggle.isChecked() for group in self.result_groups
        }
        scroll_value = self.results_scroll.verticalScrollBar().value()
        queued = self._queued_result_ids()
        visible = [
            post for post in self._all_search_results
            if not self.hide_queued_results.isChecked() or int(post.get("id", 0)) not in queued
        ]
        super().show_results(visible, presentation_only=presentation_only)
        self._set_card_tooltips()
        if current_view is not self.search_view:
            self.mode_stack.setCurrentWidget(current_view)
        for post_id in selected:
            if post_id in self.result_buttons:
                self.result_buttons[post_id].setChecked(True)
        for group in self.result_groups:
            if group.title in expanded:
                group.toggle.setChecked(expanded[group.title])
        self._selection_changed()
        QTimer.singleShot(
            0, lambda value=scroll_value: self.results_scroll.verticalScrollBar().setValue(value)
        )
        self._render_reviewed_checks()
        matching_queued = sum(
            int(post.get("id", 0)) in queued for post in self._all_search_results
        )
        self._update_result_filter_counts(matching_queued)

    def _update_result_filter_counts(self, matching_queued: int | None = None) -> None:
        if matching_queued is None:
            queued = self._queued_result_ids()
            matching_queued = sum(
                int(post.get("id", 0)) in queued for post in self._all_search_results
            )
        self.result_filter_counts.setText(
            self.catalog.text(
                "tagging.results.counts", total=len(self._all_search_results),
                queued=matching_queued, displayed=len(self.result_posts),
            )
        )

    def set_reviewed_post_ids(self, post_ids: set[int]) -> None:
        self._reviewed_post_ids = {int(post_id) for post_id in post_ids}
        self.processed_in_session.update(self._reviewed_post_ids)
        self._render_reviewed_checks()

    def _render_reviewed_checks(self) -> None:
        for post_id, button in self.result_buttons.items():
            entry = self._batch_entries_by_post.get(post_id)
            state = getattr(entry.get("publish_state"), "value", None) if entry else None
            status = ""
            if state == "failed":
                status = self.catalog.text("tagging.card.status.failed")
            elif state == "published":
                status = self.catalog.text("tagging.card.status.published")
            elif state == "publishing":
                status = self.catalog.text("tagging.card.status.publishing")
            elif post_id in self._reviewed_post_ids or entry is not None:
                status = self.catalog.text("tagging.card.status.processed")
            additions = list(entry.get("additions", [])) if entry else []
            removals = list(entry.get("removals", [])) if entry else []
            first, second = compact_result_card_text(
                post_id, int(button.post.get("tag_count", 0)), status,
                len(additions), len(removals),
            )
            score_value = self._confidence_score_for_post(post_id)
            if score_value is not None:
                score = round(score_value * 100)
                second += f" · WD14 {score} %"
            button.setText(f"{first}\n{second}")
            button.setToolTip(existing_tags_tooltip(button.post))
        self._update_counter()

    def _confidence_score_for_post(self, post_id: int) -> float | None:
        active_tags = [
            tag for tag in self._confidence_tags
            if tag in self._confidence_scores_by_tag
        ]
        if not active_tags:
            return None
        values = [
            self._confidence_scores_by_tag.get(tag, {}).get(post_id)
            for tag in active_tags
        ]
        return min(values) if all(value is not None for value in values) else None

    def mark_reviewed_and_advance(self, post_id: int) -> bool:
        """Advance from the actual displayed result, never a stale cursor."""
        self._reviewed_post_ids.add(int(post_id))
        self.processed_in_session.add(int(post_id))
        self._render_reviewed_checks()
        current = next(
            (
                index
                for index, post in enumerate(self.result_posts)
                if int(post.get("id", 0)) == int(post_id)
            ),
            None,
        )
        if current is None:
            return False
        indexes = list(range(current + 1, len(self.result_posts))) + list(range(current))
        target = next(
            (
                index
                for index in indexes
                if int(self.result_posts[index].get("id", 0)) not in self._reviewed_post_ids
            ),
            None,
        )
        if target is None:
            self.finish_individual_review()
        else:
            self._open_result(target)
        return True

    def next_individual_review_post(self) -> dict | None:
        """The Space target in the current ordered grid, before queue filtering."""
        current = next((i for i, post in enumerate(self.result_posts)
                        if self._post_id(post) == self.current_post_id), None)
        if current is None:
            return None
        indexes = list(range(current + 1, len(self.result_posts))) + list(range(current))
        return next((self.result_posts[i] for i in indexes
                     if self._post_id(self.result_posts[i]) not in self._reviewed_post_ids), None)

    def _build_manual_entry(self) -> None:
        self.legacy_tag_row.setVisible(False)
        self.review.layout().removeWidget(self.legacy_tag_row)
        container = QWidget(self.review)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        self.manual_add_label = QLabel()
        layout.addWidget(self.manual_add_label)
        self.manual_tag = QLineEdit()
        self.manual_add = QPushButton()
        layout.addWidget(self.manual_tag, 1)
        layout.addWidget(self.manual_add)
        review_layout = self.review.layout()
        review_layout.insertWidget(review_layout.indexOf(self.action_bar), container)

        self.manual_suggestion_model = TagSuggestionListModel(self)
        self.manual_completer = QCompleter(self.manual_suggestion_model, self)
        self.manual_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.manual_completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion
        )
        configure_tag_completer(self.manual_completer)
        self.manual_tag.setCompleter(self.manual_completer)
        self._manual_suggestion_values: dict[str, str] = {}
        self.manual_completer.activated[QModelIndex].connect(
            self._select_manual_suggestion_index
        )
        self.manual_lookup_timer = QTimer(self)
        self.manual_lookup_timer.setSingleShot(True)
        self.manual_lookup_timer.setInterval(250)
        self.manual_lookup_timer.timeout.connect(self._emit_manual_lookup)
        self.manual_tag.textEdited.connect(self._schedule_manual_lookup)
        self.manual_add.clicked.connect(self._emit_manual_add)
        self.manual_tag.returnPressed.connect(self._emit_manual_add)

    def _build_batch_view(self) -> None:
        self.batch_view = QWidget()
        self.mode_stack.addWidget(self.batch_view)
        root = QVBoxLayout(self.batch_view)
        root.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.batch_back_button = QPushButton()
        self.batch_refresh_button = QPushButton()
        self.batch_filter = QComboBox()
        for label, value in (
            (self.catalog.text("tagging.batch.filter.all"), "all"),
            (self.catalog.text("tagging.batch.filter.pending"), "pending_publish"),
            (self.catalog.text("tagging.batch.filter.published"), "published"),
            (self.catalog.text("tagging.batch.filter.failed"), "failed"),
            (self.catalog.text("tagging.batch.filter.local"), "local"),
            (self.catalog.text("tagging.batch.filter.skipped"), "skipped"),
        ):
            self.batch_filter.addItem(label, value)
        self.batch_counts = QLabel()
        toolbar.addWidget(self.batch_back_button)
        self.batch_filter_label = QLabel()
        toolbar.addWidget(self.batch_filter_label)
        toolbar.addWidget(self.batch_filter)
        toolbar.addWidget(self.batch_refresh_button)
        toolbar.addWidget(self.batch_counts, 1)
        root.addLayout(toolbar)
        self.batch_table = DataTable(0, 7)
        self.batch_table.setHorizontalHeaderLabels(
            ("Image / post", "Site", "Additions", "Removals", "State", "Reviewed at", "Item")
        )
        self.batch_table.setColumnHidden(6, True)
        self.batch_table.setSortingEnabled(False)
        self.batch_table.set_empty_text(self.catalog.text("table.empty_batch"))
        self.batch_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.batch_table.setColumnWidth(2, 220)
        root.addWidget(self.batch_table, 1)
        actions = QHBoxLayout()
        self.batch_review_button = QPushButton()
        self.batch_remove_button = QPushButton()
        self.batch_remove_completed_button = QPushButton()
        self.batch_open_button = QPushButton()
        self.batch_retry_button = QPushButton()
        self.batch_session_test_button = QPushButton()
        self.batch_session_test_button.setEnabled(False)
        self.batch_publish_button = QPushButton()
        self.batch_cancel_button = QPushButton()
        self.batch_cancel_button.setVisible(False)
        self.batch_publish_button.setEnabled(False)
        for button in (self.batch_review_button, self.batch_remove_button, self.batch_remove_completed_button, self.batch_open_button):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.batch_retry_button)
        actions.addWidget(self.batch_session_test_button)
        actions.addWidget(self.batch_publish_button)
        actions.addWidget(self.batch_cancel_button)
        root.addLayout(actions)
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        self.batch_status = QLabel()
        self.batch_status.setWordWrap(True)
        root.addWidget(self.batch_progress)
        root.addWidget(self.batch_status)
        self._publish_progress_event = None
        self._publish_wait_deadline = 0.0
        self.publish_countdown_timer = QTimer(self)
        self.publish_countdown_timer.setInterval(100)
        self.publish_countdown_timer.timeout.connect(self._refresh_publish_progress)
        self.batch_entries: list[dict[str, object]] = []
        self._gelbooru_publish_configured = True
        self._e621_publish_configured = False
        self.batch_back_button.clicked.connect(self._return_from_batch)
        self.batch_refresh_button.clicked.connect(self.batch_refresh_requested)
        self.batch_filter.currentIndexChanged.connect(self._render_batch_entries)
        self.batch_table.itemSelectionChanged.connect(self._update_batch_actions)
        self.batch_review_button.clicked.connect(self._request_batch_review)
        self.batch_remove_button.clicked.connect(self._request_batch_remove)
        self.batch_remove_completed_button.clicked.connect(self.batch_remove_completed_requested)
        self.batch_open_button.clicked.connect(self._request_batch_open)
        self.batch_publish_button.clicked.connect(self.batch_publish_requested)
        self.batch_retry_button.clicked.connect(self._request_batch_retry)
        self.batch_session_test_button.clicked.connect(self.batch_session_test_requested)
        self.batch_cancel_button.clicked.connect(self.batch_cancel_requested)
        self.batch_button = QToolButton()
        self.batch_button.setCheckable(True)
        self.batch_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.batch_button.setArrowType(Qt.ArrowType.RightArrow)
        self._batch_glow = QGraphicsDropShadowEffect(self.batch_button)
        self._batch_glow.setBlurRadius(24)
        self._batch_glow.setOffset(0, 0)
        self._batch_glow.setEnabled(False)
        self.batch_button.setGraphicsEffect(self._batch_glow)
        self.batch_button.installEventFilter(self)
        self._batch_attention = False
        self._first_publish_started = bool(self.settings.get("tagging_first_publish_started", False))
        self._publish_pulse_on = False
        self._publish_pulse_timer = QTimer(self)
        self._publish_pulse_timer.setInterval(900)
        self._publish_pulse_timer.timeout.connect(self._advance_publish_pulse)
        root = self.layout()
        root.removeWidget(self.title)
        self.page_header = QWidget()
        self.page_header_layout = QHBoxLayout(self.page_header)
        self.page_header_layout.setContentsMargins(0, 0, 0, 0)
        self.page_header_layout.addWidget(self.title)
        self.page_header_layout.addStretch(1)
        root.insertWidget(0, self.page_header)
        self.group.layout().addWidget(self.batch_button, 0, 7)
        self.batch_button.toggled.connect(self._toggle_batch)
        self._update_batch_actions()

    def show_batch(self) -> None:
        if not self.batch_button.isChecked():
            self.batch_button.setChecked(True)
        else:
            self._toggle_batch(True)

    def _toggle_batch(self, expanded: bool) -> None:
        self.batch_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        if expanded:
            self._batch_attention = False
            self._update_batch_attention()
            self.mode_stack.setCurrentWidget(self.batch_view)
            self._update_publish_pulse()
            self.batch_refresh_requested.emit()
        elif self.mode_stack.currentWidget() is self.batch_view:
            self.show_search()
            self._update_publish_pulse()

    def _return_from_batch(self) -> None:
        self.batch_button.setChecked(False)

    def show_batch_entries(self, entries: list[dict[str, object]]) -> None:
        self.batch_entries = list(entries)
        self._update_batch_attention()
        states = [str(entry["publish_state"].value) for entry in self.batch_entries]
        local_count = sum(
            entry["site"] is None or str(entry["publish_state"].value) == "reviewed"
            for entry in self.batch_entries
        )
        self._update_batch_counts(states, local_count)
        self._render_batch_entries()

    def mark_batch_added(self) -> None:
        self._batch_attention = True
        self._update_batch_attention()

    def _update_batch_attention(self) -> None:
        if not hasattr(self, "batch_button"):
            return
        count = sum(
            getattr(entry.get("publish_state"), "value", entry.get("publish_state"))
            in {"pending_publish", "failed", "skipped"}
            for entry in self.batch_entries
        )
        label = self.catalog.text("tagging.batch.button")
        self.batch_button.setText(f"{label} [{count}]" if count else label)
        attention = bool(count and self._batch_attention)
        self.batch_button.setStyleSheet(
            "QToolButton { background: palette(highlight); color: palette(highlighted-text); "
            "border: 3px solid palette(highlight); border-radius: 6px; "
            "padding: 5px 12px; font-weight: 700; }"
            if attention else ""
        )
        self._batch_glow.setColor(self.batch_button.palette().highlight().color())
        self._batch_glow.setEnabled(attention)
        if not count:
            self._batch_attention = False
        self._update_publish_pulse()

    def _update_publish_pulse(self) -> None:
        pending = any(
            getattr(entry.get("publish_state"), "value", entry.get("publish_state"))
            == "pending_publish" and self._has_changes(entry)
            for entry in self.batch_entries
        )
        active = (self.mode_stack.currentWidget() is self.batch_view and pending
                  and not self._first_publish_started
                  and self.batch_publish_button.isEnabled()
                  and not getattr(self, "_batch_publish_running", False))
        if active and not self._publish_pulse_timer.isActive():
            self._publish_pulse_timer.start()
            self._advance_publish_pulse()
        elif not active:
            self._publish_pulse_timer.stop()
            self._publish_pulse_on = False
            self.batch_publish_button.setStyleSheet("")

    def _advance_publish_pulse(self) -> None:
        self._publish_pulse_on = not self._publish_pulse_on
        self.batch_publish_button.setStyleSheet(
            "QPushButton { border: 2px solid palette(highlight); border-radius: 5px; }"
            if self._publish_pulse_on else ""
        )

    def mark_publish_started(self) -> None:
        if self._first_publish_started:
            return
        self._first_publish_started = True
        self.settings["tagging_first_publish_started"] = True
        self.search_settings_saved.emit({"tagging_first_publish_started": True})
        self._update_publish_pulse()

    def _plural(self, key: str, count: int) -> str:
        return self.catalog.text(f"{key}.one" if count == 1 else f"{key}.other", count=count)

    def _update_batch_counts(self, states: list[str] | None = None, local_count: int | None = None) -> None:
        states = states if states is not None else [str(entry["publish_state"].value) for entry in self.batch_entries]
        local_count = local_count if local_count is not None else sum(
            entry["site"] is None or str(entry["publish_state"].value) == "reviewed"
            for entry in self.batch_entries
        )
        self.batch_counts.setText(self.catalog.text(
            "tagging.batch.counts",
            pending=self._plural(
                "tagging.batch.count.pending",
                sum(
                    str(entry["publish_state"].value) == "pending_publish"
                    and bool(entry.get("additions") or entry.get("removals"))
                    for entry in self.batch_entries
                ),
            ),
            local=self._plural("tagging.batch.count.local", local_count),
            published=self._plural("tagging.batch.count.published", states.count("published")),
            failed=self._plural("tagging.batch.count.failed", states.count("failed")),
            skipped=self._plural("tagging.batch.count.skipped", states.count("skipped")),
        ))

    def _render_batch_entries(self, *_args) -> None:
        started = perf_counter()
        mode = str(self.batch_filter.currentData())
        visible = [
            entry
            for entry in self.batch_entries
            if mode == "all"
            or (
                mode == "local"
                and (entry["site"] is None or str(entry["publish_state"].value) == "reviewed")
            )
            or str(entry["publish_state"].value) == mode
        ]
        self.batch_table.setRowCount(len(visible))
        for row, entry in enumerate(visible):
            site = str(entry["site"] or self.catalog.text("tagging.batch.local"))
            post_id = entry["post_id"]
            identity = f"{site} #{post_id}" if post_id else self.catalog.text("tagging.batch.local_file")
            skipped = str(entry["publish_state"].value) == "skipped"
            shown_additions = entry.get("requested_additions") or ([] if skipped else entry["additions"])
            shown_removals = entry.get("requested_removals") or ([] if skipped else entry["removals"])
            additions = " ".join(shown_additions) or "—"
            removals = " ".join(shown_removals) or "—"
            state_text = self.catalog.text(
                f"tagging.batch.state.{entry['publish_state'].value}"
            )
            failure_reason = str(entry.get("failure_reason") or "")
            if str(entry["publish_state"].value) == "failed" and failure_reason:
                key = (
                    f"tagging.batch.failure.{failure_reason}"
                    if failure_reason in {"locked_image", "unexpected_global_redirect"}
                    else "tagging.batch.failure.other"
                )
                state_text = self.catalog.text(
                    "tagging.batch.failed_reason", reason=self.catalog.text(key)
                )
            elif str(entry["publish_state"].value) == "skipped":
                reason = str(entry.get("failure_reason") or entry.get("last_error") or "")
                state_text = self.catalog.text(
                    "tagging.batch.skipped_reason",
                    reason=(self.catalog.text("tagging.bulk.no_changes")
                            if reason == "no_effective_delta" else reason),
                )
            values = (
                identity,
                site,
                additions,
                removals,
                state_text,
                str(entry["reviewed_at"]),
                str(entry["item_id"]),
            )
            for column, value in enumerate(values):
                self.batch_table.setItem(row, column, QTableWidgetItem(value))
        self._update_batch_actions()
        self._perf_log("batch_state_refresh", started)

    def _selected_batch_ids(self) -> list[int]:
        return [
            int(self.batch_table.item(index.row(), 6).text())
            for index in self.batch_table.selectionModel().selectedRows()
            if self.batch_table.item(index.row(), 6) is not None
        ]

    def _selected_batch_entries(self) -> list[dict[str, object]]:
        selected = set(self._selected_batch_ids())
        return [entry for entry in self.batch_entries if int(entry["item_id"]) in selected]

    def batch_sites_present(self) -> tuple[str, ...]:
        """Return remote sites represented by actual batch rows."""
        return tuple(
            site
            for site in ("gelbooru", "e621")
            if any(entry.get("site") == site and entry.get("post_id") for entry in self.batch_entries)
        )

    @staticmethod
    def _has_changes(entry: dict[str, object]) -> bool:
        return bool(entry.get("additions") or entry.get("removals"))

    def _update_batch_actions(self) -> None:
        entries = self._selected_batch_entries() if hasattr(self, "batch_entries") else []
        self.batch_review_button.setEnabled(len(entries) == 1)
        self.batch_open_button.setEnabled(len(entries) == 1 and entries[0]["site"] is not None)
        self.batch_remove_button.setEnabled(bool(entries))
        self.batch_remove_completed_button.setEnabled(any(
            str(entry["publish_state"].value) == "published"
            for entry in getattr(self, "batch_entries", [])
        ))
        running = getattr(self, "_batch_publish_running", False)
        pending_gelbooru = any(
            entry["site"] == "gelbooru"
            and entry["post_id"]
            and str(entry["publish_state"].value) == "pending_publish"
            and self._has_changes(entry)
            and int(entry.get("publish_attempts", 0)) < MAX_PUBLISH_ATTEMPTS
            for entry in getattr(self, "batch_entries", [])
        )
        pending_e621 = any(
            entry["site"] == "e621"
            and entry["post_id"]
            and str(entry["publish_state"].value) == "pending_publish"
            and self._has_changes(entry)
            for entry in getattr(self, "batch_entries", [])
        )
        gelbooru_publishable = pending_gelbooru and self._gelbooru_publish_configured
        e621_publishable = pending_e621 and self._e621_publish_configured
        publishable = gelbooru_publishable or e621_publishable
        self.batch_publish_button.setEnabled(publishable and not running)
        self._update_publish_pulse()
        self.batch_publish_button.setToolTip(
            self.catalog.text("tagging.publish.e621_credentials_missing")
            if pending_e621 and not self._e621_publish_configured and not gelbooru_publishable
            else ""
        )
        self.batch_session_test_button.setEnabled(bool(self.batch_sites_present()) and not running)
        self.batch_retry_button.setEnabled(
            not running and any(can_retry_publication(entry) for entry in self.batch_entries)
        )

    def set_e621_publish_configured(self, configured: bool) -> None:
        self._e621_publish_configured = bool(configured)
        self._update_batch_actions()

    def set_gelbooru_publish_configured(self, configured: bool) -> None:
        self._gelbooru_publish_configured = bool(configured)
        self._update_batch_actions()

    def _request_batch_review(self) -> None:
        ids = self._selected_batch_ids()
        if len(ids) == 1:
            self.batch_review_requested.emit(ids[0])

    def _request_batch_remove(self) -> None:
        ids = self._selected_batch_ids()
        if ids:
            self.batch_remove_requested.emit(ids)

    def _request_batch_open(self) -> None:
        ids = self._selected_batch_ids()
        if len(ids) == 1:
            self.batch_open_requested.emit(ids[0])

    def _request_batch_retry(self) -> None:
        ids = list(dict.fromkeys(
            int(entry["item_id"]) for entry in self.batch_entries
            if can_retry_publication(entry)
        ))
        if ids:
            self.batch_retry_requested.emit(ids)

    def set_batch_publish_running(self, running: bool) -> None:
        self._batch_publish_running = running
        self.batch_progress.setVisible(running)
        self.batch_cancel_button.setVisible(running)
        if not running:
            self.batch_progress.setValue(0)
            self.publish_countdown_timer.stop()
            self._publish_wait_deadline = 0.0
        self._update_batch_actions()

    def set_batch_publish_progress(self, current: int, total: int, post_id: str) -> None:
        self.batch_progress.setRange(0, max(1, total))
        self.batch_progress.setValue(max(0, current - 1))

    @staticmethod
    def format_duration(seconds: float) -> str:
        total = max(0, round(seconds))
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d} h {minutes:02d} min {seconds:02d} s"
        if minutes:
            return f"{minutes:d} min {seconds:02d} s"
        return f"{seconds:d} s"

    def show_batch_publish_progress(self, event) -> None:
        self._publish_progress_event = event
        self.batch_progress.setRange(0, max(1, event.total))
        self.batch_progress.setValue(event.processed)
        if event.phase == "waiting":
            self._publish_wait_deadline = perf_counter() + event.wait_seconds
            self.publish_countdown_timer.start()
        elif event.phase in {"sending", "completed"}:
            self._publish_wait_deadline = 0.0
            self.publish_countdown_timer.stop()
        self._refresh_publish_progress()

    def _refresh_publish_progress(self) -> None:
        event = self._publish_progress_event
        if event is None:
            return
        countdown = max(0.0, self._publish_wait_deadline - perf_counter())
        if self._publish_wait_deadline and countdown <= 0:
            self.publish_countdown_timer.stop()
        elapsed_wait = max(0.0, event.wait_seconds - countdown) if event.phase == "waiting" else 0.0
        eta_seconds = max(0.0, event.estimated_remaining_seconds - elapsed_wait)
        target = event.post_id.replace(":", " #", 1) or "—"
        result = (
            self.catalog.text(f"tagging.publish.result.{event.result}")
            if event.result else "—"
        )
        wait = (
            self.catalog.text("tagging.publish.sending")
            if event.phase == "sending"
            else self.catalog.text("tagging.publish.wait", seconds=countdown)
            if event.phase == "waiting"
            else "—"
        )
        self.batch_status.setText(
            self.catalog.text(
                "tagging.publish.detail",
                processed=event.processed,
                total=event.total,
                remaining=event.remaining,
                target=target,
                result=result,
                wait=wait,
                eta=self.format_duration(eta_seconds),
            )
        )

    def show_batch_publish_summary(self, text: str) -> None:
        self.batch_status.setText(text)

    def _emit_manual_add(self) -> None:
        value = self.manual_tag.text().strip()
        if value:
            self.manual_add_requested.emit(value)

    def _schedule_manual_lookup(self, value: str) -> None:
        self.manual_lookup_timer.stop()
        if len(value.strip()) < AUTOCOMPLETE_MIN_CHARS:
            self.manual_suggestion_model.setStringList([])
            return
        self.manual_lookup_timer.start()

    def _emit_manual_lookup(self) -> None:
        value = self.manual_tag.text().strip()
        if len(value) >= AUTOCOMPLETE_MIN_CHARS:
            self.manual_lookup_requested.emit(value)

    def _select_manual_suggestion(self, label: str) -> None:
        self.manual_tag.setText(self._manual_suggestion_values.get(label, label))

    def _select_manual_suggestion_index(self, index: QModelIndex) -> None:
        value = str(index.data(CANONICAL_TAG_ROLE) or "")
        if value:
            self.manual_tag.setText(value)
            QTimer.singleShot(0, lambda value=value: self.manual_tag.setText(value))

    def set_manual_suggestions(
        self, suggestions: list[TagSuggestion | str | tuple[str, str | None]]
    ) -> None:
        values: list[str] = []
        structured: list[TagSuggestion] = []
        self._manual_suggestion_values = {}
        for suggestion in suggestions:
            if isinstance(suggestion, TagSuggestion):
                value, alias_source = suggestion.canonical_name, suggestion.alias_source
                item = suggestion
            elif isinstance(suggestion, str):
                value, alias_source = suggestion, None
                item = TagSuggestion(self.active_site, value, value, "unknown", 0)
            else:
                value, alias_source = suggestion
                item = TagSuggestion(
                    self.active_site, value, value, "unknown", 0, alias_source
                )
            label = suggestion_display_text(item)
            values.append(label)
            structured.append(item)
            self._manual_suggestion_values[label] = value
            self._manual_suggestion_values[value] = value
            if alias_source:
                self._manual_suggestion_values[f"{value} ← {alias_source}"] = value
        populate_suggestion_model(self.manual_suggestion_model, structured, self.palette())
        if values and self.manual_tag.hasFocus():
            show_tag_completer(self.manual_completer)

    def clear_manual_entry(self) -> None:
        self.manual_tag.clear()
        self.manual_suggestion_model.setStringList([])
        self._manual_suggestion_values = {}

    def refresh_theme(self) -> None:
        if hasattr(self, "manual_suggestion_model"):
            refresh_suggestion_model(self.manual_suggestion_model, self.palette())

    def _copy_and_open(self) -> None:
        """Phase 2A primary gesture: persist locally and advance, never publish."""
        if self.current_post_id or getattr(self, "_batch_local_item_id", None) is not None:
            self.review_validation_requested.emit()

    def show_review_completion(self, message: str) -> None:
        self.analysis_state.setText(message)

    def finish_individual_review(self) -> None:
        """End a loaded sequence with no image-less review left behind."""
        self.show_search()
        self.current_post_id = None
        self._displayed_post_id = None
        self.current_result_index = -1
        self.current_post = {}
        self._all_suggestion_rows = []
        self.suggestions.setRowCount(0)
        self.preview.set_image(None)
        self._review_ready = False
        self._update_review_action_states()
        self._update_counter()
        self.page_status.show_message(self.catalog.text("tagging.review.pool_finished"))

    def show_local_batch_review(
        self, item_id: int, image_path, original_tags: list[str], final_tags: list[str]
    ) -> None:
        self._review_origin = "batch"
        self._initial_review_tags = None
        self._batch_local_item_id = item_id
        self.current_post_id = None
        self._displayed_post_id = None
        self.current_post = {}
        self.review_title.setText(self.catalog.text("tagging.review.local_item", item_id=item_id))
        self.show_local_review(self.catalog.text("tagging.analysis.reviewed"), image_path, original_tags, [], [], final_tags)
        self.copy_open_button.setEnabled(True)

    def show_local_review(
        self, state, image_path, source_tags, rows, suggested_additions, final_tags,
        *, preview_data: bytes | None = None,
    ) -> None:
        started = perf_counter()
        if getattr(self, "_initial_review_tags", None) is None:
            self._initial_review_tags = frozenset(canonical_tag_value(tag) for tag in source_tags)
        rows = [dict(row, present_initially=(
            canonical_tag_value(str(row["tag"])) in self._initial_review_tags
            or str(row["id"]).startswith("existing:")
        )) for row in rows]
        super().show_local_review(
            state,
            image_path,
            source_tags,
            rows,
            suggested_additions,
            final_tags,
            preview_data=preview_data,
        )
        editable = state.startswith((self.catalog.text("tagging.analysis.ready"), self.catalog.text("tagging.analysis.reviewed")))
        self.manual_tag.setEnabled(editable)
        self.manual_add.setEnabled(editable)
        self._perf_log("filter_review_refresh", started)

    def retranslate(self) -> None:
        super().retranslate()
        text = self.catalog.text
        self.title.setText(text("nav.tagging"))
        if hasattr(self, "active_filters_label"):
            self.active_filters_label.setText(text("tagging.active_filters"))
        active_site = getattr(self, "active_site", "gelbooru")
        self.group.setTitle(
            text("tagging.group_site", site=site_definition(active_site).display_name)
        )
        self.query_label.setText(text("tagging.search_label"))
        if hasattr(self.query, "retranslate"):
            self.query.retranslate()
        if hasattr(self, "threshold_summary"):
            self._update_threshold_summary()
        if hasattr(self, "bulk_group"):
            self.bulk_group.setTitle(text("tagging.bulk.title"))
            self.bulk_palette_label.setText(text("tagging.bulk.palette"))
            self.bulk_palette.input.setPlaceholderText(text("tagging.bulk.placeholder"))
            self.bulk_palette.active_tooltip = text("tagging.bulk.checkbox_tip")
            for chip in self.bulk_palette._chips.values():
                chip.active.setToolTip(self.bulk_palette.active_tooltip)
            self._update_bulk_action()
            bulk_expanded = not self.bulk_group.isHidden()
            self.bulk_collapse.setToolTip(text("tagging.bulk.collapse"))
            self.bulk_collapse.setAccessibleName(text("tagging.bulk.collapse"))
            self.bulk_reopen.setToolTip(text("tagging.bulk.expand"))
            self.bulk_reopen.setAccessibleName(text("tagging.bulk.expand"))
            self.bulk_collapse.setVisible(bulk_expanded)
        if hasattr(self, "hide_queued_results"):
            self.hide_queued_results.setText(text("tagging.results.hide_queued"))
            self.checkbox_selection.setText(text("tagging.results.checkbox_selection"))
            self.bulk_intent_help.setText(text("tagging.bulk.intent_help"))
        if hasattr(self, "wd14_cancel_button"):
            self.wd14_cancel_button.setText(text("tagging.wd14.cancel"))
        if hasattr(self, "site_label"):
            self.site_label.setText(text("tagging.site"))
            if hasattr(self, "batch_session_test_button"):
                self._update_batch_actions()
        if not hasattr(self, "manual_add_label"):
            return
        self.copy_open_button.setText(text("tagging.review.validate_next"))
        self.copy_open_button.setToolTip(text("tagging.review.validate_next_tip"))
        self.open_button.setText(
            text("tagging.review.open_site", site=site_definition(self.active_site).display_name)
        )
        self.manual_add_label.setText(text("tagging.review.manual_label")); self.manual_tag.setPlaceholderText(text("tagging.review.manual_placeholder")); self.manual_add.setText(text("tagging.review.add"))
        self.reanalyze_button.setText(text("tagging.review.reanalyze"))
        self.reanalyze_button.setToolTip(text("tagging.review.reanalyze_tip"))
        self.suggestions.setHorizontalHeaderLabels(tuple(text(f"tagging.review.header.{key}") for key in ("tag", "confidence", "match", "category", "decision", "id")) + ("? / ✓ / ✕",))
        for row in range(self.suggestions.rowCount()):
            item = self.suggestions.item(row, 4)
            if item is not None and item.data(Qt.ItemDataRole.UserRole):
                item.setText(text(f"tagging.review.decision.{item.data(Qt.ItemDataRole.UserRole)}"))
        if not hasattr(self, "batch_table"):
            return
        selected_ids = self._selected_batch_ids()
        self.batch_back_button.setText(text("tagging.batch.back")); self.batch_refresh_button.setText(text("tagging.batch.refresh")); self.batch_filter_label.setText(text("tagging.batch.label"))
        for index, key in enumerate(("all", "pending", "published", "failed", "local", "skipped")): self.batch_filter.setItemText(index, text(f"tagging.batch.filter.{key}"))
        for index, key in enumerate(("identity", "site", "additions", "removals", "state", "reviewed_at", "item")): self.batch_table.horizontalHeaderItem(index).setText(text(f"tagging.batch.header.{key}"))
        self.batch_review_button.setText(text("tagging.batch.review")); self.batch_remove_button.setText(text("tagging.batch.remove")); self.batch_remove_completed_button.setText(text("tagging.batch.remove_completed")); self.batch_open_button.setText(text("tagging.batch.open")); self.batch_retry_button.setText(text("tagging.batch.retry")); self.batch_session_test_button.setText(text("tagging.batch.session_test")); self.batch_publish_button.setText(text("tagging.batch.publish")); self.batch_cancel_button.setText(text("tagging.batch.cancel")); self._update_batch_attention()
        self.batch_table.set_empty_text(text("table.empty_batch")); self._update_batch_counts(); self._render_batch_entries()
        for row in range(self.batch_table.rowCount()):
            item = self.batch_table.item(row, 6)
            if item is not None and int(item.text()) in selected_ids:
                self.batch_table.selectRow(row)
        if hasattr(self, "batch_table"):
            self.batch_table.set_empty_text(self.catalog.text("table.empty_batch"))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in (
            self.accept_shortcut,
            self.reject_shortcut,
            self.undo_shortcut,
            self.redo_shortcut,
            self.redo_alias_shortcut,
        ):
            shortcut.setEnabled(True)

    def closeEvent(self, event) -> None:
        for shortcut in (
            self.accept_shortcut,
            self.reject_shortcut,
            self.undo_shortcut,
            self.redo_shortcut,
            self.redo_alias_shortcut,
        ):
            shortcut.setEnabled(False)
        super().closeEvent(event)
