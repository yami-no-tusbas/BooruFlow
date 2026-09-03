import tempfile
import unittest
from array import array
from pathlib import Path

from booruflow.application.embedding import EmbeddingIndexService, EmbeddingResult
from booruflow.application.similar_artists import ArtistProfileService, centroid
from booruflow.domain.image_analysis import (
    AnalysisItem,
    ColorStatistics,
    DecisionState,
    InputKind,
    ObservationSource,
    SourceReference,
    SourceTag,
)
from booruflow.domain.similar_artists import ArtistIdentity, EmbeddingSpace
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService


class SimilarArtistsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.repository = ImageAnalysisRepository(self.root / "analysis.sqlite")

    def tearDown(self):
        self.repository.close(); self.temporary.cleanup()

    def item(self, post_id: int, artist: str, vector, *, stats=None) -> int:
        path = self.root / f"{post_id}.png"; path.write_bytes(b"image")
        item = AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=str(post_id)),
            cached_path=path, content_sha256=f"{post_id:064x}", mime_type="image/png",
            width=10, height=10,
        )
        item_id = self.repository.add_item(
            item, (SourceTag("solo", ObservationSource.GELBOORU),), (artist,),
        )
        run_id = self.repository.begin_model_run(item_id, "author_id_embedding", "Author_ID", "v1", "cfg")
        values = array("f", vector)
        self.repository.save_embedding(item_id, run_id, values.tobytes(), len(values))
        if stats:
            classic = self.repository.begin_model_run(item_id, "classic", "classic", "1", "cfg")
            self.repository.save_statistics(item_id, classic, ColorStatistics((), *stats))
        return item_id

    def test_centroid_is_normalized_and_dispersion_is_separate(self):
        center, metrics = centroid(((2, 0), (0, 3)))
        self.assertAlmostEqual(sum(value * value for value in center), 1.0)
        self.assertAlmostEqual(metrics.mean_similarity, 2 ** -0.5)
        self.assertGreaterEqual(metrics.distance_variance, 0)

    def test_profile_aggregates_embeddings_palette_and_separated_tags(self):
        first = self.item(1, "butterchalk", (1, 0), stats=(.2, .4, .1, .8, .5))
        self.item(2, "butterchalk", (.8, .2), stats=(.4, .6, .3, .4, .7))
        wd14 = self.repository.begin_model_run(first, "onnx", "wd14", "1", "cfg")
        self.repository.save_tag_predictions(first, wd14, [
            ("blue_hair", "general", .9), ("red_hair", "general", .8),
        ])
        observations = self.repository.observations(first)
        self.repository.decide_observation(observations[0][0], DecisionState.ACCEPTED)
        self.repository.decide_observation(observations[1][0], DecisionState.REJECTED)
        service = ArtistProfileService(self.repository)
        profile = service.build_profile(ArtistIdentity("gelbooru", "butterchalk"))
        self.assertEqual(profile.image_count, 2)
        self.assertEqual(profile.confidence_level, "low")
        self.assertAlmostEqual(profile.palette["mean_saturation"].mean, .3)
        self.assertEqual(profile.source_tag_frequency["solo"], 2)
        self.assertEqual(profile.accepted_wd14_frequency, {"blue_hair": 1})
        self.assertNotIn("red_hair", profile.accepted_wd14_frequency)

    def test_profile_cache_multi_provenance_and_invalidation(self):
        item_id = self.item(1, "artist", (1, 0))
        self.repository.reuse_item(
            item_id, SourceReference(InputKind.E621_POST, site="e621", post_id="99"),
            artist_tags=("other_identity",),
        )
        service = ArtistProfileService(self.repository)
        artist = ArtistIdentity("gelbooru", "artist")
        first = service.build_profile(artist)
        second = service.build_profile(artist)
        self.assertEqual(first.dependency_hash, second.dependency_hash)
        self.assertEqual(first.image_count, 1)
        run_id = self.repository.begin_model_run(item_id, "openclip", "ViT-B-32", "v1", "cfg")
        vector = array("f", (0, 1)); self.repository.save_embedding(
            item_id, run_id, vector.tobytes(), 2
        )
        self.assertIsNone(service.get_profile(artist))
        rebuilt = service.build_profile(artist)
        self.assertIn("openclip", {value.space.backend for value in rebuilt.embeddings.values()})

    def test_image_and_artist_ranking_are_deterministic_and_exclude_self(self):
        query = self.item(1, "A", (1, 0))
        self.item(2, "A", (.9, .1)); self.item(3, "B", (0, 1)); self.item(4, "C", (-1, 0))
        service = ArtistProfileService(self.repository); service.build_all()
        image_rows = service.rank_artists_for_image(query, "author_id_embedding")
        self.assertEqual(image_rows[0].artist.tag, "A")
        self.assertIsNotNone(image_rows[0].best_image_similarity)
        artist_rows = service.rank_artists_for_artist(
            ArtistIdentity("gelbooru", "A"), "author_id_embedding"
        )
        self.assertNotIn("A", [row.artist.tag for row in artist_rows])
        self.assertEqual([row.artist.tag for row in artist_rows], ["B", "C"])

    def test_index_service_skips_cached_identity_and_versions_configuration(self):
        item_id = self.item(1, "A", (1, 0))
        class Backend:
            def __init__(self, config):
                self.space = EmbeddingSpace("fake", "model", "1", config, 2)
                self.calls = 0
            def prepare(self): pass
            def close(self): pass
            def encode(self, _path):
                self.calls += 1; return EmbeddingResult(self.space, (0.0, 1.0))
        index = EmbeddingIndexService(self.repository)
        first = Backend("a"); report = index.encode_missing(first, [item_id, item_id])
        self.assertEqual((report["images_eligible"], first.calls), (1, 1))
        self.assertEqual(index.encode_missing(first, [item_id])["embeddings_missing"], 0)
        changed = Backend("b"); index.encode_missing(changed, [item_id])
        self.assertEqual(changed.calls, 1)
        self.assertEqual(index.encode_missing(first, [item_id])["embeddings_missing"], 0)

    def test_multi_image_query_profile_centroid_coherence_and_rebuild(self):
        first=self.item(1,"A",(1,0));second=self.item(2,"A",(0,1));self.item(3,"B",(.8,.2))
        service=ArtistProfileService(self.repository);service.build_all();query=service.build_query_profile([first,second,first])
        self.assertEqual(query.item_ids,(first,second));self.assertEqual(query.image_count,2);self.assertEqual(query.quality_level,"low")
        space=service._space_query(query,"author_id_embedding");self.assertAlmostEqual(space.centroid[0],2**-.5);self.assertAlmostEqual(space.dispersion.mean_similarity,2**-.5)
        rebuilt=service.build_query_profile([first]);self.assertEqual(rebuilt.quality_level,"very_low");self.assertNotEqual(rebuilt.embeddings,query.embeddings)
        rows=service.rank_artists_for_query(query,"author_id_embedding");self.assertEqual(rows[0].artist.tag,"A")

    def test_gallery_deduplicates_canonical_item_and_excludes_references_before_top_k(self):
        first=self.item(1,"A",(1,0));second=self.item(2,"A",(.8,.2));query=self.item(3,"Q",(1,0))
        self.repository.reuse_item(first,SourceReference(InputKind.E621_POST,site="e621",post_id="99"),artist_tags=("other",))
        service=ArtistProfileService(self.repository);service.build_all();profile=service.build_query_profile([query])
        rows=service.closest_candidate_images(ArtistIdentity("gelbooru","A"),"author_id_embedding",query_profile=profile,exclude_item_ids=(first,))
        self.assertEqual([row["item_id"] for row in rows],[second]);self.assertEqual(len(rows[0]["provenances"]),1)
        all_rows=service.closest_candidate_images(ArtistIdentity("gelbooru","A"),"author_id_embedding",query_profile=profile)
        self.assertEqual([row["item_id"] for row in all_rows],[first,second]);self.assertEqual(len(all_rows[0]["provenances"]),2)

    def test_local_bulk_assignment_rebuilds_unique_profile_and_persists(self):
        item_ids=[]
        for index in range(10):
            path=self.root/f"local-{index}.png";path.write_bytes(b"image")
            item_id=self.repository.add_item(AnalysisItem(SourceReference(InputKind.LOCAL_FILE,original_path=path),cached_path=path,content_sha256=f"{index+100:064x}",mime_type="image/png",width=10,height=10));item_ids.append(item_id)
            run=self.repository.begin_model_run(item_id,"author_id_embedding","Author_ID","v1","cfg");vector=array("f",(1,index/100));self.repository.save_embedding(item_id,run,vector.tobytes(),2)
        service=ArtistProfileService(self.repository);report=service.assign_items_to_artist(item_ids+item_ids[:3],ArtistIdentity("local","test_artist"))
        self.assertEqual(report,{"associated":10,"image_count":10});self.assertEqual(len(self.repository.artist_profile_inputs("local","test_artist")),10)
        self.repository.close();self.repository=ImageAnalysisRepository(self.root/"analysis.sqlite");reloaded=ArtistProfileService(self.repository).get_profile(ArtistIdentity("local","test_artist"));self.assertIsNotNone(reloaded);self.assertEqual(reloaded.image_count,10)

    def test_structured_remote_metadata_repairs_artist_but_folder_name_never_does(self):
        remote=self.repository.add_item(AnalysisItem(SourceReference(InputKind.GELBOORU_POST,site="gelbooru",post_id="42"),cached_path=self.root/"artist_b.png",content_sha256="a"*64,mime_type="image/png",width=1,height=1))
        local_path=self.root/"artist_b"/"local.png";local_path.parent.mkdir();local_path.write_bytes(b"x");local=self.repository.add_item(AnalysisItem(SourceReference(InputKind.LOCAL_FILE,original_path=local_path),cached_path=local_path,content_sha256="b"*64,mime_type="image/png",width=1,height=1))
        self.repository.cache_post_metadata("gelbooru","42","https://example/42.png",(),("artist_a",));report=self.repository.repair_structured_artist_associations()
        self.assertEqual(report["gelbooru"],1);self.assertEqual(self.repository.artist_tags(remote),("artist_a",));self.assertEqual(self.repository.artist_tags(local),())
        diagnostics=ArtistProfileService(self.repository).unassigned_artist_report();row=next(value for value in diagnostics["items"] if value["item_id"]==local);self.assertEqual(row["reason"],"local_only_no_artist_metadata")

    def test_historical_filename_repair_reuses_embeddings_and_builds_profile(self):
        md5="9fed177a4599ae9acba6bc6ba6423c1a";ids=[]
        for index in range(10):
            path=self.root/f"historic_artist - {index+1} - general - {md5}.jpg";path.write_bytes(b"historic")
            item_id=self.repository.add_item(AnalysisItem(SourceReference(InputKind.LOCAL_FILE,original_path=path),cached_path=path,content_sha256=f"{index+300:064x}",mime_type="image/jpeg",width=1,height=1));ids.append(item_id);run=self.repository.begin_model_run(item_id,"author_id_embedding","Author_ID","v1","cfg");vector=array("f",(1,index/100));self.repository.save_embedding(item_id,run,vector.tobytes(),2)
        before=self.repository.embedding_counts();sources=ImageSourceService(self.repository,self.root/"cache");preview=sources.preview_filename_repairs();report=sources.repair_filename_metadata(preview);profiles=ArtistProfileService(self.repository).build_all();profile=ArtistProfileService(self.repository).get_profile(ArtistIdentity("local","historic_artist"))
        self.assertEqual((preview["compatible"],report["associated"],profile.image_count),(10,10,10));self.assertEqual(self.repository.embedding_counts(),before);self.assertEqual(profiles["profiles_failed"],0)


if __name__ == "__main__": unittest.main()
