"""Optional local WD14 ONNX backend and model artifact diagnostics."""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
import json
from collections import deque
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageOps

from booruflow.domain.image_analysis import ModelIdentity
from booruflow.infrastructure.onnx_runtime import (
    OnnxRuntimeDiagnostic,
    create_inference_session,
    serialized_wd14_execution,
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


def resolve_wd14_batch_size(selection: str, provider: str) -> int:
    """Resolve the conservative first-pass batch size for a provider."""
    value = str(selection or "auto").casefold()
    if value != "auto":
        try:
            return max(1, int(value))
        except ValueError:
            return 1
    return 4 if provider == "CUDAExecutionProvider" else 1


@dataclass(frozen=True, slots=True)
class WD14Config:
    model_directory: Path
    model_id: str = DEFAULT_MODEL_ID
    store_threshold: float = 0.10
    provider_preference: tuple[str, ...] = (
        "CUDAExecutionProvider", "CPUExecutionProvider"
    )
    input_strategy: str = "native_v1"

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


def wd14_config_identity(config: WD14Config) -> ModelIdentity:
    """Resolve the persistent WD14 identity without loading the ONNX runtime."""
    metadata_path = config.model_directory / METADATA_FILENAME
    if not metadata_path.is_file():
        raise WD14UnavailableError(f"WD14 metadata is missing: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        version = str(metadata["model_sha256"])
        model_id = str(metadata["model_id"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WD14UnavailableError(f"WD14 metadata is invalid: {exc}") from exc
    if model_id != config.model_id or len(version) != 64:
        raise WD14UnavailableError("WD14 metadata identifies an unexpected model or version")
    identity_payload = {
        "model_id": model_id, "model_sha256": version,
        "preprocessing": PREPROCESSING_VERSION,
        "store_threshold": config.store_threshold,
    }
    # Keep the historical native-image identity stable; only the targeted
    # Gelbooru sample strategy needs a distinct cache namespace.
    if config.input_strategy != "native_v1":
        identity_payload["input_strategy"] = config.input_strategy
    config_value = json.dumps(identity_payload, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_value.encode("utf-8")).hexdigest()
    return ModelIdentity("wd14", model_id, version, config_hash, "")


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
        self.effective_batch_size: int | None = None
        self.last_batch_metrics: dict[str, float | int | str] = {}
        self._trace = None
        self._first_batch_logged = False
        self._first_inference_logged = False

    @property
    def model_path(self) -> Path:
        return self.config.model_directory / MODEL_FILENAME

    @property
    def tags_path(self) -> Path:
        return self.config.model_directory / TAGS_FILENAME

    def prepare(self,trace=None) -> None:
        started = perf_counter()
        self._trace = trace
        if trace:
            trace("WD14 model preparation started")
        if not self.model_path.is_file():
            raise WD14UnavailableError(f"WD14 model is missing: {self.model_path}")
        persistent_identity = wd14_config_identity(self.config)
        labels_started = perf_counter()
        self.tags = load_selected_tags(self.tags_path)
        if trace:
            trace(
                f"WD14 labels/configuration loaded elapsed_ms={(perf_counter() - labels_started) * 1000:.1f}"
            )
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
        self.identity = ModelIdentity(
            persistent_identity.backend, persistent_identity.name,
            persistent_identity.version, persistent_identity.configuration_hash,
            self.device,
        )
        if trace:
            trace(
                f"WD14 model preparation completed provider={self.provider} "
                f"elapsed_ms={(perf_counter() - started) * 1000:.1f}"
            )

    def _result_from_scores(self, scores) -> WD14Result:
        predictions = tuple(
            WD14Tag(name, category, float(score))
            for (name, category), score in zip(self.tags, scores, strict=True)
            if float(score) >= self.config.store_threshold
        )
        return WD14Result(predictions)

    def _prepare_paths(
        self, paths: Sequence[Path | bytes], *, workers: int = 1
    ):
        import numpy

        def prepare(source: Path | bytes):
            image_started = perf_counter()
            stream = io.BytesIO(source) if isinstance(source, bytes) else source
            with Image.open(stream) as image:
                tensor = prepare_wd14_image(image, self.target_size)
            if self._trace and not self._first_batch_logged:
                label = "memory" if isinstance(source, bytes) else source.name
                self._trace(
                    f"WD14 first image read/decode/preprocess completed source={label} "
                    f"elapsed_ms={(perf_counter() - image_started) * 1000:.1f}"
                )
            return tensor

        if workers <= 1 or len(paths) <= 1:
            return numpy.concatenate([prepare(path) for path in paths], axis=0)
        with ThreadPoolExecutor(max_workers=max(1, int(workers)), thread_name_prefix="wd14-prep") as executor:
            return numpy.concatenate(list(executor.map(prepare, paths)), axis=0)

    def _run_tensor_batch_once(self, tensor, count: int) -> tuple[WD14Result, ...]:
        if self.session is None:
            self.prepare()
        run_started = perf_counter()
        if self._trace and not self._first_inference_logged:
            self._trace(f"WD14 first inference started batch_size={count}")
        try:
            with serialized_wd14_execution():
                scores = self.session.run([self.output_name], {self.input_name: tensor})[0]
            run_ms = (perf_counter() - run_started) * 1000
        except Exception as exc:
            message = str(exc)
            if "out of memory" in message.casefold() or (
                "cuda" in message.casefold() and "alloc" in message.casefold()
            ):
                raise WD14OutOfMemoryError(message) from exc
            raise RuntimeError(f"WD14 inference failed: {message}") from exc
        if len(scores) != count:
            raise RuntimeError(
                f"WD14 batch output count {len(scores)} does not match input count {count}"
            )
        post_started = perf_counter()
        results = tuple(self._result_from_scores(row) for row in scores)
        self.last_batch_metrics = {
            "provider": self.provider,
            "batch_size": count,
            "preprocess_ms": 0.0,
            "session_run_ms": run_ms,
            "postprocess_ms": (perf_counter() - post_started) * 1000,
            "total_ms": (perf_counter() - run_started) * 1000,
        }
        if self._trace and not self._first_inference_logged:
            self._trace(
                f"WD14 first inference completed elapsed_ms={(perf_counter() - run_started) * 1000:.1f}"
            )
            self._first_inference_logged = True
        return results

    def _run_batch_once(
        self, paths: Sequence[Path | bytes], *, preprocess_workers: int = 1
    ) -> tuple[WD14Result, ...]:
        started = perf_counter()
        if self._trace and not self._first_batch_logged:
            self._trace(f"WD14 first batch prepare started batch_size={len(paths)}")
        tensor = self._prepare_paths(paths, workers=preprocess_workers)
        if self._trace and not self._first_batch_logged:
            self._trace(
                f"WD14 first batch tensor constructed shape={getattr(tensor, 'shape', None)} "
                f"elapsed_ms={(perf_counter() - started) * 1000:.1f}"
            )
            self._first_batch_logged = True
        results = self._run_tensor_batch_once(tensor, len(paths))
        self.last_batch_metrics["preprocess_ms"] = (perf_counter() - started) * 1000 - float(
            self.last_batch_metrics.get("session_run_ms", 0.0)
        ) - float(self.last_batch_metrics.get("postprocess_ms", 0.0))
        self.last_batch_metrics["total_ms"] = (perf_counter() - started) * 1000
        return results

    def analyze_batch(
        self, paths: Sequence[Path | bytes], *, batch_size: int | None = None,
        preprocess_workers: int = 1, pipeline_queue: int = 2,
    ) -> tuple[WD14Result, ...]:
        """Analyze multiple images using one session and one run per sub-batch."""
        if not paths:
            return ()
        requested = max(1, int(batch_size or self.effective_batch_size or len(paths)))
        if self.effective_batch_size is not None:
            requested = min(requested, self.effective_batch_size)
        results: list[WD14Result] = []
        index = 0
        if requested == 1:
            while index < len(paths):
                results.extend(self._run_batch_once(paths[index:index + 1], preprocess_workers=1))
                index += 1
            return tuple(results)
        with ThreadPoolExecutor(
            max_workers=max(1, int(preprocess_workers)), thread_name_prefix="wd14-prep"
        ) as executor:
            pending: deque[Future] = deque()
            next_submit = 0
            while index < len(paths):
                current = min(requested, len(paths) - index)
                capacity = max(1, int(pipeline_queue)) * requested
                while next_submit < len(paths) and len(pending) < capacity:
                    pending.append(executor.submit(self._prepare_paths, (paths[next_submit],), workers=1))
                    next_submit += 1
                batch_futures = [pending.popleft() for _ in range(current)]
                try:
                    import numpy
                    if self._trace and not self._first_batch_logged:
                        self._trace(f"WD14 first batch prepare started batch_size={current}")
                    tensor = numpy.concatenate([future.result() for future in batch_futures], axis=0)
                    if self._trace and not self._first_batch_logged:
                        self._trace(
                            f"WD14 first batch tensor constructed shape={getattr(tensor, 'shape', None)}"
                        )
                        self._first_batch_logged = True
                    chunk = self._run_tensor_batch_once(tensor, current)
                except WD14OutOfMemoryError:
                    if current <= 1:
                        raise
                    current = max(1, current // 2)
                    self.effective_batch_size = current
                    pending.clear()
                    next_submit = index
                    requested = current
                    continue
                results.extend(chunk)
                if len(paths) > 1 and len(paths) - index >= requested:
                    self.effective_batch_size = (
                        current if self.effective_batch_size is None
                        else min(self.effective_batch_size, current)
                    )
                index += current
        return tuple(results)

    def analyze(self, path: Path | bytes) -> WD14Result:
        return self.analyze_batch([path], batch_size=1)[0]

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
