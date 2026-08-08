"""Lazy taxonomy browser and editor."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from booruflow.application.taxonomy import iter_tag_paths
from booruflow.infrastructure.localization import LanguageCatalog


ROLE_NODE = Qt.ItemDataRole.UserRole
ROLE_PATH = Qt.ItemDataRole.UserRole + 1
ROLE_KIND = Qt.ItemDataRole.UserRole + 2


class OrganizationPage(QWidget):
    save_requested = Signal(object)
    update_requested = Signal()
    review_tags_requested = Signal(tuple)

    def __init__(self, catalog: LanguageCatalog, document: dict) -> None:
        super().__init__(); self.catalog = catalog; self.document = document
        layout = QVBoxLayout(self); layout.setContentsMargins(28, 20, 28, 24)
        self.title = QLabel(); self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.title)
        top = QHBoxLayout(); self.board_label = QLabel(); self.board = QComboBox()
        self.board.addItem("Gelbooru", "gelbooru"); self.board.addItem("e621", "e621")
        self.search = QLineEdit(); self.search_button = QPushButton(); self.update_button = QPushButton()
        top.addWidget(self.board_label); top.addWidget(self.board); top.addSpacing(12)
        top.addWidget(self.search, 1); top.addWidget(self.search_button); top.addWidget(self.update_button)
        layout.addLayout(top)
        splitter = QSplitter()
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        self.results = QListWidget(); self.results.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection); splitter.addWidget(self.tree); splitter.addWidget(self.results)
        splitter.setStretchFactor(0, 3); splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.add_category = QPushButton(); self.add_tags = QPushButton(); self.import_tree = QPushButton(); self.rename = QPushButton(); self.delete = QPushButton(); self.send_review = QPushButton(); self.save = QPushButton()
        for button in (self.add_category, self.add_tags, self.import_tree, self.rename, self.delete): actions.addWidget(button)
        actions.addWidget(self.send_review)
        actions.addStretch(1); actions.addWidget(self.save); layout.addLayout(actions)
        self.state = QLabel(); self.state.setWordWrap(True); self.state.setContentsMargins(2, 6, 2, 6); layout.addWidget(self.state)
        self.board.currentIndexChanged.connect(self.reload)
        self.tree.itemExpanded.connect(self._populate)
        self.search_button.clicked.connect(self._search)
        self.results.itemActivated.connect(self._open_result)
        self.add_category.clicked.connect(self._add_category)
        self.add_tags.clicked.connect(self._add_tags)
        self.import_tree.clicked.connect(self._import_tree)
        self.rename.clicked.connect(self._rename)
        self.delete.clicked.connect(self._delete)
        self.send_review.clicked.connect(self._send_to_review)
        self.save.clicked.connect(lambda: self.save_requested.emit(self.document))
        self.update_button.clicked.connect(self.update_requested.emit)
        self.reload(); self.retranslate()

    def _board_root(self) -> dict:
        return self.document.setdefault("boards", {}).setdefault(str(self.board.currentData()), {})

    def reload(self) -> None:
        self.tree.clear()
        for key, node in sorted(self._board_root().items(), key=lambda item: item[0].casefold()):
            self._add_item(self.tree.invisibleRootItem(), str(key), node, (str(key),), "category")

    def _add_item(self, parent: QTreeWidgetItem, label: str, node, path: tuple[str, ...], kind: str) -> None:
        item = QTreeWidgetItem(parent, [label]); item.setData(0, ROLE_NODE, node); item.setData(0, ROLE_PATH, path); item.setData(0, ROLE_KIND, kind)
        has_children = bool(node) if isinstance(node, (dict, list)) else False
        if has_children: QTreeWidgetItem(item, [""])

    def _populate(self, item: QTreeWidgetItem) -> None:
        if item.childCount() != 1 or item.child(0).text(0): return
        item.takeChildren(); node = item.data(0, ROLE_NODE); path = tuple(item.data(0, ROLE_PATH))
        if isinstance(node, list):
            for tag in sorted(map(str, node), key=str.casefold): self._add_item(item, tag, tag, path, "tag")
        elif isinstance(node, dict):
            for tag in sorted(map(str, node.get("__tags__", [])), key=str.casefold): self._add_item(item, tag, tag, path, "tag")
            if node.get("__tag__"): self._add_item(item, str(node["__tag__"]), str(node["__tag__"]), path, "tag")
            for key, child in sorted(node.items(), key=lambda pair: str(pair[0]).casefold()):
                if not str(key).startswith("__"): self._add_item(item, str(key), child, path + (str(key),), "category")

    def _selected_container(self) -> tuple[dict, tuple[str, ...]]:
        item = self.tree.currentItem(); path = tuple(item.data(0, ROLE_PATH)) if item else ()
        if item and item.data(0, ROLE_KIND) == "tag": path = path
        node = self._board_root()
        for key in path:
            child = node.get(key)
            if not isinstance(child, dict):
                node[key] = {"__tags__": list(child) if isinstance(child, list) else []}
            node = node[key]
        return node, path

    def _add_category(self) -> None:
        node, _ = self._selected_container(); value, ok = QInputDialog.getText(self, self.catalog.text("organization.add_category"), self.catalog.text("organization.name"))
        if ok and value.strip(): node.setdefault(value.strip(), {}); self.reload()

    def _add_tags(self) -> None:
        node, _ = self._selected_container(); value, ok = QInputDialog.getMultiLineText(self, self.catalog.text("organization.add_tags"), self.catalog.text("organization.tags_prompt"))
        if ok:
            tags = node.setdefault("__tags__", [])
            for tag in value.replace(";", "\n").splitlines():
                if tag.strip() and tag.strip() not in tags: tags.append(tag.strip())
            self.reload()

    def _import_tree(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.catalog.text("organization.import"), "", "Text (*.txt *.md);;All files (*)")
        if not path: return
        try:
            from legacy.wiki_tag_importer import parse_pasted_tag_list
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
        for item in self.results.selectedItems():
            tag = item.text().split("  —  ", 1)[0].strip()
            if tag and tag not in tags: tags.append(tag)
        if not tags:
            current = self.tree.currentItem()
            if current:
                node = current.data(0, ROLE_NODE)
                tags = list(dict.fromkeys(tag for tag, _path in iter_tag_paths(node)))
                if current.data(0, ROLE_KIND) == "tag": tags = [current.text(0)]
        if tags: self.review_tags_requested.emit(tuple(tags))

    def _parent_and_key(self, path: tuple[str, ...]):
        node = self._board_root()
        for key in path[:-1]: node = node[key]
        return node, path[-1]

    def _rename(self) -> None:
        item = self.tree.currentItem()
        if not item or item.data(0, ROLE_KIND) != "category": return
        path = tuple(item.data(0, ROLE_PATH)); parent, key = self._parent_and_key(path)
        value, ok = QInputDialog.getText(self, self.catalog.text("organization.rename"), self.catalog.text("organization.name"), text=key)
        if ok and value.strip() and value.strip() != key: parent[value.strip()] = parent.pop(key); self.reload()

    def _delete(self) -> None:
        item = self.tree.currentItem()
        if not item: return
        if QMessageBox.question(self, self.catalog.text("organization.delete"), self.catalog.text("organization.confirm_delete", name=item.text(0))) != QMessageBox.StandardButton.Yes: return
        path = tuple(item.data(0, ROLE_PATH)); kind = item.data(0, ROLE_KIND)
        if kind == "category": parent, key = self._parent_and_key(path); parent.pop(key, None)
        else:
            node = self._board_root()
            for key in path: node = node[key]
            tag = item.text(0)
            if tag in node.get("__tags__", []): node["__tags__"].remove(tag)
            elif node.get("__tag__") == tag: node.pop("__tag__", None)
        self.reload()

    def _search(self) -> None:
        needle = self.search.text().strip().casefold(); self.results.clear()
        if not needle: return
        for tag, path in iter_tag_paths(self._board_root()):
            if needle in tag.casefold():
                self.results.addItem(f"{tag}  —  {' / '.join(path)}")
                self.results.item(self.results.count() - 1).setData(ROLE_PATH, path)
                if self.results.count() >= 500: break
        self.state.setText(self.catalog.text("organization.search_count", count=self.results.count()))

    def _open_result(self, item) -> None:
        path = tuple(item.data(ROLE_PATH)); current = self.tree.invisibleRootItem()
        for key in path:
            found = next((current.child(i) for i in range(current.childCount()) if current.child(i).text(0) == key), None)
            if not found: break
            self.tree.expandItem(found); current = found
        self.tree.setCurrentItem(current)

    def set_busy(self, busy: bool) -> None:
        self.save.setEnabled(not busy); self.update_button.setEnabled(not busy)

    def retranslate(self) -> None:
        text = self.catalog.text; self.title.setText(text("nav.organization")); self.board_label.setText(text("organization.board")); self.search.setPlaceholderText(text("organization.search")); self.search_button.setText(text("organization.search_button")); self.update_button.setText(text("organization.update")); self.add_category.setText(text("organization.add_category")); self.add_tags.setText(text("organization.add_tags")); self.import_tree.setText(text("organization.import")); self.rename.setText(text("organization.rename")); self.delete.setText(text("organization.delete")); self.send_review.setText(text("organization.send_review")); self.save.setText(text("organization.save"))
        if not self.state.text(): self.state.setText(text("organization.ready"))
