import unittest
from pathlib import Path

from booruflow.domain.image_analysis import (
    AnalysisItem,
    AnalysisState,
    DecisionState,
    DetectedLocalSource,
    InputKind,
    ModelIdentity,
    ObjectDetection,
    ObservationSource,
    SourceReference,
    SourceTag,
    TagObservation,
    detect_local_source,
    parse_booru_filename,
)


class ImageAnalysisDomainTests(unittest.TestCase):
    def test_enum_values_are_persistent_storage_values(self) -> None:
        self.assertEqual(InputKind.LOCAL_FILE, "local_file")
        self.assertEqual(AnalysisState.READY_FOR_REVIEW, "ready_for_review")
        self.assertEqual(DecisionState.UNREVIEWED, "unreviewed")
        self.assertEqual(ObservationSource.WD14, "wd14")

    def test_source_reference_enforces_kind_specific_fields(self) -> None:
        SourceReference(InputKind.LOCAL_FILE, original_path=Path("image.png"))
        SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42")
        with self.assertRaises(ValueError):
            SourceReference(InputKind.E621_POST, site="gelbooru", post_id="42")

    def test_item_validates_hash_dimensions_and_transitions(self) -> None:
        item = AnalysisItem(
            SourceReference(InputKind.LOCAL_FILE, original_path=Path("image.png")),
            content_sha256="a" * 64,
            width=20,
            height=10,
        )
        self.assertEqual(item.transition_to(AnalysisState.PROCESSING).state, "processing")
        with self.assertRaises(ValueError):
            item.transition_to(AnalysisState.REVIEWED)
        with self.assertRaises(ValueError):
            AnalysisItem(item.source, content_sha256="not-a-hash")

    def test_source_tags_remain_booru_only(self) -> None:
        SourceTag("artist_name", ObservationSource.GELBOORU, "artist")
        with self.assertRaises(ValueError):
            SourceTag("predicted", ObservationSource.WD14)

    def test_observation_confidence_and_manual_decisions(self) -> None:
        TagObservation("smile", ObservationSource.WD14, 0.8)
        TagObservation("custom", ObservationSource.MANUAL, decision=DecisionState.ACCEPTED)
        with self.assertRaises(ValueError):
            TagObservation("smile", ObservationSource.WD14, 1.1)
        TagObservation("custom", ObservationSource.MANUAL)
        with self.assertRaises(ValueError):
            TagObservation("custom", ObservationSource.MANUAL, confidence=0.5)

    def test_detection_requires_normalized_ordered_box(self) -> None:
        model = ModelIdentity("yolo", "future-model")
        ObjectDetection("hat", 0.9, (0.1, 0.2, 0.8, 0.9), model)
        with self.assertRaises(ValueError):
            ObjectDetection("hat", 0.9, (0.8, 0.2, 0.1, 0.9), model)

    def test_local_source_detection_uses_explicit_marker_and_filename_convention(self) -> None:
        md5="9fed177a4599ae9acba6bc6ba6423c1a"
        cases = {
            Path(rf"D:\Tags (Gelbooru)\été\butterchalk - 5980652 - explicit - {md5}.png"):
                DetectedLocalSource("gelbooru", "5980652"),
            Path(rf"D:\Artists (E621)\r2d2_artist - 123456 - safe - {md5}.jpg"):
                DetectedLocalSource("e621", "123456"),
        }
        for path, expected in cases.items():
            self.assertEqual(detect_local_source(path), expected)
        for path in (
            Path(r"D:\Gelbooru\artist - 123 - safe - hash.png"),
            Path(r"D:\Tags (Gelbooru)\artist 7 - 123 - 456 - hash.png"),
            Path(r"D:\Tags (Gelbooru)\artist - 7 - 5980652 - explicit - hash.png"),
            Path(r"D:\Tags (Gelbooru) (e621)\artist - 123 - safe - hash.png"),
            Path(r"D:\Tags (Gelbooru)\artist_123_safe_hash.png"),
        ):
            self.assertIsNone(detect_local_source(path))

    def test_booru_filename_parser_preserves_artist_and_parses_from_the_right(self)->None:
        md5="9fed177a4599ae9acba6bc6ba6423c1a"
        for extension in ("jpg","jpeg","png","webp"):
            parsed=parse_booru_filename(Path(f"artist-with-name - part - 7338857 - general - {md5}.{extension}"));self.assertEqual((parsed.artist,parsed.post_id,parsed.rating,parsed.source_md5),("artist-with-name - part","7338857","general",md5))
        self.assertEqual(parse_booru_filename(Path(f"0_0c0ff - 7338857 - general - {md5}.jpg")).artist,"0_0c0ff")
        self.assertIsNone(parse_booru_filename(Path("random_image.jpg")))

    def test_two_artist_filename_uses_only_spaced_ampersand(self)->None:
        md5="a6297ef37ae97de7bd9c1d000eabf46a"
        parsed=parse_booru_filename(Path(f"alphonse_(white_datura) & muk_(monsieur) - 6663430 - questionable - {md5}.jpg"));self.assertEqual(parsed.artists,("alphonse_(white_datura)","muk_(monsieur)"))
        spaced=parse_booru_filename(Path(f" first_artist  &  second_artist  - 1 - safe - {md5}.png"));self.assertEqual(spaced.artists,("first_artist","second_artist"))
        bare=parse_booru_filename(Path(f"rock&roll - 1 - safe - {md5}.png"));self.assertEqual(bare.artists,("rock&roll",))


if __name__ == "__main__":
    unittest.main()
