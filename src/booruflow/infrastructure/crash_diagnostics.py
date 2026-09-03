"""Persistent local diagnostics for uncaught Python and native faults."""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class CrashDiagnostics:
    """Own faulthandler and exception hooks for exactly one GUI session."""

    def __init__(self, project_root: Path) -> None:
        self.logs = project_root / "var" / "logs"
        self.state = project_root / "var" / "state"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        self.fatal_path = self.logs / "booruflow-fatal.log"
        self.marker = self.state / f"booruflow-session-{os.getpid()}.json"
        self._stream: IO[str] = self.fatal_path.open("a", encoding="utf-8", buffering=1)
        self._logger: Callable[[str], None] | None = None
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self._old_sys_hook = sys.excepthook
        self._old_thread_hook = threading.excepthook
        self._closed = False
        self._qt_handler_installed = False
        self._old_qt_handler = None
        self._find_abnormal_sessions()
        self.marker.write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "interpreter": sys.executable,
        }), encoding="utf-8")
        self._write(f"session start pid={os.getpid()} interpreter={sys.executable!r}")
        faulthandler.enable(file=self._stream, all_threads=True)
        sys.excepthook = self._sys_exception
        threading.excepthook = self._thread_exception

    def _find_abnormal_sessions(self) -> None:
        for marker in self.state.glob("booruflow-session-*.json"):
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
                pid = int(data.get("pid", 0))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pid = 0
                data = {}
            if pid == os.getpid() or _pid_is_running(pid):
                continue
            self._pending.append(
                "Previous BooruFlow session ended abnormally: "
                f"pid={pid or '?'} started_at={data.get('started_at', '?')}"
            )
            try:
                marker.unlink()
            except OSError:
                pass

    def set_logger(self, logger: Callable[[str], None]) -> None:
        self._logger = logger
        for message in self._pending:
            logger(f"[WARNING] [Crash] {message}")
        self._pending.clear()

    def install_qt_message_handler(self) -> None:
        """Persist Qt warnings/fatals, including Fast-Fail paths faulthandler cannot catch."""
        if self._qt_handler_installed:
            return
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        def handler(mode, context, message) -> None:
            if mode in {QtMsgType.QtWarningMsg,QtMsgType.QtCriticalMsg,QtMsgType.QtFatalMsg}:
                location=f"{context.file or '?'}:{context.line or 0} {context.function or '?'}"
                self._write(f"Qt message level={mode.name} location={location} message={message}")
            if self._old_qt_handler is not None:
                self._old_qt_handler(mode,context,message)

        self._old_qt_handler=qInstallMessageHandler(handler)
        self._qt_handler_installed=True

    def _write(self, message: str) -> None:
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._lock:
            self._stream.write(f"{timestamp} {message}\n")
            self._stream.flush()

    def record_exception(self, exc_type, exc_value, exc_traceback, *, thread: str) -> None:
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        message = (
            f"Uncaught Python exception thread={thread} "
            f"type={getattr(exc_type, '__name__', exc_type)} message={exc_value}\n{detail}"
        )
        self._write(message)
        if self._logger is not None:
            self._logger(f"[ERROR] [Crash] {message}")

    def _sys_exception(self, exc_type, exc_value, exc_traceback) -> None:
        self.record_exception(
            exc_type, exc_value, exc_traceback,
            thread=f"{threading.current_thread().name}:{threading.get_ident()}",
        )
        self._old_sys_hook(exc_type, exc_value, exc_traceback)

    def _thread_exception(self, args: threading.ExceptHookArgs) -> None:
        self.record_exception(
            args.exc_type, args.exc_value, args.exc_traceback,
            thread=f"{args.thread.name}:{args.thread.ident}" if args.thread else "unknown",
        )
        self._old_thread_hook(args)

    def close(self, *, clean: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if clean:
            self._write(f"session clean exit pid={os.getpid()}")
            try:
                self.marker.unlink()
            except OSError:
                pass
        sys.excepthook = self._old_sys_hook
        threading.excepthook = self._old_thread_hook
        if self._qt_handler_installed:
            from PySide6.QtCore import qInstallMessageHandler

            qInstallMessageHandler(self._old_qt_handler)
            self._qt_handler_installed=False
        if faulthandler.is_enabled():
            faulthandler.disable()
        self._stream.close()


def start_crash_diagnostics(project_root: Path) -> CrashDiagnostics:
    return CrashDiagnostics(project_root)
