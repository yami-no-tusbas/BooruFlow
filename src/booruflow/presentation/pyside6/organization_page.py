"""Lazy taxonomy browser and editor."""

from __future__ import annotations

import html
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.taxonomy import iter_tag_paths
from booruflow.infrastructure.localization import LanguageCatalog

ROLE_NODE = Qt.ItemDataRole.UserRole
ROLE_PATH = Qt.ItemDataRole.UserRole + 1
ROLE_KIND = Qt.ItemDataRole.UserRole + 2
ROLE_TAG = Qt.ItemDataRole.UserRole + 3


class NavigableTagList(QListWidget):
    tag_clicked = Signal(object)

    def mouseReleaseEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        on_name = item is not None and event.position().x() > self.visualItemRect(item).left() + 26
        super().mouseReleaseEvent(event)
        if on_name:
            self.tag_clicked.emit(item)


class OrganizationPage(QWidget):
    save_requested = Signal(object)
    update_requested = Signal()
    review_tags_requested = Signal(tuple)
    tag_details_requested = Signal(str, str, str)
    wiki_draft_requested = Signal(str)

    def __init__(self, catalog: LanguageCatalog, document: dict, browser_launcher=None) -> None:
        super().__init__(); self.catalog = catalog; self.document = document; self.browser_launcher = browser_launcher
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 20, 16, 24)
        self.title = QLabel(); self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.title)
        top = QHBoxLayout(); self.board_label = QLabel(); self.board = QComboBox()
        self.board.addItem("Gelbooru", "gelbooru"); self.board.addItem("e621", "e621")
        self.search = QLineEdit(); self.search_button = QPushButton(); self.update_button = QPushButton()
        top.addWidget(self.board_label); top.addWidget(self.board); top.addSpacing(12)
        top.addWidget(self.search, 1); top.addWidget(self.search_button); top.addWidget(self.update_button)
        layout.addLayout(top)
        splitter = QSplitter()
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results = QListWidget(); self.results.setMaximumHeight(180); self.results.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        right = QWidget(); right_layout = QVBoxLayout(right); right_layout.setContentsMargins(0, 0, 0, 0)
        self.search_results_label = QLabel(); right_layout.addWidget(self.search_results_label); right_layout.addWidget(self.results)
        self.details_title = QLabel(); self.details_title.setStyleSheet("font-size: 16px; font-weight: 600;"); right_layout.addWidget(self.details_title)
        self.definition = QTextBrowser(); self.definition.setOpenLinks(False); self.definition.setMinimumHeight(130); self.definition.anchorClicked.connect(self._definition_link_clicked); right_layout.addWidget(self.definition)
        self.wiki_button = QPushButton(); self.wiki_button.hide(); self.wiki_button.clicked.connect(self._open_wiki); right_layout.addWidget(self.wiki_button)
        self.recurring_label = QLabel(); self.recurring_label.setWordWrap(True); right_layout.addWidget(self.recurring_label)
        self.recurring = NavigableTagList(); self.recurring.setFlow(QListWidget.Flow.LeftToRight); self.recurring.setWrapping(True); self.recurring.setMaximumHeight(120); self.recurring.tag_clicked.connect(self._open_recurring); self.recurring.itemActivated.connect(self._open_recurring); right_layout.addWidget(self.recurring)
        self.send_recurring_review = QPushButton(); self.send_recurring_review.clicked.connect(self._send_recurring_to_review); right_layout.addWidget(self.send_recurring_review)
        self.samples = QWidget(); self.samples_grid = QGridLayout(self.samples); self.samples_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft); right_layout.addWidget(self.samples)
        self.network = QNetworkAccessManager(self); self.details_generation = 0; self.wiki_url = ""
        splitter.addWidget(self.tree); splitter.addWidget(right)
        splitter.setStretchFactor(0, 3); splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.add_category = QPushButton(); self.add_tags = QPushButton(); self.import_tree = QPushButton(); self.rename = QPushButton(); self.delete = QPushButton(); self.send_review = QPushButton(); self.save = QPushButton()
        for button in (self.add_category, self.add_tags, self.import_tree, self.rename, self.delete): actions.addWidget(button)
        actions.addWidget(self.send_review)
        actions.addStretch(1); actions.addWidget(self.save); layout.addLayout(actions)
        self.state = QLabel(); self.state.setWordWrap(True); self.state.setContentsMargins(2, 6, 2, 6); layout.addWidget(self.state)
        self.board.currentIndexChanged.connect(lambda _index: self.reload(False))
        self.tree.itemExpanded.connect(self._populate)
        self.tree.itemChanged.connect(self._tree_checked)
        self.tree.currentItemChanged.connect(self._inspect_item)
        self.tree.customContextMenuRequested.connect(self._show_tree_menu)
        self.search_button.clicked.connect(self._search)
        self.search.returnPressed.connect(self._search)
        self.results.itemClicked.connect(self._open_result)
        self.results.itemActivated.connect(self._open_result)
        self.add_category.clicked.connect(self._add_category)
        self.add_tags.clicked.connect(self._add_tags)
        self.import_tree.clicked.connect(self._import_tree)
        self.rename.clicked.connect(self._rename)
        self.delete.clicked.connect(self._delete)
        self.send_review.clicked.connect(self._send_to_review)
        self.save.clicked.connect(lambda: self.save_requested.emit(self.document))
        self.update_button.clicked.connect(self.update_requested.emit)
        self.reload(False); self.retranslate()

    def _board_root(self) -> dict:
        return self.document.setdefault("boards", {}).setdefault(str(self.board.currentData()), {})

    def _tree_state(self) -> tuple[set[tuple[str, ...]], tuple[str, ...]]:
        expanded: set[tuple[str, ...]] = set()
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.isExpanded(): expanded.add(tuple(item.data(0, ROLE_PATH) or ()))
            iterator += 1
        current = self.tree.currentItem()
        return expanded, tuple(current.data(0, ROLE_PATH) or ()) if current else ()

    def reload(self, preserve_state: bool = True) -> None:
        expanded, selected = self._tree_state() if preserve_state else (set(), ())
        self.tree.clear()
        for key, node in sorted(self._board_root().items(), key=lambda item: item[0].casefold()):
            self._add_item(self.tree.invisibleRootItem(), str(key), node, (str(key),), "category")
        self._restore_tree_state(expanded, selected)

    def _restore_tree_state(self, expanded: set[tuple[str, ...]], selected: tuple[str, ...]) -> None:
        for path in sorted(expanded, key=len):
            item = self._select_path(path, select=False)
            if item is not None: self.tree.expandItem(item)
        if selected: self._select_path(selected)

    def _add_item(self, parent: QTreeWidgetItem, label: str, node, path: tuple[str, ...], kind: str) -> None:
        empty_legacy_tag = kind == "tag" and isinstance(node, dict) and not node
        display = label + (" *" if empty_legacy_tag else "")
        item = QTreeWidgetItem(parent, [display]); item.setData(0, ROLE_NODE, node); item.setData(0, ROLE_PATH, path); item.setData(0, ROLE_KIND, kind)
        metadata = self.document.get("metadata", {}).get(str(self.board.currentData()), {}).get(label, {})
        has_wiki = isinstance(metadata, dict) and bool(metadata.get("wiki_url"))
        tag = label if kind == "tag" or has_wiki else str(node.get("__tag__", "")) if isinstance(node, dict) else ""
        item.setData(0, ROLE_TAG, tag)
        if empty_legacy_tag: item.setToolTip(0, self.catalog.text("organization.empty_entry"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        has_children = bool(node) if isinstance(node, list) else (
            bool(node.get("__tags__")) or any(not str(key).startswith("__") for key in node)
        ) if isinstance(node, dict) else False
        if has_children: QTreeWidgetItem(item, [""])

    def _populate(self, item: QTreeWidgetItem) -> None:
        if item.childCount() != 1 or item.child(0).text(0): return
        item.takeChildren(); node = item.data(0, ROLE_NODE); path = tuple(item.data(0, ROLE_PATH))
        if isinstance(node, list):
            for tag in sorted(map(str, node), key=str.casefold): self._add_item(item, tag, tag, path, "tag")
        elif isinstance(node, dict):
            for tag in sorted(map(str, node.get("__tags__", [])), key=str.casefold): self._add_item(item, tag, tag, path, "tag")
            for key, child in sorted(node.items(), key=lambda pair: str(pair[0]).casefold()):
                if not str(key).startswith("__"):
                    kind = "tag" if self._is_tag_leaf(child) else "category"
                    self._add_item(item, str(key), child, path + (str(key),), kind)
        if item.checkState(0) == Qt.CheckState.Checked:
            self.tree.blockSignals(True)
            for index in range(item.childCount()): item.child(index).setCheckState(0, Qt.CheckState.Checked)
            self.tree.blockSignals(False)

    @staticmethod
    def _is_tag_leaf(node) -> bool:
        return isinstance(node, dict) and not any(not str(key).startswith("__") for key in node)

    def _inspect_item(self, item: QTreeWidgetItem | None, _previous=None) -> None:
        tag = str(item.data(0, ROLE_TAG) or "") if item else ""
        if not tag:
            self.details_title.setText(self.catalog.text("organization.no_tag_selected"))
            self.definition.clear(); self.wiki_button.hide(); self._clear_recurring(); self._clear_samples(); return
        self.details_generation += 1
        self.details_title.setText(tag)
        self.definition.setPlainText(self.catalog.text("organization.details_loading"))
        self.wiki_button.hide(); self._clear_recurring(); self._clear_samples()
        self.tag_details_requested.emit(str(self.board.currentData()), tag, self._wiki_url(tag))

    def _wiki_url(self, tag: str) -> str:
        value = self.document.get("metadata", {}).get(str(self.board.currentData()), {}).get(tag, {})
        return str(value.get("wiki_url", "")) if isinstance(value, dict) else ""

    def _tree_checked(self, item: QTreeWidgetItem, _column: int) -> None:
        state = item.checkState(0)
        self.tree.blockSignals(True)
        for index in range(item.childCount()):
            child = item.child(index)
            if child.text(0): child.setCheckState(0, state)
        self.tree.blockSignals(False)

    def _selected_container(self) -> tuple[dict, tuple[str, ...]]:
        item = self.tree.currentItem(); path = tuple(item.data(0, ROLE_PATH)) if item else ()
        node = self._board_root()
        if item and item.data(0, ROLE_KIND) == "tag" and not isinstance(item.data(0, ROLE_NODE), dict):
            parent = node
            for key in path[:-1]: parent = parent[key]
            tag = str(item.data(0, ROLE_TAG) or path[-1])
            values = parent.get("__tags__", [])
            if tag in values: values.remove(tag)
            node = parent.setdefault(tag, {"__tag__": tag})
            return node, path
        for key in path:
            child = node.get(key)
            if not isinstance(child, dict):
                node[key] = {"__tags__": list(child) if isinstance(child, list) else []}
            node = node[key]
        if item and item.data(0, ROLE_KIND) == "tag":
            node.setdefault("__tag__", str(item.data(0, ROLE_TAG) or path[-1]))
        return node, path

    def _add_category(self) -> None:
        node, _ = self._selected_container(); value, ok = QInputDialog.getText(self, self.catalog.text("organization.add_category"), self.catalog.text("organization.name"))
        if ok and value.strip(): node.setdefault(value.strip(), {}); self.reload()

    def _add_tags(self) -> None:
        self._add_tags_to_item(self.tree.currentItem())

    @staticmethod
    def _insert_child_tags(node: dict, value: str) -> int:
        added = 0
        for raw_tag in value.replace(";", "\n").replace(",", "\n").splitlines():
            tag = raw_tag.strip()
            if not tag or tag in node: continue
            node[tag] = {"__tag__": tag}; added += 1
        return added

    def _add_tags_to_item(self, item: QTreeWidgetItem | None) -> None:
        if item is not None: self.tree.setCurrentItem(item)
        node, path = self._selected_container()
        value, ok = QInputDialog.getMultiLineText(
            self, self.catalog.text("organization.add_child_tags"),
            self.catalog.text("organization.child_tags_prompt"),
        )
        if not ok: return
        added = self._insert_child_tags(node, value)
        self.reload(); self._select_path(path)
        self.state.setText(self.catalog.text("organization.tags_added", count=added))

    def _show_tree_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None: return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        add_category = menu.addAction(self.catalog.text("organization.add_child_category"))
        add_children = menu.addAction(self.catalog.text("organization.add_child_tags"))
        wiki = menu.addAction(self.catalog.text("organization.set_wiki"))
        prepare_wiki = menu.addAction(self.catalog.text("organization.prepare_wiki"))
        menu.addSeparator()
        rename = menu.addAction(self.catalog.text("organization.rename"))
        delete = menu.addAction(self.catalog.text("organization.delete"))
        selected = menu.exec(self.tree.viewport().mapToGlobal(position))
        if selected is add_category: self._add_category()
        elif selected is add_children: self._add_tags_to_item(item)
        elif selected is wiki: self._set_wiki(item)
        elif selected is prepare_wiki: self.wiki_draft_requested.emit(str(item.data(0, ROLE_TAG) or tuple(item.data(0, ROLE_PATH))[-1]))
        elif selected is rename: self._rename()
        elif selected is delete: self._delete()

    def _set_wiki(self, item: QTreeWidgetItem) -> None:
        tag = str(item.data(0, ROLE_TAG) or tuple(item.data(0, ROLE_PATH))[-1])
        current = self._wiki_url(tag)
        value, ok = QInputDialog.getText(
            self, self.catalog.text("organization.set_wiki"),
            self.catalog.text("organization.wiki_prompt"), text=current,
        )
        if not ok: return
        value = value.strip(); board = str(self.board.currentData())
        if value.isdigit():
            value = f"https://e621.net/wiki_pages/{value}" if board == "e621" else f"https://gelbooru.com/index.php?page=wiki&s=view&id={value}"
        metadata = self.document.setdefault("metadata", {}).setdefault(board, {})
        entry = metadata.setdefault(tag, {})
        if value: entry["wiki_url"] = value
        else:
            entry.pop("wiki_url", None)
            if not entry: metadata.pop(tag, None)
        path = tuple(item.data(0, ROLE_PATH)); self.reload(); self._select_path(path)
        self.state.setText(self.catalog.text("organization.wiki_set") if value else self.catalog.text("organization.wiki_removed"))

    def _select_path(self, path: tuple[str, ...], select: bool = True) -> QTreeWidgetItem | None:
        current = self.tree.invisibleRootItem()
        for key in path:
            self.tree.expandItem(current)
            found = next((
                current.child(index) for index in range(current.childCount())
                if current.child(index).text(0) == key
                or str(current.child(index).data(0, ROLE_TAG) or "") == key
            ), None)
            if found is None: return None
            self._populate(found); self.tree.expandItem(found); current = found
        if select and current is not self.tree.invisibleRootItem(): self.tree.setCurrentItem(current)
        return current

    def _import_tree(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.catalog.text("organization.import"), "", "Text (*.txt *.md);;All files (*)")
        if not path: return
        try:
            from booruflow.infrastructure.wiki_tag_importer import parse_pasted_tag_list
            incoming = parse_pasted_tag_list(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
            target, _ = self._selected_container()
            self._merge(target, incoming)
            self.reload(); self.state.setText(self.catalog.text("organization.imported", path=path))
        except (OSError, ValueError, TypeError) as exc:
            self.state.setText(self.catalog.text("organization.failed", error=exc))

    @classmethod
    def _merge(cls, target: dict, incoming: dict) -> None:
        for key, value in incoming.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                cls._merge(target[key], value)
            elif key not in target:
                target[key] = value

    def _send_to_review(self) -> None:
        tags: list[str] = []
        checked_results = [self.results.item(index) for index in range(self.results.count()) if self.results.item(index).checkState() == Qt.CheckState.Checked]
        for item in checked_results or self.results.selectedItems():
            tag = item.text().split("  —  ", 1)[0].strip()
            if tag and tag not in tags: tags.append(tag)
        iterator = QTreeWidgetItemIterator(self.tree, QTreeWidgetItemIterator.IteratorFlag.Checked)
        while iterator.value():
            item = iterator.value()
            if item.data(0, ROLE_KIND) == "tag":
                tag = str(item.data(0, ROLE_TAG) or item.text(0)).removesuffix(" *")
                if tag not in tags: tags.append(tag)
            elif item.data(0, ROLE_KIND) == "category":
                for tag, _path in iter_tag_paths(item.data(0, ROLE_NODE)):
                    if tag not in tags: tags.append(tag)
            iterator += 1
        if not tags:
            for current in self.tree.selectedItems():
                node = current.data(0, ROLE_NODE)
                values = [str(current.data(0, ROLE_TAG) or current.text(0)).removesuffix(" *")] if current.data(0, ROLE_KIND) == "tag" else [tag for tag, _path in iter_tag_paths(node)]
                for tag in values:
                    if tag not in tags: tags.append(tag)
        if tags: self.review_tags_requested.emit(tuple(tags))

    def _parent_and_key(self, path: tuple[str, ...]):
        node = self._board_root()
        for key in path[:-1]: node = node[key]
        return node, path[-1]

    def _rename(self) -> None:
        item = self.tree.currentItem()
        if not item: return
        path = tuple(item.data(0, ROLE_PATH)); stored = item.data(0, ROLE_NODE)
        expanded, _selected = self._tree_state()
        key = str(item.data(0, ROLE_TAG) or path[-1])
        value, ok = QInputDialog.getText(self, self.catalog.text("organization.rename"), self.catalog.text("organization.name"), text=key)
        new = value.strip()
        if not ok or not new or new == key: return
        if isinstance(stored, dict):
            parent, path_key = self._parent_and_key(path)
            if new in parent: return
            parent[new] = parent.pop(path_key)
            if stored.get("__tag__") == key: stored["__tag__"] = new
            selected_path = path[:-1] + (new,)
        else:
            parent = self._board_root()
            for path_key in path: parent = parent[path_key]
            tags = parent.get("__tags__", [])
            if new in tags: return
            if key in tags: tags[tags.index(key)] = new
            selected_path = path
        board_metadata = self.document.setdefault("metadata", {}).setdefault(str(self.board.currentData()), {})
        if key in board_metadata: board_metadata[new] = board_metadata.pop(key)
        rewritten = {
            selected_path + expanded_path[len(path):] if expanded_path[:len(path)] == path else expanded_path
            for expanded_path in expanded
        }
        self.reload(False); self._restore_tree_state(rewritten, selected_path)

    def _delete(self) -> None:
        item = self.tree.currentItem()
        if not item: return
        if QMessageBox.question(self, self.catalog.text("organization.delete"), self.catalog.text("organization.confirm_delete", name=item.text(0))) != QMessageBox.StandardButton.Yes: return
        path = tuple(item.data(0, ROLE_PATH)); kind = item.data(0, ROLE_KIND)
        parent_path = path[:-1] if isinstance(item.data(0, ROLE_NODE), dict) else path
        if kind == "category": parent, key = self._parent_and_key(path); parent.pop(key, None)
        else:
            stored = item.data(0, ROLE_NODE)
            tag = str(item.data(0, ROLE_TAG) or item.text(0)).removesuffix(" *")
            if isinstance(stored, dict) and path:
                parent, key = self._parent_and_key(path); parent.pop(key, None)
            else:
                node = self._board_root()
                for key in path: node = node[key]
                if tag in node.get("__tags__", []): node["__tags__"].remove(tag)
        self.reload(); self._select_path(parent_path)

    def _search(self) -> None:
        needle = self.search.text().strip().casefold(); self.results.clear()
        if not needle: return
        for tag, path in iter_tag_paths(self._board_root()):
            if needle in tag.casefold():
                self.results.addItem(f"{tag}  —  {' / '.join(path)}")
                result = self.results.item(self.results.count() - 1)
                result.setData(ROLE_PATH, path)
                result.setData(ROLE_TAG, tag)
                result.setFlags(result.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                result.setCheckState(Qt.CheckState.Unchecked)
                if self.results.count() >= 500: break
        self.state.setText(self.catalog.text("organization.search_count", count=self.results.count()))

    def _open_result(self, item) -> None:
        path = tuple(item.data(ROLE_PATH)); current = self.tree.invisibleRootItem()
        for key in path:
            found = next((
                current.child(i) for i in range(current.childCount())
                if current.child(i).text(0) == key or str(current.child(i).data(0, ROLE_TAG) or "") == key
            ), None)
            if not found: break
            self._populate(found); self.tree.expandItem(found); current = found
        wanted_tag = str(item.data(ROLE_TAG) or "")
        if wanted_tag and str(current.data(0, ROLE_TAG) or "").casefold() != wanted_tag.casefold():
            self._populate(current); self.tree.expandItem(current)
            child = next((
                current.child(index) for index in range(current.childCount())
                if str(current.child(index).data(0, ROLE_TAG) or "").casefold() == wanted_tag.casefold()
            ), None)
            if child is not None: current = child
        already_current = self.tree.currentItem() is current
        self.tree.setCurrentItem(current)
        if already_current:
            self._inspect_item(current)

    def _navigate_to_tag(self, tag: str) -> None:
        exact_path = next((path for value, path in iter_tag_paths(self._board_root()) if value.casefold() == tag.casefold()), None)
        if exact_path is not None:
            self.search.setText(tag); self._search()
            for index in range(self.results.count()):
                item = self.results.item(index)
                if tuple(item.data(ROLE_PATH)) == tuple(exact_path):
                    self._open_result(item); return
        self.details_generation += 1
        self.details_title.setText(tag)
        self.definition.setPlainText(self.catalog.text("organization.details_loading"))
        self.wiki_button.hide(); self._clear_recurring(); self._clear_samples()
        self.tag_details_requested.emit(str(self.board.currentData()), tag, self._wiki_url(tag))

    def set_busy(self, busy: bool) -> None:
        self.save.setEnabled(not busy); self.update_button.setEnabled(not busy)

    def show_tag_details(self, details: dict) -> None:
        tag = str(details.get("tag", ""))
        if tag != self.details_title.text(): return
        definition = str(details.get("definition", "")).strip()
        errors = [str(value) for value in details.get("errors", []) if str(value)]
        wiki_tags = [str(value) for value in details.get("wiki_tags", []) if str(value) and str(value).casefold() != tag.casefold()]
        if definition or wiki_tags:
            body = html.escape(definition).replace("\n", "<br>")
            if wiki_tags:
                links = ", ".join(
                    f'<a href="booruflow-tag:{urllib.parse.quote(value, safe="")}">{html.escape(value)}</a>'
                    for value in wiki_tags
                )
                body += f'<p><b>{html.escape(self.catalog.text("organization.wiki_references"))}</b> {links}</p>'
            self.definition.setHtml(body)
        elif details.get("online"):
            self.definition.setPlainText(self.catalog.text("organization.wiki_empty"))
        elif details.get("cached"):
            self.definition.setPlainText(self.catalog.text("organization.cached_offline"))
        else:
            self.definition.setPlainText(self.catalog.text("organization.internet_required", error="; ".join(errors)))
        self.wiki_url = str(details.get("wiki_url", ""))
        self.wiki_button.setVisible(bool(self.wiki_url)); self.wiki_button.setText(self.catalog.text("organization.open_wiki"))
        self._show_recurring(list(details.get("recurring", [])), int(details.get("sample_size", 0)))
        self._show_samples(list(details.get("samples", [])))

    def _definition_link_clicked(self, url: QUrl) -> None:
        if url.scheme() == "booruflow-tag":
            encoded_tag = url.path() or url.toString().partition(":")[2]
            self._navigate_to_tag(urllib.parse.unquote(encoded_tag))
        else:
            self._open_remote_url(url.toString())

    def _clear_recurring(self) -> None:
        self.recurring.clear(); self.recurring_label.clear(); self.recurring.hide(); self.recurring_label.hide(); self.send_recurring_review.hide()

    def _show_recurring(self, recurring: list[dict], sample_size: int) -> None:
        self._clear_recurring()
        self.recurring_label.setText(self.catalog.text("organization.recurring", sample_size=sample_size, count=len(recurring)))
        self.recurring_label.show()
        if not recurring:
            self.recurring_label.setText(self.catalog.text("organization.no_recurring", sample_size=sample_size)); return
        for entry in recurring:
            tag = str(entry.get("tag", "")); count = int(entry.get("count", 0))
            if not tag: continue
            self.recurring.addItem(f"{tag} ({count})")
            item = self.recurring.item(self.recurring.count() - 1); item.setData(ROLE_TAG, tag)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(self.catalog.text("organization.recurring_tooltip"))
        self.recurring.show(); self.send_recurring_review.show()

    def _open_recurring(self, item) -> None:
        self._navigate_to_tag(str(item.data(ROLE_TAG) or ""))

    def _send_recurring_to_review(self) -> None:
        tags = tuple(
            str(self.recurring.item(index).data(ROLE_TAG))
            for index in range(self.recurring.count())
            if self.recurring.item(index).checkState() == Qt.CheckState.Checked
        )
        if tags: self.review_tags_requested.emit(tags)

    def _open_wiki(self) -> None:
        if self.wiki_url: self._open_remote_url(self.wiki_url)

    def _open_remote_url(self, url: str) -> None:
        if self.browser_launcher and "gelbooru.com" in url.casefold(): self.browser_launcher.open(url)
        else: QDesktopServices.openUrl(QUrl(url))

    def _clear_samples(self) -> None:
        while self.samples_grid.count():
            item = self.samples_grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _show_samples(self, samples: list[dict]) -> None:
        self._clear_samples(); generation = self.details_generation
        if not samples:
            label = QLabel(self.catalog.text("organization.no_samples")); self.samples_grid.addWidget(label, 0, 0); return
        for index, sample in enumerate(samples[:6]):
            button = QToolButton(); button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon); button.setIconSize(QSize(115, 95)); button.setFixedSize(130, 125)
            button.setText(f"#{int(sample.get('id', 0))}"); url = str(sample.get("post_url", "")); button.clicked.connect(lambda _checked=False, value=url: self._open_remote_url(value))
            self.samples_grid.addWidget(button, index // 3, index % 3)
            preview = str(sample.get("preview_url", ""))
            if preview:
                request = QNetworkRequest(QUrl(preview)); request.setRawHeader(b"User-Agent", b"BooruFlow/0.1"); request.setRawHeader(b"Referer", b"https://e621.net/" if "e621.net" in preview else b"https://gelbooru.com/")
                reply = self.network.get(request); reply.finished.connect(lambda current=reply, target=button, value=generation: self._sample_ready(current, target, value))

    def _sample_ready(self, reply: QNetworkReply, button: QToolButton, generation: int) -> None:
        try:
            if generation == self.details_generation and reply.error() == QNetworkReply.NetworkError.NoError:
                pixmap = QPixmap();
                if pixmap.loadFromData(bytes(reply.readAll())): button.setIcon(QIcon(pixmap))
        except RuntimeError:
            pass
        finally:
            reply.deleteLater()

    def retranslate(self) -> None:
        text = self.catalog.text; self.title.setText(text("nav.organization")); self.board_label.setText(text("organization.board")); self.search.setPlaceholderText(text("organization.search")); self.search_button.setText(text("organization.search_button")); self.update_button.setText(text("organization.update")); self.add_category.setText(text("organization.add_category")); self.add_tags.setText(text("organization.add_tags")); self.import_tree.setText(text("organization.import")); self.rename.setText(text("organization.rename")); self.delete.setText(text("organization.delete")); self.send_review.setText(text("organization.send_review")); self.save.setText(text("organization.save")); self.search_results_label.setText(text("organization.search_results"))
        self.send_recurring_review.setText(text("organization.send_recurring_review"))
        if not self.state.text(): self.state.setText(text("organization.ready"))
