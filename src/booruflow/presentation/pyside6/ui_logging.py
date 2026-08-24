"""Shared helpers for readable logs ingested by Qt."""

from __future__ import annotations

import re

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


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
