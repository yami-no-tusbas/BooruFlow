"""Qt coordinator for Similar Artists; the page never touches SQLite directly."""

from __future__ import annotations

import importlib.util
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)

from booruflow.application.embedding import EmbeddingIndexService
from booruflow.application.library_indexer import LibraryIndexService
from booruflow.application.remote_discovery import RemoteDiscoveryService, dominant_source_artists
from booruflow.application.similar_artists import ArtistProfileService
from booruflow.domain.similar_artists import ArtistIdentity
from booruflow.infrastructure.classic_image_analysis import ClassicImageAnalyzer
from booruflow.infrastructure.embedding_backends import (
    AuthorIdEmbeddingBackend,
    OpenClipEmbeddingBackend,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    E621PostProvider,
    GelbooruPostProvider,
    ImageSourceService,
    artist_page_url,
    post_page_url,
)
from booruflow.infrastructure.remote_pixels import RemotePixelSession
from booruflow.infrastructure.tag_category_lookup import LocalTagCategoryLookup
from booruflow.presentation.pyside6.image_analysis_controller import DroppedSourceScanWorker
from booruflow.presentation.pyside6.image_analysis_page import ScaledImageLabel
from booruflow.presentation.pyside6.similar_artists_page import ImageGalleryDialog
from booruflow.presentation.pyside6.ui_logging import log_event


class SimilarBuildWorker(QThread):
    completed = Signal(dict); failed = Signal(str)
    progress = Signal(str,int,int)
    def __init__(self,database:Path,root:Path,item_ids:list[int],build_all:bool=False)->None:
        super().__init__();self.database=database;self.root=root;self.item_ids=item_ids;self.build_all=build_all
    def run(self)->None:
        reports={}
        try:
            with ImageAnalysisRepository(self.database) as repository:
                index=EmbeddingIndexService(repository)
                model=self.root/"var"/"experiments"/"models"/"author-id-embedding.onnx"
                backends=[]
                if model.is_file():backends.append(AuthorIdEmbeddingBackend(model,model,"cuda"))
                try:
                    import open_clip  # noqa:F401
                    backends.append(OpenClipEmbeddingBackend(device="cuda"))
                except ImportError:reports["openclip_unavailable"]=True
                total=max(1,len(backends)*len(self.item_ids));done=0
                for backend in backends:
                    base=done
                    reports[backend.space.backend]=index.encode_missing(backend,self.item_ids,lambda current,count,name=backend.space.backend,offset=base:self.progress.emit(name,offset+current,total));done+=len(self.item_ids);self.progress.emit("Embeddings",done,total)
                service=ArtistProfileService(repository)
                reports["profiles"]=service.build_all();self.progress.emit("Profils",total,total)
            self.completed.emit(reports)
        except Exception as exc:  # noqa:BLE001 - thread boundary
            self.failed.emit(str(exc))


class ReferencePrepareWorker(QThread):
    completed=Signal(list,int,int);progress=Signal(int,int);failed=Signal(str)
    def __init__(self,database:Path,cache:Path,paths:list[str])->None:super().__init__();self.database=database;self.cache=cache;self.paths=paths
    def run(self)->None:
        entries=[];duplicates=invalid=0
        try:
            with ImageAnalysisRepository(self.database) as repository:
                sources=ImageSourceService(repository,self.cache);seen=set()
                for index,value in enumerate(self.paths,1):
                    try:
                        result=sources.add_local_with_result(Path(value));item=repository.get_item(result.item_id)
                        if result.item_id in seen:duplicates+=1
                        else:
                            seen.add(result.item_id);entries.append({"item_id":result.item_id,"path":str(item.cached_path)})
                        if result.outcome=="new":repository.suppress_analysis_request(result.item_id)
                    except Exception:invalid+=1  # noqa:BLE001 - isolate invalid reference
                    self.progress.emit(index,len(self.paths))
            self.completed.emit(entries,duplicates,invalid)
        except Exception as exc:self.failed.emit(str(exc))  # noqa:BLE001


class RemoteReferenceWorker(QThread):
    completed=Signal(int,str,list);failed=Signal(str)
    def __init__(self,database:Path,cache:Path,site:str,post_id:str,credentials:dict,tag_database:Path|None=None)->None:super().__init__();self.database=database;self.cache=cache;self.site=site;self.post_id=post_id;self.credentials=credentials;self.tag_database=tag_database
    def run(self)->None:
        try:
            with ImageAnalysisRepository(self.database) as repository:
                existing=repository.item_by_remote_source(self.site,self.post_id);sources=ImageSourceService(repository,self.cache)
                gel=self.credentials.get("gelbooru",{});gel=gel if isinstance(gel,dict) else {}
                lookup=LocalTagCategoryLookup(self.tag_database) if self.tag_database and self.tag_database.is_file() else None
                provider=GelbooruPostProvider(str(gel.get("user_id","")),str(gel.get("api_key","")),category_lookup=lookup) if self.site=="gelbooru" else E621PostProvider()
                item_id=sources.add_post(provider,self.post_id)
                if existing is None:repository.suppress_analysis_request(item_id)
                self.completed.emit(item_id,self.site,list(repository.artist_tags(item_id)))
        except Exception as exc:self.failed.emit(str(exc))  # noqa:BLE001


def _encode_for_similarity(repository:ImageAnalysisRepository,root:Path,item_ids:list[int],progress=None)->dict:
    reports={};classic=ClassicImageAnalyzer();unique_ids=tuple(dict.fromkeys(item_ids))
    for classic_done,item_id in enumerate(unique_ids,1):
        item=repository.get_item(item_id)
        if repository.statistics(item_id) is None and item and item.cached_path and item.cached_path.is_file():
            identity=classic.identity;run=repository.begin_model_run(item_id,identity.backend,identity.name,identity.version,identity.configuration_hash);repository.save_statistics(item_id,run,classic.analyze(item.cached_path))
        if progress:progress("Statistiques",classic_done,len(unique_ids))
    backends=[];model=root/"var"/"experiments"/"models"/"author-id-embedding.onnx"
    if model.is_file():backends.append(AuthorIdEmbeddingBackend(model,model,"cuda"))
    try:
        import open_clip  # noqa:F401
        backends.append(OpenClipEmbeddingBackend(device="cuda"))
    except ImportError:reports["openclip_unavailable"]=True
    index=EmbeddingIndexService(repository)
    for backend in backends:reports[backend.space.backend]=index.encode_missing(backend,item_ids,lambda done,total,name=backend.space.backend:progress(name,done,total) if progress else None)
    return reports


class LibraryIndexWorker(QThread):
    progress=Signal(dict);completed=Signal(dict);failed=Signal(str)
    def __init__(self,database:Path,cache:Path,root:Path,roots:list[Path],job_id:str|None=None)->None:super().__init__();self.database=database;self.cache=cache;self.root=root;self.roots=roots;self.job_id=job_id;self.pause_requested=False;self.cancel_requested=False
    def run(self)->None:
        try:
            with ImageAnalysisRepository(self.database) as repository:
                encode_progress=lambda phase,done,total:self.progress.emit({"phase":phase,"phase_done":done,"phase_total":total})
                service=LibraryIndexService(repository,self.cache,batch_size=128,encoder=lambda ids:_encode_for_similarity(repository,self.root,ids,encode_progress));job_id=self.job_id or service.create_job(self.roots);report=service.run(job_id,progress=self.progress.emit,should_pause=lambda:self.pause_requested,should_cancel=lambda:self.cancel_requested);self.progress.emit({"phase":"Profils artistes","phase_done":0,"phase_total":1});report["profiles"]=ArtistProfileService(repository).build_all()
            self.completed.emit(report)
        except Exception as exc:self.failed.emit(str(exc))  # noqa: BLE001


class RemoteDiscoveryWorker(QThread):
    progress=Signal(dict);completed=Signal(list);failed=Signal(str)
    def __init__(self,database:Path,root:Path,query,backend:str,mode:str,providers:dict,pixels:RemotePixelSession,only_new:bool=False)->None:super().__init__();self.database=database;self.root=root;self.query=query;self.backend=backend;self.mode=mode;self.providers=providers;self.pixels=pixels;self.only_new=only_new;self.cancel_requested=False
    def run(self)->None:
        try:
            with ImageAnalysisRepository(self.database) as repository:
                service=RemoteDiscoveryService(repository,self.pixels,self.providers,lambda ids:_encode_for_similarity(repository,self.root,ids),throttle_seconds=.25);rows=service.discover(self.query,self.backend,self.mode,progress=self.progress.emit,cancelled=lambda:self.cancel_requested,only_new=self.only_new)
            self.completed.emit(rows)
        except Exception as exc:self.failed.emit(str(exc))  # noqa: BLE001


class QueryRankWorker(QThread):
    completed=Signal(list,object);failed=Signal(str)
    def __init__(self,database:Path,query,backend:str,reference_ids:list[int],excluded:set[tuple[str,str]])->None:super().__init__();self.database=database;self.query=query;self.backend=backend;self.reference_ids=reference_ids;self.excluded=excluded
    def run(self)->None:
        try:
            with ImageAnalysisRepository(self.database) as repository:
                service=ArtistProfileService(repository);primary=[value for value in service.rank_artists_for_query(self.query,self.backend,limit=1000) if (value.artist.site,value.artist.tag.casefold()) not in self.excluded];other="openclip" if self.backend=="author_id_embedding" else "author_id_embedding"
                try:secondary=service.rank_artists_for_query(self.query,other,limit=1000)
                except KeyError:secondary=[]
                other_scores={value.artist:value.centroid_similarity for value in secondary};rows=[]
                for value in primary:
                    profile=service.get_profile(value.artist);comparison=service.compare_query_to_artist(self.query,profile);representative=service.closest_candidate_images(value.artist,self.backend,query_profile=self.query,exclude_item_ids=self.reference_ids,limit=1);rows.append({"artist":value.artist,"author_id":value.centroid_similarity if self.backend=="author_id_embedding" else other_scores.get(value.artist),"openclip":value.centroid_similarity if self.backend=="openclip" else other_scores.get(value.artist),"palette_distance":comparison["palette_distance"],"image_count":value.image_count,"confidence":profile.confidence_level,"coherence":value.coherence,"representative":representative[0]["path"] if representative else None})
                try:identification=service.suggest_artist_for_query(self.query)
                except KeyError:identification=None
            self.completed.emit(rows,identification)
        except Exception as exc:self.failed.emit(str(exc))  # noqa: BLE001


class SimilarArtistsController(QObject):
    def __init__(self,root:Path,page,image_analysis,log,task_manager,parent=None)->None:
        super().__init__(parent);self.root=root;self.page=page;self.image_analysis=image_analysis;self.log=log;self.task_manager=task_manager
        self.service=ArtistProfileService(image_analysis.repository,logger=self._log);repair=image_analysis.repository.repair_structured_artist_associations();configured=image_analysis.settings.get("gelbooru_database","");tag_database=Path(configured) if configured else None;category_repair=image_analysis.repository.repair_gelbooru_tag_categories(LocalTagCategoryLookup(tag_database)) if tag_database and tag_database.is_file() else 0;repair_profiles=self.service.build_all() if sum(repair.values())+category_repair else None;self.worker=None;self.rank_worker=None;self.current_artist=None;self.current_item_id=None;self.task_id=None
        gel=image_analysis.credentials().get("gelbooru",{});gel=gel if isinstance(gel,dict) else {};lookup=LocalTagCategoryLookup(tag_database) if tag_database and tag_database.is_file() else None;self.providers={"gelbooru":GelbooruPostProvider(str(gel.get("user_id","")),str(gel.get("api_key","")),category_lookup=lookup),"e621":E621PostProvider()};self.remote_pixels=RemotePixelSession(root/"var"/"tmp"/"remote_images");self.library_worker=None;self.discovery_worker=None;self.resume_library_job=None;self.library_task_id=None;self.discovery_task_id=None
        self.reference_ids=[];self.query_profile=None;self.active_reference_id=None;self.single_confirmed=False;self.scan_worker=None;self.prepare_worker=None;self.remote_worker=None;self.duplicate_count=0;self.invalid_count=0
        page.artist_search_requested.connect(self.search_artist);page.item_search_requested.connect(self.search_item);page.local_image_requested.connect(self.search_local);page.update_requested.connect(self.update_profiles);page.gallery_requested.connect(self.open_gallery);page.compare_requested.connect(self.compare)
        page.backend.currentIndexChanged.connect(self._backend_changed)
        page.references_added.connect(self.add_reference_sources);page.reference_removed.connect(self.remove_reference);page.references_cleared.connect(self.clear_references);page.remote_requested.connect(self.load_remote);page.continue_requested.connect(self.continue_single)
        page.reference_activated.connect(self.inspect_reference);page.corpus_requested.connect(self.use_reference_corpus)
        page.unassigned_examine_requested.connect(self.examine_unassigned);page.references_assign_requested.connect(self.assign_references)
        page.filename_repair_requested.connect(self.repair_filename_metadata)
        page.library_index_requested.connect(self.start_library_index);page.library_resume_requested.connect(self.resume_library_index);page.library_pause_requested.connect(self.pause_library_index);page.library_cancel_requested.connect(self.cancel_library_index);page.remote_discovery_requested.connect(self.start_remote_discovery);page.artist_open_requested.connect(self.open_artist)
        page.remote_purge_requested.connect(self.purge_remote_profiles)
        page.remote_cancel_requested.connect(self.cancel_remote_discovery)
        page.local_duplicates_requested.connect(self.show_local_duplicates)
        if sum(repair.values())+category_repair:self._log(f"Structured metadata artist repair: {repair}; Gelbooru catalogue: {category_repair}; profiles={repair_profiles}")
        resumable=image_analysis.repository.resumable_library_jobs()
        if resumable:self.resume_library_job=resumable[-1]["id"];page.library_resume.show();page.library_status.setText(f"Indexation interrompue disponible : {resumable[-1]['scanned']} fichiers traités");page.library_status.show()
        if self.remote_pixels.cleared_stale_files:self._log(f"[RemotePixels] Crash cleanup: {self.remote_pixels.cleared_stale_files} files")
        model=self.root/"var"/"experiments"/"models"/"author-id-embedding.onnx";page.set_backend_available("author_id_embedding",model.is_file(),"Modèle Author_ID absent" if not model.is_file() else "")
        if importlib.util.find_spec("open_clip") is None:
            page.set_backend_available("openclip",False,"Dépendance OpenCLIP absente")
        self.refresh_catalog()
    def _log(self,message:str)->None:self.log(log_event("SimilarArtists",message))
    def refresh_catalog(self)->None:
        self.page.set_artists(self.service.list_artist_options());status=self.service.corpus_status();counts=status["embedding_counts"]
        self.page.corpus.setText(f"Profils : {status['profiles']} · Images avec artiste : {status['images_eligible']} · sans artiste : {status['images_skipped']} · Author_ID : {counts.get('author_id_embedding',0)} · OpenCLIP : {counts.get('openclip',0)}")
        self.page.unassigned_status.setText(f"{status['images_skipped']} images sans artiste")
    def _rank(self,artist=None,item_id=None)->None:
        backend=str(self.page.backend.currentData());self._log(f"Query {'artist '+artist.site+':'+artist.tag if artist else 'image '+str(item_id)}; backend={backend}")
        primary=(self.service.rank_artists_for_artist(artist,backend,limit=1000) if artist else self.service.rank_artists_for_image(item_id,backend,limit=1000));other="openclip" if backend=="author_id_embedding" else "author_id_embedding"
        try:secondary=(self.service.rank_artists_for_artist(artist,other,limit=1000) if artist else self.service.rank_artists_for_image(item_id,other,limit=1000))
        except KeyError:secondary=[]
        secondary_map={row.artist:row.centroid_similarity for row in secondary};rows=[]
        query_profile=self.service.get_profile(artist) if artist else None
        for value in primary:
            candidate=self.service.get_profile(value.artist);comparison=self.service.compare_artists(artist,value.artist) if artist and query_profile else {"palette_distance":None}
            representative=self.service.closest_candidate_images(value.artist,backend,item_id=item_id,query_artist=artist,limit=1)
            rows.append({"artist":value.artist,"author_id":value.centroid_similarity if backend=="author_id_embedding" else secondary_map.get(value.artist),"openclip":value.centroid_similarity if backend=="openclip" else secondary_map.get(value.artist),"palette_distance":comparison["palette_distance"],"image_count":value.image_count,"confidence":candidate.confidence_level if candidate else "unbuilt","coherence":value.coherence,"representative":representative[0]["path"] if representative else None})
        try:identification=self.service.suggest_artist_for_image(item_id) if item_id is not None else None
        except KeyError:identification=None
        label=(f"{artist.tag} · {artist.site} · {query_profile.image_count if query_profile else 0} images" if artist else f"AnalysisItem #{item_id}")
        self.page.show_results(label,rows,identification);self.page.state.setText(f"{len(rows)} candidats classés — aucun tag artiste modifié.")
    def search_artist(self,artist:ArtistIdentity)->None:
        self.current_artist=artist;self.current_item_id=None;self.active_reference_id=None;self.page.set_single_reference_mode(False,len(self.reference_ids))
        profile=self.service.get_profile(artist)
        if profile is None:
            profile=self.service.build_profile(artist)
        try:self._rank(artist=artist)
        except KeyError as exc:self.page.state.setText(f"Profil sans embedding compatible : {exc}. Utilisez Mettre à jour les profils.")
    def _backend_changed(self)->None:
        try:
            if self.active_reference_id:self._rank_active_reference()
            elif self.query_profile:self._rank_query()
            elif self.current_artist:self._rank(artist=self.current_artist)
            elif self.current_item_id:self._rank(item_id=self.current_item_id)
        except KeyError as exc:self.page.state.setText(f"Backend indisponible : {exc}")
    def search_item(self,item_id:int)->None:
        if item_id<=0:return
        self.current_item_id=item_id;self.current_artist=None
        try:self._rank(item_id=item_id)
        except KeyError as exc:self.page.state.setText(f"{exc}. Calcul des embeddings nécessaire.")
    def search_local(self,path:str)->None:
        self.add_reference_sources([path])
    def add_reference_sources(self,paths:list[str])->None:
        if self.scan_worker and self.scan_worker.isRunning():self.page.state.setText("Une préparation est déjà en cours.");return
        self.scan_worker=DroppedSourceScanWorker([Path(value) for value in paths]);self.scan_worker.completed.connect(self._scan_done);self.scan_worker.start();self.page.state.setText("Recherche des images…")
    def _scan_done(self,paths:list[str],ignored:int)->None:
        self.invalid_count+=ignored
        if not paths:self._refresh_references();return
        self.prepare_worker=ReferencePrepareWorker(self.image_analysis.database,self.image_analysis.cache,paths);self.prepare_worker.progress.connect(lambda done,total:self.page.state.setText(f"Préparation : {done} / {total}"));self.prepare_worker.completed.connect(self._prepare_done);self.prepare_worker.failed.connect(self._build_failed);self.prepare_worker.start()
    def _prepare_done(self,entries:list[dict],duplicates:int,invalid:int)->None:
        self.duplicate_count+=duplicates;self.invalid_count+=invalid
        for entry in entries:
            if entry["item_id"] in self.reference_ids:self.duplicate_count+=1
            else:self.reference_ids.append(entry["item_id"])
        self.current_artist=None;self.current_item_id=None;self.single_confirmed=False;self._refresh_references();self._start_build(self.reference_ids,False)
    def remove_reference(self,item_id:int)->None:
        was_active=item_id==self.active_reference_id;self.reference_ids=[value for value in self.reference_ids if value!=item_id];self.single_confirmed=False
        if was_active:self.active_reference_id=None
        self._refresh_references();self._recalculate_query()
    def clear_references(self)->None:
        self.reference_ids=[];self.query_profile=None;self.active_reference_id=None;self.duplicate_count=0;self.invalid_count=0;self.page.set_single_reference_mode(False,0);self.page.show_references([],"Corpus vidé","—");self.page.show_results("",[])
    def continue_single(self)->None:self.single_confirmed=True;self._recalculate_query()
    def _refresh_references(self)->None:
        query=self.query_profile;backend=str(self.page.backend.currentData());space=self.service._space_query(query,backend) if query else None;similarities=query.item_similarity.get(space.space.key,{}) if query and space else {}
        entries=[]
        for item_id in self.reference_ids:
            item=self.image_analysis.repository.get_item(item_id)
            if item and item.cached_path:entries.append({"item_id":item_id,"path":str(item.cached_path),"similarity":similarities.get(item_id),"provenance":self._provenance_summary(item_id)})
        quality=query.quality_level if query else ({1:"very_low"}.get(len(entries),"pending"));warning=""
        if len(entries)==1:warning="Échantillon très faible : le résultat peut refléter le contenu plutôt que le style."
        elif space and space.dispersion.mean_similarity<0.65:warning="Références visuellement hétérogènes : plusieurs styles peuvent être mélangés."
        coherence=[]
        if query:
            for key,profile in query.embeddings.items():
                label="Author_ID" if key.startswith("author_id_embedding:") else "OpenCLIP" if key.startswith("openclip:") else profile.space.backend
                coherence.append(f"Cohérence {label} : {profile.dispersion.mean_similarity:.2f}")
        artist_counts=Counter(artist for item_id in self.reference_ids for artist in set(self.image_analysis.repository.artist_tags(item_id)))
        artist_note="Artiste source : inconnu"
        if artist_counts:
            artist,count=artist_counts.most_common(1)[0];others=[name for name,_count in artist_counts.most_common()[1:]];kind="Artiste détecté" if count==len(self.reference_ids) else "Artiste principal";artist_note=f"{kind} : {artist} ({count}/{len(self.reference_ids)})"+(f" · Collaborateurs/autres : {', '.join(others)}" if others else "")
        elif suggestion:=self._folder_artist_suggestion(self.reference_ids):artist_note=f"Artiste supposé d’après le dossier : {suggestion} — confirmation requise"
        summary=" · ".join([f"{len(entries)} utilisable(s)",f"{self.duplicate_count} doublon(s) ignoré(s)",f"{self.invalid_count} invalide(s)",artist_note,*coherence])
        self.page.show_references(entries,summary,quality,warning,self.active_reference_id)
    def _provenance_summary(self,item_id:int)->str:
        rows=self.service.item_provenances(item_id);local=sum(row["kind"]=="local_file" for row in rows);remote=[f"{row['site']} #{row['post_id']}" for row in rows if row["site"]]
        values=([f"Local — {local} fichier(s)"] if local else [])+remote
        return " + ".join(values) or "Source inconnue"

    def _folder_artist_suggestion(self,item_ids:list[int])->str|None:
        parents={Path(str(item.cached_path)).parent for item_id in item_ids if (item:=self.image_analysis.repository.get_item(item_id)) and item.cached_path}
        return next(iter(parents)).name if len(parents)==1 else None
    def _recalculate_query(self)->None:
        if not self.reference_ids:self.query_profile=None;self.active_reference_id=None;self.page.set_single_reference_mode(False,0);self.page.show_results("",[]);return
        self.query_profile=self.service.build_query_profile(self.reference_ids);self._refresh_references()
        if self.active_reference_id:
            self._rank_active_reference();return
        if len(self.reference_ids)==1 and not self.single_confirmed:self.page.state.setText("Échantillon très faible — ajoutez des images ou cliquez Continuer quand même.");return
        try:self._rank_query()
        except KeyError as exc:self.page.state.setText(str(exc))
    def _rank_query(self)->None:
        self.current_artist=None;self.current_item_id=None;self.page.set_single_reference_mode(False,len(self.reference_ids))
        if self.rank_worker and self.rank_worker.isRunning():self.page.state.setText("Un classement est déjà en cours…");return
        backend=str(self.page.backend.currentData());excluded=dominant_source_artists(self.image_analysis.repository,self.reference_ids);self.rank_worker=QueryRankWorker(self.image_analysis.database,self.query_profile,backend,list(self.reference_ids),excluded);self.rank_worker.completed.connect(self._query_rank_done);self.rank_worker.failed.connect(self._build_failed);self.rank_worker.start();self.page.state.setText("Classement des artistes en arrière-plan…")

    def _query_rank_done(self,rows:list,identification)->None:
        self.page.show_results(f"Requête : {len(self.reference_ids)} références visuelles",rows,identification);self.page.state.setText("Analyse terminée · Aucun tag artiste n’a été modifié.");self.rank_worker.deleteLater();self.rank_worker=None

    def activate_reference(self,item_id:int)->None:
        if item_id not in self.reference_ids:return
        self.active_reference_id=item_id;self.current_artist=None;self.current_item_id=item_id;self._refresh_references();self._rank_active_reference()

    def inspect_reference(self,item_id:int)->None:
        item=self.image_analysis.repository.get_item(item_id)
        if item is None:return
        repo=self.image_analysis.repository;provenances=[dict(row) for row in repo.provenances(item_id)];artists=repo.artist_associations(item_id);metadata=repo.connection.execute("SELECT rating,source_md5 FROM local_filename_metadata WHERE item_id=? ORDER BY parsed_at DESC LIMIT 1",(item_id,)).fetchone();tags=list(repo.connection.execute("SELECT tag_name,category,source FROM source_tags WHERE item_id=? ORDER BY category,tag_name",(item_id,)));wd14=list(repo.connection.execute("SELECT raw_tag_name,confidence,decision FROM tag_observations WHERE item_id=? AND source='wd14' ORDER BY confidence DESC",(item_id,)));embeddings={str(row[0]) for row in repo.connection.execute("SELECT DISTINCT r.backend FROM embeddings e JOIN model_runs r ON r.id=e.model_run_id WHERE e.item_id=?",(item_id,))}
        dialog=QDialog(self.page);dialog.setWindowTitle("Détails de l’image");dialog.resize(1000,720);root=QVBoxLayout(dialog);body=QHBoxLayout();preview=ScaledImageLabel();preview.setMinimumSize(420,420)
        if item.cached_path and item.cached_path.is_file():preview.set_image(item.cached_path)
        body.addWidget(preview,1);details=QTextBrowser();lines=[f"<h2>Aperçu</h2><b>Dimensions :</b> {item.width or '?'} × {item.height or '?'}<br><b>État :</b> {item.state.value}","<h2>Provenance</h2>"]
        for row in provenances:lines.append(str(row.get("local_path") or f"{row.get('site')} #{row.get('post_id')}"))
        if metadata:lines.append(f"<br><b>Source MD5 :</b> {metadata['source_md5']}<br><h2>Rating</h2>{metadata['rating']}")
        else:lines.append("<br><h2>Rating</h2>Aucune donnée disponible")
        lines.append("<h2>Artistes</h2>"+("<br>".join(f"{row['artist_tag']} — {row['provenance']}" for row in artists) or "Aucun artiste associé"));lines.append("<h2>Tags source</h2>"+("<br>".join(f"{row['tag_name']} — {row['category'] or 'catégorie inconnue'} ({row['source']})" for row in tags) or "Aucune donnée source disponible"));lines.append("<h2>WD14</h2>"+("<br>".join(f"{row['raw_tag_name']} — {float(row['confidence'] or 0):.1%} — {row['decision']}" for row in wd14) or "Aucune analyse WD14"));lines.append(f"<h2>Embeddings</h2>Author_ID : {'disponible' if 'author_id_embedding' in embeddings else 'absent'}<br>OpenCLIP : {'disponible' if 'openclip' in embeddings else 'absent'}");details.setHtml("".join(lines));body.addWidget(details,1);root.addLayout(body,1);actions=QDialogButtonBox();open_file=actions.addButton("Ouvrir le fichier",QDialogButtonBox.ButtonRole.ActionRole);open_remote=actions.addButton("Ouvrir sur le booru",QDialogButtonBox.ButtonRole.ActionRole);single=actions.addButton("Utiliser cette image seule",QDialogButtonBox.ButtonRole.ActionRole);close=actions.addButton(QDialogButtonBox.StandardButton.Close);open_file.setEnabled(bool(item.cached_path and item.cached_path.is_file()));remote=next((row for row in provenances if row.get("site") and row.get("post_id")),None);open_remote.setEnabled(remote is not None);open_file.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.cached_path))));open_remote.clicked.connect(lambda:QDesktopServices.openUrl(QUrl(post_page_url(remote["site"],remote["post_id"]))) if remote else None);single.clicked.connect(lambda:(dialog.accept(),self.activate_reference(item_id)));close.clicked.connect(dialog.reject);root.addWidget(actions);dialog.exec()

    def _rank_active_reference(self)->None:
        item_id=self.active_reference_id
        if item_id is None:return
        self.current_artist=None;self.current_item_id=item_id;self._rank(item_id=item_id)
        item=self.image_analysis.repository.get_item(item_id);label=Path(str(item.cached_path)).name if item and item.cached_path else f"image #{item_id}"
        provenance=self._provenance_summary(item_id)
        if provenance!="Source inconnue" and "Local" not in provenance:label=provenance
        self.page.query_summary.setText(f"Requête : 1 image sélectionnée — {label}")
        self.page.state.setText("Analyse terminée · Recherche basée sur une seule image : le sujet, la composition ou la palette peuvent davantage influencer les résultats.")
        self.page.set_single_reference_mode(True,len(self.reference_ids));self._refresh_references()

    def use_reference_corpus(self)->None:
        self.active_reference_id=None;self.current_item_id=None;self._refresh_references();self._rank_query()

    def assign_references(self)->None:
        selected=[int(item.data(Qt.ItemDataRole.UserRole)) for item in self.page.references.selectedItems()]
        self._assign_items(selected or self.reference_ids)

    def _assign_items(self,item_ids:list[int])->None:
        if not item_ids:return
        dialog=QDialog(self.page);dialog.setWindowTitle("Associer à un artiste");layout=QFormLayout(dialog);site=QComboBox();site.addItems(("local","gelbooru","e621"));tag=QLineEdit();tag.setPlaceholderText("artist_tag");tag.setText(self._folder_artist_suggestion(item_ids) or "");layout.addRow("Identité :",site);layout.addRow("Artiste :",tag);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted or not tag.text().strip():return
        artist=ArtistIdentity(site.currentText(),tag.text().strip());answer=QMessageBox.question(self.page,"Confirmer l’association",f"Associer {len(set(item_ids))} image(s) canoniques à {artist.site}:{artist.tag} ?")
        if answer!=QMessageBox.StandardButton.Yes:return
        report=self.service.assign_items_to_artist(item_ids,artist);self._log(f"Manual artist assignment {artist.site}:{artist.tag}: {report}");self.refresh_catalog();self._refresh_references();self.page.state.setText(f"Association terminée : {report['associated']} ajout(s), profil {report['image_count']} image(s).")

    def examine_unassigned(self)->None:
        report=self.service.unassigned_artist_report();dialog=QDialog(self.page);dialog.setWindowTitle(f"Images sans artiste — {report['without_artist']}");dialog.resize(1050,650);layout=QVBoxLayout(dialog);counts=report["counts"];layout.addWidget(QLabel(f"Local uniquement : {counts['local_only']} · Gelbooru : {counts['gelbooru']} · e621 : {counts['e621']} · metadata disponible : {counts['metadata_available']} · metadata manquante : {counts['metadata_missing']} · provenances multiples : {counts['multiple_provenances']}"));table=QTableWidget(0,5);table.setHorizontalHeaderLabels(("AnalysisItem","SHA","Source","Chemin / post","Diagnostic"));table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        for row in report["items"]:
            index=table.rowCount();table.insertRow(index);sources=", ".join(f"{value.get('site')} #{value.get('post_id')}" if value.get("site") else "Local" for value in row["provenances"]);locations="\n".join(str(value.get("local_path") or value.get("post_id") or "") for value in row["provenances"]);values=(row["item_id"],row["sha"],sources,locations,row["reason"])
            for column,value in enumerate(values):table.setItem(index,column,QTableWidgetItem(str(value)))
        layout.addWidget(table,1);assign=QDialogButtonBox();button=assign.addButton("Associer la sélection",QDialogButtonBox.ButtonRole.ActionRole);close=assign.addButton(QDialogButtonBox.StandardButton.Close);close.clicked.connect(dialog.accept);button.clicked.connect(lambda:self._assign_items([int(table.item(index.row(),0).text()) for index in table.selectionModel().selectedRows()]));layout.addWidget(assign);dialog.exec();self.refresh_catalog()

    def repair_filename_metadata(self)->None:
        sources=ImageSourceService(self.image_analysis.repository,self.image_analysis.cache);preview=sources.preview_filename_repairs();message=(f"Images sans artiste : {preview['without_artist']}\nFilename compatible : {preview['compatible']}\nArtistes récupérables : {preview['artist_count']}\nSite Gelbooru : {preview['gelbooru']}\nSite e621 : {preview['e621']}\nIdentité locale : {preview['local']}\nNon parsables : {preview['unrecognized']}\n\nAppliquer sans recalculer les embeddings ?")
        if QMessageBox.question(self.page,"Réparation depuis les noms de fichiers",message)!=QMessageBox.StandardButton.Yes:return
        report=sources.repair_filename_metadata(preview);profiles=self.service.build_all();self._log(f"Filename metadata repair: {report}; profiles={profiles}");self.refresh_catalog();self._refresh_references();self.page.state.setText(f"Réparation terminée : {report['associated']} image(s), {report['artist_count']} artiste(s), {report['conflicts']} conflit(s).")

    def start_library_index(self,roots:list[str],job_id:str|None=None)->None:
        if self.library_worker and self.library_worker.isRunning():return
        if self.discovery_worker and self.discovery_worker.isRunning():self.page.library_status.setText("Attendez la fin de la découverte distante.");return
        paths=[Path(value) for value in roots];self.library_worker=LibraryIndexWorker(self.image_analysis.database,self.image_analysis.cache,self.root,paths,job_id);self.library_worker.progress.connect(self._library_progress);self.library_worker.completed.connect(self._library_done);self.library_worker.failed.connect(self._library_failed);self.library_worker.start();self.page.library_pause.setEnabled(True);self.page.library_cancel.setEnabled(True);self.page.library_status.show();self.library_task_id=self.task_manager.start("library_indexer","Indexation bibliothèque",f"{len(paths)} source(s)")

    def resume_library_index(self)->None:
        if self.resume_library_job:self.start_library_index([],self.resume_library_job)

    def _library_progress(self,value:dict)->None:
        phase=value.get("phase","Scan / Metadata")
        self.page.library_phase.setText(f"Phase : {phase}")
        if "phase_done" in value:
            done=int(value["phase_done"]);total=max(1,int(value["phase_total"]));self.page.library_progress.setRange(0,total);self.page.library_progress.setValue(done);self.page.library_progress.setFormat(f"{done} / {total} · {done/total*100:.1f} %");return
        done=int(value["scanned"]);total=max(1,int(value["detected"]));self.page.library_progress.setRange(0,total);self.page.library_progress.setValue(done);self.page.library_progress.setFormat(f"{done} / {value['detected']} · {done/total*100:.1f} %");self.page.library_current.setText(f"Fichier courant : {value.get('current','—')}");self.page.library_status.setText(f"Nouveaux : {value['imported']} · même chemin : {value.get('same_path',0)} · local ↔ local : {value.get('local_local',0)} · local ↔ distant : {value.get('local_remote',0)} · métadonnées : {value['metadata_parsed']} · invalides : {value['invalid']}");self.task_manager.progress(self.library_task_id,done,int(value["detected"]),phase,value.get("current",""))

    def show_local_duplicates(self)->None:
        rows=self.image_analysis.repository.local_binary_duplicates();dialog=QDialog(self.page);dialog.setWindowTitle(f"Doublons binaires locaux — {len(rows)} groupe(s)");dialog.resize(1050,650);layout=QVBoxLayout(dialog);table=QTableWidget(0,4);table.setHorizontalHeaderLabels(("Aperçu","SHA-256","Chemins locaux","Ouvrir"))
        for row in rows:
            index=table.rowCount();table.insertRow(index);preview=QTableWidgetItem(f"AnalysisItem #{row['item_id']}");preview.setIcon(QIcon(row["thumbnail"]));table.setItem(index,0,preview);table.setItem(index,1,QTableWidgetItem(row["sha256"]));table.setItem(index,2,QTableWidgetItem("\n".join(row["paths"])));button=QPushButton("Ouvrir le premier fichier");button.clicked.connect(lambda _checked=False,path=row["paths"][0]:QDesktopServices.openUrl(QUrl.fromLocalFile(path)));table.setCellWidget(index,3,button)
        table.resizeColumnsToContents();layout.addWidget(table,1);close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);close.rejected.connect(dialog.reject);layout.addWidget(close);dialog.exec()

    def pause_library_index(self)->None:
        if self.library_worker:self.library_worker.pause_requested=True

    def cancel_library_index(self)->None:
        if self.library_worker:self.library_worker.cancel_requested=True

    def _library_done(self,report:dict)->None:
        state=str(report["state"]);self.page.library_pause.setEnabled(False);self.page.library_cancel.setEnabled(False);self.resume_library_job=report["id"] if state=="paused" else None;self.page.library_resume.setVisible(state=="paused");self.page.library_status.setText(f"Indexation {state} : {report['scanned']} scannés, {report['duplicates']} doublons, {report['invalid']} erreurs");self.task_manager.finish(self.library_task_id,"completed" if state=="completed" else state,str(report));self._log(f"[LibraryIndexer] {state}: {report['scanned']} scanned, {report['duplicates']} duplicates, {report['invalid']} invalid");self.refresh_catalog()
        if state=="completed" and self.query_profile:self._rank_query()

    def _library_failed(self,error:str)->None:self.page.library_pause.setEnabled(False);self.page.library_cancel.setEnabled(False);self.page.library_status.setText(f"Échec indexation : {error}");self.task_manager.finish(self.library_task_id,"failed",error)

    def _remote_source_sites(self, requested: str, query) -> tuple[str, ...]:
        if requested in self.providers:
            return (requested,)
        if requested == "all":
            return tuple(self.providers)
        sites = {
            str(row["site"])
            for item_id in query.item_ids
            for row in self.image_analysis.repository.provenances(item_id)
            if str(row["site"] or "") in self.providers
        }
        return tuple(sorted(sites)) if sites else tuple(self.providers)

    def start_remote_discovery(self,mode:str,requested_source:str="auto")->None:
        if self.discovery_worker and self.discovery_worker.isRunning():return
        if self.library_worker and self.library_worker.isRunning():self.page.state.setText("Attendez la fin de l’indexation de bibliothèque.");return
        query=self.service.build_query_profile([self.active_reference_id]) if self.active_reference_id else self.query_profile
        if query is None:self.page.state.setText("Ajoutez d’abord des références visuelles.");return
        sites=self._remote_source_sites(requested_source,query);providers={site:self.providers[site] for site in sites};source_label="Tous" if len(sites)>1 else sites[0].title();self.discovery_worker=RemoteDiscoveryWorker(self.image_analysis.database,self.root,query,str(self.page.backend.currentData()),mode,providers,self.remote_pixels,self.page.only_new.isChecked());self.discovery_worker.progress.connect(self._remote_discovery_progress);self.discovery_worker.completed.connect(self._remote_discovery_done);self.discovery_worker.failed.connect(self._remote_discovery_failed);self.discovery_worker.start();self.page.remote_cancel.setEnabled(True);self.page.remote_status.setText(f"Source : {source_label}\nRecherche des candidats…");self.discovery_task_id=self.task_manager.start("remote_discovery","Découverte d’artistes distants",f"{mode}; {source_label}")

    def _remote_discovery_progress(self,value:dict)->None:
        labels={"distinctive_tags":"Tags distinctifs","candidates":"Recherche de candidats","profiles":"Téléchargement / embeddings / profils","retry":"Nouvelle tentative","warning":"Post ignoré","finished":"Terminé"};phase=value["phase"];done=int(value.get("processed",value.get("evaluated",0)));total=max(1,int(value.get("artist_total",value.get("filtered",0) or 1)));self.page.remote_progress.setRange(0,total);self.page.remote_progress.setValue(min(done,total));reason=value.get("reason","");text=(f"Phase : {labels.get(phase,phase)}\nTags distinctifs : {value.get('tags',0)} · Requêtes : {value.get('queries',0)} · Posts reçus : {value.get('posts',0)} · Artistes trouvés : {value.get('artists',0)} · Après filtrage : {value.get('filtered',0)}\nImages utilisables : {value.get('images',0)} · Ignorées : {value.get('images_ignored',0)} · Profils : {value.get('profiles',0)} / {value.get('artist_total',value.get('filtered',0))} · Erreurs réseau : {value.get('network_errors',0)}"+(f"\n{value.get('site','')} {value.get('role','')} {value.get('artist','')} {value.get('post_ref','')} : {value.get('result','')}" if phase in {'warning','retry'} else "")+(f"\nRaison : {reason}" if reason else ""));self.page.remote_status.setText(text);self.task_manager.progress(self.discovery_task_id,done,total,phase,text)

    def cancel_remote_discovery(self)->None:
        if self.discovery_worker and self.discovery_worker.isRunning():self.discovery_worker.cancel_requested=True;self.page.remote_status.setText("Annulation demandée…")

    def _remote_discovery_done(self,results:list)->None:
        query=self.discovery_worker.query;backend=str(self.page.backend.currentData());other="openclip" if backend=="author_id_embedding" else "author_id_embedding"
        try:other_map={row.artist:row.centroid_similarity for row in self.service.rank_artists_for_query(query,other,limit=100000)}
        except KeyError:other_map={}
        rows=[]
        for result in results:
            profile=self.service.get_profile(result.artist);comparison=self.service.compare_query_to_artist(query,profile);representative=self.service.closest_candidate_images(result.artist,backend,query_profile=query,exclude_item_ids=self.reference_ids,limit=1);rows.append({"artist":result.artist,"author_id":result.similarity if backend=="author_id_embedding" else other_map.get(result.artist),"openclip":result.similarity if backend=="openclip" else other_map.get(result.artist),"palette_distance":comparison["palette_distance"],"image_count":result.image_count,"confidence":("Nouveau · " if result.is_new else result.collection_state+" · ")+profile.confidence_level,"coherence":self.service._space(profile,backend).dispersion.mean_similarity if self.service._space(profile,backend) else None,"representative":representative[0]["path"] if representative else None,"remote_discovery":True,"is_new":result.is_new})
        self.page.remote_cancel.setEnabled(False);self.page.show_results(f"Résultats distants · {len(rows)} artistes évalués",rows);self.task_manager.finish(self.discovery_task_id,"completed",f"{len(rows)} artists");self._log(f"[RemoteDiscovery] Completed: {len(rows)} visually ranked artists")

    def _remote_discovery_failed(self,error:str)->None:self.page.remote_cancel.setEnabled(False);self.page.remote_status.setText(f"Échec découverte distante : {error}");self.task_manager.finish(self.discovery_task_id,"failed",error)

    def open_artist(self,artist:ArtistIdentity)->None:
        if artist.site in {"gelbooru","e621"}:QDesktopServices.openUrl(QUrl(artist_page_url(artist.site,artist.tag)));self.image_analysis.repository.touch_remote_artist(artist.site,artist.tag,used=True)

    def purge_remote_profiles(self,days:int)->None:
        cutoff=(datetime.now(UTC)-timedelta(days=days)).isoformat(timespec="seconds");preview=self.image_analysis.repository.preview_remote_profile_purge(cutoff)
        message=f"{preview['profiles']} profils distants concernés\n{preview['embeddings']} embeddings\nLes artistes présents dans la collection sont protégés."
        if not preview["profiles"]:QMessageBox.information(self.page,"Purge des profils distants",message);return
        if QMessageBox.question(self.page,"Purge des profils distants",message+"\n\nAppliquer ?")!=QMessageBox.StandardButton.Yes:return
        report=self.image_analysis.repository.purge_remote_profiles(preview["identities"]);self.refresh_catalog();self.page.state.setText(f"Purge terminée : {report['profiles']} profils, {report['embeddings']} embeddings.")

    def shutdown(self)->None:
        for worker in (self.rank_worker,self.worker,self.scan_worker,self.prepare_worker,self.remote_worker):
            if worker and worker.isRunning():worker.requestInterruption();worker.wait(30000)
        if self.library_worker and self.library_worker.isRunning():self.library_worker.pause_requested=True;self.library_worker.wait(30000)
        if self.discovery_worker and self.discovery_worker.isRunning():self.discovery_worker.cancel_requested=True;self.discovery_worker.wait(30000)
        removed=self.remote_pixels.close();self._log(f"[RemotePixels] Session cache cleared: {removed} files")
    def load_remote(self,site:str,post_id:str)->None:
        if self.remote_worker and self.remote_worker.isRunning():return
        configured=self.image_analysis.settings.get("gelbooru_database","");tag_database=Path(configured) if configured else None
        self.page.state.setText(f"Chargement {site} #{post_id}…");self.remote_worker=RemoteReferenceWorker(self.image_analysis.database,self.image_analysis.cache,site,post_id,self.image_analysis.credentials(),tag_database);self.remote_worker.completed.connect(self._remote_done);self.remote_worker.failed.connect(self._build_failed);self.remote_worker.start()
    def _remote_done(self,item_id:int,site:str,artists:list)->None:
        if artists:
            artist=ArtistIdentity(site,artists[0]);option=next((value for value in self.service.list_artist_options() if value["artist"]==artist),None);count=option["image_count"] if option else 1
            if count>=10 and self.service.get_profile(artist):
                answer=QMessageBox.question(self.page,"Profil artiste disponible",f"{artist.tag} possède {count} images connues. Utiliser le profil existant ?\nChoisissez Non pour utiliser seulement ce post.")
                if answer==QMessageBox.StandardButton.Yes:self.clear_references();self.search_artist(artist);return
            elif count<5:self.page.state.setText(f"{artist.tag} n’est représenté que par {count} image(s). Ajoutez des références ou continuez quand même.")
        if item_id in self.reference_ids:self.duplicate_count+=1
        else:self.reference_ids.append(item_id)
        self.single_confirmed=False;self._refresh_references();self._start_build(self.reference_ids,False)
    def update_profiles(self)->None:
        ids=self.image_analysis.repository.embeddable_item_ids();answer=QMessageBox.question(self.page,"Mettre à jour les profils",f"{len(ids)} images peuvent être vérifiées. Les embeddings en cache seront réutilisés. Continuer ?")
        if answer==QMessageBox.StandardButton.Yes:self._start_build(ids,True)
    def _start_build(self,ids:list[int],build_all:bool)->None:
        if not ids:return
        if self.worker and self.worker.isRunning():return
        self.task_id=self.task_manager.start("similar_artists","Building Similar Artists profiles",f"{len(ids)} images")
        self.page.state.setText(f"Embeddings : 0 / {len(ids)} — opération en arrière-plan")
        self.worker=SimilarBuildWorker(self.image_analysis.database,self.root,ids,build_all);self.worker.completed.connect(self._build_done);self.worker.failed.connect(self._build_failed);self.worker.progress.connect(self._build_progress);self.worker.start()
    def _build_progress(self,phase:str,completed:int,total:int)->None:
        self.page.state.setText(f"{phase} : {completed} / {total}");self.task_manager.progress(self.task_id,completed,total,phase)
    def _build_done(self,report:dict)->None:
        self.task_manager.finish(self.task_id,"completed",str(report));self.page.state.setText(f"Mise à jour terminée : {report}");self.refresh_catalog()
        if self.reference_ids:self._recalculate_query()
        elif self.current_item_id:
            try:self._rank(item_id=self.current_item_id)
            except KeyError as exc:self.page.state.setText(str(exc))
    def _build_failed(self,error:str)->None:self.task_manager.finish(self.task_id,"failed",error);self.page.state.setText(f"Échec : {error}")
    def open_gallery(self,candidate:ArtistIdentity)->None:
        excluded=[self.active_reference_id] if self.active_reference_id else self.reference_ids
        backend=str(self.page.backend.currentData());images=self.service.closest_candidate_images(candidate,backend,item_id=self.current_item_id,query_artist=self.current_artist,query_profile=None if self.active_reference_id else self.query_profile,exclude_item_ids=excluded,limit=24)
        if not images and self.reference_ids:
            answer=QMessageBox.question(self.page,"Galerie", "Toutes les images connues font partie des références. Les afficher quand même ?")
            if answer==QMessageBox.StandardButton.Yes:images=self.service.closest_candidate_images(candidate,backend,query_profile=self.query_profile,limit=24)
        all_images=[{"item_id":int(row["id"]),"path":str(row["cached_path"]),"score":None,"provenances":self.service.item_provenances(int(row["id"]))} for row in self.image_analysis.repository.artist_image_rows(candidate.site,candidate.tag)];dialog=ImageGalleryDialog(f"Œuvres — {candidate.tag}",images,self.page,pixel_resolver=lambda item_id:self.remote_pixels.ensure(self.image_analysis.repository,item_id,self.providers),all_images=all_images);self.image_analysis.repository.touch_remote_artist(candidate.site,candidate.tag,used=True) if candidate.site in {"gelbooru","e621"} else None;dialog.exec()
    def compare(self,candidate:ArtistIdentity)->None:
        right=self.service.get_profile(candidate)
        if self.current_artist is not None:data=self.service.compare_artists(self.current_artist,candidate);left=self.service.get_profile(self.current_artist);title=f"{self.current_artist.tag} vs {candidate.tag}";left_count=data["left_image_count"]
        elif self.query_profile is not None:
            data=self.service.compare_query_to_artist(self.query_profile,right);left=None;title=f"Références vs {candidate.tag}";left_count=len(self.query_profile.item_ids);reference_source,reference_wd14=self.service.query_tag_frequencies(self.query_profile.item_ids)
        else:self.page.state.setText("Aucune requête à comparer.");return
        dialog=QDialog(self.page);dialog.setWindowTitle(title);dialog.resize(760,600);layout=QVBoxLayout(dialog);form=QFormLayout()
        form.addRow("Images",QLabel(f"{left_count} vs {right.image_count}"));form.addRow("Palette Δ",QLabel("—" if data["palette_distance"] is None else f"{data['palette_distance']:.4f}"))
        for backend,value in data["embeddings"].items():form.addRow(backend,QLabel(f"similarité {value['centroid_similarity']:.4f}"))
        layout.addLayout(form)
        def frequencies(values,total):
            return " | ".join(f"{tag}: {count/max(1,total):.0%}" for tag,count in sorted(values.items(),key=lambda value:(-value[1],value[0]))[:15]) or "Aucune donnée disponible"
        for section,first,second in (("Tags source fréquents",left.source_tag_frequency if left else reference_source,right.source_tag_frequency),("WD14 acceptés fréquents",left.accepted_wd14_frequency if left else reference_wd14,right.accepted_wd14_frequency)):
            first_name=left.artist.tag if left else "Références";common=set(first)&set(second);common_text=" | ".join(sorted(common)[:15]) if common else "Aucun tag fréquent commun";label=QLabel(f"{section}\nCommuns : {common_text}\n{first_name}: {frequencies(first,left_count)}\n{right.artist.tag}: {frequencies(second,right.image_count)}");label.setWordWrap(True);layout.addWidget(label)
        dialog.exec()
