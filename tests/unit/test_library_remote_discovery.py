import io
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from booruflow.application.library_indexer import LibraryIndexService
from booruflow.application.remote_discovery import RemoteDiscoveryService, dominant_source_artists
from booruflow.application.similar_artists import ArtistProfileService
from booruflow.domain.image_analysis import (
    AnalysisItem,
    InputKind,
    ObservationSource,
    SourceReference,
    SourceTag,
)
from booruflow.domain.similar_artists import ArtistIdentity
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    ImageSourceService,
    LocalImportResult,
    NormalizedPost,
)
from booruflow.infrastructure.remote_pixels import RemotePixelSession


def png(color:int)->bytes:
    output=io.BytesIO();Image.new("RGB",(3,3),(color,0,0)).save(output,"PNG");return output.getvalue()


class LibraryAndRemoteDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name);self.database=self.root/"state.sqlite";self.repository=ImageAnalysisRepository(self.database)
    def tearDown(self):self.repository.close();self.temporary.cleanup()

    def test_25000_streamed_entries_resume_after_8000_without_reencoding(self):
        encoded=[]
        class Sources:
            def add_local_with_result(_self,path):return LocalImportResult(int(path.stem)+1,"new")
        service=LibraryIndexService(self.repository,self.root/"cache",batch_size=127,encoder=lambda ids:encoded.extend(ids));service.sources=Sources();job=service.create_job([self.root]);entries=[self.root/f"{index:05d}.jpg" for index in range(25000)]
        with patch("booruflow.application.library_indexer.iter_library_images",lambda _roots:iter(entries)):
            first=service.run(job,max_files=8000);first_encoded=set(encoded);self.assertEqual((first["state"],first["scanned"]),("paused",8000));second=service.run(job)
        self.assertEqual((second["state"],second["scanned"]),("completed",25000));self.assertEqual(len(encoded),25000);self.assertEqual(len(first_encoded),8000);self.assertEqual(len(set(encoded)),25000)

    def test_library_reports_same_path_local_local_and_local_remote_separately(self):
        first=self.root/"first.png";copy=self.root/"copy.png";remote_copy=self.root/"remote-copy.png";first.write_bytes(png(10));copy.write_bytes(first.read_bytes());remote_copy.write_bytes(png(11));sources=ImageSourceService(self.repository,self.root/"cache");sources.add_local(first)
        remote_sha=__import__("hashlib").sha256(remote_copy.read_bytes()).hexdigest();self.repository.add_item(AnalysisItem(SourceReference(InputKind.GELBOORU_POST,site="gelbooru",post_id="42"),cached_path=remote_copy,content_sha256=remote_sha,mime_type="image/png",width=3,height=3))
        service=LibraryIndexService(self.repository,self.root/"cache",batch_size=1);job=service.create_job([first,copy,remote_copy]);report=service.run(job)
        self.assertEqual(report["duplicates"],3)
        self.assertEqual(self.repository.library_match_counts(job),{"local_local":1,"local_remote":1,"same_path":1})
        groups=self.repository.local_binary_duplicates();self.assertEqual(len(groups),1);self.assertEqual(set(groups[0]["paths"]),{str(first.resolve()),str(copy.resolve())})

    def test_multi_artist_filename_is_one_item_one_embedding_and_two_profiles(self):
        folder=self.root/"Artists (Gelbooru)";folder.mkdir();path=folder/"alphonse_(white_datura) & muk_(monsieur) - 6663430 - questionable - a6297ef37ae97de7bd9c1d000eabf46a.jpg";Image.new("RGB",(3,3),(1,2,3)).save(path);sources=ImageSourceService(self.repository,self.root/"cache");item_id=sources.add_local(path);self.assertEqual(set(self.repository.artist_tags(item_id)),{"alphonse_(white_datura)","muk_(monsieur)"});self.assertEqual(sources.add_local(path),item_id);run=self.repository.begin_model_run(item_id,"author_id_embedding","Author_ID","1","cfg");self.repository.save_embedding(item_id,run,array("f",(1,0)).tobytes(),2);service=ArtistProfileService(self.repository);left=service.build_profile(ArtistIdentity("gelbooru","alphonse_(white_datura)"));right=service.build_profile(ArtistIdentity("gelbooru","muk_(monsieur)"));self.assertEqual((left.image_count,right.image_count),(1,1));self.assertEqual(self.repository.connection.execute("SELECT COUNT(*) FROM analysis_items").fetchone()[0],1);self.assertEqual(self.repository.connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0],1);self.assertEqual(self.repository.connection.execute("SELECT COUNT(*) FROM item_artists").fetchone()[0],2)

    def test_source_exclusion_handles_single_collaboration_and_minor_collaborator(self):
        ids=[]
        for index in range(20):
            path=self.root/f"source-{index}.png";path.write_bytes(png(index));item=self.repository.add_item(AnalysisItem(SourceReference(InputKind.LOCAL_FILE,original_path=path),cached_path=path,content_sha256=f"{index+30:064x}",mime_type="image/png",width=3,height=3));self.repository.assign_artist([item],"gelbooru","alphonse_(white_datura)");ids.append(item)
        self.repository.assign_artist([ids[0]],"gelbooru","muk_(monsieur)");self.assertEqual(dominant_source_artists(self.repository,[ids[0]]),{("gelbooru","alphonse_(white_datura)"),("gelbooru","muk_(monsieur)")});self.assertEqual(dominant_source_artists(self.repository,ids),{("gelbooru","alphonse_(white_datura)")})

    def test_gelbooru_artist_categories_are_resolved_in_batch(self):
        calls=[]
        def fetch(url,_headers):
            calls.append(url)
            if "s=tag" in url:return {"tag":[{"name":"artist_one","type":1},{"name":"artist_two","type":1},{"name":"scenery","type":0}]}
            return {"post":[{"id":1,"file_url":"https://example/1.png","tags":"artist_one artist_two scenery"}]}
        from booruflow.infrastructure.image_sources import GelbooruPostProvider
        posts=GelbooruPostProvider(json_fetcher=fetch).search_posts(["scenery"],10);self.assertEqual(posts[0].artist_tags,("artist_one","artist_two"));self.assertEqual(sum("s=tag" in value for value in calls),1)

    def test_remote_tags_discover_but_embeddings_rank_c_b_d_and_survive_pixel_cleanup(self):
        query_path=self.root/"query.png";query_path.write_bytes(png(1));query=self.repository.add_item(AnalysisItem(SourceReference(InputKind.LOCAL_FILE,original_path=query_path),cached_path=query_path,content_sha256="1"*64,mime_type="image/png",width=3,height=3),(SourceTag("distinctive",ObservationSource.GELBOORU,"general"),),())
        run=self.repository.begin_model_run(query,"author_id_embedding","Author_ID","v1","cfg");self.repository.save_embedding(query,run,array("f",(1,0)).tobytes(),2);profile=ArtistProfileService(self.repository).build_query_profile([query]);posts={name:NormalizedPost("gelbooru",str(index),f"https://example/{index}.png",(SourceTag(name,ObservationSource.GELBOORU,"artist"),),(name,)) for index,name in enumerate(("B","C","D"),2)}
        class Provider:
            def discover_candidates(self,tags,limit):self.tags=tags;return ["B","C","D"]
            def sample_artist_posts(self,artist,limit):return [posts[artist]]
            def fetch_post(self,post_id):return next(value for value in posts.values() if value.post_id==post_id)
        provider=Provider();pixels=RemotePixelSession(self.root/"remote",bytes_fetcher=lambda url,_headers:png(int(Path(url).stem)))
        vectors={"B":array("f",(.8,.2)),"C":array("f",(.99,.01)),"D":array("f",(0,1))}
        def encode(ids):
            for item_id in ids:
                artist=self.repository.artist_tags(item_id)[0];run_id=self.repository.begin_model_run(item_id,"author_id_embedding","Author_ID","v1","cfg");self.repository.save_embedding(item_id,run_id,vectors[artist].tobytes(),2)
            return {}
        results=RemoteDiscoveryService(self.repository,pixels,{"gelbooru":provider},encode).discover(profile,"author_id_embedding","quick");self.assertEqual([value.artist.tag for value in results],["C","B","D"]);self.assertEqual(provider.tags,["distinctive"]);self.assertGreater(pixels.close(),0)
        candidate_ids=[int(row[0]) for row in self.repository.connection.execute("SELECT item_id FROM item_artists WHERE artist_tag IN ('B','C','D')")]
        self.assertTrue(candidate_ids);self.assertEqual(self.repository.connection.execute(f"SELECT COUNT(*) FROM analysis_items WHERE id IN ({','.join('?' for _ in candidate_ids)}) AND (analysis_requested=1 OR queue_visible=1)",candidate_ids).fetchone()[0],0)
        self.repository.close();self.repository=ImageAnalysisRepository(self.database);ranking=ArtistProfileService(self.repository).rank_artists_for_query(profile,"author_id_embedding");self.assertEqual([row.artist.tag for row in ranking[:3]],["C","B","D"]);c_item=int(self.repository.connection.execute("SELECT item_id FROM item_artists WHERE artist_tag='C'").fetchone()[0]);second=RemotePixelSession(self.root/"remote",bytes_fetcher=lambda url,_headers:png(int(Path(url).stem)));self.assertTrue(second.ensure(self.repository,c_item,{"gelbooru":provider}).is_file());self.assertEqual(second.close(),1)

    def test_remote_retry_and_invalid_post_do_not_abort_other_artist_images(self):
        query_path=self.root/"retry-query.png";query_path.write_bytes(png(1));query_id=self.repository.add_item(AnalysisItem(SourceReference(InputKind.LOCAL_FILE,original_path=query_path),cached_path=query_path,content_sha256="a"*64,mime_type="image/png",width=3,height=3),(SourceTag("distinctive",ObservationSource.GELBOORU,"general"),),())
        run=self.repository.begin_model_run(query_id,"author_id_embedding","Author_ID","v1","cfg");self.repository.save_embedding(query_id,run,array("f",(1,0)).tobytes(),2);query=ArtistProfileService(self.repository).build_query_profile([query_id])
        valid=NormalizedPost("gelbooru","2","https://example/2.png",(SourceTag("artist","gelbooru","artist"),),("artist",));invalid=NormalizedPost("gelbooru","3","",(),("artist",))
        class Provider:
            calls=0
            def discover_candidates(self,_tags,_limit):
                self.calls+=1
                if self.calls==1:raise RuntimeError("HTTP error 503")
                return ["artist"]
            def sample_artist_posts(self,_artist,_limit):return [invalid,valid]
        def fetch(url,_headers):
            if not url:raise RuntimeError("no image URL")
            return png(2)
        provider=Provider();pixels=RemotePixelSession(self.root/"retry",bytes_fetcher=fetch);progress=[]
        def encode(ids):
            for item_id in ids:
                run_id=self.repository.begin_model_run(item_id,"author_id_embedding","Author_ID","v1","cfg");self.repository.save_embedding(item_id,run_id,array("f",(1,0)).tobytes(),2)
            return {}
        RemoteDiscoveryService(self.repository,pixels,{"gelbooru":provider},encode,retry_delays=(0,)).discover(query,"author_id_embedding","quick",progress=progress.append)
        self.assertGreaterEqual(provider.calls,2);self.assertTrue(any(value.get("phase")=="retry" for value in progress));self.assertTrue(any(value.get("images_ignored",0)==1 for value in progress))

    def test_missing_local_pixels_fall_back_to_remote_and_crash_cache_is_cleaned(self):
        stale=self.root/"remote"/"old";stale.mkdir(parents=True);(stale/"pixel.png").write_bytes(png(4));session=RemotePixelSession(self.root/"remote",bytes_fetcher=lambda _url,_headers:png(5));self.assertEqual(session.cleared_stale_files,1)
        local=self.root/"Artists (Gelbooru)"/"artist - 42 - general - 9fed177a4599ae9acba6bc6ba6423c1a.png";local.parent.mkdir();local.write_bytes(png(6))
        item_id=ImageSourceService(self.repository,self.root/"cache").add_local(local);run=self.repository.begin_model_run(item_id,"author_id_embedding","Author_ID","1","cfg");self.repository.save_embedding(item_id,run,array("f",(1,0)).tobytes(),2);profile=ArtistProfileService(self.repository).build_profile(ArtistIdentity("gelbooru","artist"));local.unlink();self.assertEqual(profile.image_count,1)
        class Provider:
            def fetch_post(self,post_id):return NormalizedPost("gelbooru",post_id,"https://example/recovered.png",(),("artist",))
        recovered=session.ensure(self.repository,item_id,{"gelbooru":Provider()});self.assertTrue(recovered.is_file());self.assertEqual(session.availability(self.repository,item_id),"temporary_remote_available");self.assertEqual(session.close(),1);self.assertFalse(recovered.exists());self.assertIsNotNone(self.repository.embedding(item_id,"author_id_embedding"))

    def test_md5_fallback_and_remote_profile_purge_preview(self):
        md5="9fed177a4599ae9acba6bc6ba6423c1a";path=self.root/"Artists (Gelbooru)"/f"remote_artist - 99 - general - {md5}.png";path.parent.mkdir();path.write_bytes(png(7));item_id=ImageSourceService(self.repository,self.root/"cache").add_local(path);run=self.repository.begin_model_run(item_id,"author_id_embedding","Author_ID","1","cfg");self.repository.save_embedding(item_id,run,array("f",(1,0)).tobytes(),2);ArtistProfileService(self.repository).build_profile(ArtistIdentity("gelbooru","remote_artist"));path.unlink()
        class Provider:
            def fetch_post(self,_post_id):raise RuntimeError("deleted")
            def resolve_post_by_md5(self,value):self.md5=value;return NormalizedPost("gelbooru","100","https://example/md5.png",(),("remote_artist",))
        provider=Provider();session=RemotePixelSession(self.root/"remote",bytes_fetcher=lambda *_args:png(8));self.assertTrue(session.ensure(self.repository,item_id,{"gelbooru":provider}).is_file());self.assertEqual(provider.md5,md5);session.close();self.repository.touch_remote_artist("gelbooru","remote_artist");self.repository.connection.execute("UPDATE remote_artist_state SET last_seen_at='2000-01-01T00:00:00+00:00' WHERE artist_tag='remote_artist'");self.repository.connection.commit();preview=self.repository.preview_remote_profile_purge("2020-01-01T00:00:00+00:00");self.assertEqual(preview["profiles"],0)  # local provenance protects it


if __name__=="__main__":unittest.main()
