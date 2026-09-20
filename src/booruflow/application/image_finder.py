"""Application orchestration for site-neutral image discovery."""

from __future__ import annotations

from booruflow.domain.image_finder import ImageSource, SearchQuery, SearchResult


class ImageFinderService:
    def __init__(self, sources: tuple[ImageSource, ...]) -> None:
        self._sources = {source.source_id: source for source in sources}

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(self._sources)

    def search(self, source_id: str, query: SearchQuery) -> SearchResult:
        try:
            source = self._sources[source_id]
        except KeyError as exc:
            raise ValueError(f"unknown image source: {source_id}") from exc
        return source.search(query)

    def artwork(self, source_id: str, artwork_id: str):
        try:
            source = self._sources[source_id]
        except KeyError as exc:
            raise ValueError(f"unknown image source: {source_id}") from exc
        return source.artwork(artwork_id)
