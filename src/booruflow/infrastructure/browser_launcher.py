"""Configurable, privacy-preserving launcher for Gelbooru web pages."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SYSTEM_DEFAULT = "system"
DEDICATED_PROFILE = "dedicated"
CUSTOM_COMMAND = "custom"


@dataclass(frozen=True)
class DetectedBrowser:
    browser_id: str
    name: str
    executable: Path


@dataclass(frozen=True)
class BrowserLaunchSettings:
    mode: str = SYSTEM_DEFAULT
    custom_command: str = ""
    clear_profile_on_close: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> BrowserLaunchSettings:
        return cls(
            mode=str(values.get("gelbooru_browser_mode", SYSTEM_DEFAULT)),
            custom_command=str(values.get("gelbooru_browser_custom_command", "")),
            clear_profile_on_close=bool(
                values.get("gelbooru_browser_clear_profile_on_close", False)
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "gelbooru_browser_mode": self.mode,
            "gelbooru_browser_custom_command": self.custom_command,
            "gelbooru_browser_clear_profile_on_close": self.clear_profile_on_close,
        }

    @property
    def custom_command_valid(self) -> bool:
        return bool(self.custom_command.strip()) and "{url}" in self.custom_command


class BrowserDetector:
    """Detect supported Chromium-family browsers in a stable preference order."""

    WINDOWS_CANDIDATES = (
        ("brave", "Brave", "BraveSoftware/Brave-Browser/Application/brave.exe"),
        ("chrome", "Google Chrome", "Google/Chrome/Application/chrome.exe"),
        ("edge", "Microsoft Edge", "Microsoft/Edge/Application/msedge.exe"),
        ("chromium", "Chromium", "Chromium/Application/chrome.exe"),
    )
    LINUX_CANDIDATES = (
        ("brave", "Brave", "brave-browser"),
        ("chrome", "Google Chrome", "google-chrome"),
        ("chromium", "Chromium", "chromium"),
        ("chromium", "Chromium", "chromium-browser"),
        ("edge", "Microsoft Edge", "microsoft-edge"),
    )

    def __init__(
        self,
        *,
        platform: str | None = None,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.platform = platform or os.name
        self.environ = dict(os.environ if environ is None else environ)
        self.which = which

    def detect_all(self) -> list[DetectedBrowser]:
        if self.platform == "nt":
            roots = [
                self.environ.get("PROGRAMFILES", ""),
                self.environ.get("PROGRAMFILES(X86)", ""),
                self.environ.get("LOCALAPPDATA", ""),
            ]
            found: list[DetectedBrowser] = []
            for browser_id, name, relative in self.WINDOWS_CANDIDATES:
                executable = next(
                    (Path(root) / Path(relative) for root in roots if root and (Path(root) / Path(relative)).is_file()),
                    None,
                )
                if executable is not None:
                    found.append(DetectedBrowser(browser_id, name, executable))
            return found
        found = []
        seen: set[str] = set()
        for browser_id, name, command in self.LINUX_CANDIDATES:
            executable = self.which(command)
            if executable and browser_id not in seen:
                found.append(DetectedBrowser(browser_id, name, Path(executable)))
                seen.add(browser_id)
        return found

    def detect(self) -> DetectedBrowser | None:
        browsers = self.detect_all()
        return browsers[0] if browsers else None


class BrowserLauncher:
    """Build and execute browser commands without inspecting browser data."""

    def __init__(
        self,
        app_data_dir: Path,
        settings: BrowserLaunchSettings | Mapping[str, object] | None = None,
        *,
        detector: BrowserDetector | None = None,
        process_runner: Callable[[Sequence[str]], object] | None = None,
        default_opener: Callable[[str], object] = webbrowser.open,
    ) -> None:
        self.app_data_dir = Path(app_data_dir)
        self.settings = (
            settings if isinstance(settings, BrowserLaunchSettings)
            else BrowserLaunchSettings.from_mapping(settings or {})
        )
        self.detector = detector or BrowserDetector()
        self.process_runner = process_runner or self._start_process
        self.default_opener = default_opener
        self._active_profile: Path | None = None
        self._dedicated_session_started = False
        self._debugging_port: int | None = None

    @property
    def debugging_port(self) -> int | None:
        return self._debugging_port

    def _ensure_debugging_port(self) -> int:
        if self._debugging_port is None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                self._debugging_port = int(probe.getsockname()[1])
        return self._debugging_port

    @staticmethod
    def _start_process(command: Sequence[str]) -> object:
        return subprocess.Popen(list(command), close_fds=True)

    def update_settings(self, settings: BrowserLaunchSettings | Mapping[str, object]) -> None:
        updated = (
            settings if isinstance(settings, BrowserLaunchSettings)
            else BrowserLaunchSettings.from_mapping(settings)
        )
        self.settings = updated

    def profile_dir(self, browser_id: str) -> Path:
        return self.app_data_dir / "BrowserProfiles" / f"gelbooru-{browser_id}"

    def chromium_command(
        self, browser: DetectedBrowser, url: str, *, new_window: bool = True,
    ) -> list[str]:
        profile = self.profile_dir(browser.browser_id)
        command = [
            str(browser.executable),
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={self._ensure_debugging_port()}",
            "--no-first-run",
            "--disable-sync",
        ]
        if new_window:
            command.append("--new-window")
        command.append(url)
        return command

    def custom_command(self, url: str) -> list[str] | None:
        if not self.settings.custom_command_valid:
            return None
        replaced = self.settings.custom_command.replace("{url}", url)
        parts = shlex.split(replaced, posix=os.name != "nt")
        if os.name == "nt":
            parts = [part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part for part in parts]
        return parts or None

    def command_for(self, url: str, *, dedicated_new_window: bool = True) -> list[str] | None:
        if self.settings.mode == DEDICATED_PROFILE:
            browser = self.detector.detect()
            return (
                self.chromium_command(browser, url, new_window=dedicated_new_window)
                if browser else None
            )
        if self.settings.mode == CUSTOM_COMMAND:
            return self.custom_command(url)
        return None

    def open(self, url: str, *, allow_default_fallback: bool = True) -> bool:
        command = self.command_for(
            url, dedicated_new_window=not self._dedicated_session_started,
        )
        if command is None:
            return bool(self.default_opener(url)) if allow_default_fallback else False
        try:
            if self.settings.mode == DEDICATED_PROFILE:
                self._active_profile = Path(command[1].partition("=")[2])
                self._active_profile.mkdir(parents=True, exist_ok=True)
            self.process_runner(command)
            if self.settings.mode == DEDICATED_PROFILE:
                # Chromium routes a URL invoked with this same profile to its
                # existing instance.  Do not force a second window after the
                # first session launch.
                self._dedicated_session_started = True
            return True
        except (OSError, ValueError):
            return bool(self.default_opener(url)) if allow_default_fallback else False

    def ensure_dedicated(self, url: str) -> bool:
        """Start the isolated Chromium instance once, independently from open-post settings."""
        if self._dedicated_session_started and self._debugging_port is not None:
            return True
        browser = self.detector.detect()
        if browser is None:
            return False
        command = self.chromium_command(browser, url, new_window=True)
        try:
            self._active_profile = self.profile_dir(browser.browser_id)
            self._active_profile.mkdir(parents=True, exist_ok=True)
            self.process_runner(command)
            self._dedicated_session_started = True
            return True
        except (OSError, ValueError):
            return False

    def reset_dedicated_profile(self, browser_id: str | None = None) -> bool:
        detected = self.detector.detect() if browser_id is None else None
        selected_id = browser_id or (detected.browser_id if detected else "")
        if not selected_id or not selected_id.replace("-", "").isalnum():
            return False
        profiles_root = self.app_data_dir / "BrowserProfiles"
        if profiles_root.is_symlink():
            return False
        root = profiles_root.resolve()
        target = self.profile_dir(selected_id)
        if target.is_symlink() or target.parent.resolve() != root or target.name != f"gelbooru-{selected_id}":
            return False
        if target.exists():
            shutil.rmtree(target)
        return True

    def close(self) -> None:
        if self.settings.clear_profile_on_close and self._active_profile is not None:
            self.reset_dedicated_profile(self._active_profile.name.removeprefix("gelbooru-"))
        self._active_profile = None
        self._dedicated_session_started = False
        self._debugging_port = None
