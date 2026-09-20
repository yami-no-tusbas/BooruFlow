from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@pytest.fixture(scope="module")
def app():
    if not PYSIDE6_AVAILABLE:
        pytest.skip("PySide6 is not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _catalog():
    from booruflow.infrastructure.localization import LanguageCatalog

    return LanguageCatalog(LANGUAGES, "fr")


def _scan(tmp_path: Path):
    from booruflow.infrastructure.folder_artists import ArtistCount, FolderArtistScan

    return FolderArtistScan(
        tmp_path,
        tuple(
            ArtistCount(name, count)
            for name, count in (("A", 800), ("B", 450), ("C", 100), ("D", 75), ("E", 20))
        ),
        1445,
        1445,
        0,
        0,
    )


def test_page_displays_numeric_counts_and_opens_selection(app, tmp_path: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy

    from booruflow.infrastructure.folder_artists import ArtistCount, FolderArtistScan
    from booruflow.presentation.pyside6.folder_artists_page import FolderArtistsPage

    page = FolderArtistsPage(_catalog())
    page.show_scan(FolderArtistScan(tmp_path, (ArtistCount("small", 9), ArtistCount("big", 90)), 99, 99, 0, 0))
    assert page.table.item(0, 1).data(Qt.ItemDataRole.DisplayRole) == 90
    page.table.selectRow(0)
    opened = QSignalSpy(page.open_requested)
    page.open_button.click()
    assert opened.at(0) == ["big"]
    page.close()


def test_controller_builds_scoped_command_without_opening_real_everything(app, tmp_path: Path) -> None:
    from booruflow.infrastructure.settings import JsonSettingsRepository
    from booruflow.presentation.pyside6.folder_artists_controller import FolderArtistsController
    from booruflow.presentation.pyside6.folder_artists_page import FolderArtistsPage

    executable = tmp_path / "Everything.exe"
    executable.touch()
    settings = JsonSettingsRepository(tmp_path / "settings.json")
    settings.save({"everything_executable": str(executable)})
    page = FolderArtistsPage(_catalog(), str(tmp_path))
    controller = FolderArtistsController(_catalog(), page, settings, lambda _message: None)
    controller.scanned_root = tmp_path

    with patch(
        "booruflow.presentation.pyside6.folder_artists_controller.launch_everything"
    ) as launch:
        controller.open_artist("foo")

    command = launch.call_args.args[0]
    assert command[1:3] == ["-path", str(tmp_path.resolve())]
    assert command[-1].endswith("no-case:no-path:regex*:^foo\\ \\-\\ ")
    page.close()


def test_filter_hides_rows_without_rescanning_and_empty_restores_all(app, tmp_path: Path) -> None:
    from booruflow.presentation.pyside6.folder_artists_page import FolderArtistsPage

    page = FolderArtistsPage(_catalog())
    page.show_scan(_scan(tmp_path))
    page.count_filter.setText("<500")
    visible = [
        page.table.item(row, 0).text()
        for row in range(page.table.rowCount())
        if not page.table.isRowHidden(row)
    ]
    assert visible == ["B", "C", "D", "E"]

    page.count_filter.setText("50 ~ 100")
    visible = [
        page.table.item(row, 0).text()
        for row in range(page.table.rowCount())
        if not page.table.isRowHidden(row)
    ]
    assert visible == ["C", "D"]

    page.count_filter.clear()
    assert all(not page.table.isRowHidden(row) for row in range(page.table.rowCount()))
    page.close()


def test_invalid_filter_keeps_last_valid_rows_and_marks_field(app, tmp_path: Path) -> None:
    from booruflow.presentation.pyside6.folder_artists_page import FolderArtistsPage

    page = FolderArtistsPage(_catalog())
    page.show_scan(_scan(tmp_path))
    page.count_filter.setText(">=100")
    expected = [page.table.isRowHidden(row) for row in range(page.table.rowCount())]
    page.count_filter.setText("100 ~")
    assert [page.table.isRowHidden(row) for row in range(page.table.rowCount())] == expected
    assert page.count_filter_error.text() == "Expression invalide"
    assert page.count_filter.styleSheet()
    page.close()


def test_filtered_sorted_double_click_opens_visible_artist(app, tmp_path: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy

    from booruflow.presentation.pyside6.folder_artists_page import FolderArtistsPage

    page = FolderArtistsPage(_catalog())
    page.show_scan(_scan(tmp_path))
    page.count_filter.setText("50~100")
    page.table.sortItems(1, Qt.SortOrder.AscendingOrder)
    visible_rows = [
        row for row in range(page.table.rowCount()) if not page.table.isRowHidden(row)
    ]
    assert [page.table.item(row, 0).text() for row in visible_rows] == ["D", "C"]

    target_row = visible_rows[1]
    page.table.selectRow(target_row)
    opened = QSignalSpy(page.open_requested)
    page.table.doubleClicked.emit(page.table.model().index(target_row, 0))
    assert opened.at(0) == ["C"]
    page.close()


def test_manual_everything_path_is_saved_then_reused_without_detection(app, tmp_path: Path) -> None:
    from booruflow.infrastructure.settings import JsonSettingsRepository
    from booruflow.presentation.pyside6.folder_artists_controller import FolderArtistsController
    from booruflow.presentation.pyside6.folder_artists_page import FolderArtistsPage

    beta = tmp_path / "Everything Beta" / "Everything.exe"
    beta.parent.mkdir()
    beta.touch()
    settings = JsonSettingsRepository(tmp_path / "settings.json")
    settings.save({})
    first_page = FolderArtistsPage(_catalog(), str(tmp_path))
    first_page.choose_everything = lambda: str(beta)
    first = FolderArtistsController(_catalog(), first_page, settings, lambda _message: None)
    first.scanned_root = tmp_path

    with (
        patch("booruflow.infrastructure.everything.shutil.which", return_value=None),
        patch("booruflow.infrastructure.everything._registry_app_paths", return_value=()),
        patch(
            "booruflow.presentation.pyside6.folder_artists_controller.launch_everything"
        ),
    ):
        first.open_artist("foo")
    assert Path(settings.load()["everything_executable"]) == beta.resolve()

    second_page = FolderArtistsPage(_catalog(), str(tmp_path))
    second_page.choose_everything = Mock(side_effect=AssertionError("manual prompt must not reopen"))
    second = FolderArtistsController(_catalog(), second_page, settings, lambda _message: None)
    second.scanned_root = tmp_path
    with (
        patch(
            "booruflow.infrastructure.everything.shutil.which",
            side_effect=AssertionError("automatic detection must not run"),
        ),
        patch(
            "booruflow.presentation.pyside6.folder_artists_controller.launch_everything"
        ) as launch,
    ):
        second.open_artist("foo")
    assert Path(launch.call_args.args[0][0]) == beta.resolve()
    first_page.close()
    second_page.close()
