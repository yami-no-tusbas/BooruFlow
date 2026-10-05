"""Assisted Gelbooru wiki draft editor."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QModelIndex, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.ports import SettingsRepository
from booruflow.application.tag_autocomplete import TagAutocompleteService
from booruflow.application.wiki import (
    TEMPLATES,
    missing_local_tags,
    referenced_tags,
    render_wiki_preview,
    validate_wiki_source,
)
from booruflow.application.wiki_import import (
    DanbooruToGelbooruConverter,
    DanbooruWikiParser,
    GelbooruTagResolver,
    WikiImportResult,
)
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.wiki_clients import (
    DanbooruWikiClient,
    GelbooruWikiClient,
    RemoteWikiPage,
)
from booruflow.infrastructure.wiki_tag_importer import WikiPageNotFoundError
from booruflow.presentation.pyside6.context_actions import (
    bind_browser_links,
    bind_tag_popup,
    exec_menu,
    open_url,
    tag_actions,
)
from booruflow.presentation.pyside6.icons import wiki_tool_icon
from booruflow.presentation.pyside6.status_bar import PageStatus
from booruflow.presentation.pyside6.tag_autocomplete import TagAutocompleteCoordinator
from booruflow.presentation.pyside6.tag_presentation import (
    CANONICAL_TAG_ROLE,
    TagSuggestionListModel,
    configure_tag_completer,
    show_tag_completer,
)


class WikiTagState(str, Enum):
    UNKNOWN = "unknown"
    NEW_GELBOORU_WIKI = "new_gelbooru_wiki"
    EXISTING_GELBOORU_WIKI = "existing_gelbooru_wiki"


class WikiNetworkWorker(QThread):
    completed = Signal(str, int, str, object)
    failed = Signal(str, int, str, object)

    def __init__(self, operation: str, generation: int, tag: str, callback) -> None:
        super().__init__()
        self.operation = operation
        self.generation = generation
        self.tag = tag
        self.callback = callback

    def run(self) -> None:
        try:
            result = self.callback(self.tag)
        except Exception as exc:  # noqa: BLE001 - thread boundary
            self.failed.emit(self.operation, self.generation, self.tag, exc)
        else:
            self.completed.emit(self.operation, self.generation, self.tag, result)


class WikiPage(QWidget):
    entity_profile_requested = Signal(str, str)
    organization_tag_requested = Signal(str)
    LAST_LOAD_DIRECTORY_KEY = "wiki_last_load_directory"

    def __init__(
        self,
        catalog: LanguageCatalog,
        drafts_directory: Path,
        tag_database_path: Path | None = None,
        settings_repository: SettingsRepository | None = None,
        browser_launcher=None,
        alias_database_path: Path | None = None,
        gelbooru_wiki_client: GelbooruWikiClient | None = None,
        danbooru_wiki_client: DanbooruWikiClient | None = None,
        context_launcher=None,
    ) -> None:
        super().__init__(); self.catalog = catalog; self.drafts_directory = drafts_directory; self.tag_database_path = tag_database_path
        self.page_status = PageStatus("wiki", self)
        self.settings_repository = settings_repository
        self.browser_launcher = browser_launcher
        self.context_launcher = context_launcher if context_launcher is not None else browser_launcher
        self._active_draft_path: Path | None = None
        self._active_draft_tag = ""
        self.wiki_state = WikiTagState.UNKNOWN
        self._checked_wiki: RemoteWikiPage | None = None
        self._tag_generation = 0
        self.worker: WikiNetworkWorker | None = None
        self.gelbooru_wiki_client = gelbooru_wiki_client or GelbooruWikiClient()
        self.danbooru_wiki_client = danbooru_wiki_client or DanbooruWikiClient()
        self.danbooru_parser = DanbooruWikiParser()
        self.danbooru_converter = DanbooruToGelbooruConverter(
            GelbooruTagResolver(tag_database_path, alias_database_path)
        )
        self.autocomplete_service = TagAutocompleteService(
            {"gelbooru": tag_database_path}, {"gelbooru": alias_database_path}
        )
        self.autocomplete = TagAutocompleteCoordinator(self.autocomplete_service, self)
        self.autocomplete.suggestions_ready.connect(self._autocomplete_ready)
        layout = QVBoxLayout(self); layout.setContentsMargins(28, 20, 28, 24); layout.setSpacing(10)
        self.title = QLabel(); self.title.setStyleSheet("font-size: 22px; font-weight: 600;"); layout.addWidget(self.title)
        setup = QHBoxLayout(); self.tag_label = QLabel(); self.tag = QLineEdit(); self.check_gelbooru = QPushButton(); self.import_danbooru = QPushButton(); self.template_label = QLabel(); self.template = QComboBox()
        self.suggestion_model = TagSuggestionListModel(self, detailed=True)
        self.completer = QCompleter(self.suggestion_model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        configure_tag_completer(self.completer)
        self.tag.setCompleter(self.completer)
        bind_tag_popup(self.completer, catalog, launcher=self.context_launcher,
                       entity=self.entity_profile_requested.emit)
        self.tag.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tag.customContextMenuRequested.connect(self._tag_menu)
        self.autocomplete_timer = QTimer(self)
        self.autocomplete_timer.setSingleShot(True)
        self.autocomplete_timer.setInterval(200)
        self.tag.textEdited.connect(lambda _value: self.autocomplete_timer.start())
        self.autocomplete_timer.timeout.connect(
            lambda: self.autocomplete.request(
                "wiki.tag", "gelbooru", self.tag.text().strip()
            )
        )
        self.completer.activated[QModelIndex].connect(self._autocomplete_activated)
        for key in TEMPLATES: self.template.addItem("", key)
        self.apply_template = QPushButton(); setup.addWidget(self.tag_label); setup.addWidget(self.tag, 1); setup.addWidget(self.check_gelbooru); setup.addWidget(self.import_danbooru); layout.addLayout(setup)
        template_setup = QHBoxLayout(); template_setup.addWidget(self.template_label); template_setup.addWidget(self.template); template_setup.addWidget(self.apply_template); template_setup.addStretch(1); layout.addLayout(template_setup)
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
        bind_browser_links(self.preview, catalog,
                           opener=lambda url: self._open_preview_link(QUrl(url)))
        source_box.setMinimumWidth(300); preview_box.setMinimumWidth(300)
        splitter.addWidget(source_box); splitter.addWidget(preview_box); splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 1); splitter.setSizes([500, 500]); layout.addWidget(splitter, 1)
        self.operation_status = QLabel(); self.operation_status.setWordWrap(True); self.operation_status.setContentsMargins(8, 6, 8, 6); layout.addWidget(self.operation_status)
        self.import_report = QTextBrowser(); self.import_report.setMaximumHeight(150); self.import_report.setVisible(False); layout.addWidget(self.import_report)
        self.validation = QLabel(); self.validation.setWordWrap(True); self.validation.setContentsMargins(8, 6, 8, 6); layout.addWidget(self.validation)
        actions = QHBoxLayout(); self.load = QPushButton(); self.save = QPushButton(); self.copy = QPushButton(); self.open_create = QPushButton()
        actions.addWidget(self.load); actions.addWidget(self.save); actions.addStretch(1); actions.addWidget(self.copy); actions.addWidget(self.open_create); layout.addLayout(actions)
        self.preview_timer = QTimer(self); self.preview_timer.setSingleShot(True); self.preview_timer.setInterval(180); self.preview_timer.timeout.connect(self._refresh)
        self.autosave_timer = QTimer(self); self.autosave_timer.setSingleShot(True); self.autosave_timer.setInterval(900); self.autosave_timer.timeout.connect(lambda: self._save_draft(silent=True))
        self.source.textChanged.connect(self._changed); self.tag.textChanged.connect(self._tag_changed)
        self.check_gelbooru.clicked.connect(self._check_on_gelbooru); self.import_danbooru.clicked.connect(self._import_from_danbooru)
        self.apply_template.clicked.connect(self._apply_template); self.load.clicked.connect(self._load_draft); self.save.clicked.connect(self._save_draft); self.copy.clicked.connect(self._copy_source); self.open_create.clicked.connect(self._publish)
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

    def closeEvent(self, event) -> None:
        self.autocomplete.shutdown()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(2_000)
        super().closeEvent(event)

    def _autocomplete_ready(self, owner: str, _request: object, suggestions: object) -> None:
        if owner != "wiki.tag" or not isinstance(suggestions, list):
            return
        self.suggestion_model.set_suggestions(suggestions, self.palette())
        if suggestions and self.tag.hasFocus():
            show_tag_completer(self.completer)

    def _autocomplete_activated(self, index: QModelIndex) -> None:
        value = str(index.data(CANONICAL_TAG_ROLE) or "")
        if value:
            self.tag.setText(value)
            QTimer.singleShot(0, lambda value=value: self.tag.setText(value))

    def _tag_changed(self, _value: str = "") -> None:
        self._tag_generation += 1
        self.wiki_state = WikiTagState.UNKNOWN
        self._checked_wiki = None
        self.operation_status.clear()
        self.import_report.setVisible(False)
        self._changed()

    def _current_tag(self) -> str:
        return self.tag.text().strip()

    def _check_on_gelbooru(self) -> None:
        self._start_network_operation("check", self.gelbooru_wiki_client.fetch)

    def _import_from_danbooru(self) -> None:
        self._start_network_operation("import", self._fetch_and_convert_danbooru)

    def _publish(self) -> None:
        # Deliberately recheck every time: the remote wiki may have changed.
        self._start_network_operation("publish", self.gelbooru_wiki_client.fetch)

    def _fetch_and_convert_danbooru(self, tag: str) -> WikiImportResult:
        remote = self.danbooru_wiki_client.fetch(tag)
        document = self.danbooru_parser.parse(remote.text)
        return self.danbooru_converter.convert(document, tag, source_url=remote.source_url)

    def _start_network_operation(self, operation: str, callback) -> None:
        tag = self._current_tag()
        if not tag:
            self._set_operation_status(self.catalog.text("wiki.tag_required"), error=True)
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self._set_busy(True)
        self._set_operation_status(self.catalog.text(f"wiki.{operation}_started", tag=tag))
        worker = WikiNetworkWorker(operation, self._tag_generation, tag, callback)
        worker.completed.connect(self._network_completed)
        worker.failed.connect(self._network_failed)
        self.worker = worker
        worker.start()

    def _network_completed(
        self, operation: str, generation: int, tag: str, result: object
    ) -> None:
        self._set_busy(False)
        if generation != self._tag_generation or tag != self._current_tag():
            self._set_operation_status(self.catalog.text("wiki.stale_result"))
            self._release_worker()
            return
        if operation == "check" and isinstance(result, RemoteWikiPage):
            self.wiki_state = WikiTagState.EXISTING_GELBOORU_WIKI
            self._checked_wiki = result
            self.source.setPlainText(result.text)
            self._refresh()
            message = self.catalog.text("wiki.gelbooru_found")
            if result.warnings:
                message += "\n" + "\n".join(f"• {warning}" for warning in result.warnings)
            self._set_operation_status(message)
        elif operation == "import" and isinstance(result, WikiImportResult):
            if not self._confirm_draft_replacement():
                self._set_operation_status(self.catalog.text("wiki.import_cancelled"))
                self._release_worker()
                return
            self.source.setPlainText(result.text)
            self._refresh()
            self._show_import_report(result)
            self._set_operation_status(self.catalog.text("wiki.danbooru_imported"))
        elif operation == "publish" and isinstance(result, RemoteWikiPage):
            self.wiki_state = WikiTagState.EXISTING_GELBOORU_WIKI
            self._checked_wiki = result
            self._open_existing_wiki(result)
        self._release_worker()

    def _network_failed(
        self, operation: str, generation: int, tag: str, error: object
    ) -> None:
        self._set_busy(False)
        if generation != self._tag_generation or tag != self._current_tag():
            self._set_operation_status(self.catalog.text("wiki.stale_result"))
            self._release_worker()
            return
        if isinstance(error, WikiPageNotFoundError):
            if operation == "check":
                self.wiki_state = WikiTagState.NEW_GELBOORU_WIKI
                self._checked_wiki = None
                self._set_operation_status(self.catalog.text("wiki.gelbooru_missing"))
            elif operation == "import":
                self._set_operation_status(self.catalog.text("wiki.danbooru_missing"))
            elif operation == "publish":
                self.wiki_state = WikiTagState.NEW_GELBOORU_WIKI
                self._checked_wiki = None
                self._open_new_wiki()
        else:
            self._set_operation_status(
                self.catalog.text("wiki.network_error", error=error), error=True
            )
        self._release_worker()

    def _set_busy(self, busy: bool) -> None:
        for button in (self.check_gelbooru, self.import_danbooru, self.open_create):
            button.setEnabled(not busy)

    def _release_worker(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def _set_operation_status(self, message: str, *, error: bool = False) -> None:
        self.operation_status.setText(message)
        self.operation_status.setStyleSheet(
            "color:palette(bright-text);" if error else "color:palette(text);"
        )
        self.page_status.show_message(
            message,
            timeout_ms=0 if error else 8_000,
            log=True,
            global_message=error,
            level="ERROR" if error else "NORMAL",
        )

    def _confirm_draft_replacement(self) -> bool:
        if not self.source.toPlainText().strip():
            return True
        answer = QMessageBox.question(
            self,
            self.catalog.text("wiki.replace_draft_title"),
            self.catalog.text("wiki.replace_draft_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _show_import_report(self, result: WikiImportResult) -> None:
        visible = [item for item in result.tag_conversions if item.reason != "same_name"]
        modified = [item for item in visible if item.reason != "unresolved"]
        unresolved = [item for item in visible if item.reason == "unresolved"]
        lines = [self.catalog.text("wiki.report_imported")]
        if result.source_url:
            lines.append(result.source_url)
        if modified:
            lines.append(self.catalog.text("wiki.report_modified", count=len(modified)))
        if unresolved:
            lines.append(self.catalog.text("wiki.report_unresolved", count=len(unresolved)))
        reason_labels = {
            "gelbooru_alias": self.catalog.text("wiki.reason_gelbooru_alias"),
            "known_crossboard_mapping": self.catalog.text("wiki.reason_crossboard"),
            "normalized_syntax": self.catalog.text("wiki.reason_normalized"),
        }
        for item in visible:
            if item.reason == "unresolved":
                lines.extend(("", item.source, self.catalog.text("wiki.unresolved_preserved")))
            else:
                lines.extend((
                    "", f"{item.source} → {item.target}",
                    self.catalog.text("wiki.report_reason", reason=reason_labels[item.reason]),
                ))
        if result.warnings:
            lines.extend(("", self.catalog.text("wiki.report_warnings")))
            lines.extend(f"• {warning}" for warning in result.warnings)
        self.import_report.setPlainText("\n".join(lines))
        self.import_report.setVisible(True)

    def _open_existing_wiki(self, page: RemoteWikiPage) -> None:
        url = page.source_url
        if not url and page.wiki_id is not None:
            url = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(
                {"page": "wiki", "s": "view", "id": page.wiki_id}
            )
        self._open_browser(url)
        self._set_operation_status(self.catalog.text("wiki.opened_existing"))

    def _open_new_wiki(self) -> None:
        QApplication.clipboard().setText(self.source.toPlainText())
        url = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(
            {"page": "wiki", "s": "create", "title": self._current_tag()}
        )
        self._open_browser(url)
        self._set_operation_status(self.catalog.text("wiki.opened_create"))

    def _open_browser(self, url: str) -> None:
        if self.browser_launcher:
            self.browser_launcher.open(url)
        else:
            open_url(url)

    def _tag_menu(self, position):
        menu = self.tag.createStandardContextMenu()
        handlers = {}
        if self._current_tag():
            menu.addSeparator()
            handlers = tag_actions(menu, self.catalog, self._current_tag(),
                                   launcher=self.context_launcher, entity=self.entity_profile_requested.emit)
        exec_menu(menu, self.tag.mapToGlobal(position), handlers)

    def _ribbon_group(self, key: str) -> tuple[QFrame, QHBoxLayout]:
        frame = QFrame(); frame.setFrameShape(QFrame.Shape.StyledPanel); outer = QVBoxLayout(frame); outer.setContentsMargins(6, 4, 6, 3); outer.setSpacing(2)
        row = QHBoxLayout(); row.setSpacing(3); label = QLabel(); label.setAlignment(Qt.AlignmentFlag.AlignCenter); label.setStyleSheet("color:palette(placeholder-text);font-size:10px;")
        self.ribbon_labels[key] = label; outer.addLayout(row); outer.addWidget(label); return frame, row

    def _tool_button(self, key: str) -> QToolButton:
        button = QToolButton(); button.setIcon(wiki_tool_icon(key)); button.setIconSize(QSize(24, 24)); button.setFixedSize(34, 34)
        self.tool_buttons[key] = button; return button

    def refresh_theme(self) -> None:
        """Regenerate palette-colored toolbar icons for the current theme."""
        for key, button in self.tool_buttons.items():
            button.setIcon(wiki_tool_icon(key))
        self.suggestion_model.refresh_palette(self.palette())

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
            self.validation.setText(self.catalog.text("wiki.validation_ok")); self.validation.setStyleSheet("color:palette(text);")
            return
        messages = [self.catalog.text(f"wiki.issue_{code}", value=value) for code, value in issues]
        self.validation.setText("\n".join(f"• {message}" for message in messages)); self.validation.setStyleSheet("color:palette(text);")

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
        data = {"tag": self.tag.text().strip(), "template": str(self.template.currentData()), "source": self.source.toPlainText(), "updated_at": datetime.now(UTC).isoformat(timespec="seconds")}
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
        QApplication.clipboard().setText(self.source.toPlainText())
        self.page_status.show_message(
            self.catalog.text("wiki.copied"), timeout_ms=5_000, log=True
        )

    def _open_create(self) -> None:
        """Compatibility entry point: publication now always performs a fresh check."""
        self._publish()

    def _open_preview_link(self, url: QUrl) -> None:
        if url.scheme() == "booruflow-tag": self.organization_tag_requested.emit(urllib.parse.unquote(url.path()))
        else: open_url(url.toString(), self.context_launcher)

    def retranslate(self) -> None:
        text = self.catalog.text; self.title.setText(text("nav.wiki")); self.tag_label.setText(text("wiki.tag")); self.tag.setPlaceholderText(text("wiki.tag_placeholder")); self.check_gelbooru.setText(text("wiki.check_gelbooru")); self.import_danbooru.setText(text("wiki.import_danbooru")); self.template_label.setText(text("wiki.template")); self.apply_template.setText(text("wiki.apply_template")); self.source_label.setText(text("wiki.source")); self.preview_label.setText(text("wiki.preview")); self.load.setText(text("wiki.load")); self.save.setText(text("wiki.save")); self.copy.setText(text("wiki.copy")); self.open_create.setText(text("wiki.publish"))
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
