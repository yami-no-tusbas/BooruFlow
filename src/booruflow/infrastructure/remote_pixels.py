"""Session-scoped remote pixels; extracted knowledge remains in SQLite."""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import uuid
from pathlib import Path

from booruflow.infrastructure.image_analysis_cache import (
    ImageAnalysisCache,
    image_cache_database_path,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    _default_bytes_fetcher,
    analysis_cache_directory_for_repository,
    inspect_image_bytes,
)


class RemotePixelUnavailable(RuntimeError):pass


class RemotePixelSession:
    def __init__(self,root:Path,*,bytes_fetcher=_default_bytes_fetcher)->None:
        self.root=root.resolve();self.root.mkdir(parents=True,exist_ok=True);self.bytes_fetcher=bytes_fetcher;self.cleared_stale_files=0;self.paths={}
        for child in tuple(self.root.iterdir()):
            if child.is_dir():self.cleared_stale_files+=sum(1 for value in child.rglob("*") if value.is_file());shutil.rmtree(child)
        self.directory=self.root/str(uuid.uuid4());self.directory.mkdir()

    def availability(self,repository:ImageAnalysisRepository,item_id:int)->str:
        for row in repository.provenances(item_id):
            if row["local_path"] and Path(str(row["local_path"])).is_file():return "local_available"
        item=repository.get_item(item_id)
        if item_id in self.paths and self.paths[item_id].is_file():return "temporary_remote_available"
        if item and item.cached_path and item.cached_path.is_file() and self.directory in item.cached_path.parents:return "temporary_remote_available"
        if item and item.content_sha256:
            if any(self.directory.glob(f"{item.content_sha256}.*")):
                return "temporary_remote_available"
            with ImageAnalysisCache(self._cache_path(repository)) as cache:
                if cache.contains(item.content_sha256):return "persistent_remote_available"
        return "not_currently_available"

    def ensure(self,repository:ImageAnalysisRepository,item_id:int,providers:dict[str,object])->Path:
        for row in repository.provenances(item_id):
            if row["local_path"] and Path(str(row["local_path"])).is_file():return Path(str(row["local_path"]))
        item=repository.get_item(item_id)
        if item and item.cached_path and item.cached_path.is_file():return item.cached_path
        if item and item.content_sha256:
            with ImageAnalysisCache(self._cache_path(repository)) as cache:
                entry=cache.get(item.content_sha256)
            if entry is not None:
                suffix=mimetypes.guess_extension(entry.mime_type) or ".img"
                path=self.directory/f"{entry.sha256}{suffix}"
                path.write_bytes(entry.data)
                self.paths[item_id]=path
                return path
        errors=[]
        for row in repository.provenances(item_id):
            site=str(row["site"] or "");post_id=str(row["post_id"] or "");provider=providers.get(site)
            if not provider or not post_id:continue
            try:return self._download(repository,item_id,provider.fetch_post(post_id))
            except Exception as exc:errors.append(str(exc))  # noqa: BLE001 - provider boundary
        metadata=repository.connection.execute("SELECT source_md5,site FROM local_filename_metadata WHERE item_id=? AND source_md5<>'' ORDER BY state='applied' DESC LIMIT 1",(item_id,)).fetchone()
        if metadata:
            provider=providers.get(str(metadata["site"]));resolver=getattr(provider,"resolve_post_by_md5",None)
            if resolver:
                try:return self._download(repository,item_id,resolver(str(metadata["source_md5"])))
                except Exception as exc:errors.append(str(exc))  # noqa: BLE001
        raise RemotePixelUnavailable("; ".join(errors) or "image unavailable: no recoverable remote source")

    def _download(self,repository,item_id,post)->Path:
        headers={"User-Agent":"BooruFlow/0.1 RemotePixels"};data=self.bytes_fetcher(post.file_url,headers);suffix=Path(post.file_url.split("?",1)[0]).suffix or ".img";digest=hashlib.sha256(data).hexdigest();metadata=inspect_image_bytes(data,filename=f"image{suffix}")
        with ImageAnalysisCache(self._cache_path(repository)) as cache:
            stored=cache.put(digest,data,site=post.site,post_id=post.post_id,source_kind="preview",mime_type=metadata.mime_type,width=metadata.width,height=metadata.height,sha256=digest)
        if not stored.stored:raise RemotePixelUnavailable("image exceeds analysis cache limit")
        path=self.directory/f"{digest}{suffix}";path.write_bytes(data);self.paths[item_id]=path;return path

    @staticmethod
    def _cache_path(repository:ImageAnalysisRepository)->Path:
        return image_cache_database_path(analysis_cache_directory_for_repository(repository))

    def close(self)->int:
        count=sum(1 for value in self.directory.rglob("*") if value.is_file()) if self.directory.exists() else 0
        if self.directory.exists():shutil.rmtree(self.directory)
        self.paths.clear()
        return count
