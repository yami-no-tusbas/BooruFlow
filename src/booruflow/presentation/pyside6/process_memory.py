"""Small, read-only Windows process-tree memory sampler for the status bar."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessMemoryRow:
    pid: int
    parent_pid: int
    name: str
    working_set: int


@dataclass(frozen=True, slots=True)
class ProcessMemorySnapshot:
    total_bytes: int
    rows: tuple[ProcessMemoryRow, ...]
    available_bytes: int | None = None


def collect_process_memory(root_pid: int | None = None) -> ProcessMemorySnapshot:
    if os.name != "nt":
        return ProcessMemorySnapshot(0, ())
    root_pid = int(root_pid or os.getpid())
    processes = _process_table()
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent_pid, _name) in processes.items():
            if parent_pid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    rows = tuple(
        ProcessMemoryRow(pid, parent, name, _working_set(pid))
        for pid, (parent, name) in processes.items()
        if pid in descendants
    )
    return ProcessMemorySnapshot(
        sum(row.working_set for row in rows),
        rows,
        _available_memory(),
    )


def _process_table() -> dict[int, tuple[int, str]]:
    snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return {}

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    result: dict[int, tuple[int, str]] = {}
    try:
        first = ctypes.windll.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while first:
            result[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID), str(entry.szExeFile)
            )
            if not ctypes.windll.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        ctypes.windll.kernel32.CloseHandle(snapshot)
    return result


def _working_set(pid: int) -> int:
    handle = ctypes.windll.kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
    if not handle:
        return 0

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    try:
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        )
        return int(counters.WorkingSetSize) if ok else 0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _available_memory() -> int | None:
    class Status(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    status = Status()
    status.dwLength = ctypes.sizeof(Status)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return int(status.ullAvailPhys)
    return None


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(max(0, value))
    unit = units[0]
    for unit in units[1:]:
        if amount < 1024:
            break
        amount /= 1024
    return f"{amount:.2f} {unit}"
