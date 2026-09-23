import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class PySide6WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def catalog():
        from booruflow.infrastructure.localization import LanguageCatalog

        return LanguageCatalog(LANGUAGES, "en")

    def test_cleanup_owns_the_existing_blacklist_setting(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.cleanup_page import CleanupPage

        page = CleanupPage(self.catalog(), {"blacklist_file": "D:/lists/blacklist.txt"})
        changed = QSignalSpy(page.blacklist_changed)
        self.assertEqual(page.blacklist_file.text(), "D:/lists/blacklist.txt")
        page.blacklist_file.setText("D:/lists/new.txt")
        page.blacklist_file.editingFinished.emit()
        self.assertEqual(changed.at(0), ["D:/lists/new.txt"])
        page.close()

    def test_storage_actions_live_in_options_and_cleanup_stays_blacklist_only(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.cleanup_page import CleanupPage
        from booruflow.presentation.pyside6.options_page import OptionsPage

        with tempfile.TemporaryDirectory() as directory:
            cleanup = CleanupPage(self.catalog(), {}, Path(directory))
            self.assertFalse(hasattr(cleanup, "hydra_status_label"))
            self.assertFalse(hasattr(cleanup, "refresh_disk_usage"))

            page = OptionsPage(self.catalog(), {}, project_root=Path(directory))
            install = QSignalSpy(page.hydra_install_requested)
            page.hydra_install.click()
            self.assertEqual(install.count(), 1)
            page.show_storage(
                {"total": 0, "wd14": 0, "e621": 0, "other": 0},
                {"state": "absent", "size": 0, "legacy": False},
            )
            self.assertIn("absent", page.hydra_status_label.text())
            self.assertFalse(page.hydra_remove.isEnabled())
            cleanup.close()
            page.close()

    def test_options_storage_refresh_runs_worker_on_temporary_models(self) -> None:
        from booruflow.presentation.pyside6.options_maintenance_controller import (
            OptionsMaintenanceController,
        )
        from booruflow.presentation.pyside6.options_page import OptionsPage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "var/models/image_analysis/wd-vit-tagger-v3/model.onnx"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"x" * 2048)
            page = OptionsPage(self.catalog(), {}, project_root=root)
            logs = []
            controller = OptionsMaintenanceController(
                root, page.catalog, page, logs.append
            )

            controller.refresh_storage()
            self.assertIsNotNone(controller.worker)
            controller.worker.wait(5_000)
            self.app.processEvents()

            self.assertIn("2.0 Kio", page.model_storage.text())
            self.assertIn("absent", page.hydra_status_label.text())
            self.assertEqual(logs, [])
            self.assertTrue(controller.shutdown())
            page.close()

    def test_tagging_site_selector_switches_context_without_stale_results(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {"tagging_query": "wolf"})
        changed = QSignalSpy(page.site_changed)
        page.show_results([{"id": 42, "tags": "solo", "priority": "low", "tag_count": 1}])
        self.assertEqual(page.active_site, "gelbooru")
        page.site_selector.setCurrentIndex(page.site_selector.findData("e621"))
        self.assertEqual(changed.at(0), ["e621"])
        self.assertEqual(page.active_site, "e621")
        self.assertEqual(page.result_posts, [])
        self.assertEqual(page.query.text(), "wolf")
        requested = QSignalSpy(page.start_requested)
        page.start_button.click()
        self.assertEqual(requested.at(0)[0].site, "e621")
        self.assertFalse(hasattr(page, "alias_group"))
        self.assertIn("stored locally", page.batch_status.text())
        catalog = page.catalog
        catalog.set_language("fr")
        page.retranslate()
        self.assertEqual(page.active_site, "e621")
        self.assertEqual(page.query.text(), "wolf")
        self.assertEqual(page.site_label.text(), "Site :")
        page.site_selector.setCurrentIndex(page.site_selector.findData("gelbooru"))
        self.assertEqual(page.active_site, "gelbooru")
        page.close()

    def test_tagging_batch_button_is_a_reusable_two_way_expander(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        batch_view = page.batch_view
        refreshes = QSignalSpy(page.batch_refresh_requested)
        self.assertFalse(page.batch_button.isChecked())
        self.assertEqual(page.batch_button.arrowType(), Qt.ArrowType.RightArrow)
        self.assertTrue(page.page_header.isAncestorOf(page.batch_button))
        self.assertEqual(page.page_header_layout.indexOf(page.title), 0)
        self.assertEqual(
            page.page_header_layout.indexOf(page.batch_button),
            page.page_header_layout.count() - 1,
        )
        self.assertEqual(page.layout().indexOf(page.batch_button), -1)

        page.batch_button.click()
        self.assertTrue(page.batch_button.isChecked())
        self.assertEqual(page.batch_button.arrowType(), Qt.ArrowType.DownArrow)
        self.assertIs(page.mode_stack.currentWidget(), batch_view)
        self.assertEqual(refreshes.count(), 1)

        page.batch_button.click()
        self.assertFalse(page.batch_button.isChecked())
        self.assertEqual(page.batch_button.arrowType(), Qt.ArrowType.RightArrow)
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        self.assertIs(page.batch_view, batch_view)
        page.close()

    def test_tagging_bulk_filter_and_derived_threshold_layout(self) -> None:
        from booruflow.presentation.pyside6.pages import ScrollablePageHost
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(
            self.catalog(),
            {
                "tagging_pages": 10,
                "tagging_start": 3,
                "tagging_minimum": 0,
                "tagging_maximum": 12,
            },
        )
        host = ScrollablePageHost(page)
        host.resize(1_100, 700)
        host.show()
        self.app.processEvents()
        self.assertTrue(page.bulk_group.isAncestorOf(page.hide_queued_results))
        self.assertNotIn("critical", page.spins)
        self.assertNotIn("high", page.spins)
        expected = {
            "pages": ("Pages per block:", 10),
            "start": ("First page:", 3),
            "minimum": ("Minimum tags:", 0),
            "maximum": ("Maximum tags:", 12),
        }
        label_positions = []
        for key, (label_text, value) in expected.items():
            label = page.spin_labels[key]
            self.assertEqual(label.text(), label_text)
            self.assertTrue(label.isVisibleTo(page))
            self.assertGreater(label.width(), 0)
            self.assertEqual(page.spins[key].value(), value)
            label_positions.append(label.mapTo(page.parameter_row, label.rect().topLeft()).x())
        self.assertEqual(label_positions, sorted(label_positions))
        self.assertIn("Critical ≤ 5", page.threshold_summary.text())
        self.assertIn("High ≤ 8", page.threshold_summary.text())
        self.assertGreater(
            page.threshold_summary.mapTo(page.group, page.threshold_summary.rect().topLeft()).y(),
            page.parameter_row.mapTo(page.group, page.parameter_row.rect().topLeft()).y(),
        )
        page.spins["maximum"].setValue(1_000)
        self.assertIn("Critical ≤ 10", page.threshold_summary.text())
        self.assertIn("High ≤ 20", page.threshold_summary.text())
        host.close()

    def test_derived_tagging_thresholds_respect_small_ranges(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import derived_tagging_thresholds

        for minimum, maximum, expected in (
            (0, 1_000, (10, 20)),
            (0, 100, (5, 8)),
            (0, 4, (4, 4)),
            (7, 7, (7, 7)),
            (7, 100, (7, 8)),
        ):
            with self.subTest(minimum=minimum, maximum=maximum):
                thresholds = derived_tagging_thresholds(minimum, maximum)
                self.assertEqual(thresholds, expected)
                self.assertLessEqual(minimum, thresholds[0])
                self.assertLessEqual(thresholds[0], thresholds[1])
                self.assertLessEqual(thresholds[1], maximum)

    def test_tagging_result_selection_groups_and_bulk_signal(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QSignalSpy, QTest

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        posts = [
            {"id": 1, "tags": "blue_hair", "priority": "critical", "tag_count": 1},
            {"id": 2, "tags": "red_hair", "priority": "critical", "tag_count": 1},
            {"id": 3, "tags": "solo", "priority": "low", "tag_count": 1},
        ]
        page.show_results(posts)
        page.result_groups[0].select_all.click()
        self.assertEqual([post["id"] for post in page.selected_posts()], [1, 2])
        QTest.mouseClick(
            page.result_buttons[2], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier
        )
        self.assertEqual([post["id"] for post in page.selected_posts()], [1])
        QTest.mouseClick(
            page.result_buttons[3], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier
        )
        self.assertEqual([post["id"] for post in page.selected_posts()], [1, 2, 3])
        page.select_all_shortcut.activated.emit()
        self.assertEqual(len(page.selected_posts()), 3)
        self.assertEqual(page.result_groups[0].select_all.text(), "2 / 2")

        applied = QSignalSpy(page.bulk_apply_requested)
        page.bulk_add.add_tag("1girl")
        page.bulk_add.add_tag("child")
        page.bulk_add.add_tag("1girl")
        page.bulk_remove.add_tag("2girls")
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok):
            page.bulk_apply.click()
        self.assertEqual(applied.count(), 1)
        self.assertEqual(applied.at(0)[1:], [["1girl", "child"], ["2girls"]])
        page.close()

    def test_bulk_tag_chips_autocomplete_remove_backspace_and_opposite_cancel(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QSignalSpy, QTest

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        lookups = QSignalSpy(page.bulk_lookup_requested)
        page.bulk_add.input.setFocus()
        page.bulk_add.input.setText("1g")
        page.bulk_add.lookup_requested.emit("1g")
        self.assertEqual(lookups.at(0), ["add", "1g"])
        page.set_bulk_suggestions("add", ["1girl", "1girls"])
        page.bulk_add._commit_suggestion("1girl")
        self.assertEqual(page.bulk_add.tags(), ["1girl"])
        self.assertFalse(page.bulk_add.add_tag("1girl"))
        page.bulk_add.add_tag("child")
        page.bulk_add.input.clear()
        QTest.keyClick(page.bulk_add.input, Qt.Key.Key_Backspace)
        self.assertEqual(page.bulk_add.tags(), ["1girl", "child"])
        page.bulk_add._chips["1girl"].click()
        self.assertEqual(page.bulk_add.tags(), ["child"])
        page.bulk_add._chips["child"].click()
        self.assertEqual(page.bulk_add.tags(), [])

        page.bulk_remove.add_tag("1girl")
        page.bulk_add.add_tag("1girl")
        self.assertEqual(page.bulk_add.tags(), ["1girl"])
        self.assertEqual(page.bulk_remove.tags(), [])
        page.close()

    def test_bulk_alias_suggestion_mouse_and_enter_commit_canonical_and_clear(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from booruflow.presentation.pyside6.tagging_page import TagTokenEditor

        mouse_editor = TagTokenEditor()
        mouse_editor.set_suggestions([("1girl", "1girls")])
        mouse_editor.completer.activated[str].emit("1girl ← 1girls")
        self.assertEqual(mouse_editor.tags(), ["1girl"])
        self.assertEqual(mouse_editor.input.text(), "")
        self.assertTrue(mouse_editor.add_tag("abigail_williams_(fate) ← abigail_williams_(fate/grand_order)"))
        self.assertNotIn("←", mouse_editor.tags()[-1])
        mouse_editor.close()

        remove_editor = TagTokenEditor()
        remove_editor.set_suggestions([("1girl", "1girls")])
        remove_editor.completer.activated[str].emit("1girl ← 1girls")
        self.assertEqual(remove_editor.tags(), ["1girl"])
        remove_editor.close()

        # Enter path must not commit the decorated display label.
        editor = TagTokenEditor()
        editor.set_suggestions([("abigail_williams_(fate)", "abigail_williams_(fate/grand_order)")])
        editor.completer.setCurrentRow(0)
        editor.input.setFocus()
        editor.completer.popup().show()
        QTest.keyClick(editor.input, Qt.Key.Key_Return)
        self.assertEqual(editor.tags(), ["abigail_williams_(fate)"])
        self.assertEqual(editor.input.text(), "")
        editor.close()

    def test_bulk_click_enter_plain_and_alias_match_for_add_and_remove(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QSignalSpy, QTest

        from booruflow.presentation.pyside6.tagging_page import TagTokenEditor

        for canonical, alias in (("sword", None), ("qipao", "china_dress")):
            suggestions = [(canonical, alias)]
            for target in ("add", "remove"):
                click_editor = TagTokenEditor(confidence_actions=target == "add")
                click_commits = QSignalSpy(click_editor.tag_added)
                click_editor.show()
                click_editor.input.setFocus()
                click_editor.input.setText(alias or canonical)
                click_editor.set_suggestions(suggestions)
                click_editor.completer.popup().show()
                self.app.processEvents()
                popup = click_editor.completer.popup()
                index = popup.model().index(0, 0)
                QTest.mouseClick(
                    popup.viewport(), Qt.MouseButton.LeftButton,
                    pos=popup.visualRect(index).center(),
                )
                self.app.processEvents()

                enter_editor = TagTokenEditor(confidence_actions=target == "add")
                enter_commits = QSignalSpy(enter_editor.tag_added)
                enter_editor.input.setText(alias or canonical)
                enter_editor.set_suggestions(suggestions)
                enter_editor.completer.setCurrentRow(0)
                enter_editor.input.setFocus()
                enter_editor.completer.popup().show()
                QTest.keyClick(enter_editor.input, Qt.Key.Key_Return)
                self.app.processEvents()

                with self.subTest(target=target, canonical=canonical, alias=alias):
                    self.assertEqual(click_editor.tags(), [canonical])
                    self.assertEqual(enter_editor.tags(), [canonical])
                    self.assertEqual(click_editor.input.text(), "")
                    self.assertEqual(enter_editor.input.text(), "")
                    self.assertFalse(click_editor.completer.popup().isVisible())
                    self.assertFalse(enter_editor.completer.popup().isVisible())
                    self.assertEqual(click_commits.count(), 1)
                    self.assertEqual(enter_commits.count(), 1)
                    self.assertEqual(click_commits.at(0), [canonical])
                    self.assertEqual(enter_commits.at(0), [canonical])
                click_editor.close()
                enter_editor.close()

        no_commit = TagTokenEditor()
        no_commit.add_tag("sword")
        no_commit.input.setText("keep_me")
        no_commit._commit_suggestion("sword")
        self.app.processEvents()
        self.assertEqual(no_commit.tags(), ["sword"])
        self.assertEqual(no_commit.input.text(), "keep_me")
        no_commit.close()

    def test_bulk_labels_apply_alignment_and_chip_flow_stay_responsive(self) -> None:
        from PySide6.QtTest import QTest

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.resize(760, 700)
        page.show()
        self.app.processEvents()

        self.assertEqual(page.bulk_add_label.text(), "Add tags:")
        self.assertEqual(page.bulk_remove_label.text(), "Remove tags:")
        self.assertTrue(page.bulk_add_label.property("preserveHorizontalSize"))
        self.assertTrue(page.bulk_remove_label.property("preserveHorizontalSize"))
        self.assertIs(page.bulk_apply.parentWidget(), page.bulk_group)
        self.assertTrue(page.bulk_apply.isEnabled() is False)
        self.assertTrue(page.bulk_add.chips.isHidden())
        self.assertTrue(page.bulk_add.input.isVisibleTo(page.bulk_add))

        long_tag = "the_legend_of_zelda_a_link_to_the_past"
        tags = [long_tag, *[f"very_long_responsive_tag_number_{index}" for index in range(10)]]
        for tag in tags:
            page.bulk_add.add_tag(tag)
        page.bulk_add.resize(360, page.bulk_add.sizeHint().height())
        page.bulk_add.show()
        QTest.qWait(20)
        self.app.processEvents()

        chip = page.bulk_add._chips[long_tag]
        self.assertEqual(page.bulk_add.tags()[0], long_tag)
        self.assertNotEqual(chip.label.text(), long_tag)
        self.assertIn("…", chip.label.text())
        self.assertEqual(chip.label.toolTip(), long_tag)
        self.assertFalse(chip.analysis.isHidden())
        self.assertFalse(chip.remove.isHidden())
        self.assertLessEqual(chip.label.width(), page.bulk_add.CHIP_TEXT_MAX_WIDTH)
        narrow_height = page.bulk_add.chips_layout.heightForWidth(340)
        wide_height = page.bulk_add.chips_layout.heightForWidth(1_200)
        self.assertGreater(narrow_height, wide_height)
        self.assertGreater(len({item.widget().y() for item in page.bulk_add.chips_layout._items}), 1)
        self.assertTrue(
            all(
                item.geometry().right()
                <= page.bulk_add.chips_layout.geometry().right()
                for item in page.bulk_add.chips_layout._items
            )
        )
        self.assertTrue(page.bulk_add.input.isVisibleTo(page.bulk_add))
        self.assertGreaterEqual(page.bulk_add.input.width(), 120)

        page.bulk_add.resize(1_000, page.bulk_add.sizeHint().height())
        self.app.processEvents()
        self.assertEqual(page.bulk_add.tags(), tags)
        page.bulk_add.remove_tag(long_tag)
        self.assertNotIn(long_tag, page.bulk_add.tags())
        page.close()

    def test_bulk_autocomplete_debounces_short_input_and_emits_latest_query(self) -> None:
        from PySide6.QtTest import QSignalSpy, QTest

        from booruflow.presentation.pyside6.tagging_page import TagTokenEditor

        editor = TagTokenEditor()
        editor.show()
        requested = QSignalSpy(editor.lookup_requested)
        editor.input.setFocus()
        QTest.keyClicks(editor.input, "t")
        QTest.qWait(230)
        self.assertEqual(requested.count(), 0)
        QTest.keyClicks(editor.input, "he_leg")
        QTest.qWait(230)
        self.assertEqual(requested.count(), 1)
        self.assertEqual(requested.at(0), ["the_leg"])
        editor.input.clear()
        QTest.qWait(230)
        self.assertEqual(requested.count(), 1)
        editor.close()

    def test_catalog_ready_refreshes_text_without_image_analysis_feature(self) -> None:
        from booruflow.application.tag_lookup import TagLookupSuggestion
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tags.db"
            settings = {"gelbooru_tag_database": str(database)}
            page = TaggingPage(self.catalog(), settings)
            controller = TaggingController(
                self.catalog(), page, dict, lambda *_args: None, settings=settings
            )
            page.manual_tag.setText("armor")
            with patch(
                "booruflow.presentation.pyside6.tagging_controller.lookup_gelbooru_suggestions",
                return_value=[TagLookupSuggestion("armor")],
            ) as lookup:
                controller.notify_tag_database_ready()
            lookup.assert_called_once()
            self.assertEqual(page.manual_suggestion_model.stringList(), ["armor"])
            page.close()

    def test_bulk_autocomplete_runs_off_gui_thread_ignores_stale_and_caches(self) -> None:
        import time
        from time import perf_counter
        from types import SimpleNamespace

        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "tags.sqlite"
            database.touch()
            page = TaggingPage(self.catalog(), {"tagging_site": "e621"})
            controller = TaggingController(self.catalog(), page, dict, lambda *_args: None)
            controller.image_analysis = SimpleNamespace(
                settings={"e621_database": str(database)}
            )
            calls = []

            def slow_lookup(site, path, query, *, limit):
                calls.append((site, path, query, limit))
                time.sleep(0.12)
                return [SimpleNamespace(name=f"{query}_result")]

            with patch(
                "booruflow.presentation.pyside6.tagging_controller.lookup_tags",
                side_effect=slow_lookup,
            ):
                page.bulk_add.input.setText("th")
                started = perf_counter()
                controller.lookup_bulk_tags("add", "th")
                self.assertLess(perf_counter() - started, 0.05)
                first = controller.bulk_lookup_workers["add"]

                page.bulk_add.input.setText("the")
                controller.lookup_bulk_tags("add", "the")
                self.assertEqual(controller._bulk_lookup_pending["add"], "the")
                first.wait(2_000)
                for _ in range(8):
                    self.app.processEvents()
                second = controller.bulk_lookup_workers["add"]
                self.assertEqual(second.query, "the")
                second.wait(2_000)
                for _ in range(8):
                    self.app.processEvents()

                self.assertEqual(page.bulk_add.model.stringList(), ["the_result"])
                self.assertEqual([call[2] for call in calls], ["th", "the"])
                self.assertTrue(all(call[3] == 20 for call in calls))

                page.bulk_add.model.setStringList([])
                controller.lookup_bulk_tags("add", "the")
                self.assertEqual(page.bulk_add.model.stringList(), ["the_result"])
                self.assertEqual(len(calls), 2)
                controller.lookup_bulk_tags("add", "")
                self.assertEqual(len(calls), 2)
            self.assertTrue(controller.shutdown())
            page.close()

    def test_bulk_controller_stages_locally_without_analysis_or_publication(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtCore import Qt

        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "analysis.sqlite"
            repository = ImageAnalysisRepository(database)
            page = TaggingPage(self.catalog(), {})
            published = []
            controller = TaggingController(
                self.catalog(), page, dict, lambda *_args, **_kwargs: None,
                publisher_factory=lambda: published.append(True),
            )
            controller.image_analysis = SimpleNamespace(repository=repository, settings={})
            page.show_results(
                [{"id": 42, "tags": "blue_hair 2girls", "priority": "critical", "tag_count": 2}]
            )
            controller.apply_bulk_manual_tags(page.result_posts, ["1girl", "child"], ["2girls"])

            repository.close()
            reopened = ImageAnalysisRepository(database)
            entry = reopened.list_batch_entries()[0]
            try:
                self.assertEqual(entry["additions"], ["1girl", "child"])
                self.assertEqual(entry["removals"], ["2girls"])
                self.assertEqual(reopened.pending_change_count(), 1)
                self.assertEqual(published, [])
                self.assertNotIn(42, page.result_buttons)
                self.assertEqual(page.selected_posts(), [])
                self.assertIn("0 displayed", page.result_filter_counts.text())
                self.assertIn("0 image", page.bulk_selection_count.text())
                self.assertTrue(
                    all(
                        group.select_all.checkState() == Qt.CheckState.Unchecked
                        for group in page.result_groups
                    )
                )
            finally:
                page.close(); reopened.close()

    def test_bulk_of_many_posts_refreshes_once_and_removes_cards_incrementally(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            repository = ImageAnalysisRepository(Path(temporary) / "analysis.sqlite")
            page = TaggingPage(self.catalog(), {})
            controller = TaggingController(self.catalog(), page, dict, lambda *_args: None)
            controller.image_analysis = SimpleNamespace(repository=repository, settings={})
            posts = [
                {"id": post_id, "tags": "solo", "priority": "low", "tag_count": 1}
                for post_id in range(1, 51)
            ]
            page.show_results(posts)
            untouched = page.result_buttons[50]

            with patch.object(
                page, "set_batch_queue_entries", wraps=page.set_batch_queue_entries
            ) as refresh:
                controller.apply_bulk_manual_tags(posts[:40], ["1girl"], [])

            self.assertEqual(refresh.call_count, 1)
            self.assertEqual(set(page.result_buttons), set(range(41, 51)))
            self.assertIs(page.result_buttons[50], untouched)
            self.assertEqual(repository.pending_change_count(), 40)
            page.close(); repository.close()

    def test_bulk_apply_skips_posts_without_an_effective_delta(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtTest import QSignalSpy

        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            repository = ImageAnalysisRepository(Path(temporary) / "analysis.sqlite")
            page = TaggingPage(self.catalog(), {"tagging_hide_queued_results": False})
            footer = QSignalSpy(page.page_status.message_changed)
            controller = TaggingController(self.catalog(), page, dict, lambda *_args: None)
            controller.image_analysis = SimpleNamespace(repository=repository, settings={})
            posts = [
                {"id": 1, "tags": "1girl blue_hair", "priority": "low", "tag_count": 2},
                {"id": 2, "tags": "blue_hair", "priority": "low", "tag_count": 1},
            ]
            page.show_results(posts)
            controller.apply_bulk_manual_tags(posts, ["1girl"], [])
            entries = repository.list_batch_entries()
            self.assertEqual([entry["post_id"] for entry in entries], ["2", "1"])
            self.assertEqual(entries[1]["publish_state"].value, "skipped")
            self.assertEqual(entries[1]["failure_reason"], "no_effective_delta")
            self.assertEqual(entries[1]["requested_additions"], ["1girl"])
            self.assertEqual(entries[1]["additions"], [])
            self.assertEqual(entries[0]["requested_additions"], ["1girl"])
            self.assertEqual(repository.pending_change_count(), 1)
            self.assertIn("1 queued · 1 skipped", footer.at(footer.count() - 1)[1])
            self.assertIn("No tag changes needed", page.catalog.text("tagging.bulk.no_changes"))
            self.assertIn(1, page.result_buttons)
            controller.apply_bulk_manual_tags(
                [{"id": 3, "tags": "solo", "priority": "low", "tag_count": 1}],
                ["solo"], [],
            )
            self.assertIn("0 queued · 1 skipped — No tag changes needed",
                          footer.at(footer.count() - 1)[1])
            page.close(); repository.close()

    def test_new_query_resets_page_but_same_query_preserves_pagination(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {"tagging_query": "solo"})
        requests = QSignalSpy(page.start_requested)
        page.spins["start"].setValue(12)
        page._start()
        self.assertEqual(requests.at(0)[0].start_page, 12)
        page.query.setText("1girl")
        page._start()
        self.assertEqual(requests.at(1)[0].start_page, 1)
        page.spins["start"].setValue(3)
        page._start()
        self.assertEqual(requests.at(2)[0].start_page, 3)
        page.close()

    def test_partial_noop_keeps_requested_display_and_effective_publish_delta(self) -> None:
        from types import SimpleNamespace

        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            repository = ImageAnalysisRepository(Path(temporary) / "analysis.sqlite")
            page = TaggingPage(self.catalog(), {"tagging_hide_queued_results": False})
            controller = TaggingController(self.catalog(), page, dict, lambda *_args: None)
            controller.image_analysis = SimpleNamespace(repository=repository, settings={})
            controller.apply_bulk_manual_tags(
                [{"id": 42, "tags": "1girl", "priority": "low", "tag_count": 1}],
                ["1girl", "solo"], [],
            )
            entry = repository.list_batch_entries()[0]
            self.assertEqual(entry["publish_state"].value, "pending_publish")
            self.assertEqual(entry["requested_additions"], ["1girl", "solo"])
            self.assertEqual(entry["additions"], ["solo"])
            self.assertEqual(page.batch_table.item(0, 2).text(), "1girl solo")
            page.close(); repository.close()

    def test_checkbox_mode_card_checkbox_and_normal_selection(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {"tagging_hide_queued_results": False})
        page.show_results([
            {"id": value, "tags": "solo", "priority": "low", "tag_count": 1}
            for value in (1, 2, 3)
        ])
        page.checkbox_selection.setChecked(True)
        QTest.mouseClick(page.result_buttons[1], Qt.MouseButton.LeftButton)
        QTest.mouseClick(page.result_buttons[2], Qt.MouseButton.LeftButton)
        self.assertEqual([post["id"] for post in page.selected_posts()], [1, 2])
        QTest.mouseClick(page.result_buttons[1], Qt.MouseButton.LeftButton)
        self.assertEqual([post["id"] for post in page.selected_posts()], [2])
        page.result_buttons[3].selection_checkbox.click()
        self.assertEqual([post["id"] for post in page.selected_posts()], [2, 3])
        QTest.mouseDClick(page.result_buttons[2], Qt.MouseButton.LeftButton)
        self.assertEqual([post["id"] for post in page.selected_posts()], [2, 3])
        page.checkbox_selection.setChecked(False)
        QTest.mouseClick(page.result_buttons[1], Qt.MouseButton.LeftButton)
        self.assertEqual([post["id"] for post in page.selected_posts()], [1])
        page.close()

    def test_tooltip_only_uses_loaded_tags_and_truncates(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage, existing_tags_tooltip

        post = {"id": 17, "tags": "1girl blonde_hair solo", "tag_count": 3,
                "priority": "low", "score": 100}
        page = TaggingPage(self.catalog(), {})
        page.show_results([post])
        self.assertEqual(page.result_buttons[17].toolTip(), "1girl, blonde_hair, solo")
        self.assertEqual(existing_tags_tooltip(post, max_tags=2), "1girl, blonde_hair, …")
        page.close()

    def test_bulk_apply_confirmation_cancel_and_accept(self) -> None:
        from PySide6.QtTest import QSignalSpy
        from PySide6.QtWidgets import QMessageBox

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {"tagging_hide_queued_results": False})
        page.show_results([{"id": 1, "tags": "solo", "priority": "low", "tag_count": 1}])
        page.result_buttons[1].setChecked(True)
        page.bulk_add.add_tag("1girl")
        page.bulk_remove.add_tag("solo")
        applied = QSignalSpy(page.bulk_apply_requested)
        prompts = []
        def cancel(dialog):
            prompts.append(dialog.text())
            return QMessageBox.StandardButton.Cancel
        with patch.object(QMessageBox, "exec", cancel):
            page.bulk_apply.click()
        self.assertEqual(applied.count(), 0)
        self.assertIn("Add: 1girl", prompts[0])
        self.assertIn("Remove: solo", prompts[0])
        self.assertIn("added to the batch", prompts[0])
        self.assertEqual(page.bulk_add.tags(), ["1girl"])
        self.assertEqual(len(page.selected_posts()), 1)
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok):
            page.bulk_apply.click()
        self.assertEqual(applied.count(), 1)
        self.assertEqual(applied.at(0)[1:], [["1girl"], ["solo"]])
        page.close()

    def test_batch_badge_attention_and_first_publish_guidance(self) -> None:
        from PySide6.QtGui import QColor, QPalette

        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        skipped = {"item_id": 1, "site": "gelbooru", "post_id": "1",
                   "additions": [], "removals": [], "requested_additions": ["solo"],
                   "requested_removals": [], "reviewed_at": "now",
                   "publish_state": PublishState.SKIPPED,
                   "failure_reason": "no_effective_delta"}
        completed = {**skipped, "item_id": 2, "post_id": "2",
                     "requested_additions": [], "publish_state": PublishState.PUBLISHED}
        pending = {**skipped, "item_id": 3, "post_id": "3", "additions": ["1girl"],
                   "publish_state": PublishState.PENDING_PUBLISH}
        page.show_batch_entries([skipped, completed])
        page.mark_batch_added()
        self.assertIn("[1]", page.batch_button.text())
        self.assertIn("palette(highlight)", page.batch_button.styleSheet())
        self.assertIn("palette(highlighted-text)", page.batch_button.styleSheet())
        self.assertTrue(page._batch_glow.isEnabled())
        for accent in (QColor("#1b5fa7"), QColor("#ffbd54")):
            palette = QPalette(page.batch_button.palette())
            palette.setColor(QPalette.ColorRole.Highlight, accent)
            page.batch_button.setPalette(palette)
            self.assertEqual(page._batch_glow.color(), accent)
            self.assertIn("[1]", page.batch_button.text())
        page.show_batch()
        self.assertEqual(page.batch_button.styleSheet(), "")
        self.assertFalse(page._batch_glow.isEnabled())
        self.assertFalse(page._publish_pulse_timer.isActive())
        self.assertEqual(page.batch_table.item(1, 2).text(), "—")
        self.assertIn("No tag changes needed", page.batch_table.item(0, 4).text())
        page.show_batch_entries([skipped, completed, pending])
        self.assertTrue(page._publish_pulse_timer.isActive())
        page._return_from_batch()
        page.mark_batch_added()
        self.assertTrue(page._batch_glow.isEnabled())
        page.show_batch()
        self.assertFalse(page._batch_glow.isEnabled())
        page.mark_publish_started()
        self.assertFalse(page._publish_pulse_timer.isActive())
        self.assertTrue(page.settings["tagging_first_publish_started"])
        page.show_batch_entries([completed])
        self.assertEqual(page.batch_button.text(), "Batch")
        page.close()

    def test_publish_result_event_updates_badge_and_batch_rows(self) -> None:
        from types import SimpleNamespace

        from booruflow.application.batch_publisher import BatchPublishProgress
        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        entries = [
            {"item_id": item_id, "site": "gelbooru", "post_id": str(item_id),
             "additions": ["solo"], "removals": [], "reviewed_at": "now",
             "publish_state": PublishState.PENDING_PUBLISH}
            for item_id in (1, 2)
        ]
        page = TaggingPage(self.catalog(), {})
        page.show_batch_entries(entries)
        controller = TaggingController(self.catalog(), page, dict, lambda *_args: None)
        controller.image_analysis = SimpleNamespace(repository=SimpleNamespace(
            list_batch_entries=lambda: list(entries)
        ))
        self.assertIn("[2]", page.batch_button.text())
        entries[0]["publish_state"] = PublishState.PUBLISHED
        controller._batch_publish_status(BatchPublishProgress("result", 1, 2, 1))
        self.assertIn("[1]", page.batch_button.text())
        self.assertIn("Published", page.batch_table.item(0, 4).text())
        entries[1]["publish_state"] = PublishState.FAILED
        controller._batch_publish_status(BatchPublishProgress("result", 2, 2, 2))
        self.assertIn("[1]", page.batch_button.text())
        self.assertIn("Failed", page.batch_table.item(1, 4).text())
        page.close()

    def test_result_checkboxes_share_selection_and_group_tristate(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QSignalSpy, QTest

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {"tagging_hide_queued_results": False})
        posts = [
            {"id": 1, "tags": "a", "priority": "critical", "tag_count": 1},
            {"id": 2, "tags": "b", "priority": "critical", "tag_count": 1},
            {"id": 3, "tags": "c", "priority": "critical", "tag_count": 1},
        ]
        page.show_results(posts)
        opened = QSignalSpy(page.post_selected)
        group = page.result_groups[0]
        self.assertEqual(group.select_all.checkState(), Qt.CheckState.Unchecked)

        page.result_buttons[1].selection_checkbox.click()
        self.assertTrue(page.result_buttons[1].isChecked())
        self.assertEqual(group.select_all.checkState(), Qt.CheckState.PartiallyChecked)
        self.assertEqual(opened.count(), 0)
        page.result_buttons[1].selection_checkbox.click()
        self.assertFalse(page.result_buttons[1].isChecked())

        QTest.mouseClick(
            page.result_buttons[1], Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.assertTrue(page.result_buttons[1].selection_checkbox.isChecked())
        QTest.mouseClick(
            page.result_buttons[3], Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertTrue(all(button.selection_checkbox.isChecked() for button in page.result_buttons.values()))
        self.assertEqual(group.select_all.checkState(), Qt.CheckState.Checked)
        group.select_all.click()
        self.assertFalse(any(button.isChecked() for button in page.result_buttons.values()))
        self.assertEqual(group.select_all.checkState(), Qt.CheckState.Unchecked)
        page.select_all_shortcut.activated.emit()
        self.assertTrue(all(button.isChecked() for button in page.result_buttons.values()))
        QTest.mouseDClick(page.result_buttons[2], Qt.MouseButton.LeftButton)
        self.assertEqual(opened.count(), 1)
        page.close()

    def test_queued_result_filter_tracks_batch_rows_and_restores_removed_results(self) -> None:
        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        posts = [
            {"id": value, "tags": "tag", "priority": "low", "tag_count": 1}
            for value in range(1, 6)
        ]
        page.show_results(posts)
        page.set_batch_queue_entries([
            {"site": "gelbooru", "post_id": "1", "additions": ["a"], "removals": [], "publish_state": PublishState.PENDING_PUBLISH},
            {"site": "gelbooru", "post_id": "2", "additions": [], "removals": ["b"], "publish_state": PublishState.PUBLISHING},
            {"site": "gelbooru", "post_id": "3", "additions": ["c"], "removals": [], "publish_state": PublishState.PUBLISHED},
            {"site": "gelbooru", "post_id": "4", "additions": ["d"], "removals": [], "publish_state": PublishState.FAILED},
            {"site": "gelbooru", "post_id": "5", "additions": [], "removals": [], "publish_state": PublishState.SKIPPED},
        ])
        self.assertEqual(set(page.result_buttons), set())
        self.assertEqual(len(page._all_search_results), 5)
        self.assertIn("5 already queued", page.result_filter_counts.text())
        page.hide_queued_results.setChecked(False)
        self.assertEqual(set(page.result_buttons), {1, 2, 3, 4, 5})
        page.hide_queued_results.setChecked(True)
        self.assertEqual(set(page.result_buttons), set())
        page.set_batch_queue_entries([])
        self.assertEqual(set(page.result_buttons), {1, 2, 3, 4, 5})
        page.close()

    def test_compact_result_card_format_never_renders_zero_deltas(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import compact_result_card_text

        self.assertEqual(compact_result_card_text(42, 10, "✓ Processed"), ("✓ Processed · #42", "10 tags"))
        self.assertEqual(compact_result_card_text(42, 10, "✓ Processed", 1, 0)[1], "10 tags · +1")
        self.assertEqual(compact_result_card_text(42, 10, "✓ Processed", 2, 1)[1], "10 tags · +2 / -1")
        self.assertEqual(compact_result_card_text(42, 10, "⚠ Failed", 0, 3)[1], "10 tags · -3")
        self.assertNotIn("+0", compact_result_card_text(42, 10, "✓ Published", 0, 0)[1])

    def test_tagging_ctrl_wheel_uses_discrete_persisted_thumbnail_levels(self) -> None:
        from PySide6.QtCore import QEvent, QPoint, Qt

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        class WheelEvent:
            accepted = False

            @staticmethod
            def type(): return QEvent.Type.Wheel

            @staticmethod
            def modifiers(): return Qt.KeyboardModifier.ControlModifier

            @staticmethod
            def angleDelta(): return QPoint(0, 120)

            def accept(self): self.accepted = True

        settings = {"tagging_thumbnail_size": 160}
        page = TaggingPage(self.catalog(), settings)
        page.show_results([{"id": 1, "tags": "solo", "priority": "low", "tag_count": 1}])
        event = WheelEvent()
        self.assertTrue(page.eventFilter(page.results_scroll.viewport(), event))
        self.assertTrue(event.accepted)
        self.assertEqual(settings["tagging_thumbnail_size"], 192)
        self.assertEqual(page.result_buttons[1].iconSize().width(), 192)
        page.close()

    def test_tagging_review_back_uses_explicit_origin(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        post = {"id": 42, "tags": "solo", "priority": "low", "tag_count": 1}
        page.show_results([post])

        page._open_result_post(post)
        page._back_from_review()
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)

        page.show_batch()
        page._open_result_post(post, origin="batch")
        page._back_from_review()
        self.assertIs(page.mode_stack.currentWidget(), page.batch_view)

        page._return_from_batch()
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        page.close()

    def test_batch_refresh_without_visibility_change_preserves_grid_widgets(self) -> None:
        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {"tagging_hide_queued_results": False})
        post = {"id": 42, "tags": "solo", "priority": "low", "tag_count": 1}
        page.show_results([post])
        original_card = page.result_buttons[42]
        original_card.setChecked(True)

        page.set_batch_queue_entries([{
            "site": "gelbooru", "post_id": "42", "additions": ["1girl"],
            "removals": [], "publish_state": PublishState.FAILED,
        }])
        self.assertIs(page.result_buttons[42], original_card)
        self.assertTrue(page.result_buttons[42].isChecked())
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        page.close()

    def test_batch_state_change_that_refilters_grid_does_not_navigate(self) -> None:
        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show_results([{
            "id": 42, "tags": "solo", "priority": "low", "tag_count": 1,
        }])
        page.set_batch_queue_entries([{
            "site": "gelbooru", "post_id": "42", "additions": ["1girl"],
            "removals": [], "publish_state": PublishState.FAILED,
        }])
        page.show_batch()
        page.set_batch_queue_entries([{
            "site": "gelbooru", "post_id": "42", "additions": ["1girl"],
            "removals": [], "publish_state": PublishState.PUBLISHED,
        }])

        self.assertIs(page.mode_stack.currentWidget(), page.batch_view)
        self.assertNotIn(42, page.result_buttons)
        page.close()

    def test_thumbnail_memory_cache_is_site_scoped_and_lru_bounded(self) -> None:
        from PySide6.QtGui import QImage

        from booruflow.presentation.pyside6.thumbnail_cache import (
            ThumbnailCacheKey,
            ThumbnailMemoryCache,
        )

        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(0xFF112233)
        cost = image.sizeInBytes()
        cache = ThumbnailMemoryCache(max_bytes=cost * 2)
        gelbooru = ThumbnailCacheKey("gelbooru", 42, "https://example/thumb.jpg")
        e621 = ThumbnailCacheKey("e621", 42, "https://example/thumb.jpg")
        third = ThumbnailCacheKey("gelbooru", 43, "https://example/other.jpg")

        self.assertIsNone(cache.get(gelbooru))
        self.assertTrue(cache.put(gelbooru, image))
        self.assertIsNotNone(cache.get(gelbooru))
        self.assertIsNone(cache.get(e621))
        self.assertTrue(cache.put(e621, image))
        self.assertIsNotNone(cache.get(gelbooru))  # make Gelbooru most recently used
        self.assertTrue(cache.put(third, image))
        self.assertIsNone(cache.get(e621))
        self.assertIsNotNone(cache.get(gelbooru))
        self.assertLessEqual(cache.byte_count, cache.max_bytes)
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_cached_thumbnail_skips_network_fetch(self) -> None:
        from PySide6.QtGui import QImage

        from booruflow.presentation.pyside6.tagging_page import TaggingPage
        from booruflow.presentation.pyside6.thumbnail_cache import (
            ThumbnailCacheKey,
            ThumbnailMemoryCache,
        )

        url = "https://example.invalid/thumb.jpg"
        cache = ThumbnailMemoryCache()
        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(0xFF112233)
        cache.put(ThumbnailCacheKey("gelbooru", 42, url), image)
        page = TaggingPage(self.catalog(), {}, thumbnail_cache=cache)

        class NoNetwork:
            @staticmethod
            def get(_request):
                raise AssertionError("cache hit must not start a network request")

        page.network = NoNetwork()
        page.show_results([{
            "id": 42, "tags": "solo", "priority": "low", "tag_count": 1,
            "preview_url": url,
        }])
        self.assertFalse(page.result_buttons[42].icon().isNull())
        page.close()

    def test_late_thumbnail_response_populates_cache_without_touching_stale_card(self) -> None:
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QToolButton

        from booruflow.presentation.pyside6.tagging_page import TaggingPage
        from booruflow.presentation.pyside6.thumbnail_cache import ThumbnailCacheKey

        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(0xFF112233)
        page = TaggingPage(self.catalog(), {})
        stale_card = QToolButton()
        key = ThumbnailCacheKey("gelbooru", 42, "https://example.invalid/thumb.jpg")
        page.thumbnail_cache.put(key, image)
        page._thumbnail_targets[key] = (stale_card, 1)
        page.result_generation = 2
        page._thumbnail_image_ready(key, image)

        self.assertIsNotNone(page.thumbnail_cache.get(key))
        self.assertTrue(stale_card.icon().isNull())
        page.close()

    def test_late_gelbooru_result_is_ignored_after_switch_to_e621(self) -> None:
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        controller = TaggingController(self.catalog(), page, dict, lambda _message: None)
        page.site_selector.setCurrentIndex(page.site_selector.findData("e621"))
        controller.finished(
            [{"id": 42, "tags": "solo", "priority": "low", "tag_count": 1}],
            1, 2, False, "", False, "gelbooru",
        )
        self.assertEqual(page.result_posts, [])
        self.assertEqual(page.active_site, "e621")
        page.close()

    def test_e621_batch_requires_credentials_before_publish(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {"tagging_site": "e621"})
        entry = {
            "item_id": 7, "site": "e621", "post_id": "42",
            "original_tags": ["wolf"], "additions": ["solo"], "removals": [],
            "reviewed_final_tags": ["wolf", "solo"], "reviewed_at": "now",
            "publish_state": PublishState.PENDING_PUBLISH, "publish_attempts": 0,
            "last_error": None, "last_attempt_at": None, "published_at": None,
            "published_verified_at": None, "published_final_tags": None,
        }
        page.show_batch_entries([entry])
        self.assertFalse(page.batch_publish_button.isEnabled())
        self.assertEqual(
            page.batch_publish_button.toolTip(),
            "Add and validate the e621 username and API key in Options before publishing.",
        )
        page.set_e621_publish_configured(True)
        self.assertTrue(page.batch_publish_button.isEnabled())
        self.assertEqual(page.batch_publish_button.toolTip(), "")
        called = []
        controller = TaggingController.__new__(TaggingController)
        controller.publish_worker = None
        controller.image_analysis = SimpleNamespace(
            repository=SimpleNamespace(list_batch_entries=lambda: [entry])
        )
        controller.page = page
        controller.catalog = self.catalog()
        controller._publisher_factory = lambda: called.append(True)
        controller._start_batch_publish(None)
        self.assertEqual(called, [])
        page.close()

    def test_batch_review_collision_keeps_site_identity_and_e621_tags(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import (
            AnalysisItem,
            AnalysisState,
            InputKind,
            ObservationSource,
            SourceReference,
            SourceTag,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            repository = ImageAnalysisRepository(Path(temporary) / "analysis.sqlite")
            gelbooru_id = repository.add_item(
                AnalysisItem(
                    SourceReference(
                        InputKind.GELBOORU_POST, site="gelbooru", post_id="500"
                    ),
                    state=AnalysisState.READY_FOR_REVIEW,
                ),
                (
                    SourceTag("1girl", ObservationSource.GELBOORU, "general"),
                    SourceTag("blue_hair", ObservationSource.GELBOORU, "general"),
                ),
            )
            e621_id = repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.E621_POST, site="e621", post_id="500"),
                    state=AnalysisState.READY_FOR_REVIEW,
                ),
                tuple(
                    SourceTag(tag, ObservationSource.E621, "general")
                    for tag in ("female", "solo", "canine")
                ),
            )
            repository.save_review_batch_entry(
                gelbooru_id,
                original_tags=["1girl", "blue_hair"], additions=["solo"], removals=[],
                reviewed_final_tags=["1girl", "blue_hair", "solo"],
            )
            repository.save_review_batch_entry(
                e621_id,
                original_tags=["female", "solo", "canine"], additions=[], removals=[],
                reviewed_final_tags=["female", "solo", "canine"],
            )
            gelbooru_before = repository.batch_entry(gelbooru_id)
            page = TaggingPage(self.catalog(), {})
            controller = TaggingController(
                self.catalog(), page, dict, lambda *_args, **_kwargs: None
            )
            controller.image_analysis = SimpleNamespace(
                repository=repository,
                settings={},
                worker_startup_state="ready",
            )

            controller.review_batch_item(e621_id)

            self.assertEqual(page.active_site, "e621")
            self.assertEqual(controller._current_item_id(), e621_id)
            displayed = set(str(page.current_post["tags"]).split())
            self.assertEqual(displayed, {"female", "solo", "canine"})

            controller.decide("existing:female", "rejected")
            controller.validate_current_review()

            self.assertEqual(repository.batch_entry(gelbooru_id), gelbooru_before)
            self.assertEqual(repository.batch_entry(e621_id)["site"], "e621")
            self.assertEqual(repository.batch_entry(e621_id)["removals"], ["female"])
            e621_before = repository.batch_entry(e621_id)

            controller.review_batch_item(gelbooru_id)
            self.assertEqual(page.active_site, "gelbooru")
            self.assertEqual(controller._current_item_id(), gelbooru_id)
            controller.decide("existing:blue_hair", "rejected")
            controller.validate_current_review()

            self.assertEqual(repository.batch_entry(e621_id), e621_before)
            self.assertEqual(repository.batch_entry(gelbooru_id)["removals"], ["blue_hair"])
            page.close()
            repository.close()

    def test_main_window_publisher_initialization_keeps_alias_catalogue_for_first_verify(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import AnalysisItem, InputKind, SourceReference
        from booruflow.infrastructure.gelbooru_aliases import (
            AliasRelation,
            GelbooruAliasRepository,
            ensure_alias_schema,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.infrastructure.settings import JsonSettingsRepository
        from booruflow.presentation.pyside6.main_window import MainWindow

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aliases = root / "gelbooru-aliases.db"
            ensure_alias_schema(aliases)
            GelbooruAliasRepository(aliases).upsert(
                AliasRelation("china_dress", "qipao", "active")
            )
            analysis_database = root / "analysis.sqlite"
            with ImageAnalysisRepository(analysis_database) as repository:
                item_id = repository.add_item(
                    AnalysisItem(
                        SourceReference(
                            InputKind.GELBOORU_POST,
                            site="gelbooru",
                            post_id="13992657",
                        )
                    )
                )
                repository.save_review_batch_entry(
                    item_id,
                    original_tags=["solo"],
                    additions=["china_dress"],
                    removals=[],
                    reviewed_final_tags=["china_dress", "solo"],
                )
            logs: list[str] = []
            settings_repository = JsonSettingsRepository(root / "config" / "settings.json")
            settings_repository.save(
                {
                    "gelbooru_tag_database": str(root / "tags.db"),
                    "gelbooru_alias_database": str(aliases),
                }
            )
            window = SimpleNamespace(
                image_analysis_controller=SimpleNamespace(
                    database=analysis_database,
                    settings=settings_repository.load(),
                ),
                _credentials=lambda: {"gelbooru": {}},
                _active_gelbooru_session_factory=lambda: object(),
                catalog=SimpleNamespace(text=lambda key: key),
                publish_backend="cdp",
                log_threadsafe=logs.append,
            )

            publisher = MainWindow._build_gelbooru_publisher(window)
            provider = SimpleNamespace(
                tags=["solo"],
                fetch_post=lambda _post_id: SimpleNamespace(
                    tags=[SimpleNamespace(name=name) for name in provider.tags]
                ),
            )
            publisher.preparation.gelbooru_provider = provider
            prepared = publisher.preparation.prepare(item_id)
            provider.tags = ["solo", "qipao"]
            retries: list[float] = []
            publisher.verification_attempts = 4
            publisher.verification_sleeper = retries.append

            publisher._verify_with_retries(prepared)

            self.assertEqual(retries, [])
            self.assertTrue(
                any("china_dress resolved to qipao: present" in line for line in logs)
            )
            publisher.repository.close()

    def test_tagging_batch_retranslates_without_losing_filter_selection_or_widths(self) -> None:
        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        catalog = self.catalog()
        page = TaggingPage(catalog, {})
        page.show_batch_entries([
            {
                "item_id": 17, "site": "gelbooru", "post_id": "42",
                "additions": ["blue_hair"], "removals": [],
                "reviewed_at": "2026-08-30", "publish_state": PublishState.PENDING_PUBLISH,
            }
        ])
        page.batch_filter.setCurrentIndex(page.batch_filter.findData("pending_publish"))
        page.batch_table.setColumnWidth(2, 137)
        page.batch_table.selectRow(0)

        catalog.set_language("fr")
        page.retranslate()

        self.assertEqual(page.batch_filter.currentData(), "pending_publish")
        self.assertEqual(page.batch_filter.currentText(), "En attente")
        self.assertEqual(page.batch_table.horizontalHeaderItem(2).text(), "Ajouts")
        self.assertEqual(page.batch_table.columnWidth(2), 137)
        self.assertEqual(page._selected_batch_ids(), [17])
        self.assertIn("1 en attente", page.batch_counts.text())
        self.assertEqual(page.batch_table.item(0, 4).text(), "En attente")

        catalog.set_language("en")
        page.retranslate()
        self.assertEqual(page.batch_filter.currentData(), "pending_publish")
        self.assertEqual(page.batch_filter.currentText(), "Pending")
        self.assertEqual(page.batch_table.horizontalHeaderItem(2).text(), "Additions")
        self.assertEqual(page.batch_table.columnWidth(2), 137)
        self.assertEqual(page._selected_batch_ids(), [17])
        self.assertNotIn("Actualiser", page.batch_refresh_button.text())
        page.close()

    def test_tagging_batch_empty_state_and_zero_counts_follow_language(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        catalog = self.catalog()
        page = TaggingPage(catalog, {})
        page.show_batch_entries([])
        self.assertEqual(page.batch_refresh_button.text(), "Refresh")
        self.assertIn("0 pending", page.batch_counts.text())
        self.assertEqual(page.batch_table.empty_text(), "No review item matches this filter.")
        catalog.set_language("fr")
        page.retranslate()
        self.assertEqual(page.batch_refresh_button.text(), "Actualiser")
        self.assertIn("0 en attente", page.batch_counts.text())
        self.assertEqual(page.batch_table.empty_text(), "Aucun élément de revue ne correspond à ce filtre.")
        page.close()

    def test_tagging_results_are_grouped_into_collapsible_grids(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show_results(
            [
                {"id": 10, "tag_count": 4, "priority": "critical"},
                {"id": 20, "tag_count": 7, "priority": "high"},
            ]
        )
        self.assertEqual(page.results_layout.count(), 3)
        critical = page.results_layout.itemAt(0).widget()
        self.assertEqual(critical.grid.count(), 1)
        self.assertIn("#10", critical.grid.itemAt(0).widget().text())
        self.assertFalse(critical.content.isHidden())
        critical.toggle.setChecked(False)
        self.assertTrue(critical.content.isHidden())
        page.close()

    def test_batch_publish_controls_exclude_local_and_enable_explicit_failed_retry(self) -> None:
        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        entries = [
            {
                "item_id": 1,
                "site": None,
                "post_id": None,
                "additions": [],
                "removals": [],
                "reviewed_at": "now",
                "publish_state": PublishState.REVIEWED,
            },
            {
                "item_id": 2,
                "site": "gelbooru",
                "post_id": "22",
                "additions": ["a"],
                "removals": [],
                "reviewed_at": "now",
                "publish_state": PublishState.PENDING_PUBLISH,
            },
            {
                "item_id": 3,
                "site": "gelbooru",
                "post_id": "23",
                "additions": [],
                "removals": ["b"],
                "reviewed_at": "now",
                "publish_state": PublishState.FAILED,
            },
        ]
        page.show_batch_entries(entries)
        self.assertTrue(page.batch_publish_button.isEnabled())
        page.batch_table.selectRow(2)
        self.assertTrue(page.batch_retry_button.isEnabled())
        page.set_batch_publish_running(True)
        self.assertFalse(page.batch_publish_button.isEnabled())
        self.assertFalse(page.batch_retry_button.isEnabled())
        page.close()

    def test_batch_publish_button_tracks_reopened_published_review_state(self) -> None:
        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        entry = {
            "item_id": 1,
            "site": "gelbooru",
            "post_id": "14772833",
            "additions": [],
            "removals": [],
            "reviewed_at": "now",
            "publish_state": PublishState.PUBLISHED,
        }

        page.show_batch_entries([entry])
        self.assertFalse(page.batch_publish_button.isEnabled())
        self.assertIn("0 pending", page.batch_counts.text())

        changed = dict(
            entry,
            additions=["new_tag"],
            publish_state=PublishState.PENDING_PUBLISH,
        )
        page.show_batch_entries([changed])
        self.assertTrue(page.batch_publish_button.isEnabled())
        self.assertIn("1 pending", page.batch_counts.text())
        page.batch_filter.setCurrentIndex(page.batch_filter.findData("pending_publish"))
        self.assertEqual(page.batch_table.rowCount(), 1)
        self.assertEqual(page.batch_table.item(0, 2).text(), "new_tag")
        self.assertFalse(page.batch_retry_button.isEnabled())
        page.close()

    def test_revalidating_changed_published_review_refreshes_batch_as_pending(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import (
            AnalysisItem,
            InputKind,
            ObservationSource,
            SourceReference,
            SourceTag,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            repository = ImageAnalysisRepository(Path(temporary) / "state.sqlite")
            item_id = repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="14772833")
                ),
                (SourceTag("solo", ObservationSource.GELBOORU, "general"),),
            )
            repository.add_to_tagging_pool([item_id], "test")
            repository.save_review_batch_entry(
                item_id,
                original_tags=["solo"],
                additions=[],
                removals=[],
                reviewed_final_tags=["solo"],
            )
            repository.begin_publish_attempt(item_id)
            repository.publish_succeeded(item_id)
            previous = repository.batch_entry(item_id)
            repository.add_manual_observation(item_id, "new_tag")
            logs = []
            page = TaggingPage(self.catalog(), {})
            controller = TaggingController(self.catalog(), page, dict, logs.append)
            controller.image_analysis = SimpleNamespace(repository=repository, settings={})
            controller.select_post(14772833, {"id": 14772833, "tags": "solo"})

            controller.validate_current_review()

            changed = repository.batch_entry(item_id)
            self.assertEqual(changed["publish_state"].value, "pending_publish")
            self.assertEqual(changed["additions"], ["new_tag"])
            self.assertEqual(changed["published_at"], previous["published_at"])
            self.assertEqual(changed["publish_attempts"], previous["publish_attempts"])
            self.assertTrue(page.batch_publish_button.isEnabled())
            self.assertIn("Review snapshot saved (pending_publish)", "\n".join(logs))
            page.close()
            repository.close()

    def test_batch_session_test_is_non_destructive_and_reports_validation(self) -> None:
        from types import SimpleNamespace

        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        calls = []
        factory = SimpleNamespace(validate=lambda: calls.append("validate"))
        page = TaggingPage(self.catalog(), {})
        page.show_batch_entries([{
            "item_id": 1, "site": "gelbooru", "post_id": "42",
            "additions": ["solo"], "removals": [],
            "publish_state": SimpleNamespace(value="pending_publish"),
            "reviewed_at": "now",
        }])
        controller = TaggingController(
            self.catalog(), page, dict, lambda *_args, **_kwargs: None, session_factory=factory
        )
        page.batch_session_test_button.click()
        from PySide6.QtTest import QTest

        for _attempt in range(50):
            if calls and controller.session_test_worker.isFinished():
                break
            QTest.qWait(10)
        QTest.qWait(20)
        self.assertEqual(calls, ["validate"])
        self.assertIn("authenticated", page.batch_status.text())
        self.assertIsNotNone(controller)
        page.close()

    def test_batch_session_test_distinguishes_logged_out_and_unknown(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtTest import QTest

        from booruflow.infrastructure.gelbooru_edit_transport import (
            GelbooruSessionExpiredError,
            GelbooruSessionUnknownError,
        )
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        for error, expected in (
            (GelbooruSessionExpiredError("logout"), "not signed in"),
            (GelbooruSessionUnknownError("unknown"), "unknown"),
        ):

            def validate(error=error):
                raise error

            page = TaggingPage(self.catalog(), {})
            page.show_batch_entries([{
                "item_id": 1, "site": "gelbooru", "post_id": "42",
                "additions": ["solo"], "removals": [],
                "publish_state": SimpleNamespace(value="pending_publish"),
                "reviewed_at": "now",
            }])
            controller = TaggingController(
                self.catalog(),
                page,
                dict,
                lambda *_args, **_kwargs: None,
                session_factory=SimpleNamespace(validate=validate),
            )
            page.batch_session_test_button.click()
            for _attempt in range(50):
                if controller.session_test_worker.isFinished():
                    break
                QTest.qWait(10)
            QTest.qWait(20)
            self.assertIn(expected, page.batch_status.text())
            page.close()

    def test_e621_only_session_test_uses_api_validator_and_not_gelbooru(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtTest import QTest

        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        calls: list[str] = []
        entry = {
            "item_id": 1, "site": "e621", "post_id": "42",
            "additions": ["solo"], "removals": [], "reviewed_at": "now",
            "publish_state": SimpleNamespace(value="pending_publish"),
        }
        page = TaggingPage(self.catalog(), {"tagging_site": "e621"})
        page.show_batch_entries([entry])
        controller = TaggingController(
            self.catalog(), page, lambda: {"e621": {"user_id": "user", "api_key": "key"}},
            lambda *_args, **_kwargs: None,
            session_factory=SimpleNamespace(validate=lambda: calls.append("gelbooru")),
            e621_validation_factory=lambda: SimpleNamespace(
                validate_credentials=lambda: calls.append("e621-get")
            ),
        )

        page.batch_session_test_button.click()
        for _attempt in range(50):
            if controller.session_test_worker.isFinished():
                break
            QTest.qWait(10)
        QTest.qWait(20)

        self.assertEqual(calls, ["e621-get"])
        self.assertEqual(page.batch_status.text(), "e621: API authentication valid")
        page.close()

    def test_mixed_session_test_reports_each_site_and_missing_e621_credentials(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtTest import QTest

        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        entries = [
            {"item_id": 1, "site": "gelbooru", "post_id": "10", "additions": ["a"],
             "removals": [], "reviewed_at": "now", "publish_state": SimpleNamespace(value="pending_publish")},
            {"item_id": 2, "site": "e621", "post_id": "20", "additions": ["b"],
             "removals": [], "reviewed_at": "now", "publish_state": SimpleNamespace(value="pending_publish")},
        ]
        page = TaggingPage(self.catalog(), {})
        page.show_batch_entries(entries)
        controller = TaggingController(
            self.catalog(), page, dict, lambda *_args, **_kwargs: None,
            session_factory=SimpleNamespace(validate=lambda: None),
            e621_validation_factory=lambda: None,
        )

        page.batch_session_test_button.click()
        for _attempt in range(50):
            if controller.session_test_worker.isFinished():
                break
            QTest.qWait(10)
        QTest.qWait(20)

        self.assertIn("Gelbooru: authenticated", page.batch_status.text())
        self.assertIn("e621: API credentials not configured", page.batch_status.text())
        self.assertTrue(page.batch_publish_button.isEnabled())
        page.close()

    def test_empty_batch_has_no_implicit_gelbooru_session_test(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        self.assertFalse(page.batch_session_test_button.isEnabled())
        self.assertEqual(page.batch_sites_present(), ())
        page.close()

    def test_mixed_publish_click_routes_through_common_site_aggregator(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QMessageBox

        from booruflow.application.batch_publisher import (
            BatchPublishSummary,
            MixedSiteBatchPublisher,
        )
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        entries = [
            {"item_id": 1, "site": "gelbooru", "post_id": "10", "additions": ["a"],
             "removals": [], "reviewed_at": "now", "publish_state": SimpleNamespace(value="pending_publish")},
            {"item_id": 2, "site": "e621", "post_id": "20", "additions": ["b"],
             "removals": [], "reviewed_at": "now", "publish_state": SimpleNamespace(value="pending_publish")},
        ]
        routed: list[str] = []

        class Repository:
            def list_batch_entries(self, _state=None):
                return entries

            def close(self):
                pass

        class SitePublisher:
            cancel_check = None

            def __init__(self, site):
                self.site = site

            def publish_pending(self, progress):
                routed.append(self.site)
                progress(1, 1, "10" if self.site == "gelbooru" else "20")
                return BatchPublishSummary(total=1, published=1)

        repository = Repository()
        page = TaggingPage(self.catalog(), {})
        page.show_batch_entries(entries)
        page.set_e621_publish_configured(True)
        controller = TaggingController(
            self.catalog(), page,
            lambda: {"e621": {"user_id": "user", "api_key": "key"}},
            lambda *_args, **_kwargs: None,
            publisher_factory=lambda: MixedSiteBatchPublisher(
                repository,
                {site: SitePublisher(site) for site in ("gelbooru", "e621")},
            ),
        )
        controller.image_analysis = SimpleNamespace(repository=repository)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page.batch_publish_button.click()
        for _attempt in range(50):
            if controller.publish_worker.isFinished():
                break
            QTest.qWait(10)
        QTest.qWait(20)

        self.assertEqual(routed, ["gelbooru", "e621"])
        self.assertTrue(page.settings["tagging_first_publish_started"])
        page.close()

    def test_publish_preflight_blocks_unauthenticated_embedded_session(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from PySide6.QtTest import QTest

        from booruflow.infrastructure.gelbooru_edit_transport import GelbooruSessionExpiredError
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        entries = [{
            "item_id": 1, "site": "gelbooru", "post_id": "10", "additions": ["a"],
            "removals": [], "reviewed_at": "now",
            "publish_state": SimpleNamespace(value="pending_publish"),
        }]
        class Repository:
            def list_batch_entries(self, _state=None): return entries
        page = TaggingPage(self.catalog(), {})
        page.show_batch_entries(entries)
        page.show_batch()
        self.assertTrue(page._publish_pulse_timer.isActive())
        opened = []
        class Session:
            def validate(self): raise GelbooruSessionExpiredError("expired")
        controller = TaggingController(
            self.catalog(), page, dict, lambda *_args: None,
            publisher_factory=lambda: (_ for _ in ()).throw(AssertionError("must not build")),
            session_factory=Session(), embedded_login_opener=lambda: opened.append(True),
        )
        controller.image_analysis = SimpleNamespace(repository=Repository())
        with patch("PySide6.QtWidgets.QMessageBox.question") as question:
            page.batch_publish_button.click()
            QTest.qWait(500)
        self.assertIsNone(controller.publish_worker)
        if not opened:
            controller._show_gelbooru_auth_required("expired")
        self.assertEqual(opened, [True])
        self.assertEqual(question.call_count, 0)
        self.assertIn("login", page.batch_status.text().lower())
        self.assertFalse(page.settings.get("tagging_first_publish_started", False))
        self.assertTrue(page._publish_pulse_timer.isActive())
        page.close()

    def test_publish_countdown_uses_worker_delay_and_formats_eta(self) -> None:
        from unittest.mock import patch

        from booruflow.application.batch_publisher import BatchPublishProgress
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        event = BatchPublishProgress(
            phase="waiting", current=1, total=4, processed=1, post_id="gelbooru:42",
            result="published", published=1, elapsed_seconds=3.0,
            wait_seconds=2.0, estimated_remaining_seconds=36.0,
        )
        with patch(
            "booruflow.presentation.pyside6.tagging_page.perf_counter", return_value=10.0
        ):
            page.show_batch_publish_progress(event)
        with patch(
            "booruflow.presentation.pyside6.tagging_page.perf_counter", return_value=10.6
        ):
            page._refresh_publish_progress()

        self.assertEqual(page.batch_progress.value(), 1)
        self.assertIn("1 / 4", page.batch_status.text())
        self.assertIn("3 remaining", page.batch_status.text())
        self.assertIn("1.4 s", page.batch_status.text())
        self.assertIn("35 s", page.batch_status.text())
        self.assertEqual(page.format_duration(253), "4 min 13 s")
        page.close()

    def test_publish_completion_requests_attention_only_when_window_is_inactive(self) -> None:
        from unittest.mock import Mock, patch

        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        controller = TaggingController.__new__(TaggingController)
        window = Mock()
        controller.page = Mock()
        controller.page.window.return_value = window
        with patch.object(QApplication, "alert") as alert:
            window.isActiveWindow.return_value = False
            controller._request_publish_completion_attention()
            alert.assert_called_once_with(window)
            alert.reset_mock()
            window.isActiveWindow.return_value = True
            controller._request_publish_completion_attention()
            alert.assert_not_called()

    def test_tagging_legacy_keeps_review_copy_and_configured_browser_flow(self) -> None:
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_legacy_page import TaggingLegacyPage

        opened = []
        browser = type("Browser", (), {"open": lambda _self, url: opened.append(url) or True})()
        page = TaggingLegacyPage(self.catalog(), {}, browser)
        page._select_post({"id": 42, "tags": "solo blue_hair"})
        page.show_local_review(
            "Analysis available",
            None,
            ["solo", "blue_hair"],
            [
                {
                    "id": 7,
                    "tag": "long_hair",
                    "confidence": "0.900",
                    "decision": "accepted",
                    "match": "exact",
                }
            ],
            [],
            ["blue_hair", "long_hair", "solo"],
        )
        page.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), "blue_hair long_hair solo")
        page.open_button.click()
        self.assertEqual(opened, ["https://gelbooru.com/index.php?page=post&s=view&id=42"])
        page.close()

    def test_tagging_open_uses_displayed_post_despite_stale_controller_cursor(self) -> None:
        from booruflow.domain.booru_sites import site_definition
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        for site, expected in (
            ("gelbooru", "https://gelbooru.com/index.php?page=post&s=view&id=101"),
            ("e621", "https://e621.net/posts/101"),
        ):
            opened: list[str] = []
            page = TaggingPage(
                self.catalog(), {"tagging_site": site},
                type("Browser", (), {"open": staticmethod(opened.append)})(),
            )
            posts = [{"id": 101, "tags": "a"}, {"id": 202, "tags": "b"}]
            page.show_results(posts)
            page._select_post(posts[1])
            page._select_post(posts[0])
            # Publishing/batch state may move a controller cursor, but it must
            # never replace the post actually displayed by the review page.
            page.current_post_id = 202
            page.open_button.click()
            self.assertEqual(opened, [expected])
            self.assertIn(site_definition(site).display_name, page.open_button.text())
            page.close()

    def test_tagging_local_review_selection_shortcuts_and_copy_lists(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show()
        page.activateWindow()
        QApplication.processEvents()
        page.show_local_review(
            "Analysis available",
            None,
            ["solo"],
            [
                {
                    "id": 17,
                    "tag": "blue_hair",
                    "confidence": "0.900",
                    "decision": "unreviewed",
                    "match": "exact",
                }
            ],
            ["blue_hair"],
            ["blue_hair", "unknown_tag"],
        )
        captured = []
        page.decision_requested.connect(lambda oid, state: captured.append((oid, state)))
        page.suggestions.selectRow(0)
        page.suggestions.setFocus()
        QTest.keyClick(page.suggestions, Qt.Key.Key_A)
        self.assertEqual(captured, [(17, "accepted")])
        QTest.keyClick(page.suggestions, Qt.Key.Key_R)
        self.assertEqual(captured[-1], (17, "rejected"))
        page.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), "blue_hair unknown_tag")
        page.copy_all_button.click()
        self.assertEqual(QApplication.clipboard().text(), "blue_hair unknown_tag")
        self.assertEqual(page.zoom.itemData(3), 400)
        page.close()

    def test_tagging_bulk_shortcut_emits_every_selected_mixed_review_row(self) -> None:
        from PySide6.QtCore import QItemSelectionModel

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show_local_review(
            "Analysis available",
            None,
            ["absurdres", "solo"],
            [
                {
                    "id": "existing:absurdres",
                    "tag": "absurdres",
                    "confidence": "",
                    "decision": "keep",
                    "match": "Existant",
                },
                {
                    "id": "existing:solo",
                    "tag": "solo",
                    "confidence": "",
                    "decision": "keep",
                    "match": "Existant",
                },
                {
                    "id": 17,
                    "tag": "blue_hair",
                    "confidence": "0.900",
                    "decision": "unreviewed",
                    "match": "exact",
                },
                {
                    "id": 23,
                    "tag": "long_hair",
                    "confidence": "0.800",
                    "decision": "unreviewed",
                    "match": "exact",
                },
            ],
            [],
            ["absurdres", "solo"],
        )
        page.decision_filter.setCurrentIndex(0)
        page.suggestions.clearSelection()
        for row in range(page.suggestions.rowCount()):
            page.suggestions.selectionModel().select(
                page.suggestions.model().index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        captured = []
        page.decision_requested.connect(
            lambda tokens, decision: captured.append((tokens, decision))
        )
        page._emit_decision("rejected")
        self.assertEqual(
            captured, [(["17", "23", "existing:absurdres", "existing:solo"], "rejected")]
        )
        page.close()

    def test_tagging_final_tags_field_and_primary_clipboard_are_complete_and_stable(self) -> None:
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        final_tags = ["a", "c", "d"]  # original a/c, removed b, accepted WD14 d
        page.show_local_review(
            "Analysis available",
            None,
            ["a", "b", "c"],
            [
                {
                    "id": "existing:a",
                    "tag": "a",
                    "confidence": "",
                    "decision": "keep",
                    "match": "Existant",
                },
                {
                    "id": "existing:b",
                    "tag": "b",
                    "confidence": "",
                    "decision": "remove",
                    "match": "Existant",
                },
                {
                    "id": 11,
                    "tag": "d",
                    "confidence": "0.9",
                    "decision": "accepted",
                    "match": "exact",
                },
            ],
            ["d"],
            final_tags,
        )
        self.assertEqual(page.tags_to_add.text(), "a c d")
        page.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), "a c d")
        page.show_local_review("Analysis available", None, ["a", "b", "c"], [], [], final_tags)
        self.assertEqual(page.tags_to_add.text(), "a c d")
        self.assertEqual(page._clipboard_text("final"), "a c d")
        page.close()

    def test_new_tagging_undo_shortcuts_are_limited_to_the_review_table(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show()
        QApplication.processEvents()
        page.show_local_review(
            "Analysis available",
            None,
            [],
            [
                {
                    "id": 1,
                    "tag": "robot",
                    "confidence": "0.8",
                    "decision": "unreviewed",
                    "match": "exact",
                }
            ],
            [],
            [],
        )
        events = []
        page.undo_requested.connect(lambda: events.append("undo"))
        page.redo_requested.connect(lambda: events.append("redo"))
        page.suggestions.setFocus()
        QTest.keyClick(page.suggestions, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(
            page.suggestions,
            Qt.Key.Key_Z,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        QTest.keyClick(page.suggestions, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(events, ["undo", "redo", "redo"])
        page.manual_tag.setFocus()
        QTest.keyClick(page.manual_tag, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(
            page.manual_tag,
            Qt.Key.Key_Z,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        QTest.keyRelease(page.manual_tag, Qt.Key.Key_Control)
        QTest.keyRelease(page.manual_tag, Qt.Key.Key_Shift)
        self.assertEqual(events, ["undo", "redo", "redo"])
        page.close()

    def test_new_tagging_manual_entry_has_shared_mouse_keyboard_completer(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show_local_review("Analysis available", None, [], [], [], [])
        lookups = []
        additions = []
        page.manual_lookup_requested.connect(lookups.append)
        page.manual_add_requested.connect(additions.append)
        page.manual_tag.setFocus()
        QTest.keyClicks(page.manual_tag, "b")
        QTest.qWait(300)
        self.assertEqual(lookups, [])
        QTest.keyClicks(page.manual_tag, "l")
        QTest.qWait(100)
        QTest.keyClicks(page.manual_tag, "u")
        QTest.qWait(100)
        QTest.keyClicks(page.manual_tag, "e")
        QTest.qWait(300)
        self.assertEqual(lookups, ["blue"])
        page.set_manual_suggestions(["blue_hair", "blue_eyes"])
        self.assertEqual(page.manual_suggestion_model.stringList(), ["blue_hair", "blue_eyes"])
        page.manual_completer.activated[str].emit("blue_hair")
        self.assertEqual(page.manual_tag.text(), "blue_hair")
        QTest.keyClick(page.manual_tag, Qt.Key.Key_Return)
        self.assertEqual(additions, ["blue_hair"])
        page.manual_tag.setText("blue_eyes")
        page.manual_add.click()
        self.assertEqual(additions[-1], "blue_eyes")
        page.show_local_review("Analysis pending", None, [], [], [], [])
        self.assertFalse(page.manual_tag.isEnabled())
        self.assertFalse(page.manual_add.isEnabled())
        page.close()

    def test_new_tagging_alias_suggestion_uses_canonical_value(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show_local_review("Analysis available", None, [], [], [], [])
        page.set_manual_suggestions([("qipao", "china_dress")])
        label = page.manual_suggestion_model.stringList()[0]
        self.assertIn("qipao", label)
        self.assertIn("china_dress", label)
        page.manual_completer.activated[str].emit(label)
        self.assertEqual(page.manual_tag.text(), "qipao")
        page.close()

    def test_batch_view_formats_entries_filters_and_counts_without_network(self) -> None:
        from booruflow.domain.image_analysis import PublishState
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        entries = [
            {
                "item_id": 1,
                "site": "gelbooru",
                "post_id": "10",
                "additions": ["blue_hair"],
                "removals": ["ball"],
                "reviewed_final_tags": ["blue_hair"],
                "reviewed_at": "2026-08-25T10:00:00+00:00",
                "publish_state": PublishState.PENDING_PUBLISH,
            },
            {
                "item_id": 2,
                "site": None,
                "post_id": None,
                "additions": [],
                "removals": [],
                "reviewed_final_tags": ["solo"],
                "reviewed_at": "2026-08-25T09:00:00+00:00",
                "publish_state": PublishState.REVIEWED,
            },
            {
                "item_id": 3,
                "site": "gelbooru",
                "post_id": "11",
                "additions": [],
                "removals": [],
                "reviewed_final_tags": [],
                "reviewed_at": "2026-08-25T08:00:00+00:00",
                "publish_state": PublishState.PUBLISHED,
            },
            {
                "item_id": 4,
                "site": "gelbooru",
                "post_id": "12",
                "additions": [],
                "removals": [],
                "reviewed_final_tags": [],
                "reviewed_at": "2026-08-25T07:00:00+00:00",
                "publish_state": PublishState.FAILED,
            },
        ]
        requested = []
        page.batch_refresh_requested.connect(lambda: requested.append(True))
        page.show_batch()
        self.assertEqual(requested, [True])
        page.show_batch_entries(entries)
        self.assertEqual(page.batch_table.rowCount(), 4)
        page.batch_table.setColumnWidth(2, 77)
        page.show_batch_entries(entries)
        self.assertEqual(page.batch_table.columnWidth(2), 77)
        self.assertEqual(page.batch_table.item(0, 2).text(), "blue_hair")
        self.assertEqual(page.batch_table.item(1, 2).text(), "—")
        self.assertIn("1 pending", page.batch_counts.text())
        self.assertIn("1 local", page.batch_counts.text())
        page.batch_filter.setCurrentIndex(1)
        self.assertEqual(page.batch_table.rowCount(), 1)
        page.batch_filter.setCurrentIndex(4)
        self.assertEqual(page.batch_table.rowCount(), 1)
        page.batch_table.selectRow(0)
        self.assertFalse(page.batch_open_button.isEnabled())
        self.assertTrue(page.batch_remove_button.isEnabled())
        page.batch_filter.setCurrentIndex(2)
        page.batch_table.selectRow(0)
        self.assertTrue(page.batch_remove_button.isEnabled())
        page.close()

    def test_tagging_suggestion_columns_include_numeric_category_in_requested_order(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        labels = [page.suggestions.horizontalHeaderItem(column).text() for column in range(5)]
        self.assertEqual(
            labels,
            [
                "Tag",
                "Confidence",
                "Origin / match",
                "Category",
                "Decision",
            ],
        )
        page.decision_filter.setCurrentIndex(page.decision_filter.findData("all"))
        page.show_local_review(
            "Analysis available",
            None,
            [],
            [
                {
                    "id": 1,
                    "tag": "solo",
                    "confidence": "0.972",
                    "match": "exact",
                    "category": "0",
                    "decision": "accepted",
                }
            ],
            [],
            [],
        )
        self.assertEqual(
            [page.suggestions.item(0, column).text() for column in range(5)],
            ["solo", "0.972", "exact", "0", "Accepted"],
        )
        page.close()

    def test_tagging_duplicate_existing_wd14_row_uses_existing_token_for_remove(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show_local_review(
            "Analysis available",
            None,
            ["cyborg", "cyberpunk"],
            [
                {
                    "id": "existing:cyborg",
                    "tag": "cyborg",
                    "confidence": "0.800",
                    "decision": "keep",
                    "match": "Existant · également détecté par WD14",
                },
                {
                    "id": "existing:cyberpunk",
                    "tag": "cyberpunk",
                    "confidence": "",
                    "decision": "keep",
                    "match": "Existant",
                },
                {
                    "id": 23,
                    "tag": "robot",
                    "confidence": "0.700",
                    "decision": "unreviewed",
                    "match": "exact",
                },
            ],
            [],
            ["cyborg", "cyberpunk"],
        )
        page.decision_filter.setCurrentIndex(0)
        row = next(
            index
            for index in range(page.suggestions.rowCount())
            if page.suggestions.item(index, 0).text() == "cyborg"
        )
        page.suggestions.clearSelection()
        page.suggestions.selectRow(row)
        captured = []
        page.decision_requested.connect(lambda token, decision: captured.append((token, decision)))
        page._emit_decision("rejected")
        self.assertEqual(captured, [("existing:cyborg", "rejected")])
        page.close()

    def test_tagging_selects_first_then_advances_in_visible_order(self) -> None:
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show()
        QApplication.processEvents()
        rows = [
            {
                "id": oid,
                "tag": tag,
                "confidence": confidence,
                "decision": "unreviewed",
                "match": "exact",
            }
            for oid, tag, confidence in ((30, "c", "0.3"), (10, "a", "0.9"), (20, "b", "0.6"))
        ]
        page.show_local_review("Analysis available", None, [], rows, [], [])
        self.assertEqual(page._selected_observation_id(), 10)
        page._emit_decision("accepted")
        page.show_local_review("Analysis available", None, [], [rows[0], rows[2]], [], [])
        self.assertEqual(page._selected_observation_id(), 20)
        page._emit_decision("rejected")
        page.show_local_review("Analysis available", None, [], [rows[0]], [], [])
        self.assertEqual(page._selected_observation_id(), 30)
        page._emit_decision("accepted")
        page.show_local_review("Analysis available", None, [], [], [], [])
        self.assertIsNone(page._selected_observation_id())
        page.close()

    def test_tagging_suggestion_sorting_and_decision_filters(self) -> None:
        from PySide6.QtCore import Qt

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        rows = [
            {
                "id": 1,
                "tag": "zeta",
                "confidence": "0.100",
                "decision": "accepted",
                "match": "mapping → z",
            },
            {
                "id": 2,
                "tag": "Alpha",
                "confidence": "0.950",
                "decision": "unreviewed",
                "match": "introuvable localement",
            },
            {
                "id": 3,
                "tag": "beta",
                "confidence": "0.800",
                "decision": "rejected",
                "match": "déjà présent",
            },
            {
                "id": 4,
                "tag": "gamma",
                "confidence": "0.700",
                "decision": "unreviewed",
                "match": "exact",
            },
        ]
        page.show_local_review("Analysis available", None, [], rows, [], [])
        self.assertEqual(page.decision_filter.currentData(), "unreviewed")
        self.assertEqual([page.suggestions.item(r, 5).text() for r in range(2)], ["2", "4"])

        page.decision_filter.setCurrentIndex(0)
        cases = (
            (0, ["Alpha", "beta", "gamma", "zeta"]),
            (1, ["0.100", "0.700", "0.800", "0.950"]),
            (2, ["exact", "mapping → z", "déjà présent", "introuvable localement"]),
            (4, ["To review", "To review", "Accepted", "Rejected"]),
        )
        for column, expected in cases:
            page.suggestions.sortItems(column, Qt.SortOrder.AscendingOrder)
            self.assertEqual(
                [page.suggestions.item(r, column).text() for r in range(4)],
                expected,
            )
        for index, expected_count in ((1, 2), (2, 1), (3, 1)):
            page.decision_filter.setCurrentIndex(index)
            self.assertEqual(page.suggestions.rowCount(), expected_count)
        page.close()

    def test_tagging_existing_pending_is_promoted_but_failed_is_not_requeued(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import AnalysisState
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        controller = TaggingController(self.catalog(), page, dict, lambda *_args, **_kwargs: None)
        items = {
            "8": SimpleNamespace(
                id=8, state=AnalysisState.PENDING, cached_path=None, last_error=None
            ),
            "9": SimpleNamespace(
                id=9, state=AnalysisState.FAILED, cached_path=None, last_error="boom"
            ),
        }
        added = []
        requested = []
        repository = SimpleNamespace(
            item_by_remote_source=lambda _site, post_id: items.get(post_id),
            item_queue_visible=lambda _item_id: False,
            request_analysis=lambda item_id, priority: requested.append((item_id, priority)),
            source_tags=lambda _item_id: (),
            observations=lambda _item_id: [],
        )
        prepare = MagicMock()
        ensure = MagicMock()
        controller.image_analysis = SimpleNamespace(
            repository=repository,
            add_remote_ids=lambda *_args, **_kwargs: added.append(True),
            _start_source_preparation=prepare,
            ensure_worker_available=ensure,
            settings={},
        )
        page._select_post({"id": 8, "tags": "solo"})
        self.assertIn("pending", page.analysis_state.text())
        self.assertFalse(page.analyze_button.isEnabled())
        self.assertEqual(requested, [(8, 100)])
        prepare.assert_called_once_with()
        ensure.assert_called_once_with("interactive Tagging request")
        page._select_post({"id": 9, "tags": "solo"})
        self.assertIn("boom", page.analysis_state.text())
        self.assertEqual(page.analyze_button.text(), "Retry")
        self.assertTrue(page.analyze_button.isEnabled())
        self.assertEqual(added, [])
        self.assertEqual(requested, [(8, 100)])
        page.close()

    def test_tagging_promotes_targeted_cache_row_without_creating_a_duplicate(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtTest import QSignalSpy

        from booruflow.domain.image_analysis import AnalysisItem, InputKind, SourceReference
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as directory:
            repository = ImageAnalysisRepository(Path(directory) / "analysis.sqlite")
            item_id = repository.add_item(
                AnalysisItem(
                    SourceReference(
                        InputKind.GELBOORU_POST, site="gelbooru", post_id="88"
                    )
                ),
                request_analysis=False,
            )
            page = TaggingPage(self.catalog(), {})
            prepare = MagicMock()
            ensure = MagicMock()
            controller = TaggingController(self.catalog(), page, dict, lambda *_args: None)
            controller.image_analysis = SimpleNamespace(
                repository=repository,
                settings={},
                _start_source_preparation=prepare,
                ensure_worker_available=ensure,
            )
            messages = QSignalSpy(page.page_status.message_changed)

            controller.select_post(88, {"id": 88, "tags": "solo"})
            controller.select_post(88, {"id": 88, "tags": "solo"})

            row = repository.connection.execute(
                "SELECT analysis_requested,priority FROM analysis_items WHERE id=?",
                (item_id,),
            ).fetchone()
            count = repository.connection.execute(
                "SELECT COUNT(*) FROM analysis_items WHERE source_site='gelbooru' "
                "AND source_post_id='88'"
            ).fetchone()[0]
            self.assertEqual(tuple(row), (1, 100))
            self.assertEqual(count, 1)
            self.assertGreaterEqual(ensure.call_count, 1)
            claimed = repository.claim_next()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.id, item_id)
            self.assertTrue(
                any(
                    "Analyzing image" in messages.at(index)[1]
                    for index in range(messages.count())
                )
            )
            page.close()
            repository.close()

    def test_tagging_analysis_failure_uses_status_bar_and_technical_log(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        logs = []
        controller = TaggingController(self.catalog(), page, dict, logs.append)
        controller.current_post_id = 88
        page._displayed_post_id = 88
        page.mode_stack.setCurrentWidget(page.review)
        controller.refresh_local_review = MagicMock()
        messages = QSignalSpy(page.page_status.message_changed)
        states = QSignalSpy(page.page_status.state_changed)
        progress_cleared = QSignalSpy(page.page_status.progress_cleared)

        controller._image_analysis_state_changed("failed", "WD14 technical detail")

        self.assertEqual(states.at(states.count() - 1), ["tagging", "ready"])
        self.assertEqual(progress_cleared.at(0), ["tagging", False])
        self.assertEqual(messages.at(messages.count() - 1)[1], "Analysis failed — see log")
        self.assertTrue(any("WD14 technical detail" in entry for entry in logs))
        controller.refresh_local_review.assert_called_once_with()
        page.close()

    def test_late_tagging_analysis_poll_ignores_unavailable_page(self) -> None:
        from types import SimpleNamespace

        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        controller = TaggingController(self.catalog(), page, dict, lambda *_args: None)
        controller.current_post_id = 88
        controller.image_analysis = SimpleNamespace(
            repository=SimpleNamespace(
                item_by_remote_source=MagicMock(
                    side_effect=AssertionError("late callback must not touch the repository")
                )
            )
        )
        controller._page_available = lambda: False

        controller._poll_current()

        self.assertIsNone(controller.current_post_id)
        controller.image_analysis.repository.item_by_remote_source.assert_not_called()
        page.close()

    def test_legacy_page_has_no_obsolete_pool_panel(self) -> None:
        from booruflow.presentation.pyside6.tagging_legacy_page import TaggingLegacyPage

        page = TaggingLegacyPage(self.catalog(), {})
        self.assertFalse(hasattr(page, "pool_table"))
        page.close()

    def test_space_saves_batch_snapshot_and_advances_without_browser_or_network(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import (
            AnalysisItem,
            AnalysisState,
            InputKind,
            ObservationSource,
            SourceReference,
            SourceTag,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            repository = ImageAnalysisRepository(Path(temporary) / "state.sqlite")
            first = repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="101"),
                    state=AnalysisState.READY_FOR_REVIEW,
                ),
                (
                    SourceTag("ball", ObservationSource.GELBOORU, "general"),
                    SourceTag("foo", ObservationSource.GELBOORU, "general"),
                ),
            )
            second = repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="202"),
                    state=AnalysisState.READY_FOR_REVIEW,
                ),
                (SourceTag("solo", ObservationSource.GELBOORU, "general"),),
            )
            repository.add_to_tagging_pool([first, second], "test")
            opened = []
            browser = SimpleNamespace(open=lambda url: opened.append(url))
            page = TaggingPage(self.catalog(), {}, browser)
            controller = TaggingController(
                self.catalog(), page, dict, lambda *_args, **_kwargs: None
            )
            controller.image_analysis = SimpleNamespace(repository=repository, settings={})
            controller.refresh_pool()
            page._select_post({"id": 101, "tags": "ball foo"})
            repository.set_existing_tag_decision(first, "ball", "remove")

            page._copy_and_open()

            entry = repository.batch_entry(first)
            self.assertEqual(entry["original_tags"], ["ball", "foo"])
            self.assertEqual(entry["removals"], ["ball"])
            self.assertEqual(entry["reviewed_final_tags"], ["foo"])
            self.assertEqual(entry["publish_state"].value, "pending_publish")
            self.assertEqual(repository.get_item(first).state, AnalysisState.REVIEWED)
            self.assertEqual(page.current_post_id, 202)
            self.assertEqual(opened, [])

            page._copy_and_open()
            self.assertEqual(len(repository.list_batch_entries()), 2)
            self.assertIn("pool finished", page.analysis_state.text())

            page._select_post({"id": 101, "tags": "ball foo"})
            repository.set_existing_tag_decision(first, "ball", "keep")
            page._copy_and_open()
            self.assertEqual(len(repository.list_batch_entries()), 2)
            self.assertEqual(repository.batch_entry(first)["removals"], [])
            self.assertEqual(repository.batch_entry(first)["reviewed_final_tags"], ["ball", "foo"])
            self.assertEqual(opened, [])
            page.close()
            repository.close()

    def test_batch_actions_review_remove_and_open_use_persisted_entries_only(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import (
            AnalysisItem,
            InputKind,
            ObservationSource,
            PublishState,
            SourceReference,
            SourceTag,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            repository = ImageAnalysisRepository(Path(temporary) / "state.sqlite")
            remote = repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="121")
                ),
                (SourceTag("solo", ObservationSource.GELBOORU, "general"),),
            )
            local = repository.add_item(
                AnalysisItem(SourceReference(InputKind.LOCAL_FILE, original_path=Path("local.png")))
            )
            for item_id, original in ((remote, ["solo"]), (local, ["chair"])):
                repository.save_review_batch_entry(
                    item_id,
                    original_tags=original,
                    additions=[],
                    removals=[],
                    reviewed_final_tags=original,
                )
            opened = []
            page = TaggingPage(self.catalog(), {}, SimpleNamespace(open=opened.append))
            controller = TaggingController(
                self.catalog(), page, dict, lambda *_args, **_kwargs: None
            )
            controller.image_analysis = SimpleNamespace(repository=repository, settings={})

            page.show_batch()
            self.assertEqual(page.batch_table.rowCount(), 2)
            remote_row = next(
                row
                for row in range(page.batch_table.rowCount())
                if page.batch_table.item(row, 6).text() == str(remote)
            )
            page.batch_table.selectRow(remote_row)
            page.batch_review_button.click()
            self.assertEqual(page.current_post_id, 121)
            self.assertIsNotNone(repository.batch_entry(remote))
            page.show_batch()
            remote_row = next(
                row
                for row in range(page.batch_table.rowCount())
                if page.batch_table.item(row, 6).text() == str(remote)
            )
            page.batch_table.selectRow(remote_row)
            page.batch_open_button.click()
            self.assertEqual(opened, ["https://gelbooru.com/index.php?page=post&s=view&id=121"])

            local_row = next(
                row
                for row in range(page.batch_table.rowCount())
                if page.batch_table.item(row, 6).text() == str(local)
            )
            page.batch_table.clearSelection()
            page.batch_table.selectRow(local_row)
            self.assertFalse(page.batch_open_button.isEnabled())
            page.batch_review_button.click()
            self.assertIs(page.mode_stack.currentWidget(), page.review)
            self.assertEqual(page.review_title.text(), f"Local file #{local}")
            self.assertIsNotNone(repository.batch_entry(local))
            page.show_batch()
            local_row = next(
                row
                for row in range(page.batch_table.rowCount())
                if page.batch_table.item(row, 6).text() == str(local)
            )
            page.batch_table.selectRow(local_row)
            page.batch_remove_button.click()
            self.assertIsNone(repository.batch_entry(local))
            self.assertIsNotNone(repository.get_item(local))
            self.assertIsNotNone(repository.batch_entry(remote))
            self.assertEqual(opened, ["https://gelbooru.com/index.php?page=post&s=view&id=121"])
            repository.update_publish_state(remote, PublishState.PUBLISHED)
            page.show_batch()
            published_row = next(
                row
                for row in range(page.batch_table.rowCount())
                if page.batch_table.item(row, 6).text() == str(remote)
            )
            page.batch_table.selectRow(published_row)
            self.assertTrue(page.batch_remove_button.isEnabled())
            page.batch_remove_button.click()
            self.assertNotIn(
                remote,
                {int(entry["item_id"]) for entry in repository.list_batch_entries()},
            )
            self.assertEqual(
                repository.batch_entry(remote)["publish_state"], PublishState.PUBLISHED
            )
            self.assertEqual(opened, ["https://gelbooru.com/index.php?page=post&s=view&id=121"])
            page.close()
            repository.close()

    def test_space_uses_manual_selection_and_persisted_checks_when_advancing(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import (
            AnalysisItem,
            AnalysisState,
            InputKind,
            ObservationSource,
            SourceReference,
            SourceTag,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        with tempfile.TemporaryDirectory() as temporary:
            repository = ImageAnalysisRepository(Path(temporary) / "state.sqlite")
            item_ids = []
            posts = []
            for post_id in (101, 202, 303):
                item_ids.append(
                    repository.add_item(
                        AnalysisItem(
                            SourceReference(
                                InputKind.GELBOORU_POST,
                                site="gelbooru",
                                post_id=str(post_id),
                            ),
                            state=AnalysisState.READY_FOR_REVIEW,
                        ),
                        (SourceTag("solo", ObservationSource.GELBOORU, "general"),),
                    )
                )
                posts.append({"id": post_id, "tags": "solo", "priority": "low"})
            repository.add_to_tagging_pool(item_ids, "test")
            page = TaggingPage(self.catalog(), {"tagging_hide_queued_results": False})
            controller = TaggingController(
                self.catalog(), page, dict, lambda *_args, **_kwargs: None
            )
            controller.image_analysis = SimpleNamespace(repository=repository, settings={})
            page.show_results(posts)

            page._select_post(posts[0])
            page._copy_and_open()
            self.assertIsNotNone(repository.batch_entry(item_ids[0]))
            self.assertEqual(page.current_post_id, 202)
            self.assertIn("✓", page.result_buttons[101].text())

            page._select_post(posts[2])
            page._copy_and_open()
            self.assertIsNotNone(repository.batch_entry(item_ids[2]))
            self.assertIsNone(repository.batch_entry(item_ids[1]))
            self.assertEqual(page.current_post_id, 202)
            self.assertNotEqual(page.current_post_id, 101)

            page.show_results(posts)
            controller.refresh_batch()
            self.assertIn("✓", page.result_buttons[101].text())
            self.assertIn("✓", page.result_buttons[303].text())
            page.close()
            repository.close()

    def test_tagging_space_never_opens_browser_or_replaces_clipboard(self) -> None:
        from PySide6.QtCore import QCoreApplication, QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.current_post_id = 42
        page.show()
        QApplication.processEvents()
        page.show_local_review("Analysis available", None, [], [], [], [])
        QApplication.clipboard().setText("unchanged")
        with patch(
            "booruflow.presentation.pyside6.tagging_legacy_page.QDesktopServices.openUrl"
        ) as opened:
            page.suggestions.setFocus()
            QTest.keyClick(page.suggestions, Qt.Key.Key_Space)
            opened.assert_not_called()
            repeated = QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Space,
                Qt.KeyboardModifier.NoModifier,
                " ",
                True,
                2,
            )
            QCoreApplication.sendEvent(page.suggestions, repeated)
            opened.assert_not_called()
        self.assertEqual(QApplication.clipboard().text(), "unchanged")
        page.query.setFocus()
        page.query.clear()
        with patch(
            "booruflow.presentation.pyside6.tagging_legacy_page.QDesktopServices.openUrl"
        ) as opened:
            QTest.keyClick(page.query, Qt.Key.Key_Space)
            self.assertEqual(page.query.text(), " ")
            opened.assert_not_called()
        page.close()

    def test_second_tagging_result_enters_pending_then_ready_review(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtTest import QSignalSpy

        from booruflow.domain.image_analysis import AnalysisState
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        logs = []
        controller = TaggingController(self.catalog(), page, dict, logs.append)
        items = {
            "1": SimpleNamespace(
                id=1,
                state=AnalysisState.REVIEWED,
                cached_path=None,
                last_error=None,
            )
        }
        repository = SimpleNamespace(
            item_by_remote_source=lambda _site, post_id: items.get(post_id),
            item_queue_visible=lambda _item_id: False,
            source_tags=lambda _item_id: (),
            observations=lambda _item_id: [],
        )

        def add_remote(_site, post_ids, **_kwargs):
            items[post_ids[0]] = SimpleNamespace(
                id=2,
                state=AnalysisState.PENDING,
                cached_path=None,
                last_error=None,
            )
            return [2]

        controller.image_analysis = SimpleNamespace(
            repository=repository,
            add_remote_ids=add_remote,
            settings={},
        )
        status_messages = QSignalSpy(page.page_status.message_changed)
        page._select_post({"id": 1, "tags": "solo"})
        self.assertIn("cache reused", page.analysis_state.text())
        with patch("booruflow.presentation.pyside6.tagging_legacy_page.QDesktopServices.openUrl"):
            page._copy_and_open()
        page._select_post({"id": 2, "tags": "solo"})
        self.assertIn("Analysis pending", page.analysis_state.text())
        items["2"].state = AnalysisState.READY_FOR_REVIEW
        controller._poll_current()
        self.assertEqual(page.analysis_state.text(), "Analysis available")
        self.assertTrue(
            any(
                status_messages.at(index)[1] == "Analysis complete"
                for index in range(status_messages.count())
            )
        )
        joined = "\n".join(logs)
        self.assertIn("Local analysis requested", joined)
        self.assertIn("No existing item found", joined)
        self.assertIn("ready_for_review", joined)
        page.close()

    def test_wd14_completion_only_refreshes_the_still_displayed_post(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtTest import QSignalSpy

        from booruflow.domain.image_analysis import AnalysisState
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        posts = [
            {"id": post_id, "tags": "solo", "priority": "low", "tag_count": 1}
            for post_id in (1, 2)
        ]
        items = {
            str(post_id): SimpleNamespace(
                id=post_id, state=AnalysisState.PENDING,
                cached_path=None, last_error=None,
            )
            for post_id in (1, 2)
        }
        page = TaggingPage(self.catalog(), {"tagging_hide_queued_results": False})
        page.show_results(posts)
        controller = TaggingController(self.catalog(), page, dict, lambda *_args: None)
        controller.image_analysis = SimpleNamespace(repository=SimpleNamespace(
            item_by_remote_source=lambda _site, post_id: items.get(post_id)
        ))
        controller.analyze = MagicMock()
        rendered = []

        def render_current():
            rendered.append(controller.current_post_id)
            page.show_local_review(
                items[str(controller.current_post_id)].state.value,
                None, [], [], [], [],
            )

        controller.refresh_local_review = render_current
        page._select_post(posts[0])
        self.assertIs(page.mode_stack.currentWidget(), page.review)
        page._back_from_review()
        before = len(rendered)
        status_messages = QSignalSpy(page.page_status.message_changed)
        items["1"].state = AnalysisState.READY_FOR_REVIEW
        controller._poll_current()
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        self.assertEqual(len(rendered), before)
        self.assertTrue(any(
            status_messages.at(index)[1] == "Analysis complete"
            for index in range(status_messages.count())
        ))

        page._select_post(posts[0])
        items["1"].state = AnalysisState.PROCESSING
        controller._poll_current()
        items["1"].state = AnalysisState.READY_FOR_REVIEW
        controller._poll_current()
        self.assertIs(page.mode_stack.currentWidget(), page.review)
        self.assertEqual(rendered[-1], 1)
        self.assertEqual(page.analysis_state.text(), "ready_for_review")

        page._select_post(posts[1])
        before = len(rendered)
        items["1"].state = AnalysisState.REVIEWED
        controller._poll_current()
        self.assertEqual(len(rendered), before)
        self.assertEqual(page._displayed_post_id, 2)
        self.assertIs(page.mode_stack.currentWidget(), page.review)
        page.close()

    def test_legacy_tagging_surfaces_image_analysis_startup_timeout(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import AnalysisState
        from booruflow.presentation.pyside6.tagging_legacy_controller import TaggingLegacyController
        from booruflow.presentation.pyside6.tagging_legacy_page import TaggingLegacyPage

        page = TaggingLegacyPage(self.catalog(), {})
        controller = TaggingLegacyController(self.catalog(), page, dict, lambda _line: None)
        item = SimpleNamespace(
            id=2,
            state=AnalysisState.PENDING,
            cached_path=None,
            last_error=None,
        )
        repository = SimpleNamespace(
            item_by_remote_source=lambda _site, _post_id: item,
            source_tags=lambda _item_id: (),
            observations=lambda _item_id: [],
        )
        controller.image_analysis = SimpleNamespace(
            repository=repository,
            settings={},
            worker_startup_state="startup_timeout",
            worker_startup_detail="ImageAnalysis startup timeout after 30 s",
        )
        page._select_post({"id": 4511, "tags": "solo"})
        self.assertEqual(controller.current_post_id, 4511)
        controller._image_analysis_state_changed(
            "startup_timeout", controller.image_analysis.worker_startup_detail
        )
        self.assertIn("ImageAnalysis startup timed out", page.analysis_state.text())
        self.assertTrue(page.analyze_button.isEnabled())
        page.close()

    def test_tagging_search_and_review_are_mutually_exclusive_and_restore_scroll(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.resize(1280, 720)
        page.show()
        QApplication.processEvents()
        posts = [
            {"id": value, "tag_count": 4, "priority": "critical", "tags": "solo"}
            for value in range(1, 14)
        ]
        page.show_results(posts)
        QApplication.processEvents()
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        self.assertTrue(page.search_view.isVisible())
        self.assertFalse(page.review.isVisible())
        page.results_scroll.verticalScrollBar().setValue(80)
        page._open_result(3)
        QApplication.processEvents()
        self.assertIs(page.mode_stack.currentWidget(), page.review)
        self.assertFalse(page.search_view.isVisible())
        self.assertTrue(page.review.isVisible())
        self.assertIn("Post 4 / 13", page.result_counter.text())
        page.next_button.click()
        self.assertEqual(page.current_post_id, 5)
        page.previous_button.click()
        self.assertEqual(page.current_post_id, 4)
        page.suggestions.setFocus()
        QTest.keyClick(page.suggestions, Qt.Key.Key_Escape)
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        page._open_result(3)
        page.show_search()
        QApplication.processEvents()
        self.assertEqual(page.results_scroll.verticalScrollBar().value(), page._search_scroll_value)
        self.assertEqual(len(page.result_posts), 13)
        page.close()

    def test_tagging_space_without_controller_never_uses_legacy_browser_flow(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        posts = [
            {"id": value, "tag_count": 4, "priority": "critical", "tags": "solo"}
            for value in (10, 20)
        ]
        page.show_results(posts)
        page._open_result(0)
        page.show_local_review("Analysis available", None, [], [], ["chair"], ["chair"])
        with patch(
            "booruflow.presentation.pyside6.tagging_legacy_page.QDesktopServices.openUrl"
        ) as opened:
            page.suggestions.setFocus()
            QTest.keyClick(page.suggestions, Qt.Key.Key_Space)
            opened.assert_not_called()
        self.assertIs(page.mode_stack.currentWidget(), page.review)
        self.assertEqual(page.current_post_id, 10)
        self.assertEqual(page.processed_in_session, set())
        page.close()

    def test_legacy_video_error_exposes_a_clickable_current_post_link(self) -> None:
        from types import SimpleNamespace

        from booruflow.presentation.pyside6.tagging_legacy_page import TaggingLegacyPage

        opened: list[str] = []
        page = TaggingLegacyPage(self.catalog(), {}, SimpleNamespace(open=opened.append))
        page.show()
        page._select_post({"id": 14772833, "tags": "solo"})
        page._video_error()
        self.assertIn("Video playback is unavailable", page.analysis_state.text())
        self.assertEqual(page.video_error_link.text(), "Open post #14772833")
        self.assertTrue(page.video_error_link.isVisible())
        page.video_error_link.click()
        self.assertEqual(opened, ["https://gelbooru.com/index.php?page=post&s=view&id=14772833"])
        page.close()

    def test_legacy_video_error_link_is_replaced_when_the_post_changes(self) -> None:
        from types import SimpleNamespace

        from booruflow.presentation.pyside6.tagging_legacy_page import TaggingLegacyPage

        opened: list[str] = []
        page = TaggingLegacyPage(self.catalog(), {}, SimpleNamespace(open=opened.append))
        page.show()
        page._select_post({"id": 14772833, "tags": "solo"})
        page._video_error()
        page._select_post({"id": 42, "tags": "solo"})
        self.assertFalse(page.video_error_link.isVisible())
        page._video_error()
        self.assertEqual(page.video_error_link.text(), "Open post #42")
        page.video_error_link.click()
        self.assertEqual(opened, ["https://gelbooru.com/index.php?page=post&s=view&id=42"])
        page.close()

    def test_tagging_review_action_bar_stays_inside_1280_by_720(self) -> None:
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show()
        page._select_post({"id": 42, "tags": "solo blue_hair"})
        page.show_local_review("Analysis available", None, ["solo"], [], [], [])
        for width, height in ((1280, 720), (1600, 900), (1920, 1080)):
            page.resize(width, height)
            QApplication.processEvents()
            bottom = page.action_bar.mapTo(page, page.action_bar.rect().bottomLeft()).y()
            self.assertTrue(page.action_bar.isVisible())
            self.assertGreater(page.action_bar.height(), 0)
            self.assertLessEqual(bottom, page.rect().bottom())
            self.assertEqual(page.mode_stack.currentWidget(), page.review)
            for button in (
                page.analyze_button,
                page.accept_button,
                page.reject_button,
                page.copy_open_button,
                page.open_button,
            ):
                self.assertGreater(button.width(), 0, button.text())
                self.assertGreater(button.height(), 0, button.text())
        page.close()

    def test_tagging_non_analyzed_action_becomes_ready_with_first_suggestion_selected(self) -> None:
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show()
        QApplication.processEvents()
        requested = []
        page.analyze_requested.connect(requested.append)
        page._select_post({"id": 147, "tags": "solo"})
        page.show_local_review("Non analysée", None, ["solo"], [], [], [])
        self.assertTrue(page.analyze_button.isVisible())
        self.assertTrue(page.analyze_button.isEnabled())
        self.assertFalse(page.accept_button.isEnabled())
        self.assertFalse(page.reject_button.isEnabled())
        page.analyze_button.click()
        self.assertEqual(requested, [147])
        page.set_analysis_request_state("Analysis pending…", True)
        self.assertFalse(page.analyze_button.isEnabled())
        page.show_local_review(
            "Analysis available",
            None,
            ["solo"],
            [
                {
                    "id": 9,
                    "tag": "chair",
                    "confidence": "0.9",
                    "decision": "unreviewed",
                    "match": "exact",
                }
            ],
            ["chair"],
            ["chair"],
        )
        self.assertEqual(page._selected_observation_id(), 9)
        self.assertTrue(page.accept_button.isEnabled())
        self.assertTrue(page.reject_button.isEnabled())
        self.assertTrue(page.map_button.isEnabled())
        self.assertTrue(page.copy_button.isEnabled())
        self.assertTrue(page.copy_open_button.isEnabled())
        self.assertTrue(page.open_button.isEnabled())
        page.close()

    def test_tag_browser_sorts_post_counts_as_numbers(self) -> None:
        from PySide6.QtCore import Qt

        from booruflow.infrastructure.tag_browser import TagRow
        from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage

        page = TagBrowserPage(self.catalog())
        page._show_rows(
            [
                TagRow(1, "small", 91, 0, 0),
                TagRow(2, "large", 7359, 0, 0),
                TagRow(3, "middle", 842, 0, 0),
            ]
        )
        page.table.sortItems(2, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            [page.table.item(row, 2).data(Qt.ItemDataRole.DisplayRole) for row in range(3)],
            [7359, 842, 91],
        )
        page.close()

    def test_checked_taxonomy_branch_is_sent_to_review(self) -> None:
        from PySide6.QtCore import Qt

        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"Animals": {"cat": {}, "dog": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        captured = []
        page.review_tags_requested.connect(captured.append)
        page.tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        page._send_to_review()
        self.assertEqual(set(captured[0]), {"cat", "dog"})
        page.close()

    def test_legacy_empty_leaf_is_marked_but_keeps_its_real_tag(self) -> None:
        from booruflow.presentation.pyside6.organization_page import ROLE_TAG, OrganizationPage

        document = {"boards": {"gelbooru": {"Characters": {"battlecruiser": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        branch = page.tree.topLevelItem(0)
        page.tree.expandItem(branch)
        self.app.processEvents()
        leaf = branch.child(0)
        self.assertEqual(leaf.text(0), "battlecruiser *")
        self.assertEqual(leaf.data(0, ROLE_TAG), "battlecruiser")
        page.close()

    def test_search_result_opens_a_tag_stored_in_legacy_tags_list(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"Animals": {"__tags__": ["cat"]}}}}
        page = OrganizationPage(self.catalog(), document)
        page.search.setText("cat")
        page._search()
        self.assertEqual(page.results.count(), 1)
        page._open_result(page.results.item(0))
        self.assertEqual(page.details_title.text(), "cat")
        page.close()

    def test_tag_list_adds_real_children_to_selected_node(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"Medical": {"injury": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        medical = page.tree.topLevelItem(0)
        page.tree.expandItem(medical)
        self.app.processEvents()
        injury = medical.child(0)
        with patch(
            "booruflow.presentation.pyside6.organization_page.QInputDialog.getMultiLineText",
            return_value=("wound\nbruise, scar", True),
        ):
            page._add_tags_to_item(injury)
        node = document["boards"]["gelbooru"]["Medical"]["injury"]
        self.assertEqual(node["__tag__"], "injury")
        self.assertEqual(set(node) - {"__tag__"}, {"wound", "bruise", "scar"})
        self.assertEqual(node["wound"]["__tag__"], "wound")
        page.close()

    def test_reload_preserves_expanded_branches_and_parent_selection_after_delete(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"A": {"B": {"leaf": {}}}}}}
        page = OrganizationPage(self.catalog(), document)
        branch_a = page.tree.topLevelItem(0)
        page._populate(branch_a)
        branch_a.setExpanded(True)
        branch_b = branch_a.child(0)
        page._populate(branch_b)
        branch_b.setExpanded(True)
        page.tree.setCurrentItem(branch_b.child(0))
        with patch(
            "booruflow.presentation.pyside6.organization_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            page._delete()
        restored_a = page.tree.topLevelItem(0)
        restored_b = restored_a.child(0)
        self.assertTrue(restored_a.isExpanded())
        self.assertTrue(restored_b.isExpanded())
        self.assertEqual(page.tree.currentItem().text(0).removesuffix(" *"), "B")
        page.close()

    def test_rename_preserves_expansion_under_the_renamed_branch(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"A": {"B": {"C": {"leaf": {}}}}}}}
        page = OrganizationPage(self.catalog(), document)
        branch_a = page.tree.topLevelItem(0)
        page._populate(branch_a)
        branch_a.setExpanded(True)
        branch_b = branch_a.child(0)
        page._populate(branch_b)
        branch_b.setExpanded(True)
        page.tree.setCurrentItem(branch_b)
        with patch(
            "booruflow.presentation.pyside6.organization_page.QInputDialog.getText",
            return_value=("Renamed", True),
        ):
            page._rename()
        restored_a = page.tree.topLevelItem(0)
        restored = restored_a.child(0)
        self.assertEqual(restored.text(0), "Renamed")
        self.assertTrue(restored_a.isExpanded())
        self.assertTrue(restored.isExpanded())
        page.close()

    def test_numeric_wiki_id_is_attached_to_any_node_and_used_for_details(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {
            "boards": {
                "gelbooru": {"Characters": {"List_of_Azur_Lane_characters": {"Azur_Lane": {}}}}
            }
        }
        page = OrganizationPage(self.catalog(), document)
        characters = page.tree.topLevelItem(0)
        page._populate(characters)
        node = characters.child(0)
        with patch(
            "booruflow.presentation.pyside6.organization_page.QInputDialog.getText",
            return_value=("26107", True),
        ):
            page._set_wiki(node)
        expected = "https://gelbooru.com/index.php?page=wiki&s=view&id=26107"
        self.assertEqual(
            document["metadata"]["gelbooru"]["List_of_Azur_Lane_characters"]["wiki_url"], expected
        )
        captured = []
        page.tag_details_requested.connect(lambda *values: captured.append(values))
        restored = page._select_path(("Characters", "List_of_Azur_Lane_characters"))
        page._inspect_item(restored)
        self.assertEqual(captured[-1], ("gelbooru", "List_of_Azur_Lane_characters", expected))
        page.close()

    def test_recurring_tags_can_be_checked_for_review_and_opened(self) -> None:
        from PySide6.QtCore import Qt, QUrl

        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"Medical": {"injury": {}, "wound": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        page.details_title.setText("injury")
        page.show_tag_details(
            {
                "tag": "injury",
                "definition": "Related injuries",
                "online": True,
                "sample_size": 100,
                "recurring": [{"tag": "wound", "count": 42}],
                "wiki_tags": ["wound"],
                "samples": [],
            }
        )
        captured = []
        page.review_tags_requested.connect(captured.append)
        page.recurring.item(0).setCheckState(Qt.CheckState.Checked)
        page._send_recurring_to_review()
        self.assertEqual(captured, [("wound",)])
        page._open_recurring(page.recurring.item(0))
        self.assertEqual(page.details_title.text(), "wound")
        page.details_title.setText("injury")
        page._definition_link_clicked(QUrl("booruflow-tag:wound"))
        self.assertEqual(page.details_title.text(), "wound")
        page.close()

    def test_review_engine_summary_is_captured_for_visible_status(self) -> None:
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.presentation.pyside6.main_window import MainWindow

        window = MainWindow(ApplicationCapabilities(ToolAvailability(False)), self.catalog())
        window.navigate_to_key("review")
        for _ in range(8):
            self.app.processEvents()
        window.review_coordinator.output("768 artistes e621 retenus.\n")
        self.assertEqual(window.review_coordinator.output_state.retained, 768)
        self.assertIn("768", window.review_coordinator.output_state.summary[0])
        window.close()

    def test_e621_candidate_file_is_exposed_as_review_results(self) -> None:
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.application.review import ReviewRequest
        from booruflow.domain import ToolAvailability
        from booruflow.presentation.pyside6.main_window import MainWindow

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "artists" / "e621"
            target.mkdir(parents=True)
            (target / "artistes_candidats_uniques.txt").write_text(
                "artist_one\nartist_two\n", encoding="utf-8"
            )
            request = ReviewRequest(
                ("rating:safe",),
                ("e621",),
                "artists",
                10,
                1,
                0,
                0,
                1,
                False,
                True,
                root / "gel.db",
                root / "e621.db",
                root,
                None,
            )
            window = MainWindow(ApplicationCapabilities(ToolAvailability(False)), self.catalog())
            window.navigate_to_key("review")
            for _ in range(8):
                self.app.processEvents()
            self.assertEqual(
                window.review_coordinator.result_entries(request),
                [("e621", "artist_one"), ("e621", "artist_two")],
            )
            window.close()
