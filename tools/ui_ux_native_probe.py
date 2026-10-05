"""Isolated Windows UI inspection; synthetic data, temporary state, loopback only."""
import argparse
import json
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import sleep
from unittest.mock import Mock

from PySide6.QtCore import QBuffer, QIODevice, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from booruflow.application.tag_autocomplete import TagSuggestion
from booruflow.domain.auto_organize import PostMetadata
from booruflow.domain.image_finder import RemoteArtist, RemoteArtwork, RemoteImage
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagRow
from booruflow.infrastructure.wiki_audit import AuditResult, AuditTag, WikiStatus
from booruflow.presentation.pyside6.entity_profiles import EntityProfileDialog
from booruflow.presentation.pyside6.image_finder_page import ImageFinderPage
from booruflow.presentation.pyside6.status_bar import PageStatus
from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage
from booruflow.presentation.pyside6.tagging_page import TaggingPage
from booruflow.presentation.pyside6.theme import refresh_widget_tree
from booruflow.presentation.pyside6.thumbnail_cache import ThumbnailCacheKey
from booruflow.presentation.pyside6.wiki_audit_page import WikiAuditPage
from booruflow.presentation.pyside6.wiki_page import WikiPage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    assert app.platformName() == "windows"
    catalog = LanguageCatalog(Path(__file__).resolve().parents[1] / "resources/i18n")
    page = TaggingPage(catalog, {}, Mock())
    page.resize(1200, 850)
    page.show()
    QTest.qWait(100)
    initial = (page.analysis_palette.height(), page.bulk_splitter.mapTo(page, QPoint()).y())
    page.grab().save(str(args.output / "tagging-empty.png"))
    for i in range(14):
        page.analysis_palette.add_tag(f"long_filter_{i}", emit=False)
    page.analysis_palette.show()
    QTest.qWait(100)
    filled = (page.analysis_palette.height(), page.bulk_splitter.mapTo(page, QPoint()).y())
    page.grab().save(str(args.output / "tagging-filled.png"))
    page.close()
    with tempfile.TemporaryDirectory(prefix="booruflow-ui-probe-") as tmp:
        dialog = EntityProfileDialog(Path(tmp), catalog, "test_subject", "general", dict,
                                     None, Mock(), PageStatus("tagging"))
        dialog.profile_id = dialog.repository.create("test_subject", "test_subject")
        dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
            "gelbooru", "1", ("test_subject", "hairclip", "jacket", "scythe"))])
        dialog.render()
        dialog.show()
        table = dialog.tables["attribute"]
        table.selectRow(0)
        for scheme in (Qt.ColorScheme.Light, Qt.ColorScheme.Dark):
            app.styleHints().setColorScheme(scheme)
            QTest.qWait(200)
            table.setFocus()
            QTest.qWait(50)
            dialog.grab().save(str(args.output / f"entity-{scheme.name}-focus.png"))
            dialog.filter.setFocus()
            QTest.qWait(50)
            dialog.grab().save(str(args.output / f"entity-{scheme.name}-inactive.png"))
        palette = table.palette()
        roles = {group.name: {role.name: palette.color(group, role).name()
                 for role in (QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText)}
                 for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)}
        dialog.close()
        app.processEvents()
    print(json.dumps({"platform": app.platformName(), "style": app.style().objectName(),
                      "filters_empty": initial, "filters_filled": filled, "selection": roles}))
    inspect_surfaces(app, args.output)
    inspect_loading(app, args.output)


def inspect_loading(app, output):
    image = QImage(100, 90, QImage.Format.Format_RGB32); image.fill(QColor("#62809c"))
    buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG"); png = bytes(buffer.data())
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            sleep(.25)
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.end_headers(); self.wfile.write(png)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        for language in ("en", "fr"):
            catalog = LanguageCatalog(Path(__file__).resolve().parents[1] / "resources/i18n", language)
            with tempfile.TemporaryDirectory(prefix="booruflow-loading-") as tmp:
                entity = EntityProfileDialog(Path(tmp), catalog, "subject", "general", dict,
                                             None, Mock(), PageStatus("tagging"))
                entity.profile_id = entity.repository.create("subject", "subject")
                posts = []
                for i in range(1, 7):
                    url = f"http://127.0.0.1:{server.server_port}/preview-{i}.png"
                    if i == 1:
                        entity.thumbnail_cache.put(ThumbnailCacheKey("gelbooru", i, url), image)
                    posts.append(PostMetadata("gelbooru", str(i), ("subject", "hairclip"),
                                              extra={"preview_url": url}))
                entity.repository.add_posts(entity.profile_id, posts); entity.render()
                def capture(done=False, entity=entity, language=language):
                    inspector = next(dialog for dialog in entity.findChildren(QDialog)
                                     if dialog.findChild(QObject, "entity_matching_grid") is not None)
                    inspector.grab().save(str(output / f"witnesses-{language}-{'loaded' if done else 'loading'}.png"))
                    if done:
                        inspector.accept()
                QTimer.singleShot(120, capture)
                QTimer.singleShot(1100, lambda capture=capture: capture(True))
                entity.inspect_posts("hairclip")
                entity.close(); app.processEvents()
    finally:
        server.shutdown(); server.server_close(); thread.join()


def inspect_surfaces(app, output):
    for language in ("en", "fr"):
        catalog = LanguageCatalog(Path(__file__).resolve().parents[1] / "resources/i18n", language)
        launcher = Mock()
        with tempfile.TemporaryDirectory(prefix="booruflow-surfaces-") as tmp:
            root = Path(tmp)
            browser = TagBrowserPage(catalog, browser_launcher=launcher)
            browser._show_rows([TagRow(2, "hairclip", 400, 0, 0),
                                TagRow(1, "china_dress", 120, 0, 0, canonical_name="qipao")])
            wiki = WikiPage(catalog, root / "drafts", browser_launcher=launcher)
            wiki.tag.setText("hairclip")
            wiki.source.setPlainText("h4. Hairclip\n\nA hair accessory.\n\n[[jacket]]")
            audit = WikiAuditPage(catalog, None, None, root / "wiki.sqlite", launcher)
            audit.ignore_deprecated.setChecked(False)
            audit.result = AuditResult(42, "", (
                AuditTag("hairclip", 0, 400, None, None, WikiStatus("hairclip", True, content="Accessory")),
                AuditTag("china_dress", 0, 120, None, "qipao", WikiStatus("china_dress", False)),
            ))
            audit.apply_filters()
            finder = ImageFinderPage(catalog, browser_launcher=launcher)
            finder.set_results(tuple(RemoteArtwork(
                "pixiv", str(i), f"https://www.pixiv.net/artworks/{i}",
                RemoteArtist("pixiv", "10", "Sample artist"), f"Sample artwork {i}",
                "Synthetic UI inspection data", ("hairclip", "jacket"), "general", None,
                (RemoteImage("pixiv", str(i), 0, f"https://www.pixiv.net/artworks/{i}",
                             None, None, None),)) for i in range(1, 4)), False, False)
            tagging = TaggingPage(catalog, {}, launcher)
            tagging.show_results([{"id": i, "tag_count": i, "tags": "hairclip jacket", "priority": "low"}
                                  for i in range(1, 5)])
            entity = EntityProfileDialog(root, catalog, "subject", "general", dict,
                                         None, launcher, PageStatus("tagging"))
            entity.profile_id = entity.repository.create("subject", "subject")
            image = QImage(100, 90, QImage.Format.Format_RGB32); image.fill(QColor("#62809c"))
            posts = []
            for i in range(1, 5):
                url = f"https://inspection.invalid/preview-{i}.png"
                entity.thumbnail_cache.put(ThumbnailCacheKey("gelbooru", i, url), image)
                posts.append(PostMetadata("gelbooru", str(i), ("subject", "hairclip", "jacket"),
                                          extra={"preview_url": url}))
            entity.repository.add_posts(entity.profile_id, posts); entity.render()
            widgets = {"tagging": tagging, "tag-browser": browser, "entity": entity,
                       "wiki": wiki, "wiki-audit": audit, "image-finder": finder}
            for widget in widgets.values():
                widget.resize(1200, 850)
            for scheme in (Qt.ColorScheme.Light, Qt.ColorScheme.Dark):
                app.styleHints().setColorScheme(scheme); QTest.qWait(100)
                for widget in widgets.values():
                    refresh_widget_tree(widget)
                    refresh = getattr(widget, "refresh_theme", None)
                    if callable(refresh):
                        refresh()
                for name, widget in widgets.items():
                    widget.show(); widget.raise_(); widget.activateWindow(); QTest.qWait(50)
                    view = getattr(widget, "table", None)
                    if name == "entity":
                        view = entity.tables["attribute"]
                    if name == "image-finder":
                        view = finder.results
                    if view is not None:
                        if hasattr(view, "selectRow"):
                            view.selectRow(0)
                        else:
                            view.setCurrentRow(0)
                        view.setFocus(); QTest.qWait(20)
                    widget.grab().save(str(output / f"{name}-{language}-{scheme.name}.png"))
                    if view is not None and name in {"entity", "tag-browser", "wiki-audit"}:
                        editor = {"entity": entity.filter, "tag-browser": browser.query,
                                  "wiki-audit": audit.search}[name]
                        editor.setFocus(); QTest.qWait(20)
                        widget.grab().save(str(output / f"{name}-{language}-{scheme.name}-inactive.png"))
                    widget.hide()
            browser.show()
            for row in range(2):
                capture_menu(output / f"tag-menu-{language}-{row}.png",
                             lambda row=row, browser=browser: browser._show_context_menu(QPoint(5, browser.table.rowViewportPosition(row) + 5)))
            browser.query.setFocus(); QTest.qWait(40)
            browser._autocomplete_ready("tag_browser.query", None, [TagSuggestion(
                "gelbooru", "raw_alias", "canonical_tag", "character", 12, "raw_alias")])
            app.processEvents()
            popup = browser.completer.popup()
            popup.grab().save(str(output / f"autocomplete-{language}.png"))
            capture_menu(output / f"autocomplete-menu-{language}.png", lambda popup=popup: (
                popup.customContextMenuRequested.emit(popup.visualRect(popup.model().index(0, 0)).center())))
            popup.hide(); browser.hide(); tagging.show(); QTest.qWait(30)
            card = tagging.result_buttons[1]
            capture_menu(output / f"post-menu-{language}.png", lambda card=card: (
                card.customContextMenuRequested.emit(QPoint(5, 5))))
            tagging.hide(); entity.show()
            def capture_witnesses(entity=entity, language=language):
                inspector = next((dialog for dialog in entity.findChildren(QDialog)
                                  if dialog.findChild(QObject, "entity_matching_grid") is not None), None)
                if inspector is not None:
                    inspector.grab().save(str(output / f"witnesses-{language}.png"))
                    inspector.accept()
            # Two stages show real loading counts without a network request:
            # the loader consumes synthetic thumbnails already in memory.
            QTimer.singleShot(100, capture_witnesses)
            entity.inspect_posts("hairclip")
            finder.thumbnail_loader.shutdown()
            for widget in widgets.values():
                widget.close()
            app.processEvents()


def capture_menu(path, invoke):
    def capture():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            menu.grab().save(str(path))
            menu.close()
    QTimer.singleShot(40, capture)
    invoke()


if __name__ == "__main__":
    main()
