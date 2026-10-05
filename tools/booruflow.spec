# PyInstaller onedir build for the first portable BooruFlow alpha.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

project = Path(SPECPATH).parent
src = project / "src"
runtime_profile = os.environ.get("BOORUFLOW_PORTABLE_RUNTIME_PROFILE", "CPU").upper()
if runtime_profile not in {"CPU", "CUDA"}:
    raise ValueError(f"Unsupported portable runtime profile: {runtime_profile}")

profile_binaries = []
profile_datas = []
if runtime_profile == "CUDA":
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    cuda_binaries = [
        ("onnxruntime/capi/onnxruntime_providers_cuda.dll", "onnxruntime/capi"),
        ("nvidia/cu13/bin/x86_64/cublasLt64_13.dll", "nvidia/cu13/bin/x86_64"),
        ("nvidia/cu13/bin/x86_64/cublas64_13.dll", "nvidia/cu13/bin/x86_64"),
        ("nvidia/cu13/bin/x86_64/cufft64_12.dll", "nvidia/cu13/bin/x86_64"),
        ("nvidia/cu13/bin/x86_64/cudart64_13.dll", "nvidia/cu13/bin/x86_64"),
        ("nvidia/cudnn/bin/cudnn_engines_runtime_compiled64_9.dll", "nvidia/cudnn/bin"),
        ("nvidia/cudnn/bin/cudnn_engines_precompiled64_9.dll", "nvidia/cudnn/bin"),
        ("nvidia/cudnn/bin/cudnn_heuristic64_9.dll", "nvidia/cudnn/bin"),
        ("nvidia/cudnn/bin/cudnn_ops64_9.dll", "nvidia/cudnn/bin"),
        ("nvidia/cudnn/bin/cudnn_adv64_9.dll", "nvidia/cudnn/bin"),
        ("nvidia/cudnn/bin/cudnn_graph64_9.dll", "nvidia/cudnn/bin"),
        ("nvidia/cudnn/bin/cudnn64_9.dll", "nvidia/cudnn/bin"),
        ("nvidia/cudnn/bin/cudnn_engines_tensor_ir64_9.dll", "nvidia/cudnn/bin"),
    ]
    missing = [relative for relative, _destination in cuda_binaries
               if not (site_packages / relative).is_file()]
    if missing:
        raise FileNotFoundError(
            "CUDA portable dependencies are missing: " + ", ".join(missing)
        )
    profile_binaries = [
        (str(site_packages / relative), destination)
        for relative, destination in cuda_binaries
    ]
    profile_datas = [
        *copy_metadata("nvidia-cuda-runtime"),
        *copy_metadata("nvidia-cudnn-cu13"),
    ]

hiddenimports = [
    "booruflow.worker.image_analysis_bootstrap",
    "booruflow.worker.image_analysis",
    "booruflow.cli.similar_artists",
    "booruflow.cli.gelbooru_aliases_update",
    "booruflow.cli.gelbooru_tags_update",
    "booruflow.cli.gelbooru_snapshot",
    "booruflow.cli.e621_tags_update",
    "booruflow.cli.wd14_model",
    "booruflow.cli.hydra_model",
    "booruflow.cli.gelbooru_scan",
    "booruflow.cli.e621_scan",
    "booruflow.cli.thumbnail_probe",
    "booruflow.cli.helper_io_probe",
    "onnxruntime",
]
hiddenimports += collect_submodules("booruflow.infrastructure.schema")

a = Analysis(
    [str(src / "booruflow" / "__main__.py")],
    pathex=[str(src)],
    binaries=profile_binaries,
    datas=[
        *profile_datas,
        (str(project / "resources" / "i18n"), "resources/i18n"),
        (str(project / "data" / "taxonomy" / "tag_organization.json"), "data/taxonomy"),
        *[
            (str(path), "booruflow/infrastructure/schema")
            for path in sorted((src / "booruflow" / "infrastructure" / "schema").glob("*.sql"))
        ],
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project / "tools" / "runtime_qt_path.py")],
    # Optional similarity/Hydra stacks are intentionally not part of the CPU alpha.
    # Explicit exclusions prevent PyInstaller hooks from pulling multi-GB CUDA wheels.
    excludes=[
        "tests", "pytest", "torch", "torchvision", "torchaudio", "timm",
        "open_clip", "tensorflow", "onnxruntime_gpu", "nvidia", "triton",
    ],
    noarchive=False,
)
# The Qt wheel resolves ICU from the host Windows runtime.  PyInstaller can
# otherwise pick an unrelated icuuc.dll from PATH (for example Poppler ICU 78),
# which is ABI-incompatible with this Qt6Core.dll.  Keep the final bundle free
# of that ambient DLL rather than copying one from another application.
a.binaries = [
    entry
    for entry in a.binaries
    if Path(entry[0]).name.casefold() not in {
        "icuuc.dll", "icudt.dll", "icudt78.dll",
        # Qt's OpenSSL TLS plugin crashes in this frozen CPU bundle before the
        # first HTTPS reply. Windows Schannel passed the same thumbnail probe.
        "qopensslbackend.dll",
        # TensorRT is not used by WD14 and is excluded from both profiles.
        "onnxruntime_providers_tensorrt.dll",
        # The CPU profile stays free of the CUDA provider and NVIDIA runtime.
        *({"onnxruntime_providers_cuda.dll"} if runtime_profile == "CPU" else set()),
    }
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BooruFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BooruFlow",
)
