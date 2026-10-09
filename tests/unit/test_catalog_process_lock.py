"""PID probes must never send Windows console events or steal uncertain locks."""

import ctypes
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from booruflow.infrastructure import gelbooru_aliases as aliases


def platform_os(monkeypatch, name, kill):
    replacement = SimpleNamespace(**{**vars(os), "name": name, "kill": kill})
    monkeypatch.setattr(aliases, "os", replacement)


@pytest.mark.parametrize("handle,error,wait,alive", [
    (0x123456789ABC, 0, 0x102, True),  # running, including a 64-bit handle
    (0x123456789ABC, 0, 0, False),  # exited but its process object still exists
    (None, 87, 0, False),  # nonexistent PID
    (None, 5, 0, True),  # access denied must retain the owner's lock
    (None, 8, 0, True),  # other API failure cannot prove death
    (None, 0, 0, True),  # even a missing error code is inconclusive
    (0x123456789ABC, 0, 0xFFFFFFFF, True),  # WAIT_FAILED
    (0x123456789ABC, 0, 0x80, True),  # unexpected wait result
    (0x123456789ABC, 0, OSError("wait unavailable"), True),
])
def test_windows_lock_owner_probe_is_non_destructive_and_conservative(
    tmp_path, monkeypatch, handle, error, wait, alive
):
    database = tmp_path / "tags.db"
    database.write_bytes(b"catalogue sentinel")
    lock = database.with_name(database.name + ".operation.lock")
    lock.write_text("123", encoding="ascii")
    kill = Mock(side_effect=AssertionError("Windows must not send a signal"))
    platform_os(monkeypatch, "nt", kill)
    kernel32 = SimpleNamespace(
        OpenProcess=Mock(return_value=handle),
        WaitForSingleObject=Mock(return_value=wait),
        CloseHandle=Mock(return_value=True),
    )
    if isinstance(wait, OSError):
        kernel32.WaitForSingleObject.side_effect = wait
    loader = Mock(return_value=kernel32)
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)

    if alive:
        with (
            pytest.raises(RuntimeError, match="already running"),
            aliases.catalog_operation_lock(database),
        ):
            pytest.fail("must not steal an active or uncertain lock")
        assert lock.read_text(encoding="ascii") == "123"
    else:
        with aliases.catalog_operation_lock(database):
            assert lock.read_text(encoding="ascii") == str(os.getpid())
        assert not lock.exists()
    assert database.read_bytes() == b"catalogue sentinel"
    kill.assert_not_called()
    loader.assert_called_once_with("kernel32", use_last_error=True)
    kernel32.OpenProcess.assert_called_once_with(0x00100000, False, 123)
    if handle:
        kernel32.WaitForSingleObject.assert_called_once_with(handle, 0)
        kernel32.CloseHandle.assert_called_once_with(handle)
        assert kernel32.OpenProcess.restype is ctypes.c_void_p
        assert kernel32.CloseHandle.argtypes == (ctypes.c_void_p,)
    else:
        kernel32.WaitForSingleObject.assert_not_called()
        kernel32.CloseHandle.assert_not_called()


def test_windows_api_unavailable_keeps_the_existing_lock(tmp_path, monkeypatch):
    database = tmp_path / "tags.db"
    lock = database.with_name(database.name + ".operation.lock")
    lock.write_text("123", encoding="ascii")
    kill = Mock(side_effect=AssertionError("Windows must not send a signal"))
    platform_os(monkeypatch, "nt", kill)
    monkeypatch.setattr(ctypes, "WinDLL", Mock(side_effect=OSError("unavailable")), raising=False)
    with (
        pytest.raises(RuntimeError, match="already running"),
        aliases.catalog_operation_lock(database),
    ):
        pytest.fail("an unknown process must keep its lock")
    assert lock.read_text(encoding="ascii") == "123"
    kill.assert_not_called()


@pytest.mark.parametrize("pid", [0, -1, 0x100000000])
def test_windows_invalid_pid_is_not_truncated_to_another_process(pid, monkeypatch):
    kill = Mock(side_effect=AssertionError("Windows must not send a signal"))
    platform_os(monkeypatch, "nt", kill)
    loader = Mock(side_effect=AssertionError("invalid PID must not reach the API"))
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    assert not aliases._catalog_owner_is_running(pid)
    loader.assert_not_called()
    kill.assert_not_called()


@pytest.mark.parametrize("error", [None, ProcessLookupError(), PermissionError()])
def test_posix_probe_retains_existing_signal_zero_behavior(monkeypatch, error):
    kill = Mock(side_effect=error)
    platform_os(monkeypatch, "posix", kill)
    loader = Mock(side_effect=AssertionError("POSIX must not use a Windows API"))
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    assert aliases._catalog_owner_is_running(123) is (error is None)
    kill.assert_called_once_with(123, 0)
    loader.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows process handles")
def test_windows_running_and_exited_child_keep_and_release_the_lock(tmp_path, monkeypatch):
    database = tmp_path / "tags.db"
    database.write_bytes(b"catalogue sentinel")
    lock = database.with_name(database.name + ".operation.lock")
    kill = Mock(side_effect=AssertionError("Windows must not send a signal"))
    platform_os(monkeypatch, "nt", kill)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        lock.write_text(str(child.pid), encoding="ascii")
        with (
            pytest.raises(RuntimeError, match="already running"),
            aliases.catalog_operation_lock(database),
        ):
            pytest.fail("a real running child must retain its lock")
        assert lock.read_text(encoding="ascii") == str(child.pid)
        assert child.poll() is None
        child.stdin.close()
        assert child.wait(timeout=10) == 0
        # Popen still owns a process handle: an exited object is not a running owner.
        with aliases.catalog_operation_lock(database):
            assert lock.read_text(encoding="ascii") == str(os.getpid())
        assert not lock.exists()
        assert database.read_bytes() == b"catalogue sentinel"
        kill.assert_not_called()
    finally:
        if child.poll() is None:
            child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
