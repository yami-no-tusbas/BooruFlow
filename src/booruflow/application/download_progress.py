"""Stable transfer estimates from monotonic byte counters."""

import time


class DownloadProgress:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.name = ""
        self.last_bytes = 0
        self.last_time = 0.0
        self.rate = 0.0

    def update(self, name: str, done: int, total: int) -> str:
        now = self.clock()
        if name != self.name or done < self.last_bytes:
            self.name, self.last_bytes, self.last_time, self.rate = name, 0, now, 0.0
        elapsed = now - self.last_time
        if elapsed > 0 and done > self.last_bytes:
            current = (done - self.last_bytes) / elapsed
            self.rate = current if not self.rate else 0.25 * current + 0.75 * self.rate
            self.last_bytes, self.last_time = done, now
        transferred = f"{done / 1_048_576:.1f} MiB"
        if total <= 0:
            return f"{name}: {transferred}" + (
                f" | {self.rate / 1_048_576:.1f} MiB/s" if self.rate else ""
            )
        result = f"{name}: {transferred}/{total / 1_048_576:.1f} MiB | {done / total:.1%}"
        if self.rate:
            result += f" | {self.rate / 1_048_576:.1f} MiB/s"
            if done < total:
                seconds = int((total - done) / self.rate)
                result += f" | ETA {seconds // 60:d}:{seconds % 60:02d}"
        return result


def parse_download_line(line: str):
    if not line.startswith("DOWNLOAD "):
        return None
    try:
        name, done, total = line.removeprefix("DOWNLOAD ").rsplit(" ", 2)
        return name, int(done), int(total)
    except (ValueError, TypeError):
        return None
