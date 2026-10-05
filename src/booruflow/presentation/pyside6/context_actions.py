"""Small composable menus for tags, posts and links; workflows stay in pages."""
from pathlib import Path
from weakref import ref

from PySide6.QtCore import QItemSelectionModel, QObject, QPoint, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu

from booruflow.domain.booru_sites import category_name, site_definition
from booruflow.infrastructure.browser_launcher import BrowserLauncher


def copy_text(value: str) -> None:
    QApplication.clipboard().setText(str(value))


def open_url(url: str, launcher=None) -> None:
    destination = QUrl(url)
    if destination.host().casefold() in {"gelbooru.com", "www.gelbooru.com"}:
        (launcher if launcher is not None else BrowserLauncher(Path.cwd() / "var")).open(url)
    else:
        QDesktopServices.openUrl(destination)


def add_action(menu, label, callback, handlers):
    action = menu.addAction(label)
    handlers[action] = callback
    return action


def exec_menu(menu, position, handlers) -> None:
    chosen = menu.exec(position)
    callback = handlers.get(chosen)
    if callback is not None:
        callback()


def select_context_row(table, row):
    if not table.selectionModel().isRowSelected(row):
        table.selectRow(row)
    table.selectionModel().setCurrentIndex(table.model().index(row, 0),
                                         QItemSelectionModel.SelectionFlag.NoUpdate)


def tag_actions(menu, catalog, tag, *, site="gelbooru", canonical=None, category=None,
                launcher=None, entity=None, copy=None):
    handlers = {}
    definition = site_definition(site)
    add_action(menu, catalog.text("tag_browser.open_site", site=definition.display_name),
               lambda: open_url(definition.search_url(tag), launcher), handlers)
    if canonical and canonical != tag:
        add_action(menu, catalog.text("tag_browser.open_canonical_site", site=definition.display_name),
                   lambda: open_url(definition.search_url(canonical), launcher), handlers)
    add_action(menu, catalog.text("tag_browser.copy_tag"),
               lambda: (copy or copy_text)(tag), handlers)
    if site == "gelbooru" and entity is not None:
        add_action(menu, catalog.text("entity.analyze_menu"),
                   lambda: entity(canonical or tag, category_name(site, category)), handlers)
    return handlers


def link_actions(menu, catalog, url, *, launcher=None, opener=None):
    handlers = {}
    if url:
        add_action(menu, catalog.text("context.open"),
                   lambda: (opener or (lambda value: open_url(value, launcher)))(url), handlers)
        add_action(menu, catalog.text("context.copy_url"), lambda: copy_text(url), handlers)
    return handlers


def post_actions(menu, catalog, post_id, *, site="gelbooru", source=None,
                 launcher=None, opener=None):
    handlers = {}
    url = site_definition(site).post_url(post_id)
    add_action(menu, catalog.text("context.open_post"),
               lambda: (opener or (lambda: open_url(url, launcher)))(), handlers)
    add_action(menu, catalog.text("context.copy_post_id"), lambda: copy_text(str(post_id)), handlers)
    add_action(menu, catalog.text("context.copy_post_url"), lambda: copy_text(url), handlers)
    if source:
        menu.addSeparator()
        add_action(menu, catalog.text("context.open_source"),
                   lambda: open_url(source, launcher), handlers)
        add_action(menu, catalog.text("context.copy_source_url"), lambda: copy_text(source), handlers)
    return handlers


def bind_post_menu(card, catalog, *, launcher=None, site="gelbooru", opener=None,
                   extra=None):
    """Read the card's current post at invocation, including after grid reuse."""
    def show(position):
        post = card.post
        menu = QMenu(card)
        handlers = post_actions(menu, catalog, post["id"], site=site,
                                source=post.get("source"), launcher=launcher,
                                opener=(lambda: opener(post)) if opener else None)
        if extra:
            menu.addSeparator()
            extra(menu, handlers, post)
        exec_menu(menu, card.mapToGlobal(position), handlers)
    card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    card.customContextMenuRequested.connect(show)


class _TagPopupMenu(QObject):
    """A Qt receiver owned by the completer, disconnected with its owner."""

    def __init__(self, completer, catalog, launcher, entity):
        super().__init__(completer)
        self.popup_ref = lambda: None
        self.catalog = catalog
        self.launcher = launcher
        self.entity = entity

    def bind_popup(self, popup):
        if self.popup_ref() is not popup:
            self.popup_ref = ref(popup)
            popup.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            popup.customContextMenuRequested.connect(self.show)

    @Slot(QPoint)
    def show(self, position):
        from booruflow.presentation.pyside6.tag_presentation import (
            CANONICAL_TAG_ROLE,
            TAG_CATEGORY_ROLE,
            TAG_NAME_ROLE,
            TAG_SITE_ROLE,
        )
        popup = self.popup_ref()
        if popup is None:
            return
        index = popup.indexAt(position)
        tag = index.data(TAG_NAME_ROLE)
        if not tag:
            return
        menu = QMenu(popup)
        handlers = tag_actions(menu, self.catalog, tag, site=index.data(TAG_SITE_ROLE) or "gelbooru",
                               canonical=index.data(CANONICAL_TAG_ROLE),
                               category=index.data(TAG_CATEGORY_ROLE), launcher=self.launcher,
                               entity=self.entity)
        exec_menu(menu, popup.viewport().mapToGlobal(position), handlers)


def bind_tag_popup(completer, catalog, *, launcher=None, entity=None):
    # Completion proxies preserve structured roles rather than decorated labels.
    # Leave QCompleter's popup lazy. Eager creation during page construction
    # retains obsolete inherited styles across hot theme changes on Windows.
    completer._context_menu_receiver = _TagPopupMenu(completer, catalog, launcher, entity)


def bind_browser_links(browser, catalog, *, launcher=None, opener=None):
    def show(position):
        url = browser.anchorAt(position)
        if not url:
            menu = browser.createStandardContextMenu(position)
            menu.exec(browser.viewport().mapToGlobal(position))
            return
        menu = QMenu(browser)
        handlers = link_actions(menu, catalog, url, launcher=launcher, opener=opener)
        exec_menu(menu, browser.viewport().mapToGlobal(position), handlers)
    browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    browser.customContextMenuRequested.connect(show)
