import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from booruflow.domain.image_analysis import AnalysisState, DetectedLocalSource
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    E621PostProvider,
    GelbooruPostProvider,
    ImageSourceError,
    ImageSourceService,
    PostNotFoundError,
    inspect_image,
)


def png_bytes(size: tuple[int, int] = (12, 8)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format="PNG")
    return output.getvalue()


class ImageSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.repository = ImageAnalysisRepository(root / "state.sqlite")
        self.service = ImageSourceService(
            self.repository, root / "cache", bytes_fetcher=lambda _url, _headers: png_bytes()
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary.cleanup()

    def test_local_image_hash_dimensions_and_invalid_file(self) -> None:
        path = Path(self.temporary.name) / "valid.png"
        path.write_bytes(png_bytes((13, 7)))
        item_id = self.service.add_local(path)
        item = self.repository.get_item(item_id)
        self.assertEqual((item.width, item.height), (13, 7))
        self.assertEqual(item.content_sha256, inspect_image(path).sha256)
        invalid = Path(self.temporary.name) / "invalid.png"
        invalid.write_text("not an image", encoding="utf-8")
        with self.assertRaisesRegex(ImageSourceError, "invalid or unreadable"):
            self.service.add_local(invalid)

    def test_structured_filename_is_applied_for_local_and_marked_site_imports(self)->None:
        md5="9fed177a4599ae9acba6bc6ba6423c1a";root=Path(self.temporary.name)
        local=root/f"0_0c0ff - 7338857 - general - {md5}.jpg";Image.new("RGB",(4,4),"blue").save(local);local_id=self.service.add_local(local);self.assertEqual(self.repository.artist_tags(local_id),("0_0c0ff",));self.assertEqual(self.repository.artist_associations(local_id)[0]["site"],"local")
        marked=root/"Artists (Gelbooru)";marked.mkdir();remote=marked/f"foo_bar - 123 - explicit - {md5}.png";Image.new("RGB",(5,5),"green").save(remote);remote_id=self.service.add_local(remote);self.assertEqual(self.repository.artist_associations(remote_id)[0]["site"],"gelbooru");self.assertIsNotNone(self.repository.item_by_remote_source("gelbooru","123"))
        metadata=self.repository.connection.execute("SELECT rating,source_md5,state FROM local_filename_metadata WHERE item_id=?",(remote_id,)).fetchone();self.assertEqual(tuple(metadata),("explicit",md5,"applied"))

    def test_remote_metadata_wins_over_conflicting_filename(self)->None:
        md5="9fed177a4599ae9acba6bc6ba6423c1a";payload=[{"id":42,"file_url":"https://img.example/42.png","tags":"artist_b","tag_string_artist":"artist_b"}];item_id=self.service.add_post(GelbooruPostProvider(json_fetcher=lambda *_args:payload),"42")
        local=Path(self.temporary.name)/f"artist_a - 42 - general - {md5}.png";local.write_bytes(png_bytes());self.assertEqual(self.service.add_local(local),item_id);self.assertEqual(self.repository.artist_tags(item_id),("artist_b",));state=self.repository.connection.execute("SELECT state FROM local_filename_metadata WHERE item_id=?",(item_id,)).fetchone()[0];self.assertEqual(state,"conflict")

    def test_same_content_reuses_item_and_keeps_every_local_path(self) -> None:
        first = Path(self.temporary.name) / "A.png"; first.write_bytes(png_bytes())
        second = Path(self.temporary.name) / "B copy.png"; second.write_bytes(first.read_bytes())
        first_result = self.service.add_local_with_result(first)
        run_id = self.repository.begin_model_run(
            first_result.item_id, "onnx", "wd14", "1", "cfg"
        )
        self.repository.complete_model_run(run_id)
        self.repository.add_manual_observation(first_result.item_id, "accepted_once")
        self.repository.transition(first_result.item_id, AnalysisState.PROCESSING)
        self.repository.transition(first_result.item_id, AnalysisState.READY_FOR_REVIEW)
        self.repository.transition(first_result.item_id, AnalysisState.REVIEWED)
        second_result = self.service.add_local_with_result(second)
        repeated = self.service.add_local_with_result(first)
        self.assertEqual(first_result.outcome, "new")
        self.assertEqual(second_result.item_id, first_result.item_id)
        self.assertEqual(second_result.outcome, "known_reviewed")
        self.assertEqual(repeated.item_id, first_result.item_id)
        self.assertEqual(len(self.repository.provenances(first_result.item_id)), 2)
        self.assertEqual(len(self.repository.observations(first_result.item_id)), 1)
        self.assertEqual(self.repository.connection.execute(
            "SELECT COUNT(*) FROM model_runs WHERE item_id=?", (first_result.item_id,)
        ).fetchone()[0], 1)
        self.repository.clean_queue("reviewed")
        self.assertEqual(self.repository.list_items(), [])
        restored = self.service.add_local_with_result(first)
        self.assertEqual((restored.item_id, restored.outcome),
                         (first_result.item_id, "known_reviewed"))
        self.assertTrue(self.repository.item_queue_visible(first_result.item_id))

    def test_same_remote_content_shares_visual_item_but_keeps_site_tags(self) -> None:
        gel = GelbooruPostProvider(json_fetcher=lambda _url, _headers: [{
            "id": 10, "file_url": "https://example/gel.png", "tags": "gel_tag",
            "tag_string_general": "gel_tag",
        }])
        e621 = E621PostProvider(json_fetcher=lambda _url, _headers: {"post": {
            "id": 20, "file": {"url": "https://example/e621.png"},
            "tags": {"general": ["e621_tag"]},
        }})
        gel_id = self.service.add_post(gel, "10")
        e621_id = self.service.add_post(e621, "20")
        self.assertEqual(gel_id, e621_id)
        tags = {(tag.source.value, tag.name) for tag in self.repository.source_tags(gel_id)}
        self.assertEqual(tags, {("gelbooru", "gel_tag"), ("e621", "e621_tag")})
        self.assertEqual(len(self.repository.provenances(gel_id)), 2)

    def test_reimport_preserves_every_existing_workflow_state(self) -> None:
        expected = {
            "pending": "already_queued", "processing": "already_queued",
            "ready_for_review": "already_queued", "reviewed": "known_reviewed",
            "skipped": "known_skipped", "failed": "already_queued",
        }
        for index, (state, outcome) in enumerate(expected.items(), 1):
            path = Path(self.temporary.name) / f"{state}.png"
            Image.new("RGB", (5, 5), (index, index, index)).save(path)
            item_id = self.service.add_local(path)
            if state in {"processing", "ready_for_review", "reviewed"}:
                self.repository.transition(item_id, AnalysisState.PROCESSING)
            if state in {"ready_for_review", "reviewed"}:
                self.repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)
            if state == "reviewed": self.repository.transition(item_id, AnalysisState.REVIEWED)
            elif state == "skipped": self.repository.transition(item_id, AnalysisState.SKIPPED)
            elif state == "failed": self.repository.transition(item_id, AnalysisState.FAILED, "x")
            result = self.service.add_local_with_result(path)
            self.assertEqual((result.item_id, result.outcome), (item_id, outcome))
            self.assertEqual(self.repository.get_item(item_id).state.value, state)

    def test_exif_orientation_changes_reported_dimensions(self) -> None:
        path = Path(self.temporary.name) / "oriented.jpg"
        image = Image.new("RGB", (20, 10), "blue")
        exif = image.getexif(); exif[274] = 6
        image.save(path, exif=exif)
        metadata = inspect_image(path)
        self.assertEqual((metadata.width, metadata.height), (10, 20))

    def test_local_file_missing_or_changed_after_import_is_rejected(self) -> None:
        missing = Path(self.temporary.name) / "missing_after.png"
        missing.write_bytes(png_bytes())
        missing_item = self.repository.get_item(self.service.add_local(missing))
        missing.unlink()
        with self.assertRaisesRegex(ImageSourceError, "missing"):
            self.service.validate_item_file(missing_item)
        changed = Path(self.temporary.name) / "changed.png"
        changed.write_bytes(png_bytes((11, 8)))
        changed_item = self.repository.get_item(self.service.add_local(changed))
        changed.write_bytes(png_bytes((13, 8)))
        with self.assertRaisesRegex(ImageSourceError, "changed after import"):
            self.service.validate_item_file(changed_item)

    def test_animated_image_uses_first_frame(self) -> None:
        path = Path(self.temporary.name) / "animated.gif"
        frames = [Image.new("RGB", (9, 6), color) for color in ("red", "green")]
        frames[0].save(path, save_all=True, append_images=frames[1:], format="GIF")
        self.assertEqual((inspect_image(path).width, inspect_image(path).height), (9, 6))

    def test_gelbooru_post_normalizes_tags_and_artist_provenance(self) -> None:
        payload = [{
            "id": 42, "file_url": "https://img.example/42.png",
            "tags": "artist_name blue_hair", "tag_string_artist": "artist_name",
            "tag_string_general": "blue_hair",
        }]
        provider = GelbooruPostProvider(json_fetcher=lambda _url, _headers: payload)
        item_id = self.service.add_post(provider, "42")
        self.assertEqual(self.repository.artist_tags(item_id), ("artist_name",))
        tags = {tag.name: tag for tag in self.repository.source_tags(item_id)}
        self.assertEqual(tags["artist_name"].source, "gelbooru")
        self.assertEqual(tags["artist_name"].category, "artist")

    def test_e621_post_normalizes_categories_and_reuses_cached_bytes(self) -> None:
        calls = []
        self.service.bytes_fetcher = lambda url, _headers: calls.append(url) or png_bytes()
        payload = {"post": {
            "id": 7, "file": {"url": "https://static.example/same.png"},
            "tags": {"artist": ["maker"], "general": ["tail"]},
        }}
        first_provider = E621PostProvider(json_fetcher=lambda _url, _headers: payload)
        item_id = self.service.add_post(first_provider, "7")
        tags = {tag.name: tag.category for tag in self.repository.source_tags(item_id)}
        self.assertEqual(tags, {"maker": "artist", "tail": "general"})
        self.assertEqual(self.repository.artist_tags(item_id), ("maker",))
        self.assertEqual(len(list((Path(self.temporary.name) / "cache").glob("*.png"))), 1)
        self.assertEqual(len(calls), 1)

    def test_missing_post_and_url_are_readable_errors(self) -> None:
        with self.assertRaises(PostNotFoundError):
            E621PostProvider(json_fetcher=lambda _url, _headers: {}).fetch_post("404")
        with self.assertRaisesRegex(ImageSourceError, "no image URL"):
            GelbooruPostProvider(
                json_fetcher=lambda _url, _headers: [{"id": 9, "tags": "tag"}]
            ).fetch_post("9")

    def test_missing_remote_image_is_a_readable_error(self) -> None:
        self.service.bytes_fetcher = lambda _url, _headers: (_ for _ in ()).throw(
            ImageSourceError("could not download image: HTTP 404")
        )
        provider = E621PostProvider(json_fetcher=lambda _url, _headers: {
            "post": {"id": 8, "file": {"url": "https://static.example/missing.png"},
                     "tags": {"general": ["tag"]}}
        })
        with self.assertRaisesRegex(ImageSourceError, "HTTP 404"):
            self.service.add_post(provider, "8")

    def test_local_enrichment_uses_metadata_cache_without_downloading_image(self) -> None:
        path = Path(self.temporary.name) / "local.png"; path.write_bytes(png_bytes())
        first_id = self.service.add_local(path)
        fetches = []
        provider = GelbooruPostProvider(json_fetcher=lambda _url, _headers: fetches.append(1) or [{
            "id": 5980652, "file_url": "https://remote.example/image.png",
            "tags": "butterchalk sword", "tag_string_artist": "butterchalk",
            "tag_string_general": "sword",
        }])
        detected = DetectedLocalSource("gelbooru", "5980652")
        self.service.enrich_local(first_id, detected, provider)
        second = Path(self.temporary.name) / "copy.png"; second.write_bytes(png_bytes((13, 8)))
        second_id = self.service.add_local(second)
        self.service.enrich_local(second_id, detected, provider)
        self.assertEqual(fetches, [1])
        self.assertEqual(self.repository.artist_tags(second_id), ("butterchalk",))
        self.assertEqual({tag.name for tag in self.repository.source_tags(second_id)}, {
            "butterchalk", "sword",
        })
        self.assertFalse((Path(self.temporary.name) / "cache").exists())

    def test_deleted_post_is_cached_but_local_item_remains_usable(self) -> None:
        path = Path(self.temporary.name) / "local.png"; path.write_bytes(png_bytes())
        item_id = self.service.add_local(path)
        calls = []
        provider = E621PostProvider(
            json_fetcher=lambda _url, _headers: calls.append(1) or {}
        )
        detected = DetectedLocalSource("e621", "404")
        for _attempt in range(2):
            with self.assertRaises(PostNotFoundError):
                self.service.enrich_local(item_id, detected, provider)
        self.assertEqual(calls, [1])
        self.assertIsNotNone(self.service.validate_item_file(self.repository.get_item(item_id)))


if __name__ == "__main__":
    unittest.main()
