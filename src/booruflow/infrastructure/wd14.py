"""Optional local WD14 ONNX backend and model artifact diagnostics."""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from booruflow.domain.image_analysis import ModelIdentity
from booruflow.infrastructure.onnx_runtime import (
    OnnxRuntimeDiagnostic,
    create_inference_session,
)

DEFAULT_MODEL_ID = "SmilingWolf/wd-vit-tagger-v3"
MODEL_FILENAME = "model.onnx"
TAGS_FILENAME = "selected_tags.csv"
METADATA_FILENAME = "metadata.json"
PREPROCESSING_VERSION = "wd14-v3-white-square-bicubic-bgr-f32-v1"
CATEGORY_NAMES = {0: "general", 4: "character", 9: "rating"}


class WD14UnavailableError(RuntimeError):
    pass


class WD14OutOfMemoryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WD14Tag:
    raw_name: str
    category: str
    score: float


@dataclass(frozen=True, slots=True)
class WD14Result:
    predictions: tuple[WD14Tag, ...]


@dataclass(frozen=True, slots=True)
class WD14Config:
    model_directory: Path
    model_id: str = DEFAULT_MODEL_ID
    store_threshold: float = 0.10
    provider_preference: tuple[str, ...] = (
        "CUDAExecutionProvider", "CPUExecutionProvider"
    )

    def __post_init__(self) -> None:
        if not 0.0 <= self.store_threshold <= 1.0:
            raise ValueError("WD14 store threshold must be between zero and one")


@dataclass(frozen=True, slots=True)
class WD14Diagnostic:
    available: bool
    model_id: str
    runtime: str = ""
    provider: str = ""
    device: str = ""
    message: str = ""
    onnx: OnnxRuntimeDiagnostic | None = None


def load_selected_tags(path: Path) -> tuple[tuple[str, str], ...]:
    if not path.is_file():
        raise WD14UnavailableError(f"WD14 tags file is missing: {path}")
    rows: list[tuple[str, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not {"name", "category"}.issubset(reader.fieldnames):
                raise WD14UnavailableError("selected_tags.csv has incompatible columns")
            for row in reader:
                name = str(row.get("name", "")).strip()
                category_id = int(row.get("category", -1))
                if name:
                    rows.append((name, CATEGORY_NAMES.get(category_id, f"category_{category_id}")))
    except (OSError, ValueError) as exc:
        raise WD14UnavailableError(f"could not parse selected_tags.csv: {exc}") from exc
    if not rows:
        raise WD14UnavailableError("selected_tags.csv contains no tags")
    return tuple(rows)


def prepare_wd14_image(image: Image.Image, target_size: int):
    """Apply the official v3 ONNX preprocessing and return NHWC float32 BGR."""
    if target_size < 1:
        raise ValueError("target size must be positive")
    try:
        numpy = importlib.import_module("numpy")
    except ImportError as exc:
        raise WD14UnavailableError("NumPy is required for WD14") from exc
    image = ImageOps.exif_transpose(image)
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    canvas.alpha_composite(rgba)
    rgb = canvas.convert("RGB")
    maximum = max(rgb.size)
    left = (maximum - rgb.width) // 2
    top = (maximum - rgb.height) // 2
    square = Image.new("RGB", (maximum, maximum), (255, 255, 255))
    square.paste(rgb, (left, top))
    if maximum != target_size:
        square = square.resize((target_size, target_size), Image.Resampling.BICUBIC)
    array = numpy.asarray(square, dtype=numpy.float32)[:, :, ::-1]
    return numpy.expand_dims(array, axis=0)


class WD14Backend:
    backend = "wd14"
    # WD14 is BooruFlow's Gelbooru/local tagger.  The worker uses this explicit
    # capability marker to prevent the e621-only Hydra route from invoking it.
    supported_sites = frozenset({None, "gelbooru"})

    def __init__(self, config: WD14Config) -> None:
        self.config = config
        self.session = None
        self.tags: tuple[tuple[str, str], ...] = ()
        self.input_name = ""; self.output_name = ""; self.target_size = 0
        self.runtime = ""; self.provider = ""; self.device = ""
        self.runtime_diagnostic = OnnxRuntimeDiagnostic(False)
        self.identity = ModelIdentity("wd14", config.model_id, "unprepared", "", "")

    @property
    def model_path(self) -> Path:
        return self.config.model_directory / MODEL_FILENAME

    @property
    def tags_path(self) -> Path:
        return self.config.model_directory / TAGS_FILENAME

    def prepare(self,trace=None) -> None:
        if not self.model_path.is_file():
            raise WD14UnavailableError(f"WD14 model is missing: {self.model_path}")
        metadata_path = self.config.model_directory / METADATA_FILENAME
        if not metadata_path.is_file():
            raise WD14UnavailableError(f"WD14 metadata is missing: {metadata_path}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            version = str(metadata["model_sha256"])
            model_id = str(metadata["model_id"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WD14UnavailableError(f"WD14 metadata is invalid: {exc}") from exc
        if model_id != self.config.model_id or len(version) != 64:
            raise WD14UnavailableError("WD14 metadata identifies an unexpected model or version")
        self.tags = load_selected_tags(self.tags_path)
        try:
            arguments=(self.model_path,self.config.provider_preference)
            self.session, self.runtime_diagnostic = create_inference_session(
                *arguments,trace=trace
            ) if trace else create_inference_session(*arguments)
            self.runtime = f"onnxruntime {self.runtime_diagnostic.runtime_version}"
            active_providers = self.runtime_diagnostic.active_providers
            self.provider = active_providers[0]
            self.device = "cuda" if self.provider == "CUDAExecutionProvider" else "cpu"
            input_meta = self.session.get_inputs()[0]
            output_meta = self.session.get_outputs()[0]
            shape = input_meta.shape
            self.target_size = int(shape[1])
            if len(shape) != 4 or int(shape[2]) != self.target_size or int(shape[3]) != 3:
                raise WD14UnavailableError(f"unexpected WD14 input shape: {shape}")
            output_count = output_meta.shape[-1]
            if isinstance(output_count, int) and output_count != len(self.tags):
                raise WD14UnavailableError(
                    f"WD14 output count {output_count} does not match {len(self.tags)} tags"
                )
            self.input_name = input_meta.name; self.output_name = output_meta.name
        except WD14UnavailableError:
            self.close(); raise
        except Exception as exc:
            self.close()
            raise WD14UnavailableError(f"could not initialize WD14 ONNX model: {exc}") from exc
        config_value = json.dumps({
            "model_id": model_id, "model_sha256": version,
            "preprocessing": PREPROCESSING_VERSION,
            "store_threshold": self.config.store_threshold,
        }, sort_keys=True, separators=(",", ":"))
        config_hash = hashlib.sha256(config_value.encode("utf-8")).hexdigest()
        self.identity = ModelIdentity(
            "wd14", model_id, version, config_hash, self.device
        )

    def analyze(self, path: Path) -> WD14Result:
        if self.session is None:
            self.prepare()
        try:
            with Image.open(path) as image:
                tensor = prepare_wd14_image(image, self.target_size)
            scores = self.session.run([self.output_name], {self.input_name: tensor})[0][0]
        except Exception as exc:
            message = str(exc)
            if "out of memory" in message.casefold() or "cuda" in message.casefold() and "alloc" in message.casefold():
                raise WD14OutOfMemoryError(message) from exc
            raise RuntimeError(f"WD14 inference failed: {message}") from exc
        predictions = tuple(
            WD14Tag(name, category, float(score))
            for (name, category), score in zip(self.tags, scores, strict=True)
            if float(score) >= self.config.store_threshold
        )
        return WD14Result(predictions)

    def close(self) -> None:
        self.session = None


def diagnose_wd14(config: WD14Config) -> WD14Diagnostic:
    backend = WD14Backend(config)
    try:
        backend.prepare()
        return WD14Diagnostic(
            True, config.model_id, backend.runtime, backend.provider, backend.device,
            backend.runtime_diagnostic.message, backend.runtime_diagnostic,
        )
    except WD14UnavailableError as exc:
        return WD14Diagnostic(False, config.model_id, message=str(exc))
    finally:
        backend.close()
