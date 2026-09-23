"""Run the desktop application or a small first-party command group."""

import importlib
import os
import sys
import time

from booruflow.runtime import application_root, restore_helper_stdio

os.environ.setdefault("BOORUFLOW_PROCESS_START_NS", str(time.perf_counter_ns()))


INTERNAL_MODULES = frozenset(
    {
        "booruflow.worker.image_analysis_bootstrap",
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
    }
)


def dispatch_internal_module(module_name: str, arguments: list[str]) -> int:
    """Run a whitelisted helper without entering the GUI bootstrap."""
    restore_helper_stdio()
    if module_name not in INTERNAL_MODULES:
        print(f"Unsupported BooruFlow subprocess module: {module_name}", file=sys.stderr)
        return 2
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    sys.argv[:] = [sys.argv[0], *arguments]
    try:
        module = importlib.import_module(module_name)
        return int(module.main())
    except Exception as exc:  # noqa: BLE001 - frozen helper must report, never show a PyInstaller popup
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--booruflow-module":
        module_name = sys.argv[2]
        return dispatch_internal_module(module_name, sys.argv[3:])
    if len(sys.argv) > 1 and sys.argv[1] == "similar":
        from booruflow.cli.similar_artists import main as similar_main
        return similar_main(sys.argv[2:])
    from booruflow.infrastructure.crash_diagnostics import start_crash_diagnostics

    diagnostics = start_crash_diagnostics(application_root())
    clean_exit = False
    # QtWebEngine remote debugging must be configured before importing PySide6.
    from booruflow.infrastructure.gelbooru_cdp_diagnostic import (
        configure_embedded_cdp_startup,
    )
    try:
        sys.argv[:] = configure_embedded_cdp_startup(sys.argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        diagnostics.close(clean=True)
        return 2
    try:
        from booruflow.presentation.pyside6 import run
        result = run(diagnostics=diagnostics)
        clean_exit = True
        return result
    except BaseException:
        diagnostics.record_exception(*sys.exc_info(), thread="main")
        raise
    finally:
        diagnostics.close(clean=clean_exit)


if __name__ == "__main__":
    raise SystemExit(main())
