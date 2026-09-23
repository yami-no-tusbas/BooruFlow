# PyInstaller onedir build for the first portable BooruFlow alpha.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project = Path(SPECPATH).parent
src = project / "src"

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
    binaries=[],
    datas=[
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
        # The packaged ONNX path uses CPUExecutionProvider. These optional GPU
        # providers depend on CUDA/TensorRT DLLs absent from the portable app.
        "onnxruntime_providers_cuda.dll", "onnxruntime_providers_tensorrt.dll",
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
