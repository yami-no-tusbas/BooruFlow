"""Assisted Gelbooru wiki draft editor."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QTextBrowser,
    QToolButton,
    QVBoxLayout, QWidget,
)

from booruflow.application.ports import SettingsRepository
from booruflow.application.wiki import TEMPLATES, missing_local_tags, referenced_tags, render_wiki_preview, validate_wiki_source
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.icons import wiki_tool_icon


class WikiPage(QWidget):
    organization_tag_requested = Signal(str)
    LAST_LOAD_DIRECTORY_KEY = "wiki_last_load_directory"

    def __init__(
        self,
        catalog: LanguageCatalog,
        drafts_directory: Path,
        tag_database_path: Path | None = None,
        settings_repository: SettingsRepository | None = None,
    ) -> None:
        super().__init__(); self.catalog = catalog; self.drafts_directory = drafts_directory; self.tag_database_path = tag_database_path
        self.settings_repository = settings_repository
        self._active_draft_path: Path | None = None
        self._active_draft_tag = ""
        layout = QVBoxLayout(self); layout.setContentsMargins(28, 20, 28, 24); layout.setSpacing(10)
        self.title = QLabel(); self.title.setStyleSheet("font-size: 22px; font-weight: 600;"); layout.addWidget(self.title)
        setup = QHBoxLayout(); self.tag_label = QLabel(); self.tag = QLineEdit(); self.template_label = QLabel(); self.template = QComboBox()
        for key in TEMPLATES: self.template.addItem("", key)
        self.apply_template = QPushButton(); setup.addWidget(self.tag_label); setup.addWidget(self.tag, 1); setup.addWidget(self.template_label); setup.addWidget(self.template); setup.addWidget(self.apply_template); layout.addLayout(setup)
        ribbon = QHBoxLayout(); ribbon.setSpacing(8); self.ribbon_labels: dict[str, QLabel] = {}; self.tool_buttons: dict[str, QToolButton] = {}
        style_group, style_row = self._ribbon_group("style"); self.heading_selector = QComboBox(); self.heading_selector.setMinimumWidth(125); style_row.addWidget(self.heading_selector)
        for key in ("bold", "italic"): style_row.addWidget(self._tool_button(key))
        links_group, links_row = self._ribbon_group("links")
        for key in ("tag_link", "search_link", "external"): links_row.addWidget(self._tool_button(key))
        insert_group, insert_row = self._ribbon_group("insert")
        for key in ("post", "quote", "spoiler"): insert_row.addWidget(self._tool_button(key))
        sections_group, sections_row = self._ribbon_group("sections"); sections_row.addWidget(self._tool_button("see_also"))
        for group in (style_group, links_group, insert_group, sections_group): ribbon.addWidget(group)
        ribbon.addStretch(1); layout.addLayout(ribbon)
        splitter = QSplitter(); source_box = QWidget(); source_layout = QVBoxLayout(source_box); source_layout.setContentsMargins(0, 0, 0, 0)
        self.source_label = QLabel(); self.source = QPlainTextEdit(); self.source.setTabChangesFocus(True); source_layout.addWidget(self.source_label); source_layout.addWidget(self.source)
        preview_box = QWidget(); preview_layout = QVBoxLayout(preview_box); preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_label = QLabel(); self.preview = QTextBrowser(); self.preview.setOpenLinks(False); self.preview.anchorClicked.connect(self._open_preview_link); preview_layout.addWidget(self.preview_label); preview_layout.addWidget(self.preview)
        source_box.setMinimumWidth(300); preview_box.setMinimumWidth(300)
        splitter.addWidget(source_box); splitter.addWidget(preview_box); splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 1); splitter.setSizes([500, 500]); layout.addWidget(splitter, 1)
        self.validation = QLabel(); self.validation.setWordWrap(True); self.validation.setContentsMargins(8, 6, 8, 6); layout.addWidget(self.validation)
        actions = QHBoxLayout(); self.load = QPushButton(); self.save = QPushButton(); self.copy = QPushButton(); self.open_create = QPushButton()
        actions.addWidget(self.load); actions.addWidget(self.save); actions.addStretch(1); actions.addWidget(self.copy); actions.addWidget(self.open_create); layout.addLayout(actions)
        self.preview_timer = QTimer(self); self.preview_timer.setSingleShot(True); self.preview_timer.setInterval(180); self.preview_timer.timeout.connect(self._refresh)
        self.autosave_timer = QTimer(self); self.autosave_timer.setSingleShot(True); self.autosave_timer.setInterval(900); self.autosave_timer.timeout.connect(lambda: self._save_draft(silent=True))
        self.source.textChanged.connect(self._changed); self.tag.textChanged.connect(self._changed)
        self.apply_template.clicked.connect(self._apply_template); self.load.clicked.connect(self._load_draft); self.save.clicked.connect(self._save_draft); self.copy.clicked.connect(self._copy_source); self.open_create.clicked.connect(self._open_create)
        self.tool_buttons["bold"].clicked.connect(lambda: self._wrap("[b]", "[/b]")); self.tool_buttons["italic"].clicked.connect(lambda: self._wrap("[i]", "[/i]"))
        self.heading_selector.activated.connect(self._heading_selected)
        self.tool_buttons["tag_link"].clicked.connect(lambda: self._wrap("[[", "]]", "tag_name")); self.tool_buttons["search_link"].clicked.connect(lambda: self._wrap("{{", "}}", "tag_name"))
        self.tool_buttons["post"].clicked.connect(lambda: self._wrap("[post]", "[/post]", "123456")); self.tool_buttons["quote"].clicked.connect(lambda: self._wrap("[quote]", "[/quote]")); self.tool_buttons["spoiler"].clicked.connect(lambda: self._wrap("[spoiler]", "[/spoiler]"))
        self.tool_buttons["external"].clicked.connect(lambda: self._insert_section("[b]External links:[/b]\nhttps://example.com/")); self.tool_buttons["see_also"].clicked.connect(lambda: self._insert_section("[b]See also:[/b]\n* [[related_tag]]"))
        self.shortcuts: list[QShortcut] = []
        self._add_shortcut("Ctrl+B", lambda: self._wrap("[b]", "[/b]")); self._add_shortcut("Ctrl+I", lambda: self._wrap("[i]", "[/i]")); self._add_shortcut("Ctrl+K", lambda: self._wrap("[[", "]]", "tag_name"))
        self._add_shortcut("Ctrl+Shift+K", lambda: self._wrap("{{", "}}", "tag_name")); self._add_shortcut("Ctrl+S", self._save_draft); self._add_shortcut("Ctrl+Shift+C", self._copy_source)
        for level in range(1, 6): self._add_shortcut(f"Ctrl+Alt+{level}", lambda value=level: self._wrap(f"[h{value}]", f"[/h{value}]", "Heading"))
        self.retranslate(); self._refresh()

    def _ribbon_group(self, key: str) -> tuple[QFrame, QHBoxLayout]:
        frame = QFrame(); frame.setFrameShape(QFrame.Shape.StyledPanel); outer = QVBoxLayout(frame); outer.setContentsMargins(6, 4, 6, 3); outer.setSpacing(2)
        row = QHBoxLayout(); row.setSpacing(3); label = QLabel(); label.setAlignment(Qt.AlignmentFlag.AlignCenter); label.setStyleSheet("color:#6B7280;font-size:10px;")
        self.ribbon_labels[key] = label; outer.addLayout(row); outer.addWidget(label); return frame, row

    def _tool_button(self, key: str) -> QToolButton:
        button = QToolButton(); button.setIcon(wiki_tool_icon(key)); button.setIconSize(QSize(24, 24)); button.setFixedSize(34, 34)
        self.tool_buttons[key] = button; return button

    def _heading_selected(self, index: int) -> None:
        level = int(self.heading_selector.itemData(index) or 0)
        if level: self._wrap(f"[h{level}]", f"[/h{level}]", "Heading")
        self.heading_selector.setCurrentIndex(0)

    def _add_shortcut(self, sequence: str, callback) -> None:
        shortcut = QShortcut(QKeySequence(sequence), self); shortcut.activated.connect(callback); self.shortcuts.append(shortcut)

    def set_tag(self, tag: str, template: str = "character") -> None:
        tag = tag.strip()
        if self.tag.text().strip() == tag and self.source.toPlainText().strip():
            self.tag.setFocus(); return
        if self.tag.text().strip() and self.tag.text().strip() != tag: self._save_draft(silent=True)
        path = self._draft_path_for_tag(tag)
        if path.is_file():
            self._load_draft_path(path); return
        self._active_draft_path = None; self._active_draft_tag = ""
        self.source.clear(); self.tag.setText(tag); index = self.template.findData(template)
        if index >= 0: self.template.setCurrentIndex(index)
        self._apply_template(force=True)
        self.tag.setFocus()

    def _changed(self) -> None:
        self.preview_timer.start()
        if self.tag.text().strip() and self.source.toPlainText().strip(): self.autosave_timer.start()

    def _refresh(self) -> None:
        source = self.source.toPlainText(); self.preview.setHtml(render_wiki_preview(source))
        issues = validate_wiki_source(source)
        issues.extend(("missing", tag) for tag in missing_local_tags(self.tag_database_path, referenced_tags(source)))
        if not issues:
            self.validation.setText(self.catalog.text("wiki.validation_ok")); self.validation.setStyleSheet("color:#16803b;")
            return
        messages = [self.catalog.text(f"wiki.issue_{code}", value=value) for code, value in issues]
        self.validation.setText("\n".join(f"• {message}" for message in messages)); self.validation.setStyleSheet("color:#b42318;")

    def _wrap(self, before: str, after: str, placeholder: str = "text") -> None:
        cursor = self.source.textCursor(); selected = cursor.selectedText() or placeholder
        cursor.insertText(before + selected + after); self.source.setTextCursor(cursor); self.source.setFocus()

    def _insert_section(self, text: str) -> None:
        cursor = self.source.textCursor()
        prefix = "\n\n" if self.source.toPlainText().strip() else ""
        cursor.movePosition(cursor.MoveOperation.End); cursor.insertText(prefix + text); self.source.setTextCursor(cursor); self.source.setFocus()

    def _apply_template(self, _checked: bool = False, force: bool = False) -> None:
        if self.source.toPlainText().strip() and not force:
            answer = QMessageBox.question(self, self.catalog.text("wiki.apply_template"), self.catalog.text("wiki.replace_confirm"))
            if answer != QMessageBox.StandardButton.Yes: return
        self.source.setPlainText(TEMPLATES[str(self.template.currentData())])

    def _draft_path(self) -> Path | None:
        tag = self.tag.text().strip()
        if not tag: return None
        if self._active_draft_path is not None and tag == self._active_draft_tag:
            return self._active_draft_path
        return self._draft_path_for_tag(tag)

    def _draft_path_for_tag(self, tag: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.()+-]+", "_", tag).strip("._") or "untitled"
        return self.drafts_directory / f"{safe}.json"

    def _save_draft(self, _checked: bool = False, silent: bool = False) -> None:
        path = self._draft_path()
        if path is None:
            if not silent: self.validation.setText(self.catalog.text("wiki.tag_required"))
            return
        data = {"tag": self.tag.text().strip(), "template": str(self.template.currentData()), "source": self.source.toPlainText(), "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); os.replace(temporary, path)
            self._active_draft_path = path; self._active_draft_tag = data["tag"]
            if not silent: self.validation.setText(self.catalog.text("wiki.saved", path=path))
        except OSError as exc:
            self.validation.setText(self.catalog.text("wiki.save_failed", error=exc))

    def _load_draft(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            self.catalog.text("wiki.load"),
            str(self._initial_load_directory()),
            "Wiki drafts (*.json);;All files (*)",
        )
        if not path: return
        selected = Path(path)
        self._remember_load_directory(selected.parent)
        self._load_draft_path(selected)

    def _initial_load_directory(self) -> Path:
        settings = self.settings_repository.load() if self.settings_repository else {}
        saved = str(settings.get(self.LAST_LOAD_DIRECTORY_KEY, "")).strip()
        if saved:
            directory = Path(saved)
            if directory.is_dir():
                return directory
        return self.drafts_directory

    def _remember_load_directory(self, directory: Path) -> None:
        if not self.settings_repository or not directory.is_dir():
            return
        settings = self.settings_repository.load()
        settings[self.LAST_LOAD_DIRECTORY_KEY] = str(directory.resolve())
        self.settings_repository.save(settings)

    def _load_draft_path(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig")); loaded_tag = str(data.get("tag", "")); self.tag.setText(loaded_tag)
            index = self.template.findData(str(data.get("template", "character")))
            if index >= 0: self.template.setCurrentIndex(index)
            self.source.setPlainText(str(data.get("source", "")))
            self._active_draft_path = path; self._active_draft_tag = loaded_tag
        except (OSError, ValueError, TypeError) as exc:
            self.validation.setText(self.catalog.text("wiki.load_failed", error=exc))

    def _copy_source(self) -> None:
        QApplication.clipboard().setText(self.source.toPlainText()); self.validation.setText(self.catalog.text("wiki.copied"))

    def _open_create(self) -> None:
        QApplication.clipboard().setText(self.source.toPlainText())
        QDesktopServices.openUrl(QUrl("https://gelbooru.com/index.php?page=wiki&s=create"))
        self.validation.setText(self.catalog.text("wiki.opened_create"))

    def _open_preview_link(self, url: QUrl) -> None:
        if url.scheme() == "booruflow-tag": self.organization_tag_requested.emit(urllib.parse.unquote(url.path()))
        else: QDesktopServices.openUrl(url)

    def retranslate(self) -> None:
        text = self.catalog.text; self.title.setText(text("nav.wiki")); self.tag_label.setText(text("wiki.tag")); self.tag.setPlaceholderText(text("wiki.tag_placeholder")); self.template_label.setText(text("wiki.template")); self.apply_template.setText(text("wiki.apply_template")); self.source_label.setText(text("wiki.source")); self.preview_label.setText(text("wiki.preview")); self.load.setText(text("wiki.load")); self.save.setText(text("wiki.save")); self.copy.setText(text("wiki.copy")); self.open_create.setText(text("wiki.open_create"))
        for key in TEMPLATES:
            index = self.template.findData(key)
            if index >= 0: self.template.setItemText(index, text(f"wiki.template_{key}"))
        self.heading_selector.clear(); self.heading_selector.addItem(text("wiki.normal_text"), 0)
        for level in range(1, 6): self.heading_selector.addItem(text("wiki.heading_level", level=level), level)
        shortcut_labels = {"bold": "Ctrl+B", "italic": "Ctrl+I", "tag_link": "Ctrl+K", "search_link": "Ctrl+Shift+K"}
        for key, button in self.tool_buttons.items():
            label = text(f"wiki.tool_{key}")
            if key in shortcut_labels: label += f" ({shortcut_labels[key]})"
            button.setToolTip(label); button.setAccessibleName(label)
        for key, label in self.ribbon_labels.items(): label.setText(text(f"wiki.group_{key}"))
