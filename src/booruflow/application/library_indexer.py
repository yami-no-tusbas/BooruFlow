"""Persistent streaming library indexing, independent from similarity queries."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

from booruflow.domain.image_analysis import parse_booru_filename
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService

SUPPORTED_SUFFIXES={".png",".jpg",".jpeg",".webp",".gif",".bmp"}


def iter_library_images(roots:list[Path])->Iterator[Path]:
    """Yield supported files in deterministic order, retaining only one directory at a time."""
    stack=sorted((value.resolve() for value in roots),key=lambda value:str(value).casefold(),reverse=True)
    while stack:
        current=stack.pop()
        if current.is_file():
            if current.suffix.casefold() in SUPPORTED_SUFFIXES:yield current
            continue
        if not current.is_dir():continue
        try:entries=sorted((Path(entry.path) for entry in os.scandir(current)),key=lambda value:str(value).casefold(),reverse=True)
        except OSError:continue
        stack.extend(entries)


class LibraryIndexService:
    def __init__(self,repository:ImageAnalysisRepository,cache:Path,*,batch_size:int=128,encoder:Callable[[list[int]],dict]|None=None)->None:
        self.repository=repository;self.sources=ImageSourceService(repository,cache);self.batch_size=max(1,batch_size);self.encoder=encoder

    def create_job(self,roots:list[Path])->str:
        job_id=str(uuid.uuid4());self.repository.create_library_job(job_id,[str(value.resolve()) for value in roots]);return job_id

    def run(self,job_id:str,*,progress:Callable[[dict],None]|None=None,should_pause:Callable[[],bool]|None=None,should_cancel:Callable[[],bool]|None=None,max_files:int|None=None)->dict:
        job=self.repository.library_job(job_id)
        if job is None:raise KeyError(job_id)
        roots=[Path(value) for value in json.loads(str(job["roots_json"]))];detected=int(job.get("detected",0))
        if detected<=0:detected=sum(1 for _path in iter_library_images(roots));self.repository.update_library_job(job_id,detected=detected)
        counts={key:int(job[key]) for key in ("scanned","imported","duplicates","invalid","metadata_parsed","artists_found")};categories={"same_path":0,"local_local":0,"local_remote":0,"remote_remote":0};categories.update(self.repository.library_match_counts(job_id));self.repository.update_library_job(job_id,state="running");batch=[];new_artists=set();handled=0
        def flush()->None:
            nonlocal batch
            if not batch:return
            item_ids=[]
            for path in batch:
                counts["scanned"]+=1
                try:
                    result=self.sources.add_local_with_result(path);item_ids.append(result.item_id);counts["duplicates"]+=int(result.outcome!="new");counts["imported"]+=int(result.outcome=="new");outcome=result.outcome
                    if result.outcome!="new":
                        if result.matched_same_path:outcome="same_path"
                        elif result.matched_local:outcome="local_local"
                        elif result.matched_remote:outcome="local_remote"
                        categories[outcome]=categories.get(outcome,0)+1
                    self.repository.record_library_path(job_id,path,result.item_id,outcome)
                    parsed=parse_booru_filename(path)
                    if parsed:counts["metadata_parsed"]+=1;new_artists.update(artist.casefold() for artist in parsed.artists)
                except Exception:counts["invalid"]+=1;self.repository.record_library_path(job_id,path,None,"invalid")  # noqa: BLE001 - isolate bad library entries
            if self.encoder and item_ids:self.encoder(list(dict.fromkeys(item_ids)))
            if new_artists:counts["artists_found"]=int(self.repository.connection.execute("SELECT COUNT(DISTINCT site||char(0)||artist_tag) FROM local_filename_metadata WHERE state='applied'").fetchone()[0])
            new_artists.clear();self.repository.update_library_job(job_id,last_path=str(batch[-1]),**counts)
            if progress:progress({**counts,**categories,"detected":detected,"current":str(batch[-1]),"state":"running","phase":"Scan / Metadata"})
            batch=[]
        for path in iter_library_images(roots):
            if self.repository.library_path_processed(job_id,path):continue
            if should_cancel and should_cancel():flush();self.repository.update_library_job(job_id,state="cancelled");return dict(self.repository.library_job(job_id))
            if should_pause and should_pause():flush();self.repository.update_library_job(job_id,state="paused");return dict(self.repository.library_job(job_id))
            batch.append(path);handled+=1
            if len(batch)>=self.batch_size:flush()
            if max_files is not None and handled>=max_files:flush();self.repository.update_library_job(job_id,state="paused");return dict(self.repository.library_job(job_id))
        flush();self.repository.update_library_job(job_id,state="completed");return dict(self.repository.library_job(job_id))
