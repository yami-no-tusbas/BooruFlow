"""Opt-in monotonic startup timings for the desktop application.

Set ``BOORUFLOW_STARTUP_PROFILE`` to a JSON output path to enable it.  Normal
application startup does not perform any timing or file writes.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_OUTPUT = os.environ.get("BOORUFLOW_STARTUP_PROFILE", "").strip()
_ENABLED = bool(_OUTPUT)
_START_NS = int(os.environ.get("BOORUFLOW_PROCESS_START_NS", "0") or 0)
if not _START_NS:
    _START_NS = time.perf_counter_ns()
_LAST_NS = _START_NS
_RECORDS: list[dict[str, object]] = []


def enabled() -> bool:
    return _ENABLED


def now_ns() -> int:
    return time.perf_counter_ns()


def checkpoint(phase: str) -> None:
    """Record a sequential phase ending now."""
    global _LAST_NS
    if not _ENABLED:
        return
    end_ns = time.perf_counter_ns()
    _append(phase, _LAST_NS, end_ns, "phase")
    _LAST_NS = end_ns


def duration(phase: str, start_ns: int, *, kind: str = "detail") -> None:
    """Record an independently measured (possibly overlapping) duration."""
    if _ENABLED:
        _append(phase, start_ns, time.perf_counter_ns(), kind)


def event(phase: str) -> None:
    """Record an instantaneous startup event."""
    if _ENABLED:
        current = time.perf_counter_ns()
        _append(phase, current, current, "event")


def _append(phase: str, start_ns: int, end_ns: int, kind: str) -> None:
    _RECORDS.append(
        {
            "phase": phase,
            "kind": kind,
            "duration_ms": round((end_ns - start_ns) / 1_000_000, 3),
            "cumulative_ms": round((end_ns - _START_NS) / 1_000_000, 3),
        }
    )


def write(*, visible: bool) -> None:
    if not _ENABLED:
        return
    elapsed_ms = round((time.perf_counter_ns() - _START_NS) / 1_000_000, 3)
    visible_record = next(
        (record for record in reversed(_RECORDS) if record["phase"] == "Window visible/exposed"),
        None,
    )
    payload = {
        "clock": "time.perf_counter_ns",
        "pid": os.getpid(),
        "window_visible": visible,
        "total_to_visible_ms": (
            visible_record["cumulative_ms"] if visible_record is not None else elapsed_ms
        ),
        "elapsed_at_write_ms": elapsed_ms,
        "records": _RECORDS,
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if _OUTPUT == "-":
        print(serialized, file=sys.stderr, flush=True)
    else:
        path = Path(_OUTPUT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized + "\n", encoding="utf-8")
