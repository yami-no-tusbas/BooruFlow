"""Minimal Image Analysis process entry, deliberately free of heavy imports."""

from __future__ import annotations

import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path


def _argument_value(name: str) -> str:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return ""


def _log_path() -> Path:
    database = _argument_value("--database")
    if database:
        return Path(database).resolve().parent / "worker-bootstrap.log"
    return Path.cwd() / "worker-bootstrap.log"


def bootstrap_log(message: str) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
    line = f"{timestamp} pid={os.getpid()} {message}\n"
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        stream.write(line)
    print(f"BOOTSTRAP {message}", flush=True)


def _watch_parent_early(parent_pid: int) -> None:
    """Watch the GUI before importing ONNX/PySide worker dependencies."""
    if parent_pid <= 0:
        return
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            bootstrap_log(
                f"parent unavailable before worker import error={ctypes.get_last_error()}"
            )
            os._exit(0)
        bootstrap_log("early parent watchdog armed")
        try:
            if kernel32.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
                bootstrap_log("parent exit detected")
                os._exit(0)
        finally:
            kernel32.CloseHandle(handle)
        return
    while True:
        try:
            os.kill(parent_pid, 0)
        except OSError:
            os._exit(0)
        threading.Event().wait(1.0)


def main() -> int:
    bootstrap_log("process entry")
    bootstrap_log(f"argv={sys.argv!r}")
    parent_pid = int(_argument_value("--parent-pid") or 0)
    bootstrap_log(f"parent_pid received={parent_pid}")
    bootstrap_log(
        f"interpreter={sys.executable!r} base_interpreter={getattr(sys, '_base_executable', sys.executable)!r}"
    )
    stop = threading.Event()
    threading.Thread(target=_watch_parent_early, args=(parent_pid,), daemon=True).start()
    bootstrap_log("early parent watchdog started")
    bootstrap_log("import worker module begin")
    from booruflow.worker.image_analysis import main as worker_main
    bootstrap_log("import worker module complete")
    return worker_main(
        sys.argv[1:], bootstrap_log=bootstrap_log,
        external_stop=stop, parent_watchdog_started=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
