"""Entity profile review dialog using BooruFlow tables, status, and thumbnail workers."""
from pathlib import Path
from threading import Event

from PySide6.QtCore import QEvent, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from booruflow.application.entity_profiles import EntityProfileAnalyzer
from booruflow.domain.booru_sites import category_name, site_definition
from booruflow.domain.entity_profiles import automatic_exclusion, contextual_relation_visible
from booruflow.infrastructure.entity_profile_repository import EntityProfileRepository
from booruflow.infrastructure.gelbooru_client import GelbooruDapiClient
from booruflow.infrastructure.post_metadata_cache import PostMetadataCache
from booruflow.presentation.pyside6.context_actions import (
    add_action,
    bind_post_menu,
    exec_menu,
    tag_actions,
)
from booruflow.presentation.pyside6.tag_presentation import present_tag
from booruflow.presentation.pyside6.tagging_legacy_page import CollapsibleResultGroup, ResultCard
from booruflow.presentation.pyside6.thumbnail_cache import ThumbnailCacheKey, ThumbnailMemoryCache
from booruflow.presentation.pyside6.thumbnail_loader import ThumbnailLoader
from booruflow.presentation.pyside6.ui_components import DataTable


class FrequencyDelegate(QStyledItemDelegate):
    def displayText(self, value, locale):
        return locale.toString(float(value), "f", 2) + " %"


class EntityRefreshWorker(QThread):
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, root, profile_id, count, credentials, database, parent=None):
        super().__init__(parent)
        self.root, self.profile_id, self.count = root, profile_id, count
        self.credentials, self.database = credentials, database
        self.cancelled = Event()

    def run(self):
        try:
            with EntityProfileRepository(self.root / "var/state/entity_profiles.sqlite") as repository:
                cache = PostMetadataCache(self.root / "var/cache/post_metadata.sqlite")
                try:
                    client = GelbooruDapiClient(str(self.credentials.get("user_id", "")),
                                               str(self.credentials.get("api_key", "")))
                    analyzer = EntityProfileAnalyzer(repository, client, cache, self.database)
                    result = analyzer.refresh(self.profile_id, self.count,
                                              cancelled=self.cancelled, progress=self.progress.emit)
                    self.completed.emit(result)
                finally:
                    cache.close()
        except Exception as exc:  # noqa: BLE001 - Qt worker boundary; no credential-bearing URLs
            self.failed.emit(type(exc).__name__)
        finally:
            self.credentials = {}


class EntityProfileDialog(QDialog):
    profiles_changed = Signal()

    def __init__(self, root: Path, catalog, entity_tag, category, credentials, database,
                 launcher, page_status, parent=None, thumbnail_cache=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.root, self.catalog = root, catalog
        self.source_category = category_name("gelbooru", category)
        self.credential_provider, self.database, self.launcher = credentials, database, launcher
        self.page_status = page_status
        self.thumbnail_cache = (thumbnail_cache if thumbnail_cache is not None
                                else ThumbnailMemoryCache(32 * 1024 * 1024))
        self.repository = EntityProfileRepository(root / "var/state/entity_profiles.sqlite")
        self.worker = None
        self._close_pending = False
        self.profile_id = None
        self.setWindowTitle(catalog.text("entity.title"))
        self.resize(1000, 700)
        text = catalog.text
        layout = QVBoxLayout(self)
        self.entity = QLineEdit(entity_tag)
        self.entity.setReadOnly(True)
        layout.addWidget(QLabel(text("entity.entity")))
        layout.addWidget(self.entity)
        self.datasets = QComboBox()
        self.datasets.currentIndexChanged.connect(self._select_dataset)
        layout.addWidget(self.datasets)
        layout.addWidget(QLabel(text("entity.query")))
        self.query = QLineEdit(entity_tag + (" solo" if category == "character" else ""))
        layout.addWidget(self.query)
        note = QLabel(text("entity.query_note")); note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        actions.addWidget(QLabel(text("entity.new_posts")))
        self.count = QSpinBox(); self.count.setRange(1, 10000); self.count.setValue(100)
        actions.addWidget(self.count)
        self.analyze = QPushButton(text("entity.analyze")); self.analyze.clicked.connect(self.start)
        self.cancel = QPushButton(text("entity.cancel")); self.cancel.clicked.connect(self.stop)
        self.cancel.setEnabled(False)
        actions.addWidget(self.analyze); actions.addWidget(self.cancel); actions.addStretch()
        layout.addLayout(actions)
        self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        filters = QHBoxLayout()
        self.filter = QLineEdit(); self.filter.setPlaceholderText(text("entity.filter"))
        self.status_filter = QComboBox()
        for status in ("all", "keep", "review", "ignore", "excluded"):
            self.status_filter.addItem(text(f"entity.{status}"), status)
        self.filter.textChanged.connect(self.render)
        self.status_filter.currentIndexChanged.connect(self.render)
        filters.addWidget(self.filter); filters.addWidget(self.status_filter)
        layout.addLayout(filters)
        self.tabs = QTabWidget()
        self.tables = {}
        for kind in ("attribute", "relation"):
            table = DataTable(0, 6)
            table.setHorizontalHeaderLabels([text(f"entity.col.{key}") for key in
                                             ("tag", "count", "frequency", "category", "status", "excluded")])
            table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            table.customContextMenuRequested.connect(lambda pos, table=table: self.menu(table, pos))
            table.sortItems(2, Qt.SortOrder.DescendingOrder)
            table.setItemDelegateForColumn(2, FrequencyDelegate(table))
            for key, action in (
                ("A", lambda table=table: self.set_selected_status("keep", table)),
                ("R", lambda table=table: self.set_selected_status("ignore", table)),
                ("Space", lambda table=table: self.inspect_selected(table)),
                ("B", lambda table=table: self.blacklist_selected(table)),
            ):
                shortcut = QShortcut(QKeySequence(key), table)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(action)
            self.tables[kind] = table
            self.tabs.addTab(table, text(f"entity.{kind}"))
        layout.addWidget(self.tabs, 1)
        review_actions = QHBoxLayout()
        for status, key in (("keep", "A"), ("ignore", "R")):
            button = QPushButton(f"{text(f'entity.{status}')} ({key})")
            button.clicked.connect(lambda _checked=False, status=status: self.set_selected_status(status))
            review_actions.addWidget(button)
        blacklist = QPushButton(text("entity.blacklist") + " (B)")
        blacklist.setToolTip(text("entity.ignore_global"))
        blacklist.clicked.connect(lambda: self.blacklist_selected())
        review_actions.addWidget(blacklist)
        inspect = QPushButton(text("entity.inspect") + f" ({text('entity.space')})")
        inspect.clicked.connect(lambda: self.inspect_selected())
        review_actions.addWidget(inspect)
        review_actions.addStretch()
        close = QPushButton(text("entity.close")); close.clicked.connect(self.close)
        review_actions.addWidget(close)
        layout.addLayout(review_actions)
        self.feedback = QLabel(); self.feedback.setWordWrap(True); layout.addWidget(self.feedback)
        self._reload_datasets()

    def _reload_datasets(self):
        selected = self.profile_id
        self.datasets.blockSignals(True)
        self.datasets.clear()
        self.datasets.addItem(self.catalog.text("entity.new_dataset"), None)
        for profile in self.repository.profiles(self.entity.text()):
            self.datasets.addItem(f"#{profile['id']} · {profile['query']} · {profile['sample_count']}", profile["id"])
        index = self.datasets.findData(selected)
        if selected is None and self.datasets.count() > 1:
            index = self.datasets.count() - 1
        self.datasets.setCurrentIndex(max(0, index))
        self.datasets.blockSignals(False)
        self._select_dataset()

    def _select_dataset(self, *_args):
        self.profile_id = self.datasets.currentData()
        if self.profile_id is not None:
            self.query.setText(self.repository.profile(self.profile_id)["query"])
        self.render()

    def render(self, *_args):
        profile = self.repository.profile(self.profile_id) if self.profile_id else None
        if profile:
            profile["last_run_state"] = self.catalog.text(f"entity.state.{profile['last_run_state']}")
        self.summary.setText(self.catalog.text("entity.summary", **profile) if profile else "")
        rows = self.repository.tags(self.profile_id) if profile else []
        rows = [dict(row, excluded=bool(row["excluded"] or automatic_exclusion(row["tag"], row["category"])))
                for row in rows
                if contextual_relation_visible(self.source_category, row["tag"], row["category"])]
        filter_value = self.status_filter.currentData()
        for kind, table in self.tables.items():
            table.setSortingEnabled(False)
            visible = [row for row in rows if row["kind"] == kind
                       and self.filter.text().casefold() in row["tag"].casefold()
                       and (filter_value == "excluded" or not row["excluded"])
                       and (filter_value == "all" or row["status"] == filter_value
                            or (filter_value == "excluded" and row["excluded"]))]
            table.setRowCount(len(visible))
            for index, row in enumerate(visible):
                values = (row["tag"], row["occurrences"], round(row["frequency"] * 100, 2),
                          row["category"], self.catalog.text(f"entity.{row['status']}"),
                          self.catalog.text("entity.excluded") if row["excluded"] else "")
                for column, value in enumerate(values):
                    item = QTableWidgetItem(); item.setData(Qt.ItemDataRole.DisplayRole, value)
                    item.setData(Qt.ItemDataRole.UserRole, row["tag"])
                    if column == 0:
                        item.setForeground(present_tag("gelbooru", row["tag"], row["category"],
                                                       self.palette()).style.color(self.palette()))
                    table.setItem(index, column, item)
            table.setSortingEnabled(True)
            if not table.property("entity_columns_sized") and visible:
                table.resizeColumnsToContents()
                table.setProperty("entity_columns_sized", True)
        self.profiles_changed.emit()

    def message(self, value, *, error=False):
        self.feedback.setText(value)
        self.page_status.show_message(value, timeout_ms=0, log=True, global_message=True,
                                      level="ERROR" if error else "NORMAL")

    def start(self):
        if self.worker is not None:
            return
        try:
            self.profile_id = self.repository.create(self.entity.text(), self.query.text())
            self.message(self.catalog.text("entity.started"))
            credentials = self.credential_provider()
            gelbooru = credentials.get("gelbooru", credentials)
            self.worker = EntityRefreshWorker(self.root, self.profile_id, self.count.value(),
                                               gelbooru, self.database, self)
            self.worker.progress.connect(self.progress)
            self.worker.completed.connect(self.completed)
            self.worker.failed.connect(self.failed)
            self.worker.finished.connect(self._worker_finished)
            self.analyze.setEnabled(False); self.cancel.setEnabled(True)
            self.datasets.setEnabled(False); self.query.setEnabled(False); self.count.setEnabled(False)
            self.tabs.setEnabled(False)
            self.page_status.set_progress(0, self.count.value(), global_task=True)
            self.worker.start()
        except Exception as exc:  # noqa: BLE001 - GUI action boundary
            if self.worker is not None and not self.worker.isRunning():
                self.worker.deleteLater()
                self.worker = None
            self.message(self.catalog.text("entity.error", error=type(exc).__name__), error=True)

    def progress(self, event):
        if event["phase"] == "sample":
            self.page_status.set_progress(event["new"], event["requested"], global_task=True)
            self.feedback.setText(self.catalog.text("entity.progress", **event))
        else:
            self.message(self.catalog.text("entity.retry", **event))

    def completed(self, result):
        self.message(self.catalog.text("entity.result", new=result.new_posts, total=result.total_posts,
                                       state=self.catalog.text(f"entity.state.{result.state}")))

    def failed(self, error):
        self.message(self.catalog.text("entity.error", error=error), error=True)

    def _worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.page_status.clear_progress(global_task=True)
        self.analyze.setEnabled(True); self.cancel.setEnabled(False)
        self.datasets.setEnabled(True); self.query.setEnabled(True); self.count.setEnabled(True)
        self.tabs.setEnabled(True)
        self._reload_datasets()
        if self._close_pending:
            self.close()

    def stop(self):
        if self.worker:
            self.worker.cancelled.set()
            self.cancel.setEnabled(False)
            self.message(self.catalog.text("entity.cancelling"))

    def menu(self, table, pos):
        item = table.itemAt(pos)
        if item is None or self.profile_id is None:
            return
        tag = item.data(Qt.ItemDataRole.UserRole)
        table.setCurrentCell(item.row(), 0)
        table.selectRow(item.row())
        menu = QMenu(self)
        category = table.item(item.row(), 3).text()
        handlers = tag_actions(menu, self.catalog, tag, category=category,
                               launcher=self.launcher, entity=self.open_related_profile)
        menu.addSeparator()
        for status, shortcut in (("keep", "A"), ("review", ""), ("ignore", "R")):
            label = self.catalog.text(f"entity.{status}") + (f" ({shortcut})" if shortcut else "")
            add_action(menu, label, lambda value=status: self._menu_status(tag, value), handlers)
        excluded = tag in self.repository.exclusions()
        add_action(menu, self.catalog.text("entity.restore_global" if excluded else "entity.ignore_global") + " (B)",
                   lambda: self._menu_exclude(tag, not excluded), handlers)
        add_action(menu, self.catalog.text("entity.inspect") + f" ({self.catalog.text('entity.space')})",
                   lambda: self.inspect_posts(tag), handlers)
        exec_menu(menu, table.viewport().mapToGlobal(pos), handlers)

    def _menu_status(self, tag, status):
        self.repository.set_status(self.profile_id, tag, status)
        self.render()

    def _menu_exclude(self, tag, excluded):
        self.repository.exclude(tag, excluded)
        self.render()

    def open_related_profile(self, tag, category):
        dialog = EntityProfileDialog(self.root, self.catalog, tag, category, self.credential_provider,
                                     self.database, self.launcher, self.page_status, self,
                                     thumbnail_cache=self.thumbnail_cache)
        dialog.profiles_changed.connect(self.profiles_changed)
        dialog.setModal(True)
        dialog.show()

    def refresh_theme(self):
        for table in self.tables.values():
            stylesheet = table.styleSheet()
            table.setStyleSheet("")
            table.setPalette(self.palette())
            table.setStyleSheet(stylesheet)
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                item.setForeground(present_tag("gelbooru", item.text(), table.item(row, 3).text(),
                                               self.palette()).style.color(self.palette()))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "tables"):
            QTimer.singleShot(0, self._refresh_palette)

    def _refresh_palette(self):
        from booruflow.presentation.pyside6.theme import refresh_widget_tree
        refresh_widget_tree(self)
        self.refresh_theme()

    def set_selected_status(self, status, table=None):
        if self.worker is not None or self.profile_id is None:
            return
        table = table if table is not None else self.tabs.currentWidget()
        tags = [table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in table.selectionModel().selectedRows()]
        if not tags:
            return
        # Capture the actual visible order before a decision removes or re-sorts rows.
        last = max(index.row() for index in table.selectionModel().selectedRows())
        following = [table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                     for row in range(last + 1, table.rowCount())]
        for tag in tags:
            self.repository.set_status(self.profile_id, tag, status)
        self.render()
        remaining = {table.item(row, 0).data(Qt.ItemDataRole.UserRole): row
                     for row in range(table.rowCount())}
        next_tag = next((tag for tag in following if tag in remaining), None)
        if next_tag is None:
            next_tag = next((tag for tag in reversed(tags) if tag in remaining), None)
        table.clearSelection()
        table.setCurrentCell(-1, -1)
        if next_tag is not None:
            table.setCurrentCell(remaining[next_tag], 0)
            table.selectRow(remaining[next_tag])
            table.scrollToItem(table.item(remaining[next_tag], 0))

    def blacklist_selected(self, table=None):
        if self.worker is not None or self.profile_id is None:
            return
        table = table if table is not None else self.tabs.currentWidget()
        for index in table.selectionModel().selectedRows():
            self.repository.exclude(table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
        self.render()

    def inspect_selected(self, table=None):
        if self.worker is not None or self.profile_id is None:
            return
        table = table if table is not None else self.tabs.currentWidget()
        item = table.item(table.currentRow(), 0)
        if item is not None:
            self.inspect_posts(item.data(Qt.ItemDataRole.UserRole))

    def inspect_posts(self, tag):
        dialog = QDialog(self); dialog.setWindowTitle(self.catalog.text("entity.inspect") + f" · {tag}")
        dialog.resize(760, 500)
        layout = QVBoxLayout(dialog)
        note = QLabel(self.catalog.text("entity.inspect_note")); note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        posts = CollapsibleResultGroup("")
        posts.setObjectName("entity_matching_grid")
        posts.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        posts.toggle.hide(); posts.select_all.hide()
        scroll.setWidget(posts); layout.addWidget(scroll)
        close = QPushButton(self.catalog.text("entity.close"))
        feedback = QLabel()
        layout.addWidget(feedback)
        close.clicked.connect(dialog.accept); layout.addWidget(close)
        loader = ThumbnailLoader(self.thumbnail_cache, dialog, concurrency=2)
        items = {}
        keys = []
        for post in self.repository.matching_posts(self.profile_id, tag):
            item = ResultCard({"id": int(post["post_id"]), "preview_url": post["preview_url"]})
            item.selection_checkbox.hide()
            item.setObjectName(f"entity_witness_{post['post_id']}")
            item.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            item.setText(f"#{post['post_id']}")
            placeholder = QPixmap(150, 135); placeholder.fill(self.palette().alternateBase().color())
            item.setIcon(QIcon(placeholder))
            item.open_requested.connect(lambda post: self.launcher.open(
                site_definition("gelbooru").post_url(post["id"])))
            bind_post_menu(item, self.catalog, launcher=self.launcher)
            posts.add_card(item)
            key = ThumbnailCacheKey("gelbooru", int(post["post_id"]), post["preview_url"])
            items[key] = item
            if key.url:
                keys.append(key)
        loader.image_ready.connect(lambda key, image: items[key].setIcon(QIcon(QPixmap.fromImage(image))))
        posts.set_thumbnail_size(150)
        from booruflow.presentation.pyside6.thumbnail_feedback import ThumbnailFeedback
        progress = ThumbnailFeedback(loader, lambda loaded, total, failed: feedback.setText(
            self.catalog.text("status.thumbnails", loaded=loaded, total=total)
            + (self.catalog.text("status.thumbnails_failed", failed=failed) if failed else "")), dialog)
        try:
            loader.begin_wave(keys)
            dialog.exec()
        finally:
            progress.stop()
            loader.shutdown()
            dialog.deleteLater()

    def closeEvent(self, event):
        if self.worker is not None:
            self._close_pending = True
            self.stop()
            event.ignore()
            return
        event.accept()
        self.reject()

    def reject(self):
        if self.worker is not None:
            self._close_pending = True
            self.stop()
            return
        self.repository.close()
        super().reject()
