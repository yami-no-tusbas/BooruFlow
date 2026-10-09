"""Runtime roots shared by source and frozen application launches."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from booruflow import startup_profile


def build_identity(root: Path | None = None) -> str:
    """Use generated portable metadata, including the pre-identity legacy format."""
    root = root or application_root()
    try:
        fields = dict(line.split(': ', 1) for line in
                      (root / 'BUILD_INFO.txt').read_text(encoding='utf-8-sig').splitlines()
                      if ': ' in line)
        if fields.get('Build identity'):
            return fields['Build identity']
        built = datetime.fromisoformat(fields['Built'])
        dirty = '-dirty' if fields.get('Working tree', '').upper() == 'DIRTY' else ''
        flavor = '-gpu' if fields.get('Runtime profile', '').upper() == 'CUDA' else ''
        return f"{built:%Y.%m.%d}-g{fields['Commit short']}{dirty}-{built:%H%M}{flavor}"
    except (OSError, ValueError, KeyError):
        pass
    if getattr(sys, 'frozen', False):
        return root.name.removeprefix('BooruFlow-') if root.name.startswith('BooruFlow-') else 'portable'
    import subprocess
    try:
        options = {'cwd': root, 'capture_output': True, 'text': True, 'timeout': 2,
                   'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
        sha = subprocess.run(['git', 'rev-parse', '--short=7', 'HEAD'], check=True, **options).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--untracked-files=normal'],
                                    check=True, **options).stdout.strip())
        return f"source-g{sha}" + ('-dirty' if dirty else '')
    except (OSError, subprocess.SubprocessError):
        return 'source'


def application_root() -> Path:
    """Return the writable application/user-data root."""
    if startup_profile.enabled():
        isolated = os.environ.get("BOORUFLOW_STARTUP_PROFILE_ROOT", "").strip()
        if isolated:
            return Path(isolated).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """Return the read-only bundled/source resource root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", application_root())).resolve()
    return application_root()


def bundled_bootstrap_root() -> Path | None:
    """Local writable bootstrap files exist only beside a frozen executable."""
    return application_root() if getattr(sys, "frozen", False) else None


def frozen_module_command(module: str, arguments: list[str]) -> list[str]:
    """Build subprocess arguments for a Python module in source or PyInstaller mode."""
    if getattr(sys, "frozen", False):
        return ["--booruflow-module", module, *arguments]
    return ["-u", "-m", module, *arguments]


def restore_helper_stdio() -> None:
    """Expose QProcess pipe handles to a windowed PyInstaller helper on Windows."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    if os.name == "nt":
        import ctypes
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
        kernel.GetStdHandle.restype = ctypes.c_void_p
        kernel.GetFileType.argtypes = [ctypes.c_void_p]
        kernel.GetFileType.restype = ctypes.c_ulong
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.DuplicateHandle.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong,
        ]
        kernel.DuplicateHandle.restype = ctypes.c_int
        process = kernel.GetCurrentProcess()
        for attribute, code, mode, flags in (
            ("stdin", -10, "r", os.O_RDONLY),
            ("stdout", -11, "w", os.O_WRONLY),
            ("stderr", -12, "w", os.O_WRONLY),
        ):
            existing = getattr(sys, attribute)
            try:
                valid = existing is not None and kernel.GetFileType(
                    msvcrt.get_osfhandle(existing.fileno())
                ) != 0
            except (OSError, ValueError, AttributeError):
                valid = False
            if valid:
                continue
            handle = kernel.GetStdHandle(code)
            duplicate = ctypes.c_void_p()
            if handle and handle != ctypes.c_void_p(-1).value and kernel.GetFileType(handle) != 0 and kernel.DuplicateHandle(
                process, handle, process, ctypes.byref(duplicate), 0, False, 2
            ):
                try:
                    fd = msvcrt.open_osfhandle(duplicate.value, flags)
                    setattr(sys, attribute, os.fdopen(fd, mode, encoding="utf-8", buffering=1))
                    continue
                except OSError:
                    kernel.CloseHandle(duplicate)
            setattr(sys, attribute, open(os.devnull, mode, encoding="utf-8"))  # noqa: SIM115
    sys.stdout = _ResilientHelperOutput(sys.stdout)
    sys.stderr = _ResilientHelperOutput(sys.stderr)


class _ResilientHelperOutput:
    """Never raise again while reporting a helper error through a lost pipe."""

    def __init__(self, stream: object) -> None:
        self.stream = stream

    def write(self, value: str) -> int:
        try:
            return self.stream.write(value)
        except (OSError, ValueError):
            try:
                path = application_root() / "var" / "logs" / "booruflow-helper-fallback.log"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fallback:
                    fallback.write(value)
            except OSError:
                pass
            return len(value)

    def flush(self) -> None:
        try:
            self.stream.flush()
        except (OSError, ValueError):
            pass

    def reconfigure(self, **kwargs: object) -> None:
        try:
            self.stream.reconfigure(**kwargs)
        except (OSError, ValueError, AttributeError):
            pass
