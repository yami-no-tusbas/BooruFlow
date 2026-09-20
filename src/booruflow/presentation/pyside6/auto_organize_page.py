"""Dry-run-first UI for model-driven automatic organization."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.auto_organize import rule_node_from_dict, rule_node_to_dict
from booruflow.domain.auto_organize import PlanStatus
from booruflow.presentation.pyside6.ui_components import DataTable

COLUMNS = (
    "Source",
    "Site / Post ID",
    "Règle gagnante",
    "Destination relative",
    "Destination complète",
    "État",
)

STATUS_LABELS = {
    PlanStatus.UNCHANGED: "OK — inchangé",
    PlanStatus.RENAME: "OK — renommer",
    PlanStatus.MOVE: "OK — déplacer",
    PlanStatus.RENAME_MOVE: "OK — renommer et déplacer",
    PlanStatus.AMBIGUOUS: "Ambigu",
    PlanStatus.UNRESOLVED: "Non résolu",
    PlanStatus.NOT_FOUND: "Non résolu",
    PlanStatus.UNRECOGNIZED: "Non résolu",
    PlanStatus.DESTINATION_CONFLICT: "Conflit de destination",
    PlanStatus.ERROR: "Erreur",
    PlanStatus.IGNORED: "Ignoré",
}


class FolderDropList(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)

    @staticmethod
    def normalized(path: Path) -> Path:
        return Path(path).resolve(strict=False)

    def add_paths(self, paths) -> None:
        existing = {
            str(self.normalized(Path(self.item(index).text()))).casefold()
            for index in range(self.count())
        }
        for path in paths:
            normalized = self.normalized(Path(path))
            key = str(normalized).casefold()
            if normalized.is_dir() and key not in existing:
                self.addItem(str(normalized))
                existing.add(key)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(
            Path(url.toLocalFile()).is_dir()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self.add_paths(
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        )
        event.acceptProposedAction()


class PriorityTree(QTreeWidget):
    order_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setColumnCount(6)
        self.setHeaderLabels(
            ("Priorité / règle", "Destination", "Tag(s)", "Sites", "Actif", "Type")
        )
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event) -> None:
        selected = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        if selected is None:
            event.ignore()
            return
        if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.OnItem:
            event.ignore()
            return
        selected_parent = selected.parent()
        target_parent = target.parent() if target else None
        if selected_parent is not target_parent:
            event.ignore()
            return
        super().dropEvent(event)
        self.order_changed.emit()

    def apply_filter(self, text) -> None:
        needle = str(text).strip().casefold()

        def visit(item) -> bool:
            child_visible = any(visit(item.child(index)) for index in range(item.childCount()))
            own = not needle or any(
                needle in item.text(column).casefold() for column in range(self.columnCount())
            )
            visible = own or child_visible
            item.setHidden(not visible)
            if needle and child_visible:
                item.setExpanded(True)
            return visible

        for index in range(self.topLevelItemCount()):
            visit(self.topLevelItem(index))


class AutoOrganizePage(QWidget):
    analyze_requested = Signal(tuple, str, bool, bool, bool, str, str)
    model_scan_requested = Signal(str)
    execute_requested = Signal()
    stop_requested = Signal()
    rules_changed = Signal()
    rules_save_requested = Signal()
    rules_reset_requested = Signal()

    def __init__(self, catalog) -> None:
        super().__init__()
        self.catalog = catalog
        self._plans = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 24)

        title = QLabel("Rangement auto")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(title)
        help_text = QLabel(
            "Le modèle décrit la taxonomie; la sortie reste indépendante. "
            "Analyse et aperçu sont obligatoires, et aucun cas ambigu n'est exécuté."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        sources = QGroupBox("Sources")
        sources_layout = QVBoxLayout(sources)
        self.folders = FolderDropList()
        self.folders.setMinimumHeight(90)
        sources_layout.addWidget(self.folders)
        source_actions = QHBoxLayout()
        self.add_button = QPushButton("Ajouter")
        self.remove_button = QPushButton("Retirer")
        self.clear_button = QPushButton("Vider")
        self.recursive = QCheckBox("Récursif")
        self.recursive.setChecked(True)
        for button in (self.add_button, self.remove_button, self.clear_button):
            source_actions.addWidget(button)
        source_actions.addStretch()
        source_actions.addWidget(self.recursive)
        sources_layout.addLayout(source_actions)
        layout.addWidget(sources)

        self.model_path = QLineEdit()
        self.model_path.setReadOnly(True)
        self.model_path.setPlaceholderText("Aucun modèle de classement choisi")
        self.model_button = QPushButton("Choisir")
        self.rescan_button = QPushButton("Rescanner")
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Modèle de classement"))
        model_row.addWidget(self.model_path, 1)
        model_row.addWidget(self.model_button)
        model_row.addWidget(self.rescan_button)
        layout.addLayout(model_row)

        self.output_root = QLineEdit()
        self.output_root.setReadOnly(True)
        self.output_root.setPlaceholderText("Aucune racine de sortie choisie")
        self.destination = self.output_root
        self.output_button = QPushButton("Choisir")
        self.destination_button = self.output_button
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Racine de sortie"))
        output_row.addWidget(self.output_root, 1)
        output_row.addWidget(self.output_button)
        layout.addLayout(output_row)

        options = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("Ranger", "organize")
        self.mode.addItem("Actualiser uniquement", "refresh_only")
        self.use_cache = QCheckBox("Utiliser le cache")
        self.use_cache.setChecked(True)
        self.force_refresh = QCheckBox("Forcer le rafraîchissement distant")
        options.addWidget(self.mode)
        options.addStretch()
        layout.addLayout(options)
        cache_options = QHBoxLayout()
        cache_options.addWidget(self.use_cache)
        cache_options.addWidget(self.force_refresh)
        cache_options.addStretch()
        layout.addLayout(cache_options)

        self.rules_group = QGroupBox("Priorités / règles de rangement")
        rules_layout = QVBoxLayout(self.rules_group)
        self.rule_filter = QLineEdit()
        self.rule_filter.setPlaceholderText("Rechercher une règle (office_lady, pencil_skirt…)")
        rules_layout.addWidget(self.rule_filter)
        self.priority_tree = PriorityTree()
        self.priority_tree.setMinimumHeight(230)
        rules_layout.addWidget(self.priority_tree)
        priorities = QHBoxLayout()
        self.up = QPushButton("Monter")
        self.down = QPushButton("Descendre")
        self.top = QPushButton("Tout en haut")
        self.bottom = QPushButton("Tout en bas")
        self.save_rules = QPushButton("Enregistrer")
        self.reset_rules = QPushButton("Réinitialiser les priorités")
        for button in (self.up, self.down, self.top, self.bottom):
            priorities.addWidget(button)
        priorities.addStretch()
        rules_layout.addLayout(priorities)
        persistence = QHBoxLayout()
        persistence.addStretch()
        persistence.addWidget(self.save_rules)
        persistence.addWidget(self.reset_rules)
        rules_layout.addLayout(persistence)
        self.rules_inventory = QLabel()
        self.rules_inventory.setWordWrap(True)
        rules_layout.addWidget(self.rules_inventory)
        self.rules_state = QLabel("Choisissez puis scannez un dossier modèle.")
        rules_layout.addWidget(self.rules_state)
        layout.addWidget(self.rules_group)

        actions = QHBoxLayout()
        self.analyze_button = QPushButton("Analyser")
        self.stop_button = QPushButton("Arrêter")
        self.execute_button = QPushButton("Exécuter les opérations validées")
        self.stop_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        actions.addWidget(self.analyze_button)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        actions.addWidget(self.execute_button)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.state = QLabel("Prêt.")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.last_error = QLabel("Dernière erreur : aucune")
        self.last_error.setWordWrap(True)
        layout.addWidget(self.last_error)
        self.error_summary = QPlainTextEdit()
        self.error_summary.setReadOnly(True)
        self.error_summary.setMaximumHeight(110)
        self.error_summary.setPlaceholderText("Les erreurs identiques sont regroupées ici.")
        layout.addWidget(self.error_summary)
        self._error_groups = {}

        self.table = DataTable(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSortingEnabled(True)
        self.table.set_empty_text(
            self.catalog.text("table.empty_analysis")
            if self.catalog
            else "Lancez une analyse pour prévisualiser les opérations proposées."
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((330, 140, 220, 330, 390, 180)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Sélectionnez une ligne pour voir le détail de la décision.")
        self.details.setMaximumHeight(150)
        layout.addWidget(self.details)

        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.clear_button.clicked.connect(self.folders.clear)
        self.model_button.clicked.connect(self._choose_model)
        self.rescan_button.clicked.connect(self._rescan)
        self.output_button.clicked.connect(self._choose_output)
        self.analyze_button.clicked.connect(self._analyze)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.execute_button.clicked.connect(self.execute_requested.emit)
        self.table.itemSelectionChanged.connect(self._show_details)
        self.priority_tree.order_changed.connect(self._rules_modified)
        self.priority_tree.itemChanged.connect(lambda *_: self._rules_modified())
        self.rule_filter.textChanged.connect(self.priority_tree.apply_filter)
        self.up.clicked.connect(lambda: self._move(-1, False))
        self.down.clicked.connect(lambda: self._move(1, False))
        self.top.clicked.connect(lambda: self._move(-1, True))
        self.bottom.clicked.connect(lambda: self._move(1, True))
        self.save_rules.clicked.connect(self.rules_save_requested.emit)
        self.reset_rules.clicked.connect(self.rules_reset_requested.emit)

    def _add(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "Choisir un dossier source")
        if value:
            self.folders.add_paths((Path(value),))

    def _remove(self) -> None:
        for item in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(item))

    def _choose_model(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "Choisir le modèle de classement")
        if value:
            self.model_path.setText(str(Path(value).resolve(strict=False)))
            self._rescan()

    def _rescan(self) -> None:
        value = self.model_path.text().strip()
        if not value or not Path(value).is_dir():
            self.state.setText("Choisissez un dossier modèle valide.")
            return
        self.model_scan_requested.emit(value)

    def _choose_output(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "Choisir la racine de sortie")
        if value:
            self.output_root.setText(str(Path(value).resolve(strict=False)))

    def set_paths(self, model_root: str = "", output_root: str = "") -> None:
        if model_root:
            self.model_path.setText(model_root)
        if output_root:
            self.output_root.setText(output_root)

    def _analyze(self) -> None:
        roots = tuple(Path(self.folders.item(index).text()) for index in range(self.folders.count()))
        if not roots:
            self.state.setText("Ajoutez au moins un dossier source.")
            return
        model = self.model_path.text().strip()
        destination = self.output_root.text().strip()
        if self.mode.currentData() == "organize":
            if not model or not Path(model).is_dir():
                self.state.setText("Choisissez et rescanner un modèle de classement.")
                return
            if not destination or not Path(destination).is_dir():
                self.state.setText("Choisissez une racine de sortie existante.")
                return
        self.analyze_requested.emit(
            roots,
            str(self.mode.currentData()),
            self.recursive.isChecked(),
            self.use_cache.isChecked(),
            self.force_refresh.isChecked(),
            destination,
            model,
        )

    def set_rules(self, rules) -> None:
        self.priority_tree.blockSignals(True)
        self.priority_tree.setUpdatesEnabled(False)
        self.priority_tree.clear()

        def add(parent, node) -> None:
            type_label = {
                "branch": "Branche",
                "route": "Routeur",
                "dynamic": "Fallback",
                "rule": "Règle",
            }.get(node.kind, node.kind)
            item = QTreeWidgetItem()
            item.setText(0, node.label)
            item.setText(1, node.destination)
            item.setText(2, ", ".join(node.tags))
            item.setText(3, ", ".join(node.sites))
            item.setText(4, "Oui" if node.active else "Non")
            item.setText(5, type_label)
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                4, Qt.CheckState.Checked if node.active else Qt.CheckState.Unchecked
            )
            data = rule_node_to_dict(node)
            data.pop("children", None)
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            if parent:
                parent.addChild(item)
            else:
                self.priority_tree.addTopLevelItem(item)
            for child in node.children:
                add(item, child)

        for node in rules:
            add(None, node)
        self.priority_tree.expandToDepth(1)
        self.priority_tree.blockSignals(False)
        self.priority_tree.setUpdatesEnabled(True)
        self.priority_tree.apply_filter(self.rule_filter.text())
        self.rules_state.setText("L'ordre vertical des frères détermine la priorité.")

    def set_rule_inventory(self, inventory) -> None:
        branches = " · ".join(
            f"{name}: {count}" for name, count in inventory.get("branches", {}).items()
        )
        self.rules_inventory.setText(
            f"Feuilles Tags: {inventory.get('tags_total', 0)} — "
            f"Gelbooru: {inventory.get('gelbooru', 0)} · "
            f"e621: {inventory.get('e621', 0)} · partagées: {inventory.get('shared', 0)}\n"
            f"{branches}"
        )

    def rules(self):
        def build(item):
            data = dict(item.data(0, Qt.ItemDataRole.UserRole))
            data["active"] = item.checkState(4) == Qt.CheckState.Checked
            data["children"] = [
                rule_node_to_dict(build(item.child(index)))
                for index in range(item.childCount())
            ]
            return rule_node_from_dict(data)

        return tuple(
            build(self.priority_tree.topLevelItem(index))
            for index in range(self.priority_tree.topLevelItemCount())
        )

    def _move(self, direction, to_edge) -> None:
        item = self.priority_tree.currentItem()
        if not item:
            return
        parent = item.parent()
        count = parent.childCount() if parent else self.priority_tree.topLevelItemCount()
        index = parent.indexOfChild(item) if parent else self.priority_tree.indexOfTopLevelItem(item)
        target = 0 if to_edge and direction < 0 else count - 1 if to_edge else index + direction
        if target < 0 or target >= count or target == index:
            return
        moved = parent.takeChild(index) if parent else self.priority_tree.takeTopLevelItem(index)
        if parent:
            parent.insertChild(target, moved)
        else:
            self.priority_tree.insertTopLevelItem(target, moved)
        self.priority_tree.setCurrentItem(moved)
        self._rules_modified()

    def _rules_modified(self) -> None:
        self.rules_state.setText("Modifié — enregistrez puis relancez l'analyse.")
        self.rules_changed.emit()

    def clear_plan(self, message) -> None:
        self._plans = []
        self.table.setRowCount(0)
        self.details.clear()
        self.execute_button.setEnabled(False)
        self.state.setText(message)

    def set_running(self, running) -> None:
        self.analyze_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.execute_button.setEnabled(False if running else self.execute_button.isEnabled())
        for widget in (
            self.rules_group,
            self.add_button,
            self.remove_button,
            self.clear_button,
            self.model_button,
            self.rescan_button,
            self.output_button,
        ):
            widget.setEnabled(not running)
        if running:
            self.clear_plan("Analyse en cours…")
            self.progress.setRange(0, 0)
            self._error_groups = {}
            self.error_summary.clear()
            self.last_error.setText("Dernière erreur : aucune")
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)

    def set_stopping(self) -> None:
        self.analyze_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.state.setText("Annulation demandée…")

    def set_analysis_progress(self, values) -> None:
        total = int(values.get("total", 0))
        processed = int(values.get("processed", 0))
        scanned = int(values.get("scanned", 0))
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(processed)
        else:
            self.progress.setRange(0, 0)
        self.state.setText(
            f"Fichiers : {processed}/{total or '?'} · scannés : {scanned} · "
            f"cache : {values.get('cache_hits', 0)} · API : {values.get('api_calls', 0)} · "
            f"ambiguïtés : {values.get('ambiguities', 0)} · erreurs : {values.get('errors', 0)}"
        )
        if values.get("last_error"):
            self.last_error.setText(f"Dernière erreur : {values['last_error']}")

    def record_error(self, detail) -> None:
        signature = str(
            detail.get("signature")
            or f"{detail.get('stage')}|{detail.get('exception_type')}|{detail.get('message')}"
        )
        entry = self._error_groups.setdefault(signature, {"count": 0, "sample": dict(detail)})
        entry["count"] += 1
        status = detail.get("status")
        short = f"{detail.get('site') or '?'} post {detail.get('post_id') or '?'} — " + (
            f"HTTP {status}"
            if status is not None
            else f"{detail.get('exception_type')}: {detail.get('message')}"
        )
        self.last_error.setText(f"Dernière erreur : {short}")
        visible = []
        for value in self._error_groups.values():
            sample = value["sample"]
            visible.append(
                f"[{value['count']}] {sample.get('site', '?')} · {sample.get('stage', '?')} · "
                f"{sample.get('status') or sample.get('exception_type', '?')}\n"
                f"{sample.get('message', '')}\n{sample.get('endpoint', '')}"
            )
        self.error_summary.setPlainText("\n\n".join(visible))

    def show_plans(self, plans):
        started = time.perf_counter()
        self._plans = list(plans)
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(plans))
        try:
            for row, plan in enumerate(plans):
                identity = f"{plan.site.capitalize()} #{plan.post_id}" if plan.site else "?"
                winner = " / ".join(plan.winner_path) or plan.winner or "—"
                values = (
                    plan.source,
                    identity,
                    winner,
                    plan.destination_relative or "—",
                    plan.destination or "—",
                    STATUS_LABELS.get(plan.status, plan.status.value),
                )
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(str(value)))
            populated = time.perf_counter()
            self.table.setSortingEnabled(True)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()
        finished = time.perf_counter()
        return {
            "populate_ms": (populated - started) * 1000,
            "finalize_ms": (finished - populated) * 1000,
            "total_ms": (finished - started) * 1000,
        }

    def _show_details(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._plans):
            self.details.clear()
            return
        plan = self._plans[row]
        matches = "\n".join(f"- {value}" for value in plan.candidates) or "- aucune"
        self.details.setPlainText(
            f"Route:\n{plan.route or 'normal'}\n\nMatches:\n{matches}\n\n"
            f"Winner:\n{' / '.join(plan.winner_path) or plan.winner or 'aucune'}\n\n"
            f"Fallback:\n{plan.fallback or 'aucun'}\n\n"
            f"Destination relative:\n{plan.destination_relative or 'aucune'}\n\n"
            f"Destination:\n{plan.destination or 'aucune'}\n\n"
            f"Raison:\n{plan.message or 'Aucune règle applicable.'}"
        )
