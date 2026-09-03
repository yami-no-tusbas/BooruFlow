"""Session-scoped remote pixels; extracted knowledge remains in SQLite."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import _default_bytes_fetcher


class RemotePixelUnavailable(RuntimeError):pass


class RemotePixelSession:
    def __init__(self,root:Path,*,bytes_fetcher=_default_bytes_fetcher)->None:
        self.root=root.resolve();self.root.mkdir(parents=True,exist_ok=True);self.bytes_fetcher=bytes_fetcher;self.cleared_stale_files=0
        for child in tuple(self.root.iterdir()):
            if child.is_dir():self.cleared_stale_files+=sum(1 for value in child.rglob("*") if value.is_file());shutil.rmtree(child)
        self.directory=self.root/str(uuid.uuid4());self.directory.mkdir()

    def availability(self,repository:ImageAnalysisRepository,item_id:int)->str:
        for row in repository.provenances(item_id):
            if row["local_path"] and Path(str(row["local_path"])).is_file():return "local_available"
        item=repository.get_item(item_id)
        if item and item.cached_path and item.cached_path.is_file() and self.directory in item.cached_path.parents:return "temporary_remote_available"
        return "not_currently_available"

    def ensure(self,repository:ImageAnalysisRepository,item_id:int,providers:dict[str,object])->Path:
        for row in repository.provenances(item_id):
            if row["local_path"] and Path(str(row["local_path"])).is_file():return Path(str(row["local_path"]))
        item=repository.get_item(item_id)
        if item and item.cached_path and item.cached_path.is_file():return item.cached_path
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
        headers={"User-Agent":"BooruFlow/0.1 RemotePixels"};data=self.bytes_fetcher(post.file_url,headers);suffix=Path(post.file_url.split("?",1)[0]).suffix or ".img";path=self.directory/f"{hashlib.sha256(data).hexdigest()}{suffix}";path.write_bytes(data);repository.set_cached_path(item_id,path);return path

    def close(self)->int:
        count=sum(1 for value in self.directory.rglob("*") if value.is_file()) if self.directory.exists() else 0
        if self.directory.exists():shutil.rmtree(self.directory)
        return count
