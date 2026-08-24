import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from booruflow.infrastructure.onnx_runtime import (
    create_inference_session,
    diagnose_runtime,
    expected_nvidia_versions,
)


class Session:
    active = ("CPUExecutionProvider",)

    def __init__(self, _model, providers): self.requested = providers
    def get_providers(self): return list(self.active)


class OnnxRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.model = Path(self.temporary.name) / "m.onnx"
        self.model.write_bytes(b"model")

    def tearDown(self): self.temporary.cleanup()

    @staticmethod
    def runtime(preload=None):
        value = types.SimpleNamespace(
            __version__="1.29.0",
            get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
            InferenceSession=Session,
        )
        if preload is not None: value.preload_dlls = preload
        return value

    def test_cuda_announced_but_missing_dll_falls_back_to_cpu(self):
        with patch("booruflow.infrastructure.onnx_runtime.import_onnxruntime",
                   return_value=self.runtime(lambda **_kwargs: None)):
            _session, diagnostic = create_inference_session(
                self.model, ("CUDAExecutionProvider", "CPUExecutionProvider")
            )
        self.assertEqual(diagnostic.effective_provider, "CPUExecutionProvider")
        self.assertIn("fell back", diagnostic.message)

    def test_preload_from_python_packages_and_cuda_active(self):
        calls = []
        Session.active = ("CUDAExecutionProvider", "CPUExecutionProvider")
        try:
            with patch("booruflow.infrastructure.onnx_runtime.import_onnxruntime",
                       return_value=self.runtime(lambda **kwargs: calls.append(kwargs))):
                _session, diagnostic = create_inference_session(
                    self.model, ("CUDAExecutionProvider", "CPUExecutionProvider")
                )
            self.assertEqual(calls, [{"cuda": True, "cudnn": True, "msvc": True, "directory": ""}])
            self.assertTrue(diagnostic.preload_succeeded)
            self.assertEqual(diagnostic.effective_provider, "CUDAExecutionProvider")
        finally:
            Session.active = ("CPUExecutionProvider",)

    def test_preload_failure_is_diagnostic_and_cpu_remains_usable(self):
        def fail(**_kwargs): raise RuntimeError("missing DLL")
        with patch("booruflow.infrastructure.onnx_runtime.import_onnxruntime",
                   return_value=self.runtime(fail)):
            _session, diagnostic = create_inference_session(
                self.model, ("CUDAExecutionProvider", "CPUExecutionProvider")
            )
        self.assertFalse(diagnostic.preload_succeeded)
        self.assertIn("missing DLL", diagnostic.message)

    def test_cuda_session_exception_retries_cpu(self):
        calls = []
        class RetrySession(Session):
            def __init__(self, _model, providers):
                calls.append(tuple(providers))
                if "CUDAExecutionProvider" in providers:
                    raise RuntimeError("CUDA DLL missing")
                self.requested = providers
        runtime = self.runtime(lambda **_kwargs: None)
        runtime.InferenceSession = RetrySession
        with patch("booruflow.infrastructure.onnx_runtime.import_onnxruntime",
                   return_value=runtime):
            _session, diagnostic = create_inference_session(
                self.model, ("CUDAExecutionProvider", "CPUExecutionProvider")
            )
        self.assertEqual(calls, [
            ("CUDAExecutionProvider", "CPUExecutionProvider"), ("CPUExecutionProvider",)
        ])
        self.assertIn("CPU fallback succeeded", diagnostic.message)

    def test_absent_onnxruntime_package(self):
        with patch("booruflow.infrastructure.onnx_runtime.import_onnxruntime",
                   side_effect=ImportError):
            diagnostic = diagnose_runtime(self.model, ("CPUExecutionProvider",))
        self.assertFalse(diagnostic.installed)

    def test_expected_versions(self):
        self.assertEqual(expected_nvidia_versions("1.29.0"), ("13.0", "9.x"))
        self.assertEqual(expected_nvidia_versions("1.22.0"), ("12.8", "9.x"))


if __name__ == "__main__": unittest.main()
