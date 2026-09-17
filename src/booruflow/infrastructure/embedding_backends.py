"""Optional production adapters for the validated Author_ID and OpenCLIP spaces.

Author_ID: EXIF-transposed RGBA composited on white, RGB bilinear 384x384,
float32 [0,1], ImageNet mean/std, CHW. Output is /backbone/Div_output_0 (512D).
OpenCLIP: EXIF-aware RGB input passed to the exact transform published with the
selected checkpoint (ViT-B/32 by default); its resize/crop and normalization are
therefore checkpoint-owned rather than shared with Author_ID.
"""

from __future__ import annotations

from pathlib import Path

from booruflow.application.embedding import EmbeddingResult
from booruflow.domain.similar_artists import EmbeddingSpace
from booruflow.experiments.embedding_benchmark import (
    AuthorIdEmbeddingBackend as _AuthorId,
)
from booruflow.experiments.embedding_benchmark import OpenClipBackend as _OpenClip


class AuthorIdEmbeddingBackend(_AuthorId):
    runtime = "onnxruntime"

    @property
    def space(self) -> EmbeddingSpace:
        return EmbeddingSpace(
            self.identity.backend, self.identity.model, self.identity.version,
            self.identity.key, self.dimensions, "float32", True,
            self.runtime, self.device,
        )

    def encode(self, image: Path) -> EmbeddingResult:
        vector = super().encode(image)
        return EmbeddingResult(self.space, tuple(float(value) for value in vector))

    analyze = encode


class OpenClipEmbeddingBackend(_OpenClip):
    runtime = "pytorch/open_clip"

    @property
    def space(self) -> EmbeddingSpace:
        return EmbeddingSpace(
            self.identity.backend, self.identity.model, self.identity.version,
            self.identity.key, self.dimensions or 512, "float32", True,
            self.runtime, self.device,
        )

    def encode(self, image: Path) -> EmbeddingResult:
        vector = super().encode(image)
        return EmbeddingResult(self.space, tuple(float(value) for value in vector))

    analyze = encode
