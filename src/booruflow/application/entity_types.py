"""Catégories de tags prises en charge par Artist by Tag."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityType:
    key: str
    label: str
    plural: str
    gelbooru_category: int | None
    e621_category: int
    e621_post_key: str

    @property
    def candidate_filename(self) -> str:
        return f"{self.plural}_candidats_uniques.txt"

    @property
    def sites_filename(self) -> str:
        return f"{self.plural}_candidats_sites.tsv"


ENTITY_TYPES = {
    "artists": EntityType("artists", "Artistes", "artistes", 1, 1, "artist"),
    "copyrights": EntityType("copyrights", "Copyrights", "copyrights", 3, 3, "copyright"),
    "characters": EntityType("characters", "Personnages", "personnages", 4, 4, "character"),
    "species": EntityType("species", "Espèces", "especes", None, 5, "species"),
}


def entity_type(key: str) -> EntityType:
    return ENTITY_TYPES[key]
