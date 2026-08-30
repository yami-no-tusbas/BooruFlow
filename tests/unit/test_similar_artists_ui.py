import os
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from booruflow.domain.similar_artists import ArtistIdentity
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.image_analysis_page import ScaledImageLabel
from booruflow.presentation.pyside6.similar_artists_page import (
    ImageGalleryDialog,
    SimilarArtistsPage,
)

LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


class SimilarArtistsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def page(self, language="fr"):
        return SimilarArtistsPage(LanguageCatalog(LANGUAGES, language))

    def test_primary_labels_follow_catalog_and_refresh_at_runtime(self):
        page = self.page("en")
        self.assertEqual(page.subtitle.text(), "Find artists from visual references")
        self.assertEqual(page.library_index.text(), "Index folders")
        self.assertNotIn("Références", page.references_group.title())
        page.show_references([{"item_id": 1, "path": "C:/missing.png"}], "usable", "good")
        self.assertEqual(page.references_group.title(), "1. References — 1 unique image")
        page.catalog.set_language("fr")
        page.retranslate()
        self.assertEqual(
            page.subtitle.text(), "Trouver des artistes à partir de références visuelles"
        )
        self.assertEqual(page.library_index.text(), "Indexer des dossiers")
        self.assertIn("Artistes similaires", page.results_title.text())
        page.close()

    def test_dynamic_counts_and_confidence_use_selected_language(self):
        page = self.page("en")
        artist = ArtistIdentity("gelbooru", "candidate")
        page.set_artists(
            [{"artist": artist, "image_count": 1, "profiled": False, "confidence": "unbuilt"}]
        )
        self.assertIn("1 image", page.artist_list.item(0).text())
        self.assertIn("profile not built", page.artist_list.item(0).text())
        page.minimum_images.setValue(1)
        page.show_results(
            "query",
            [
                {
                    "artist": artist,
                    "author_id": 0.5,
                    "openclip": 0.4,
                    "palette_distance": None,
                    "image_count": 1,
                    "confidence": "very_low",
                    "coherence": 0.7,
                }
            ],
        )
        self.assertEqual(page.results.item(0, 7).text(), "very low")
        self.assertEqual(page.results_title.text(), "3. Similar artists — 1 result")
        page.close()

    def test_artist_filter_results_axes_and_weak_profile_filter(self):
        page = self.page()
        a = ArtistIdentity("gelbooru", "butterchalk")
        b = ArtistIdentity("e621", "andava")
        page.set_artists(
            [
                {"artist": a, "image_count": 75, "profiled": True, "confidence": "established"},
                {"artist": b, "image_count": 1, "profiled": False, "confidence": "unbuilt"},
            ]
        )
        page.artist_search.setText("butter")
        self.assertEqual(page.artist_list.count(), 1)
        self.assertIn("75 images", page.artist_list.item(0).text())
        page.show_results(
            "Butterchalk",
            [
                {
                    "artist": a,
                    "author_id": 0.9,
                    "openclip": 0.7,
                    "palette_distance": 0.1,
                    "image_count": 75,
                    "confidence": "established",
                    "coherence": 0.8,
                },
                {
                    "artist": b,
                    "author_id": 0.8,
                    "openclip": 0.6,
                    "palette_distance": 0.2,
                    "image_count": 1,
                    "confidence": "very_low",
                    "coherence": 1.0,
                },
            ],
        )
        self.assertEqual(page.results.rowCount(), 1)
        page.minimum_images.setValue(1)
        self.assertEqual(page.results.rowCount(), 2)
        self.assertEqual(page.results.item(1, 7).text(), "très faible")
        self.assertEqual(page.results.item(0, 3).text(), "0.9000")
        page.close()

    def test_backend_switch_and_result_actions_emit_identity(self):
        page = self.page()
        artist = ArtistIdentity("gelbooru", "candidate")
        page.show_results(
            "query",
            [
                {
                    "artist": artist,
                    "author_id": 0.5,
                    "openclip": 0.4,
                    "palette_distance": None,
                    "image_count": 4,
                    "confidence": "low",
                    "coherence": 0.7,
                }
            ],
        )
        galleries = []
        comparisons = []
        page.gallery_requested.connect(galleries.append)
        page.compare_requested.connect(comparisons.append)
        page.gallery.click()
        page.compare.click()
        self.assertEqual(galleries, [artist])
        self.assertEqual(comparisons, [artist])
        page.backend.setCurrentIndex(1)
        self.assertEqual(page.backend.currentData(), "openclip")
        page.close()

    def test_missing_backend_is_disabled_without_hiding_the_other(self):
        page = self.page()
        page.set_backend_available("author_id_embedding", False, "modèle absent")
        self.assertFalse(page.backend.model().item(0).isEnabled())
        self.assertTrue(page.backend.model().item(1).isEnabled())
        self.assertEqual(page.backend.currentData(), "openclip")
        page.close()

    def test_image_identification_and_drag_drop(self):
        page = self.page()
        artist = ArtistIdentity("gelbooru", "A")
        from booruflow.domain.similar_artists import ArtistRanking

        one = ArtistRanking(artist, 0.91, 0.9, 0.93, 75, 0.8)
        two = ArtistRanking(ArtistIdentity("gelbooru", "B"), 0.73, 0.7, 0.8, 12, 0.7)
        page.show_results("image", [], {"top1": one, "top2": two, "margin": 0.18})
        self.assertIn("marge 0.1800", page.identification.text())
        dropped = []
        page.references_added.connect(dropped.append)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile("C:/tmp/query.png")])
        event = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        page.dropEvent(event)
        self.assertEqual(dropped, [["C:/tmp/query.png"]])
        page.close()

    def test_drop_emits_every_file_in_stable_url_order(self):
        page = self.page()
        captured = []
        page.references_added.connect(captured.append)
        mime = QMimeData()
        paths = [f"C:/Références/style {index}.png" for index in range(5)]
        mime.setUrls([QUrl.fromLocalFile(value) for value in paths])
        event = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        page.dropEvent(event)
        self.assertEqual(captured, [paths])
        page.close()

    def test_folder_scan_is_recursive_unicode_stable_and_deduplicated(self):
        from PIL import Image

        from booruflow.presentation.pyside6.image_analysis_controller import DroppedSourceScanWorker

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Références été"
            (root / "Sous dossier").mkdir(parents=True)
            first = root / "a image.png"
            second = root / "Sous dossier" / "é image.jpg"
            Image.new("RGB", (2, 2)).save(first)
            Image.new("RGB", (2, 2)).save(second)
            (root / "invalid.txt").write_text("no", encoding="utf-8")
            captured = []
            worker = DroppedSourceScanWorker([root, first])
            worker.completed.connect(lambda paths, ignored: captured.append((paths, ignored)))
            worker.run()
            self.assertEqual(captured[0][0], [str(first), str(second)])
            self.assertEqual(captured[0][1], 2)

    def test_bulk_prepare_parses_twenty_structured_filenames(self):
        from PIL import Image

        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.similar_artists_controller import ReferencePrepareWorker

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "Artists (Gelbooru)"
            folder.mkdir()
            md5 = "9fed177a4599ae9acba6bc6ba6423c1a"
            paths = []
            for index in range(20):
                path = folder / f"bulk_artist - {index + 1} - general - {md5}.png"
                Image.new("RGB", (index + 2, 2), (index, 0, 0)).save(path)
                paths.append(str(path))
            database = root / "state.sqlite"
            worker = ReferencePrepareWorker(database, root / "cache", paths)
            captured = []
            worker.completed.connect(lambda *args: captured.append(args))
            worker.run()
            with ImageAnalysisRepository(database) as repository:
                self.assertEqual(len(captured[0][0]), 20)
                self.assertEqual(
                    len(repository.artist_profile_inputs("gelbooru", "bulk_artist")), 20
                )
                self.assertEqual(
                    repository.connection.execute(
                        "SELECT COUNT(*) FROM local_filename_metadata"
                    ).fetchone()[0],
                    20,
                )

    def test_gallery_reuses_scaled_preview_and_zoom(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "one.png"
            Image.new("RGB", (20, 10), "blue").save(path)
            dialog = ImageGalleryDialog(
                "Gallery", [{"item_id": 1, "path": str(path), "score": 0.8}]
            )
            dialog.show()
            self.app.processEvents()
            preview = dialog.findChild(ScaledImageLabel)
            self.assertEqual(dialog.windowTitle(), "Gallery")
            self.assertFalse(preview._source.isNull())
            dialog.close()

    def test_gallery_double_click_prefers_existing_local_then_remote(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary) / "local.png"
            local.write_bytes(b"x")
            with patch(
                "booruflow.presentation.pyside6.similar_artists_page.QDesktopServices.openUrl"
            ) as opened:
                ImageGalleryDialog._open_entry(
                    {
                        "provenances": [
                            {"local_path": str(local), "site": None, "post_id": None},
                            {"local_path": None, "site": "gelbooru", "post_id": "42"},
                        ]
                    }
                )
                self.assertTrue(opened.call_args.args[0].isLocalFile())
                ImageGalleryDialog._open_entry(
                    {
                        "provenances": [
                            {"local_path": str(local) + "-missing", "site": None, "post_id": None},
                            {"local_path": None, "site": "e621", "post_id": "99"},
                        ]
                    }
                )
                self.assertEqual(opened.call_args.args[0].toString(), "https://e621.net/posts/99")
                ImageGalleryDialog._open_entry({"provenances": []})
                self.assertEqual(opened.call_count, 2)

    def test_page_remains_usable_at_supported_sizes(self):
        page = self.page()
        page.show()
        for width, height in ((1280, 720), (1600, 900), (1920, 1080)):
            page.resize(width, height)
            self.app.processEvents()
            self.assertTrue(page.results.isVisible())
            self.assertGreater(page.results.height(), 0)
            self.assertFalse(page.update_profiles.isVisible())
        page.close()

    def test_advanced_options_are_collapsed_and_internal_id_is_diagnostic_only(self):
        page = self.page()
        page.show()
        self.app.processEvents()
        self.assertFalse(page.item_id.isVisible())
        self.assertIn("interne", page.item_label.text())
        self.assertIn("diagnostic", page.item_id.toolTip())
        # The options group is the only checkable group on the page.
        options = next(
            group for group in page.findChildren(type(page.references_group)) if group.isCheckable()
        )
        options.setChecked(True)
        self.app.processEvents()
        self.assertTrue(page.item_id.isVisible())
        page.close()

    def test_reference_double_click_is_distinct_from_simple_selection(self):
        page = self.page()
        page.show_references([{"item_id": 7, "path": "C:/missing.png"}], "1 utilisable", "very_low")
        activated = []
        page.reference_activated.connect(activated.append)
        item = page.references.item(0)
        page.references.setCurrentItem(item)
        self.assertEqual(activated, [])
        page.references.itemDoubleClicked.emit(item)
        self.assertEqual(activated, [7])
        page.close()

    def test_each_result_has_actions_and_double_click_opens_gallery(self):
        page = self.page()
        artist = ArtistIdentity("gelbooru", "candidate")
        page.show_results(
            "Requête : 2 références visuelles",
            [
                {
                    "artist": artist,
                    "author_id": 0.5,
                    "openclip": 0.4,
                    "palette_distance": None,
                    "image_count": 4,
                    "confidence": "low",
                    "coherence": 0.7,
                }
            ],
        )
        galleries = []
        comparisons = []
        page.gallery_requested.connect(galleries.append)
        page.compare_requested.connect(comparisons.append)
        page.results.cellWidget(0, 9).click()
        page.results.cellWidget(0, 10).click()
        page.results.itemDoubleClicked.emit(page.results.item(0, 1))
        self.assertEqual(galleries, [artist, artist])
        self.assertEqual(comparisons, [artist])
        self.assertIn("Artistes similaires", page.results_title.text())
        page.close()


if __name__ == "__main__":
    unittest.main()
