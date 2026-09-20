"""Background orchestration for Image Finder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QFileDialog

from booruflow.application.image_finder import ImageFinderService
from booruflow.domain.image_finder import SearchQuery
from booruflow.infrastructure.pixiv import PixivSource


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Job(QRunnable):
    def __init__(self, operation) -> None:
        super().__init__(); self.operation = operation; self.signals = _Signals()

    def run(self) -> None:
        try:
            self.signals.done.emit(self.operation())
        except Exception as exc:  # noqa: BLE001 - background boundary
            self.signals.failed.emit(str(exc))


class ImageFinderController(QObject):
    def __init__(self, page, credentials_repository, log, parent=None) -> None:
        super().__init__(parent)
        self.page = page; self.credentials_repository = credentials_repository; self.log = log
        credentials = credentials_repository.load() if credentials_repository else {}
        self.source = PixivSource(str(credentials.get("pixiv_refresh_token", "")))
        self.service = ImageFinderService((self.source,))
        self.cursor: str | None = None
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(2)
        page.search_requested.connect(self.search)
        page.next_requested.connect(self.next_page)
        page.connect_requested.connect(self.connect)
        page.disconnect_requested.connect(self.disconnect)
        page.download_requested.connect(self.download)

    def _start(self, operation, done) -> None:
        self.page.set_busy(True)
        self.page.page_status.set_state("working")
        job = _Job(operation)
        job.signals.done.connect(
            lambda value: (
                self.page.set_busy(False),
                self.page.page_status.set_state("ready"),
                done(value),
            )
        )
        job.signals.failed.connect(self._failed)
        self.pool.start(job)

    def _failed(self, message: str) -> None:
        self.page.set_busy(False)
        self.page.page_status.set_state("ready")
        self.page.page_status.show_message(
            self.page.catalog.text("image_finder.failed"), timeout_ms=6_000
        )
        self.log(f"[WARNING] [Image Finder] {message}")

    def search(self, text, mode) -> None:
        query = SearchQuery(text, mode)
        self._start(lambda: self.service.search("pixiv", query), lambda result: self._results(result, False))

    def next_page(self) -> None:
        if self.cursor:
            query = SearchQuery("", cursor=self.cursor)
            self._start(lambda: self.service.search("pixiv", query), lambda result: self._results(result, True))

    def _results(self, result, append: bool) -> None:
        self.cursor = result.next_cursor
        self.page.set_results(result.artworks, append, bool(self.cursor))
        count = len(result.artworks)
        key = "image_finder.results.one" if count == 1 else "image_finder.results.many"
        self.page.page_status.show_message(
            self.page.catalog.text(key, count=count), timeout_ms=0
        )

    def connect(self, token: str) -> None:
        if not token:
            self._failed("A Pixiv refresh token is required"); return
        candidate = PixivSource(token)
        def connected(_value) -> None:
            self.source = candidate; self.service = ImageFinderService((candidate,))
            if self.credentials_repository:
                credentials = self.credentials_repository.load()
                credentials["pixiv_refresh_token"] = candidate.refresh_token
                self.credentials_repository.save(credentials)
            self.page.refresh_token.clear(); self.page.set_connected(True)
            self.page.page_status.show_message(
                self.page.catalog.text("image_finder.connected_feedback"), log=True
            )
        self._start(lambda: candidate.refresh_access_token(), connected)

    def disconnect(self) -> None:
        if self.credentials_repository:
            credentials = self.credentials_repository.load()
            credentials.pop("pixiv_refresh_token", None)
            self.credentials_repository.save(credentials)
        self.source = PixivSource(""); self.service = ImageFinderService((self.source,))
        self.page.set_connected(False); self.page.set_results((), False, False)
        self.page.page_status.show_message(
            self.page.catalog.text("image_finder.disconnected_feedback"), log=True
        )

    def download(self, image) -> None:
        suffix = Path(image.original_url or ".jpg").suffix or ".jpg"
        suggested = f"pixiv_{image.artwork_id}_p{image.page_index}{suffix}"
        destination, _ = QFileDialog.getSaveFileName(self.page, "Download Pixiv original", suggested)
        if destination:
            def downloaded(path: Path) -> None:
                self.log(f"[Image Finder] Saved: {path}")
                self.page.page_status.show_message(
                    self.page.catalog.text("image_finder.saved")
                )

            self._start(
                lambda: self.source.download_original(image, Path(destination)), downloaded
            )
