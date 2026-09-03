"""Controlled diagnostics for persistent embeddings and artist profiles."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from booruflow.application.embedding import EmbeddingIndexService
from booruflow.application.similar_artists import ArtistProfileService
from booruflow.domain.similar_artists import ArtistIdentity
from booruflow.infrastructure.embedding_backends import (
    AuthorIdEmbeddingBackend,
    OpenClipEmbeddingBackend,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository


def _artist(value: str) -> ArtistIdentity:
    try:
        site, tag = value.split(":", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("artist must be SITE:TAG") from exc
    try:
        return ArtistIdentity(site, tag)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="BooruFlow Similar Artists foundation")
    result.add_argument("--database", type=Path, default=Path("var/state/image_analysis.sqlite"))
    commands = result.add_subparsers(dest="command", required=True)
    encode = commands.add_parser("encode-missing")
    encode.add_argument("--backend", choices=("author_id", "openclip"), required=True)
    scope = encode.add_mutually_exclusive_group()
    scope.add_argument("--item", type=int); scope.add_argument("--artist", type=_artist)
    encode.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    encode.add_argument("--model-path", type=Path)
    encode.add_argument("--derived-model", type=Path)
    encode.add_argument("--model", default="ViT-B-32")
    encode.add_argument("--pretrained", default="laion2b_s34b_b79k")
    commands.add_parser("build-profiles")
    image = commands.add_parser("image"); image.add_argument("item_id", type=int)
    image.add_argument("--backend", default="author_id_embedding"); image.add_argument("--limit", type=int, default=20)
    artist = commands.add_parser("artist"); artist.add_argument("artist", type=_artist)
    artist.add_argument("--backend", default="author_id_embedding"); artist.add_argument("--limit", type=int, default=20)
    listing = commands.add_parser("profiles"); listing.add_argument("--details", action="store_true")
    return result


def _backend(args):
    if args.backend == "author_id":
        if args.model_path is None:
            raise ValueError("--model-path is required for Author_ID")
        derived = args.derived_model or Path("var/models/author-id-embedding.onnx")
        return AuthorIdEmbeddingBackend(args.model_path, derived, args.device)
    return OpenClipEmbeddingBackend(args.model, args.pretrained, args.device)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        with ImageAnalysisRepository(args.database) as repository:
            profiles = ArtistProfileService(repository, logger=lambda value: print(value, file=sys.stderr))
            if args.command == "encode-missing":
                index = EmbeddingIndexService(repository, logger=lambda value: print(value, file=sys.stderr))
                backend = _backend(args)
                item_ids = ([args.item] if args.item is not None
                            else index.eligible_item_ids(args.artist))
                missing = index.missing_item_ids(backend, item_ids)
                print(json.dumps({"images_eligible": len(item_ids),
                                  "embeddings_missing": len(missing)}))
                payload = index.encode_missing(backend, item_ids)
            elif args.command == "build-profiles":
                payload = profiles.build_all()
                payload["artists_profiled"] = len(profiles.list_profiled_artists())
            elif args.command == "image":
                payload = [asdict(row) for row in profiles.rank_artists_for_image(
                    args.item_id, args.backend, limit=args.limit
                )]
            elif args.command == "artist":
                payload = [asdict(row) for row in profiles.rank_artists_for_artist(
                    args.artist, args.backend, limit=args.limit
                )]
            else:
                values = profiles.list_profiled_artists()
                payload = [asdict(value) if args.details else {
                    "site": value.artist.site, "artist": value.artist.tag,
                    "images": value.image_count, "confidence": value.confidence_level,
                    "backends": sorted({item.space.backend for item in value.embeddings.values()}),
                } for value in values]
        print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"ERROR {exc}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
