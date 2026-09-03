"""Primary Tagging presentation entry point.

The current implementation deliberately inherits the proven review screen while
the new workflow is introduced here incrementally.  The frozen implementation
itself lives in :mod:`tagging_legacy_page` and remains available in navigation.
"""

from __future__ import annotations

from time import perf_counter

from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.database_paths import gelbooru_alias_database
from booruflow.application.tagging import TaggingRequest, parse_review_row_token
from booruflow.domain.booru_sites import site_definition
from booruflow.presentation.pyside6.tagging_legacy_page import SuggestionItem, TaggingLegacyPage
from booruflow.presentation.pyside6.ui_components import DataTable


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
    alias_update_requested = Signal(str, str)
    alias_stop_requested = Signal()
    reanalyze_requested = Signal()
    site_changed = Signal(str)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._displayed_post_id: int | None = None
        self.open_button.clicked.disconnect()
        self.open_button.clicked.connect(self._open_current_post)
        self.active_site = str(self.settings.get("tagging_site", "gelbooru"))
        self._build_site_selector()
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
        self._build_alias_section()
        self.retranslate()

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
        controls.addWidget(self.query, 0, 3, 1, 7)
        self.site_selector.currentIndexChanged.connect(self._site_selected)

    def _site_selected(self) -> None:
        site = str(self.site_selector.currentData())
        if site == self.active_site:
            return
        self.active_site = site
        self.settings["tagging_site"] = site
        self._clear_results()
        self.processed_in_session.clear()
        self._reviewed_post_ids.clear()
        self.current_post_id = None
        self._displayed_post_id = None
        self.current_post = {}
        self.state.setText(self.catalog.text("tagging.ready"))
        if hasattr(self, "alias_group"):
            self.alias_group.setVisible(site == "gelbooru")
        if hasattr(self, "batch_status") and site == "e621":
            self.batch_status.setText(self.catalog.text("tagging.publish.e621_unavailable"))
        if hasattr(self, "batch_session_test_button"):
            self._update_batch_actions()
        self.site_changed.emit(site)

    def _start(self) -> None:
        try:
            request = TaggingRequest(
                self.query.text().strip(), self.spins["pages"].value(),
                self.spins["start"].value(), self.spins["minimum"].value(),
                self.spins["maximum"].value(), self.spins["critical"].value(),
                self.spins["high"].value(), self.active_site,
            )
        except ValueError as exc:
            self.state.setText(self.catalog.text("tagging.invalid", error=exc))
            return
        self.processed_in_session.clear()
        self.settings["tagging_query"] = request.query
        self.query_saved.emit(request.query)
        self.start_requested.emit(request)

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

    def _build_alias_section(self) -> None:
        self.alias_group = QWidget(self.search_view)
        row = QHBoxLayout(self.alias_group)
        row.setContentsMargins(0, 0, 0, 0)
        self.alias_label = QLabel()
        self.alias_status = QLabel()
        self.alias_status.setWordWrap(True)
        self.alias_update = QPushButton()
        self.alias_pending = QPushButton()
        self.alias_reconcile = QPushButton()
        row.addWidget(self.alias_label)
        row.addWidget(self.alias_status, 1)
        row.addWidget(self.alias_update)
        row.addWidget(self.alias_pending)
        row.addWidget(self.alias_reconcile)
        self.search_view.layout().insertWidget(1, self.alias_group)
        self.alias_update.clicked.connect(lambda: self._alias_action("incremental"))
        self.alias_pending.clicked.connect(lambda: self._alias_action("pending"))
        self.alias_reconcile.clicked.connect(lambda: self._alias_action("full"))
        self._alias_running = False

    def _alias_action(self, mode: str) -> None:
        if self._alias_running:
            self.alias_stop_requested.emit()
            return
        database = gelbooru_alias_database(self.settings)
        self.alias_update_requested.emit(mode, str(database) if database else "")

    def set_alias_running(self, running: bool) -> None:
        self._alias_running = running
        self.alias_update.setEnabled(True)
        self.alias_pending.setEnabled(not running)
        self.alias_reconcile.setEnabled(not running)
        self.retranslate()

    def set_alias_summary(self, values: dict[str, str]) -> None:
        state = self.catalog.text(f"options.alias_state_{values.get('state', 'unknown')}")
        self.alias_status.setText(self.catalog.text(
            "options.alias_summary", active=values.get("active", "0"),
            pending=values.get("pending", "0"), missing=values.get("missing", "0"),
            new=values.get("new", "0"), modified=values.get("modified", "0"),
            checkpoint=values.get("checkpoint", "0"), state=state,
        ))

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
        super().show_results(posts)
        self._render_reviewed_checks()

    def set_reviewed_post_ids(self, post_ids: set[int]) -> None:
        self._reviewed_post_ids = {int(post_id) for post_id in post_ids}
        self.processed_in_session.update(self._reviewed_post_ids)
        self._render_reviewed_checks()

    def _render_reviewed_checks(self) -> None:
        for post_id, button in self.result_buttons.items():
            if post_id in self._reviewed_post_ids and "✓" not in button.text():
                button.setText(self.catalog.text("tagging.review.processed", label=button.text()))
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
        root.addWidget(self.batch_progress)
        root.addWidget(self.batch_status)
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
        self.batch_button = QPushButton()
        self.layout().insertWidget(1, self.batch_button)
        self.batch_button.clicked.connect(self.show_batch)
        self._update_batch_actions()

    def show_batch(self) -> None:
        self.mode_stack.setCurrentWidget(self.batch_view)
        self.batch_refresh_requested.emit()

    def _return_from_batch(self) -> None:
        self.mode_stack.setCurrentWidget(self.review if self.current_post_id else self.search_view)

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
            pending=self._plural("tagging.batch.count.pending", states.count("pending_publish")),
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
            values = (
                identity,
                site,
                additions,
                removals,
                self.catalog.text(f"tagging.batch.state.{entry['publish_state'].value}"),
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
        self._update_batch_actions()

    def set_batch_publish_progress(self, current: int, total: int, post_id: str) -> None:
        self.batch_progress.setRange(0, max(1, total))
        self.batch_progress.setValue(current)
        target = post_id.replace(":", " #", 1)
        self.batch_status.setText(
            self.catalog.text("tagging.batch.progress", current=current, total=total, target=target)
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
        if hasattr(self, "site_label"):
            self.site_label.setText(text("tagging.site"))
            self.alias_group.setVisible(self.active_site == "gelbooru")
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
        if hasattr(self, "alias_group"):
            self.alias_label.setText(text("tagging.alias.title"))
            self.alias_update.setText(text("options.stop_database") if self._alias_running else text("options.alias_update"))
            self.alias_pending.setText(text("options.alias_pending"))
            self.alias_reconcile.setText(text("options.alias_reconcile"))
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
