"""Primary Tagging presentation entry point.

The current implementation deliberately inherits the proven review screen while
the new workflow is introduced here incrementally.  The frozen implementation
itself lives in :mod:`tagging_legacy_page` and remains available in navigation.
"""

from __future__ import annotations

from time import perf_counter

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.batch_publisher import MAX_PUBLISH_ATTEMPTS
from booruflow.application.tagging import TaggingRequest, parse_review_row_token
from booruflow.application.targeted_wd14 import CONFIDENCE_BUCKETS, confidence_bucket
from booruflow.domain.booru_sites import site_definition
from booruflow.presentation.pyside6.status_bar import PageStatus
from booruflow.presentation.pyside6.tagging_legacy_page import SuggestionItem, TaggingLegacyPage
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
    state = getattr(entry.get("publish_state"), "value", entry.get("publish_state"))
    return bool(entry.get("additions") or entry.get("removals")) and state in {
        "pending_publish", "publishing", "published",
    }


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


class TokenLineEdit(QLineEdit):
    remove_last_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Backspace and not self.text():
            self.remove_last_requested.emit()
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


class TagTokenEditor(QWidget):
    """Compact autocomplete entry that commits validated text as removable chips."""

    tags_changed = Signal()
    lookup_requested = Signal(str)
    tag_added = Signal(str)
    analysis_requested = Signal(str)
    CHIP_TEXT_MAX_WIDTH = 180

    def __init__(self, parent=None, *, confidence_actions: bool = False) -> None:
        super().__init__(parent)
        self.confidence_actions = confidence_actions
        self._tags: list[str] = []
        self._chips: dict[str, QPushButton] = {}
        self._suggestion_values: dict[str, str] = {}
        self.editor_layout = QVBoxLayout(self)
        self.editor_layout.setContentsMargins(4, 2, 4, 2)
        self.editor_layout.setSpacing(4)
        self.chips = QWidget()
        self.chips.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.chips_layout = FlowLayout(self.chips)
        self.chips.hide()
        self.editor_layout.addWidget(self.chips)
        self.input = TokenLineEdit()
        self.input.setMinimumWidth(120)
        self.editor_layout.addWidget(self.input)
        self.model = QStringListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.input.setCompleter(self.completer)
        self.lookup_timer = QTimer(self)
        self.lookup_timer.setSingleShot(True)
        self.lookup_timer.setInterval(200)
        self.lookup_timer.timeout.connect(self._emit_lookup)
        self.input.textEdited.connect(self._schedule_lookup)
        self.input.returnPressed.connect(self.commit_input)
        self.input.remove_last_requested.connect(self.remove_last)
        self.completer.activated[str].connect(self._commit_suggestion)

    def tags(self) -> list[str]:
        return list(self._tags)

    def add_tag(self, value: str) -> bool:
        normalized = value.strip().replace(" ", "_")
        if not normalized or normalized in self._tags:
            self.input.clear()
            return False
        self._tags.append(normalized)
        chip = QWidget()
        chip.setProperty("tagChip", True)
        chip.setStyleSheet(
            "QWidget[tagChip='true'] { border: 1px solid palette(mid); "
            "border-radius: 8px; padding: 2px 7px; }"
        )
        chip_row = QHBoxLayout(chip); chip_row.setContentsMargins(3, 0, 3, 0); chip_row.setSpacing(2)
        chip.label = QLabel()
        metrics = QFontMetrics(chip.label.font())
        chip.label.setText(
            metrics.elidedText(
                normalized, Qt.TextElideMode.ElideRight, self.CHIP_TEXT_MAX_WIDTH
            )
        )
        chip.label.setToolTip(normalized)
        chip.label.setMaximumWidth(self.CHIP_TEXT_MAX_WIDTH)
        chip_row.addWidget(chip.label)
        chip.analysis = QPushButton("?"); chip.analysis.setFlat(True); chip.analysis.setFixedWidth(24)
        chip.analysis.setVisible(self.confidence_actions)
        chip.analysis.clicked.connect(
            lambda _checked=False, tag=normalized: self.analysis_requested.emit(tag)
        )
        chip_row.addWidget(chip.analysis)
        chip.remove = QPushButton("×"); chip.remove.setFlat(True); chip.remove.setFixedWidth(24)
        chip.remove.clicked.connect(lambda _checked=False, tag=normalized: self.remove_tag(tag))
        chip.click = chip.remove.click
        chip_row.addWidget(chip.remove)
        self._chips[normalized] = chip
        self.chips_layout.addWidget(chip)
        self.chips.show()
        self.chips_layout.invalidate()
        self.chips.updateGeometry()
        self.updateGeometry()
        self.input.clear(); self.model.setStringList([])
        self.tag_added.emit(normalized); self.tags_changed.emit()
        return True

    def set_analysis_state(self, tag: str, state: str) -> None:
        chip = self._chips.get(tag)
        if chip is None or not self.confidence_actions:
            return
        symbols = {"idle": "?", "running": "⏳", "complete": "✓", "failed": "!"}
        chip.analysis.setText(symbols.get(state, "?"))
        chip.analysis.setEnabled(state != "running")

    def remove_tag(self, value: str) -> bool:
        if value not in self._tags:
            return False
        self._tags.remove(value)
        chip = self._chips.pop(value)
        self.chips_layout.removeWidget(chip); chip.deleteLater()
        self.chips.setVisible(bool(self._tags))
        self.chips_layout.invalidate()
        self.chips.updateGeometry()
        self.updateGeometry()
        self.tags_changed.emit()
        return True

    def remove_last(self) -> None:
        if self._tags:
            self.remove_tag(self._tags[-1])

    def commit_input(self) -> None:
        self.add_tag(self.input.text())

    def set_suggestions(self, suggestions: list[str] | list[tuple[str, str | None]]) -> None:
        labels: list[str] = []
        self._suggestion_values = {}
        for suggestion in suggestions:
            value, alias = (suggestion, None) if isinstance(suggestion, str) else suggestion
            label = f"{value} ← {alias}" if alias else value
            labels.append(label); self._suggestion_values[label] = value
        self.model.setStringList(labels)
        if labels and self.input.hasFocus():
            self.completer.complete()

    def _commit_suggestion(self, label: str) -> None:
        self.add_tag(self._suggestion_values.get(label, label))

    def _schedule_lookup(self, value: str) -> None:
        self.lookup_timer.stop()
        if len(value.strip()) < 2:
            self.model.setStringList([])
            return
        self.lookup_timer.start()

    def _emit_lookup(self) -> None:
        value = self.input.text().strip()
        if len(value) >= 2:
            self.lookup_requested.emit(value)


class TaggingPage(TaggingLegacyPage):
    """Transition entry point for the new Tagging workflow."""

    undo_requested = Signal()
    redo_requested = Signal()
    manual_lookup_requested = Signal(str)
    manual_add_requested = Signal(str)
    review_validation_requested = Signal()
    batch_refresh_requested = Signal()
    batch_review_requested = Signal(int)
    batch_remove_requested = Signal(list)
    batch_open_requested = Signal(int)
    batch_publish_requested = Signal()
    batch_retry_requested = Signal(list)
    batch_session_test_requested = Signal()
    batch_cancel_requested = Signal()
    reanalyze_requested = Signal()
    site_changed = Signal(str)
    bulk_apply_requested = Signal(list, list, list)
    bulk_lookup_requested = Signal(str, str)
    targeted_wd14_requested = Signal(str, list)
    targeted_wd14_cancel_requested = Signal()
    search_settings_saved = Signal(object)

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
        self.query = QLineEdit(str(self.settings.get("tagging_query", "rating:general")))
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
        controls.addWidget(self.parameter_row, 1, 0, 1, 6)
        self.spins["maximum"].setMinimum(self.spins["minimum"].value())
        self.spins["minimum"].valueChanged.connect(self._minimum_changed)
        self.spins["maximum"].valueChanged.connect(self._update_threshold_summary)
        self.threshold_summary = QLabel()
        self.threshold_summary.setProperty("preserveHorizontalSize", True)
        controls.addWidget(self.threshold_summary, 2, 0, 1, 6)
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
        self.results = QWidget()
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.results_scroll.setWidget(self.results)
        layout.addWidget(self.results_scroll, 1)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.page_status = PageStatus("tagging", self)
        self._displayed_post_id: int | None = None
        self.open_button.clicked.disconnect()
        self.open_button.clicked.connect(self._open_current_post)
        configured_site = str(self.settings.get("tagging_site", "gelbooru"))
        self.active_site = configured_site if configured_site in {"gelbooru", "e621"} else "gelbooru"
        self._all_search_results: list[dict] = []
        self._batch_entries_by_post: dict[int, dict[str, object]] = {}
        self._confidence_tag = ""
        self._confidence_scores: dict[int, float] = {}
        self._confidence_failed: set[int] = set()
        self._build_site_selector()
        self._build_result_filter()
        self._build_bulk_editor()
        self._reviewed_post_ids: set[int] = set()
        self._suggestion_id_column = 5
        self.suggestions.setColumnCount(6)
        self.suggestions.setHorizontalHeaderLabels(
            (
                "Tag",
                "Confidence",
                "Origin / match",
                "Category",
                "Decision",
                "ID",
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
        self._build_reanalyze_action()
        self._build_batch_view()
        self._thumbnail_levels = (96, 128, 160, 192, 256, 320)
        configured_size = int(self.settings.get("tagging_thumbnail_size", 160))
        self._thumbnail_size = min(self._thumbnail_levels, key=lambda value: abs(value - configured_size))
        self.results_scroll.viewport().installEventFilter(self)
        self.select_all_shortcut = QShortcut(QKeySequence.StandardKey.SelectAll, self.results_scroll)
        self.select_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.select_all_shortcut.activated.connect(self.select_all_results)
        self.retranslate()

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

    def _result_filter_toggled(self, checked: bool) -> None:
        self.settings["tagging_hide_queued_results"] = bool(checked)
        self._apply_result_filter()

    def _build_bulk_editor(self) -> None:
        self.bulk_group = QGroupBox()
        layout = QVBoxLayout(self.bulk_group)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.hide_queued_results)
        filter_row.addStretch(1)
        filter_row.addWidget(self.result_filter_counts)
        layout.addLayout(filter_row)
        add_row = QHBoxLayout(); self.bulk_add_label = QLabel(); self.bulk_add = TagTokenEditor(confidence_actions=True)
        remove_row = QHBoxLayout(); self.bulk_remove_label = QLabel(); self.bulk_remove = TagTokenEditor()
        self.bulk_add_label.setProperty("preserveHorizontalSize", True)
        self.bulk_remove_label.setProperty("preserveHorizontalSize", True)
        add_row.addWidget(self.bulk_add_label); add_row.addWidget(self.bulk_add, 1)
        self.bulk_selection_count = QLabel()
        self.bulk_selection_count.hide()
        self.bulk_apply = QPushButton()
        remove_row.addWidget(self.bulk_remove_label)
        remove_row.addWidget(self.bulk_remove, 1)
        remove_row.addWidget(self.bulk_apply, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(add_row); layout.addLayout(remove_row)
        self.wd14_progress = QProgressBar(); self.wd14_progress.setVisible(False)
        layout.addWidget(self.wd14_progress)
        self.bulk_apply.clicked.connect(self._emit_bulk_apply)
        self.search_view.layout().insertWidget(self.search_view.layout().count() - 1, self.bulk_group)
        self.bulk_add.tags_changed.connect(self._update_bulk_action)
        self.bulk_remove.tags_changed.connect(self._update_bulk_action)
        self.bulk_add.tag_added.connect(self.bulk_remove.remove_tag)
        self.bulk_remove.tag_added.connect(self.bulk_add.remove_tag)
        self.bulk_add.lookup_requested.connect(
            lambda text: self.bulk_lookup_requested.emit("add", text)
        )
        self.bulk_add.analysis_requested.connect(self._request_targeted_wd14)
        self.bulk_add.tags_changed.connect(self._confidence_tags_changed)
        self.bulk_remove.lookup_requested.connect(
            lambda text: self.bulk_lookup_requested.emit("remove", text)
        )
        self._update_bulk_action()

    def _request_targeted_wd14(self, tag: str) -> None:
        posts = list(self.result_posts)
        if not posts:
            return
        self._confidence_tag = tag
        self.bulk_add.set_analysis_state(tag, "running")
        self.wd14_progress.setRange(0, len(posts)); self.wd14_progress.setValue(0)
        self.wd14_progress.setVisible(True)
        self.page_status.set_state("analyzing")
        self.page_status.show_message(
            self.catalog.text("tagging.wd14.starting", tag=tag), timeout_ms=0
        )
        self.page_status.set_progress(
            0,
            len(posts),
            accessible_text=self.catalog.text("tagging.wd14.progress_accessible"),
        )
        self.targeted_wd14_requested.emit(tag, posts)

    def _confidence_tags_changed(self) -> None:
        if self._confidence_tag and self._confidence_tag not in self.bulk_add.tags():
            self.clear_targeted_wd14()

    def clear_targeted_wd14(self) -> None:
        previous = self._confidence_tag
        self._confidence_tag = ""; self._confidence_scores = {}; self._confidence_failed = set()
        if previous:
            self.bulk_add.set_analysis_state(previous, "idle")
        self.wd14_progress.setVisible(False)
        self.page_status.clear_progress()
        self.page_status.clear_message()
        self.page_status.set_state("ready")
        self.targeted_wd14_cancel_requested.emit()
        if self._all_search_results:
            self._apply_result_filter()

    def show_targeted_wd14_unavailable(self, tag: str, message: str) -> None:
        self.bulk_add.set_analysis_state(tag, "failed")
        self.wd14_progress.setVisible(False)
        self.page_status.clear_progress()
        self.page_status.set_state("ready")
        self.page_status.show_message(message, timeout_ms=6_000, log=True)
        if self._confidence_tag == tag:
            self._confidence_tag = ""
            self._confidence_scores = {}; self._confidence_failed = set()
            if self._all_search_results:
                self._apply_result_filter()

    def set_targeted_wd14_progress(self, progress) -> None:
        self.wd14_progress.setRange(0, progress.total)
        self.wd14_progress.setValue(progress.completed)
        self.wd14_progress.setFormat(f"{progress.completed} / {progress.total}")
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

    def show_targeted_wd14_result(self, result) -> None:
        if result.tag != self._confidence_tag:
            return
        self._confidence_scores = dict(result.scores)
        self._confidence_failed = set(result.failed_post_ids)
        self.bulk_add.set_analysis_state(result.tag, "complete")
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

    def _result_sections(self, posts: list[dict]) -> list[tuple[str, list[dict]]]:
        if not self._confidence_tag:
            return super()._result_sections(posts)
        grouped = {key: [] for key in CONFIDENCE_BUCKETS}
        for post in posts:
            post_id = int(post.get("id", 0))
            key = confidence_bucket(
                self._confidence_scores.get(post_id), failed=post_id in self._confidence_failed
            )
            grouped[key].append(post)
        return [
            (
                self.catalog.text(f"tagging.wd14.bucket.{key}", count=len(grouped[key])),
                sorted(
                    grouped[key],
                    key=lambda post: self._confidence_scores.get(int(post.get("id", 0)), -1),
                    reverse=True,
                ),
            )
            for key in CONFIDENCE_BUCKETS if grouped[key]
        ]

    def _emit_bulk_apply(self) -> None:
        posts = self.selected_posts()
        additions = self.bulk_add.tags()
        removals = self.bulk_remove.tags()
        if posts and (additions or removals): self.bulk_apply_requested.emit(posts, additions, removals)

    def selection_changed(self) -> None:
        self._update_bulk_action()

    def _update_bulk_action(self) -> None:
        count = len(self.selected_posts()) if hasattr(self, "result_buttons") else 0
        has_delta = bool(self.bulk_add.tags() or self.bulk_remove.tags()) if hasattr(self, "bulk_add") else False
        self.bulk_apply.setEnabled(count > 0 and has_delta)
        self.bulk_apply.setText(self.catalog.text("tagging.bulk.apply", count=count))
        self.bulk_selection_count.setText(
            self.catalog.text("tagging.bulk.selected", count=count)
        )

    def set_bulk_suggestions(
        self, target: str, suggestions: list[str] | list[tuple[str, str | None]]
    ) -> None:
        editor = self.bulk_add if target == "add" else self.bulk_remove
        editor.set_suggestions(suggestions)

    def bulk_lookup_text(self, target: str) -> str:
        editor = self.bulk_add if target == "add" else self.bulk_remove
        return editor.input.text().strip()

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
        self.site_changed.emit(site)

    def _start(self) -> None:
        try:
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
        super()._open_result(index, fallback)
        if self.current_post_id is not None:
            self._displayed_post_id = int(self.current_post_id)
            definition = site_definition(self.active_site)
            self.review_title.setText(f"{definition.display_name} #{self.current_post_id}")
        self._perf_log("post_selection", started)

    def _open_current_post(self) -> None:
        if self._displayed_post_id is not None:
            self._open_post(self._displayed_post_id)

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
        if self.browser_launcher:
            self.browser_launcher.open(url)
        else:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))

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
        selected_id = self._selected_observation_id()
        decision = str(self.decision_filter.currentData())
        visible = {"accepted": {"accepted", "keep"}, "rejected": {"rejected", "remove"}}.get(
            decision, {decision}
        )
        rows = [
            row
            for row in self._all_suggestion_rows
            if decision == "all" or row["decision"] in visible
        ]
        self.suggestions.setSortingEnabled(False)
        self.suggestions.clearContents()
        self.suggestions.setRowCount(len(rows))
        decision_order = {"unreviewed": 0, "accepted": 1, "keep": 1, "rejected": 2, "remove": 2}
        match_order = {
            "exact": 0,
            "mapping": 1,
            self.catalog.text("tagging.match.already_present").casefold(): 2,
            self.catalog.text("tagging.match.missing").casefold(): 3,
            self.catalog.text("tagging.match.not_applicable").casefold(): 4,
        }
        for row_index, row in enumerate(rows):
            confidence = float(row["confidence"] or -1)
            match_text = str(row["match"])
            match_key = next(
                (rank for name, rank in match_order.items() if name in match_text.casefold()), 9
            )
            token_kind, token_value = parse_review_row_token(row["id"])
            category = str(row.get("category", ""))
            values = (
                (row["tag"], str(row["tag"]).casefold()),
                (row["confidence"], confidence),
                (match_text, match_key),
                (category, int(category) if category.isdigit() else -1),
                (self.catalog.text(f"tagging.review.decision.{row['decision']}"), decision_order.get(row["decision"], 9)),
                (str(row["id"]), (token_kind, token_value)),
            )
            for column, (text, key) in enumerate(values):
                item = SuggestionItem(str(text), key)
                if column == 4:
                    item.setData(Qt.ItemDataRole.UserRole, row["decision"])
                self.suggestions.setItem(row_index, column, item)
        self.suggestions.setSortingEnabled(True)
        self.suggestions.sortItems(self._sort_column, self._sort_order)
        if not getattr(self, "_suggestion_columns_sized", False):
            self.suggestions.resizeColumnsToContents()
            self._suggestion_columns_sized = True
        target_id = self._pending_next_id if self._pending_fallback_row is not None else selected_id
        selected_row = next(
            (
                row
                for row in range(self.suggestions.rowCount())
                if target_id is not None and self.suggestions.item(row, 5).text() == str(target_id)
            ),
            -1,
        )
        if selected_row < 0 and self.suggestions.rowCount():
            selected_row = (
                0
                if self._pending_fallback_row is None
                else min(self._pending_fallback_row, self.suggestions.rowCount() - 1)
            )
        if selected_row >= 0:
            self.suggestions.selectRow(selected_row)
            self.suggestions.setFocus()
        self._pending_next_id = None
        self._pending_fallback_row = None
        self._update_review_action_states()
        self._perf_log("proposal_table_rebuild", started)

    def _set_preview_media(self, path) -> None:
        started = perf_counter()
        super()._set_preview_media(path)
        self._perf_log("image_decode_scale", started)

    def show_results(self, posts: list[dict]) -> None:
        self._all_search_results = list(posts)
        self._apply_result_filter()

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
            self._all_search_results and previous_queued != new_queued and not incremental
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
        return removed

    def _queued_result_ids(self) -> set[int]:
        return {
            post_id
            for post_id, entry in self._batch_entries_by_post.items()
            if queued_result_entry(entry)
        }

    def _apply_result_filter(self) -> None:
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
        super().show_results(visible)
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
            if self._confidence_tag and post_id in self._confidence_scores:
                score = round(self._confidence_scores[post_id] * 100)
                second += f" · WD14 {score} %"
            button.setText(f"{first}\n{second}")
            details = [*(f"+ {tag}" for tag in additions), *(f"- {tag}" for tag in removals)]
            button.setToolTip(
                self.catalog.text("tagging.bulk.tooltip") + "\n" + "\n".join(details)
                if details else ""
            )
        self._update_counter()

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
            self.show_review_completion(self.catalog.text("tagging.review.pool_finished"))
        else:
            self._open_result(target)
        return True

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

        self.manual_suggestion_model = QStringListModel(self)
        self.manual_completer = QCompleter(self.manual_suggestion_model, self)
        self.manual_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.manual_completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.manual_tag.setCompleter(self.manual_completer)
        self._manual_suggestion_values: dict[str, str] = {}
        self.manual_completer.activated[str].connect(self._select_manual_suggestion)
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
        self.batch_open_button = QPushButton()
        self.batch_retry_button = QPushButton()
        self.batch_session_test_button = QPushButton()
        self.batch_session_test_button.setEnabled(False)
        self.batch_publish_button = QPushButton()
        self.batch_cancel_button = QPushButton()
        self.batch_cancel_button.setVisible(False)
        self.batch_publish_button.setEnabled(False)
        for button in (self.batch_review_button, self.batch_remove_button, self.batch_open_button):
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
        self.batch_open_button.clicked.connect(self._request_batch_open)
        self.batch_publish_button.clicked.connect(self.batch_publish_requested)
        self.batch_retry_button.clicked.connect(self._request_batch_retry)
        self.batch_session_test_button.clicked.connect(self.batch_session_test_requested)
        self.batch_cancel_button.clicked.connect(self.batch_cancel_requested)
        self.batch_button = QToolButton()
        self.batch_button.setCheckable(True)
        self.batch_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.batch_button.setArrowType(Qt.ArrowType.RightArrow)
        root = self.layout()
        root.removeWidget(self.title)
        self.page_header = QWidget()
        self.page_header_layout = QHBoxLayout(self.page_header)
        self.page_header_layout.setContentsMargins(0, 0, 0, 0)
        self.page_header_layout.addWidget(self.title)
        self.page_header_layout.addStretch(1)
        self.page_header_layout.addWidget(self.batch_button)
        root.insertWidget(0, self.page_header)
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
            self.mode_stack.setCurrentWidget(self.batch_view)
            self.batch_refresh_requested.emit()
        elif self.mode_stack.currentWidget() is self.batch_view:
            self.show_search()

    def _return_from_batch(self) -> None:
        self.batch_button.setChecked(False)

    def show_batch_entries(self, entries: list[dict[str, object]]) -> None:
        self.batch_entries = list(entries)
        states = [str(entry["publish_state"].value) for entry in self.batch_entries]
        local_count = sum(
            entry["site"] is None or str(entry["publish_state"].value) == "reviewed"
            for entry in self.batch_entries
        )
        self._update_batch_counts(states, local_count)
        self._render_batch_entries()

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
            additions = " ".join(entry["additions"]) or "—"
            removals = " ".join(entry["removals"]) or "—"
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
        self.batch_publish_button.setToolTip(
            self.catalog.text("tagging.publish.e621_credentials_missing")
            if pending_e621 and not self._e621_publish_configured and not gelbooru_publishable
            else ""
        )
        self.batch_session_test_button.setEnabled(bool(self.batch_sites_present()) and not running)
        self.batch_retry_button.setEnabled(
            bool(entries)
            and not running
            and all(
                entry["site"] in {"gelbooru", "e621"}
                and str(entry["publish_state"].value) == "failed"
                and entry.get("failure_retryable") is not False
                and (
                    entry["site"] != "gelbooru"
                    or int(entry.get("publish_attempts", 0)) < MAX_PUBLISH_ATTEMPTS
                )
                for entry in entries
            )
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
        ids = self._selected_batch_ids()
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
        if len(value.strip()) < 2:
            self.manual_suggestion_model.setStringList([])
            return
        self.manual_lookup_timer.start()

    def _emit_manual_lookup(self) -> None:
        value = self.manual_tag.text().strip()
        if len(value) >= 2:
            self.manual_lookup_requested.emit(value)

    def _select_manual_suggestion(self, label: str) -> None:
        self.manual_tag.setText(self._manual_suggestion_values.get(label, label))

    def set_manual_suggestions(self, suggestions: list[str] | list[tuple[str, str | None]]) -> None:
        values: list[str] = []
        self._manual_suggestion_values = {}
        for suggestion in suggestions:
            if isinstance(suggestion, str):
                value, alias_source = suggestion, None
            else:
                value, alias_source = suggestion
            label = (
                self.catalog.text("tagging.alias_suggestion", tag=value, alias=alias_source)
                if alias_source
                else value
            )
            values.append(label)
            self._manual_suggestion_values[label] = value
        self.manual_suggestion_model.setStringList(values)
        if values and self.manual_tag.hasFocus():
            self.manual_completer.complete()

    def clear_manual_entry(self) -> None:
        self.manual_tag.clear()
        self.manual_suggestion_model.setStringList([])
        self._manual_suggestion_values = {}

    def _copy_and_open(self) -> None:
        """Phase 2A primary gesture: persist locally and advance, never publish."""
        if self.current_post_id or getattr(self, "_batch_local_item_id", None) is not None:
            self.review_validation_requested.emit()

    def show_review_completion(self, message: str) -> None:
        self.analysis_state.setText(message)

    def show_local_batch_review(
        self, item_id: int, image_path, original_tags: list[str], final_tags: list[str]
    ) -> None:
        self._review_origin = "batch"
        self._batch_local_item_id = item_id
        self.current_post_id = None
        self._displayed_post_id = None
        self.current_post = {}
        self.review_title.setText(self.catalog.text("tagging.review.local_item", item_id=item_id))
        self.show_local_review(self.catalog.text("tagging.analysis.reviewed"), image_path, original_tags, [], [], final_tags)
        self.copy_open_button.setEnabled(True)

    def show_local_review(
        self, state, image_path, source_tags, rows, suggested_additions, final_tags
    ) -> None:
        started = perf_counter()
        super().show_local_review(
            state, image_path, source_tags, rows, suggested_additions, final_tags
        )
        editable = state.startswith((self.catalog.text("tagging.analysis.ready"), self.catalog.text("tagging.analysis.reviewed")))
        self.manual_tag.setEnabled(editable)
        self.manual_add.setEnabled(editable)
        self._perf_log("filter_review_refresh", started)

    def retranslate(self) -> None:
        super().retranslate()
        text = self.catalog.text
        self.title.setText(text("nav.tagging"))
        active_site = getattr(self, "active_site", "gelbooru")
        self.group.setTitle(
            text("tagging.group_site", site=site_definition(active_site).display_name)
        )
        self.query_label.setText(text("tagging.search_label"))
        if hasattr(self, "threshold_summary"):
            self._update_threshold_summary()
        if hasattr(self, "bulk_group"):
            self.bulk_group.setTitle(text("tagging.bulk.title")); self.bulk_add_label.setText(text("tagging.bulk.add")); self.bulk_remove_label.setText(text("tagging.bulk.remove"))
            self.bulk_add.input.setPlaceholderText(text("tagging.bulk.placeholder")); self.bulk_remove.input.setPlaceholderText(text("tagging.bulk.placeholder")); self._update_bulk_action()
        if hasattr(self, "hide_queued_results"):
            self.hide_queued_results.setText(text("tagging.results.hide_queued"))
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
        self.suggestions.setHorizontalHeaderLabels(tuple(text(f"tagging.review.header.{key}") for key in ("tag", "confidence", "match", "category", "decision", "id")))
        for row in range(self.suggestions.rowCount()):
            item = self.suggestions.item(row, 4)
            if item is not None and item.data(Qt.ItemDataRole.UserRole):
                item.setText(text(f"tagging.review.decision.{item.data(Qt.ItemDataRole.UserRole)}"))
        if not hasattr(self, "batch_table"):
            return
        selected_ids = self._selected_batch_ids()
        self.batch_back_button.setText(text("tagging.batch.back")); self.batch_refresh_button.setText(text("tagging.batch.refresh")); self.batch_filter_label.setText(text("tagging.batch.label"))
        for index, key in enumerate(("all", "pending", "published", "failed", "local")): self.batch_filter.setItemText(index, text(f"tagging.batch.filter.{key}"))
        for index, key in enumerate(("identity", "site", "additions", "removals", "state", "reviewed_at", "item")): self.batch_table.horizontalHeaderItem(index).setText(text(f"tagging.batch.header.{key}"))
        self.batch_review_button.setText(text("tagging.batch.review")); self.batch_remove_button.setText(text("tagging.batch.remove")); self.batch_open_button.setText(text("tagging.batch.open")); self.batch_retry_button.setText(text("tagging.batch.retry")); self.batch_session_test_button.setText(text("tagging.batch.session_test")); self.batch_publish_button.setText(text("tagging.batch.publish")); self.batch_cancel_button.setText(text("tagging.batch.cancel")); self.batch_button.setText(text("tagging.batch.button"))
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
