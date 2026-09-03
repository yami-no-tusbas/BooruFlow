"""Shared helpers for readable logs ingested by Qt."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

LOG_DIRECTORY = Path("var") / "logs"
LOG_FILENAME_FORMAT = "booruflow_%Y-%m-%d_%H-%M-%S.log"
LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_RETENTION_COUNT = 30
LOG_FILENAME_PATTERN = re.compile(
    r"^booruflow_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d{2})?\.log$"
)


@dataclass
class RunLog:
    """Best-effort disk log dedicated to one application launch."""

    path: Path | None
    retention_count: int = LOG_RETENTION_COUNT
    _diagnostic_emitted: bool = False

    @classmethod
    def create(
        cls,
        project_root: Path,
        *,
        launched_at: datetime | None = None,
        retention_count: int = LOG_RETENTION_COUNT,
    ) -> RunLog:
        log_directory = project_root / LOG_DIRECTORY
        launched_at = launched_at or datetime.now()  # noqa: DTZ005
        path: Path | None = None
        try:
            log_directory.mkdir(parents=True, exist_ok=True)
            base_name = launched_at.strftime(LOG_FILENAME_FORMAT)
            for sequence in range(100):
                candidate = log_directory / (
                    base_name if sequence == 0 else f"{Path(base_name).stem}_{sequence:02d}.log"
                )
                try:
                    candidate.touch(exist_ok=False)
                except FileExistsError:
                    continue
                path = candidate
                break
            if path is None:
                raise OSError("no unique per-run log filename was available")
        except OSError as exc:
            cls._diagnose(f"BooruFlow file logging unavailable: {exc}")

        run_log = cls(path=path, retention_count=retention_count)
        run_log.cleanup_old_logs(log_directory)
        return run_log

    @staticmethod
    def _diagnose(message: str) -> None:
        print(message, file=sys.stderr)

    def cleanup_old_logs(self, log_directory: Path | None = None) -> None:
        directory = log_directory or (self.path.parent if self.path else None)
        if directory is None:
            return
        try:
            eligible = sorted(
                (
                    candidate
                    for candidate in directory.iterdir()
                    if candidate.is_file() and LOG_FILENAME_PATTERN.fullmatch(candidate.name)
                ),
                key=lambda candidate: candidate.name,
                reverse=True,
            )
        except OSError as exc:
            self._diagnose(f"BooruFlow log cleanup skipped: {exc}")
            return

        keep = set(eligible[: max(self.retention_count, 0)])
        if self.path is not None:
            keep.add(self.path)
        for candidate in eligible:
            if candidate in keep:
                continue
            try:
                candidate.unlink()
            except OSError as exc:
                self._diagnose(f"BooruFlow could not delete old log {candidate.name}: {exc}")

    def format(self, message: str, *, logged_at: datetime | None = None) -> str:
        timestamp = logged_at or datetime.now()  # noqa: DTZ005
        return f"[{timestamp.strftime(LOG_TIMESTAMP_FORMAT)}] {sanitize_log_text(message)}"

    def write(self, formatted: str) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("a", encoding="utf-8", buffering=1) as stream:
                stream.write(formatted + "\n")
        except OSError as exc:
            if not self._diagnostic_emitted:
                self._diagnostic_emitted = True
                self._diagnose(f"BooruFlow file logging stopped: {exc}")


class StreamingLogSanitizer:
    """Strip terminal controls while retaining incomplete ANSI suffixes."""

    def __init__(self) -> None:
        self.pending = ""

    def feed(self, chunk: str) -> str:
        value = self.pending + chunk
        self.pending = ""
        output: list[str] = []
        index = 0
        while index < len(value):
            character = value[index]
            if character == "\x1b":
                if index + 1 >= len(value):
                    self.pending = value[index:]; break
                introducer = value[index + 1]
                if introducer == "[":
                    end = index + 2
                    while end < len(value) and not "@" <= value[end] <= "~": end += 1
                    if end >= len(value): self.pending = value[index:]; break
                    index = end + 1; continue
                if introducer == "]":
                    end = index + 2
                    while end < len(value):
                        if value[end] == "\a": break
                        if value[end] == "\x1b" and end + 1 < len(value) and value[end + 1] == "\\":
                            end += 1; break
                        end += 1
                    if end >= len(value): self.pending = value[index:]; break
                    index = end + 1; continue
                index += 2; continue
            if character == "\r":
                index += 1; continue
            if character in "\n\t" or ord(character) >= 32:
                output.append(character)
            index += 1
        return "".join(output)

    def flush(self) -> str:
        self.pending = ""
        return ""


def sanitize_log_text(value: str) -> str:
    sanitizer = StreamingLogSanitizer()
    return sanitizer.feed(value) + sanitizer.flush()


def log_event(component: str, message: str, *, level: str = "INFO", context: str = "") -> str:
    prefix = f"[{level.upper()}] [{component}]"
    if context:
        prefix += f" [{context}]"
    return f"{prefix} {sanitize_log_text(message)}"
