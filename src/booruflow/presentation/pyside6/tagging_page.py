"""Primary Tagging presentation entry point.

The current implementation deliberately inherits the proven review screen while
the new workflow is introduced here incrementally.  The frozen implementation
itself lives in :mod:`tagging_legacy_page` and remains available in navigation.
"""

from __future__ import annotations

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

from booruflow.application.tagging import parse_review_row_token
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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._reviewed_post_ids: set[int] = set()
        self._suggestion_id_column = 5
        self.suggestions.setColumnCount(6)
        self.suggestions.setHorizontalHeaderLabels((
            "Tag", "Confiance", "Origine / correspondance", "Catégorie",
            "Décision", "ID",
        ))
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
        self._build_batch_view()

    def _render_suggestions(self, *_args) -> None:
        selected_id = self._selected_observation_id()
        decision = str(self.decision_filter.currentData())
        visible = {"accepted": {"accepted", "keep"}, "rejected": {"rejected", "remove"}}.get(decision, {decision})
        rows = [row for row in self._all_suggestion_rows if decision == "all" or row["decision"] in visible]
        self.suggestions.setSortingEnabled(False); self.suggestions.clearContents(); self.suggestions.setRowCount(len(rows))
        decision_order = {"unreviewed": 0, "accepted": 1, "keep": 1, "rejected": 2, "remove": 2}
        match_order = {"exact": 0, "mapping": 1, "déjà présent": 2, "introuvable localement": 3, "non applicable": 4}
        for row_index, row in enumerate(rows):
            confidence = float(row["confidence"] or -1); match_text = str(row["match"])
            match_key = next((rank for name, rank in match_order.items() if name in match_text.casefold()), 9)
            token_kind, token_value = parse_review_row_token(row["id"])
            category = str(row.get("category", ""))
            values = (
                (row["tag"], str(row["tag"]).casefold()),
                (row["confidence"], confidence), (match_text, match_key),
                (category, int(category) if category.isdigit() else -1),
                (row["decision"], decision_order.get(row["decision"], 9)),
                (str(row["id"]), (token_kind, token_value)),
            )
            for column, (text, key) in enumerate(values):
                self.suggestions.setItem(row_index, column, SuggestionItem(str(text), key))
        self.suggestions.setSortingEnabled(True); self.suggestions.sortItems(self._sort_column, self._sort_order); self.suggestions.resizeColumnsToContents()
        target_id = self._pending_next_id if self._pending_fallback_row is not None else selected_id
        selected_row = next((row for row in range(self.suggestions.rowCount()) if target_id is not None and self.suggestions.item(row, 5).text() == str(target_id)), -1)
        if selected_row < 0 and self.suggestions.rowCount():
            selected_row = 0 if self._pending_fallback_row is None else min(self._pending_fallback_row, self.suggestions.rowCount() - 1)
        if selected_row >= 0:
            self.suggestions.selectRow(selected_row); self.suggestions.setFocus()
        self._pending_next_id = None; self._pending_fallback_row = None; self._update_review_action_states()

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
                button.setText(f"✓ Traité\n{button.text()}")
        self._update_counter()

    def mark_reviewed_and_advance(self, post_id: int) -> bool:
        """Advance from the actual displayed result, never a stale cursor."""
        self._reviewed_post_ids.add(int(post_id))
        self.processed_in_session.add(int(post_id))
        self._render_reviewed_checks()
        current = next(
            (index for index, post in enumerate(self.result_posts)
             if int(post.get("id", 0)) == int(post_id)),
            None,
        )
        if current is None:
            return False
        indexes = list(range(current + 1, len(self.result_posts))) + list(range(current))
        target = next(
            (index for index in indexes
             if int(self.result_posts[index].get("id", 0)) not in self._reviewed_post_ids),
            None,
        )
        if target is None:
            self.show_review_completion("Revue enregistrée — pool terminé.")
        else:
            self._open_result(target)
        return True

    def _build_manual_entry(self) -> None:
        self.legacy_tag_row.setVisible(False)
        self.review.layout().removeWidget(self.legacy_tag_row)
        container = QWidget(self.review)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Ajouter un tag :"))
        self.manual_tag = QLineEdit()
        self.manual_tag.setPlaceholderText("Rechercher dans la base locale…")
        self.manual_add = QPushButton("Ajouter")
        layout.addWidget(self.manual_tag, 1)
        layout.addWidget(self.manual_add)
        review_layout = self.review.layout()
        review_layout.insertWidget(review_layout.indexOf(self.action_bar), container)

        self.manual_suggestion_model = QStringListModel(self)
        self.manual_completer = QCompleter(self.manual_suggestion_model, self)
        self.manual_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.manual_completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.manual_tag.setCompleter(self.manual_completer)
        self.manual_completer.activated[str].connect(self.manual_tag.setText)
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
        self.batch_back_button = QPushButton("← Revue")
        self.batch_refresh_button = QPushButton("Actualiser")
        self.batch_filter = QComboBox()
        for label, value in (
            ("Tous", "all"), ("En attente", "pending_publish"),
            ("Publiés", "published"), ("Échecs", "failed"), ("Locaux", "local"),
        ):
            self.batch_filter.addItem(label, value)
        self.batch_counts = QLabel()
        toolbar.addWidget(self.batch_back_button)
        toolbar.addWidget(QLabel("Batch :"))
        toolbar.addWidget(self.batch_filter)
        toolbar.addWidget(self.batch_refresh_button)
        toolbar.addWidget(self.batch_counts, 1)
        root.addLayout(toolbar)
        self.batch_table = DataTable(0, 7)
        self.batch_table.setHorizontalHeaderLabels(
            ("Image / post", "Site", "Ajouts", "Retraits", "État", "Revu le", "Item")
        )
        self.batch_table.setColumnHidden(6, True)
        self.batch_table.setSortingEnabled(False)
        self.batch_table.set_empty_text(self.catalog.text("table.empty_batch"))
        self.batch_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.batch_table.setColumnWidth(2, 220)
        root.addWidget(self.batch_table, 1)
        actions = QHBoxLayout()
        self.batch_review_button = QPushButton("Revoir")
        self.batch_remove_button = QPushButton("Retirer du lot")
        self.batch_open_button = QPushButton("Ouvrir le post")
        self.batch_retry_button = QPushButton("Réessayer les échecs")
        self.batch_session_test_button = QPushButton("Tester la session Gelbooru")
        self.batch_publish_button = QPushButton("Publier le lot")
        self.batch_cancel_button = QPushButton("Annuler la publication")
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
        self.batch_button = QPushButton("Batch")
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
        local_count = sum(entry["site"] is None for entry in self.batch_entries)
        self.batch_counts.setText(
            f"{states.count('pending_publish')} en attente · {local_count} locaux · "
            f"{states.count('published')} publiés · {states.count('failed')} échecs"
        )
        self._render_batch_entries()

    def _render_batch_entries(self, *_args) -> None:
        mode = str(self.batch_filter.currentData())
        visible = [
            entry for entry in self.batch_entries
            if mode == "all"
            or (mode == "local" and entry["site"] is None)
            or str(entry["publish_state"].value) == mode
        ]
        self.batch_table.setRowCount(len(visible))
        for row, entry in enumerate(visible):
            site = str(entry["site"] or "Local")
            post_id = entry["post_id"]
            identity = f"{site} #{post_id}" if post_id else "Fichier local"
            additions = " ".join(entry["additions"]) or "—"
            removals = " ".join(entry["removals"]) or "—"
            values = (
                identity, site, additions, removals,
                str(entry["publish_state"].value), str(entry["reviewed_at"]),
                str(entry["item_id"]),
            )
            for column, value in enumerate(values):
                self.batch_table.setItem(row, column, QTableWidgetItem(value))
        self._update_batch_actions()

    def _selected_batch_ids(self) -> list[int]:
        return [
            int(self.batch_table.item(index.row(), 6).text())
            for index in self.batch_table.selectionModel().selectedRows()
            if self.batch_table.item(index.row(), 6) is not None
        ]

    def _selected_batch_entries(self) -> list[dict[str, object]]:
        selected = set(self._selected_batch_ids())
        return [entry for entry in self.batch_entries if int(entry["item_id"]) in selected]

    def _update_batch_actions(self) -> None:
        entries = self._selected_batch_entries() if hasattr(self, "batch_entries") else []
        self.batch_review_button.setEnabled(len(entries) == 1)
        self.batch_open_button.setEnabled(
            len(entries) == 1 and entries[0]["site"] is not None
        )
        self.batch_remove_button.setEnabled(bool(entries))
        running = getattr(self, "_batch_publish_running", False)
        pending_remote = any(
            entry["site"] == "gelbooru" and entry["post_id"]
            and str(entry["publish_state"].value) == "pending_publish"
            for entry in getattr(self, "batch_entries", [])
        )
        self.batch_publish_button.setEnabled(pending_remote and not running)
        self.batch_retry_button.setEnabled(
            bool(entries) and not running and all(
                entry["site"] == "gelbooru" and str(entry["publish_state"].value) == "failed"
                for entry in entries
            )
        )

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
        self.batch_status.setText(f"Publication {current} / {total} — Gelbooru #{post_id}")

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

    def set_manual_suggestions(self, names: list[str]) -> None:
        self.manual_suggestion_model.setStringList(names)
        if names and self.manual_tag.hasFocus():
            self.manual_completer.complete()

    def clear_manual_entry(self) -> None:
        self.manual_tag.clear()
        self.manual_suggestion_model.setStringList([])

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
        self.current_post = {}
        self.review_title.setText(f"Fichier local #{item_id}")
        self.show_local_review("Déjà analysée", image_path, original_tags, [], [], final_tags)
        self.copy_open_button.setEnabled(True)

    def show_local_review(
        self, state, image_path, source_tags, rows, suggested_additions, final_tags
    ) -> None:
        super().show_local_review(
            state, image_path, source_tags, rows, suggested_additions, final_tags
        )
        editable = state.startswith(("Analyse disponible", "Déjà analysée"))
        self.manual_tag.setEnabled(editable)
        self.manual_add.setEnabled(editable)

    def retranslate(self) -> None:
        super().retranslate()
        self.title.setText(self.catalog.text("nav.tagging"))
        self.copy_open_button.setText("Valider + suivant [Espace]")
        self.copy_open_button.setToolTip("Enregistrer la revue locale puis passer au suivant")
        if hasattr(self, "batch_table"):
            self.batch_table.set_empty_text(self.catalog.text("table.empty_batch"))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in (self.accept_shortcut, self.reject_shortcut, self.undo_shortcut, self.redo_shortcut, self.redo_alias_shortcut):
            shortcut.setEnabled(True)

    def closeEvent(self, event) -> None:
        for shortcut in (self.accept_shortcut, self.reject_shortcut, self.undo_shortcut, self.redo_shortcut, self.redo_alias_shortcut):
            shortcut.setEnabled(False)
        super().closeEvent(event)
