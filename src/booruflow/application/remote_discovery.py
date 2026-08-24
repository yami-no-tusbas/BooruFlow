"""Two-stage remote artist discovery: tags generate candidates, embeddings rank them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import sleep

from booruflow.application.similar_artists import ArtistProfileService
from booruflow.domain.similar_artists import ArtistIdentity, SimilarityQueryProfile
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService
from booruflow.infrastructure.remote_pixels import RemotePixelSession

GENERIC_TAGS={"1girl","1boy","solo","looking_at_viewer","male","female","general","safe"}
MODE_BUDGETS={"quick":(20,8),"normal":(60,16),"large":(120,30)}


def dominant_source_artists(repository:ImageAnalysisRepository,item_ids:tuple[int,...]|list[int])->set[tuple[str,str]]:
    counts={}
    for item_id in item_ids:
        for site,tag in set(repository.item_artist_identities(item_id)):counts[(site,tag.casefold())]=counts.get((site,tag.casefold()),0)+1
    return {identity for identity,count in counts.items() if len(item_ids)==1 or count/max(1,len(item_ids))>=.75}


@dataclass(frozen=True,slots=True)
class RemoteDiscoveryResult:
    artist:ArtistIdentity
    similarity:float
    image_count:int
    is_new:bool
    collection_state:str


class RemoteDiscoveryService:
    def __init__(self,repository:ImageAnalysisRepository,pixels:RemotePixelSession,providers:dict[str,object],encoder:Callable[[list[int]],dict],*,throttle_seconds:float=0,retry_delays:tuple[float,...]=(2,5,10))->None:
        self.repository=repository;self.pixels=pixels;self.providers=providers;self.encoder=encoder;self.profiles=ArtistProfileService(repository);self.throttle_seconds=max(0,throttle_seconds);self.retry_delays=retry_delays

    def _retry(self, operation: Callable[[], object], *, site: str, phase: str, artist: str = "", post_id: str = "", progress: Callable[[dict], None] | None = None):
        """Retry transient provider failures without sacrificing the rest of a job."""
        for attempt in range(1, len(self.retry_delays) + 2):
            try:
                return operation()
            except Exception as exc:  # provider boundary: malformed/deleted posts are isolated too
                message = str(exc)
                transient = any(value in message for value in ("429", "500", "502", "503", "504", "timeout", "timed out", "temporar"))
                if not transient or attempt > len(self.retry_delays):
                    raise
                delay = self.retry_delays[attempt - 1]
                if progress:
                    progress({"phase":"retry","site":site,"role":phase,"artist":artist,"post_ref":f"{site}:{post_id}" if post_id else "","attempt":attempt,"attempt_total":len(self.retry_delays)+1,"delay":delay,"network_errors":1})
                sleep(delay)

    def distinctive_tags(self,item_ids:tuple[int,...],limit:int=8)->list[str]:
        candidates=set()
        for item_id in item_ids:candidates.update(tag.name for tag in self.repository.source_tags(item_id) if tag.category not in {"artist","rating"})
        values=[]
        for tag in candidates:
            if tag.casefold() in GENERIC_TAGS:continue
            frequency=int(self.repository.connection.execute("SELECT COUNT(DISTINCT item_id) FROM source_tags WHERE tag_name=? COLLATE NOCASE",(tag,)).fetchone()[0]);values.append((frequency,tag))
        return [tag for _frequency,tag in sorted(values,key=lambda value:(value[0],value[1].casefold()))[:limit]]

    def discover(self,query:SimilarityQueryProfile,backend:str="author_id_embedding",mode:str="normal",*,progress:Callable[[dict],None]|None=None,cancelled:Callable[[],bool]|None=None,only_new:bool=False)->list[RemoteDiscoveryResult]:
        candidate_budget,sample_budget=MODE_BUDGETS.get(mode,MODE_BUDGETS["normal"]);tags=self.distinctive_tags(query.item_ids);identities=[];queries_sent=posts_received=0;images_ignored=network_errors=0
        source_artists=dominant_source_artists(self.repository,query.item_ids)
        if progress:progress({"phase":"distinctive_tags","tags":len(tags),"queries":0,"posts":0,"artists":0,"filtered":0,"images":0,"profiles":0,"evaluated":0})
        if not tags:
            if progress:progress({"phase":"finished","reason":"Aucun tag distinctif exploitable","tags":0,"queries":0,"posts":0,"artists":0,"filtered":0,"images":0,"profiles":0,"evaluated":0})
            return []
        per_site=max(1,candidate_budget//max(1,len(self.providers)))
        for site,provider in self.providers.items():
            queries=[tags[:3],*([tag] for tag in tags[:3])]
            for discovery_tags in queries:
                queries_sent+=1
                query_count = len(queries)
                try:
                    found=self._retry(lambda provider=provider, discovery_tags=discovery_tags, query_count=query_count:provider.discover_candidates(discovery_tags,max(1,per_site//query_count)),site=site,phase="candidate_discovery",progress=progress)
                except Exception as exc:  # noqa: BLE001 - external provider boundary
                    network_errors+=1
                    if progress:progress({"phase":"warning","site":site,"role":"candidate_discovery","result":str(exc),"queries":queries_sent,"posts":posts_received,"network_errors":network_errors})
                    continue
                posts_received+=int(getattr(provider,"last_search_post_count",0))
                for tag in found:
                    identity=ArtistIdentity(site,str(tag))
                    if (identity.site,identity.tag.casefold()) not in source_artists and identity not in identities:identities.append(identity)
                if self.throttle_seconds:sleep(self.throttle_seconds)
            identities=identities[:candidate_budget]
        original_profiles={identity:self.profiles.get_profile(identity) for identity in identities};known=sum(value is not None for value in original_profiles.values())
        if only_new:identities=[identity for identity in identities if original_profiles[identity] is None]
        if progress:progress({"phase":"candidates","tags":len(tags),"queries":queries_sent,"posts":posts_received,"candidates":len(original_profiles),"artists":len(original_profiles),"known":known,"new":len(original_profiles)-known,"filtered":len(identities),"images":0,"profiles":0,"evaluated":0})
        if not identities:
            reason="Tous les candidats ont été filtrés" if original_profiles else "Impossible d’extraire les artistes" if posts_received else "Aucun post reçu"
            if progress:progress({"phase":"finished","reason":reason,"tags":len(tags),"queries":queries_sent,"posts":posts_received,"artists":len(original_profiles),"filtered":0,"images":0,"profiles":0,"evaluated":0})
            return []
        sources=ImageSourceService(self.repository,self.pixels.directory,bytes_fetcher=self.pixels.bytes_fetcher);images=0
        for processed,identity in enumerate(identities,1):
            if cancelled and cancelled():break
            profile=original_profiles[identity]
            if profile is None:
                item_ids=[]
                try:
                    sampled=self._retry(lambda identity=identity:self.providers[identity.site].sample_artist_posts(identity.tag,sample_budget),site=identity.site,phase="candidate_sample",artist=identity.tag,progress=progress)
                except Exception as exc:  # noqa: BLE001 - external provider boundary
                    network_errors+=1
                    if progress:progress({"phase":"warning","site":identity.site,"role":"candidate_sample","artist":identity.tag,"result":str(exc),"network_errors":network_errors})
                    sampled=[]
                for post in sampled:
                    if cancelled and cancelled():
                        break

                    try:
                        item_id = sources.add_post(
                            _StaticPostProvider(post),
                            post.post_id,
                            request_analysis=False,
                        )
                    except Exception as exc:  # noqa: BLE001 - one post must not fail the artist
                        # Un post supprimé, sans image ou autrement inutilisable
                        # ne doit pas interrompre toute la découverte distante.
                        images_ignored += 1
                        if progress:progress({"phase":"warning","site":identity.site,"role":"candidate_sample","artist":identity.tag,"post_ref":f"{identity.site}:{post.post_id}","result":str(exc),"images_ignored":images_ignored,"network_errors":network_errors})
                        continue

                    item_ids.append(item_id)
                    images += 1
                if item_ids:self.encoder(list(dict.fromkeys(item_ids)));self.profiles.build_profile(identity,force=True)
                if self.throttle_seconds:sleep(self.throttle_seconds)
            self.repository.touch_remote_artist(identity.site,identity.tag,used=True)
            if progress:progress({"phase":"profiles","tags":len(tags),"queries":queries_sent,"posts":posts_received,"artists":len(original_profiles),"filtered":len(identities),"processed":processed,"artist_total":len(identities),"images":images,"images_ignored":images_ignored,"profiles":processed,"evaluated":0,"network_errors":network_errors})
        rankings={row.artist:row for row in self.profiles.rank_artists_for_query(query,backend,limit=100000)};results=[]
        for identity in identities:
            ranking=rankings.get(identity);profile=self.profiles.get_profile(identity)
            if ranking and profile:results.append(RemoteDiscoveryResult(identity,ranking.centroid_similarity,profile.image_count,original_profiles[identity] is None,self.repository.artist_collection_state(identity.site,identity.tag)))
        results=sorted(results,key=lambda value:(-value.similarity,value.artist))
        if progress:progress({"phase":"finished","reason":"" if results else "Aucune image candidate exploitable","tags":len(tags),"queries":queries_sent,"posts":posts_received,"artists":len(original_profiles),"filtered":len(identities),"images":images,"images_ignored":images_ignored,"profiles":len(identities),"evaluated":len(results),"network_errors":network_errors})
        return results


class _StaticPostProvider:
    def __init__(self,post)->None:self.post=post
    def fetch_post(self,_post_id):return self.post
