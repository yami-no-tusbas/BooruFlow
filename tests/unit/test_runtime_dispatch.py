import subprocess
import sys
import types

from booruflow import runtime
from booruflow.__main__ import dispatch_internal_module


def test_frozen_module_command_uses_internal_sentinel(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert runtime.frozen_module_command("booruflow.cli.wd14_model", ["diagnose"]) == [
        "--booruflow-module",
        "booruflow.cli.wd14_model",
        "diagnose",
    ]


def test_source_module_command_keeps_python_module_execution(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert runtime.frozen_module_command("booruflow.cli.wd14_model", ["diagnose"]) == [
        "-u",
        "-m",
        "booruflow.cli.wd14_model",
        "diagnose",
    ]


def test_internal_dispatch_runs_helper_without_gui(monkeypatch) -> None:
    calls: list[list[str]] = []
    helper = types.SimpleNamespace(main=lambda: calls.append(list(sys.argv[1:])) or 7)
    monkeypatch.setattr("booruflow.__main__.importlib.import_module", lambda _name: helper)
    monkeypatch.setattr(sys, "argv", ["BooruFlow.exe", "stale"])

    assert dispatch_internal_module("booruflow.cli.wd14_model", ["diagnose", "--directory", "x"]) == 7
    assert calls == [["diagnose", "--directory", "x"]]


def test_internal_dispatch_rejects_unknown_module(capsys) -> None:
    assert dispatch_internal_module("booruflow.cli.not_allowed", []) == 2
    assert "Unsupported BooruFlow subprocess module" in capsys.readouterr().err


def test_helper_exception_returns_error_without_raising(monkeypatch, capsys) -> None:
    def fail():
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr("booruflow.__main__.importlib.import_module", lambda _name: types.SimpleNamespace(main=fail))
    assert dispatch_internal_module("booruflow.cli.wd14_model", []) == 1
    assert "ERROR: OSError" in capsys.readouterr().err


def test_helper_output_survives_invalid_stream(monkeypatch, tmp_path) -> None:
    class InvalidStream:
        def write(self, _value):
            raise OSError(22, "Invalid argument")

        def flush(self):
            raise OSError(22, "Invalid argument")

    monkeypatch.setattr(runtime, "application_root", lambda: tmp_path)
    stream = runtime._ResilientHelperOutput(InvalidStream())
    assert stream.write("ERROR: first failure\n") == len("ERROR: first failure\n")
    stream.flush()
    assert (tmp_path / "var/logs/booruflow-helper-fallback.log").read_text() == "ERROR: first failure\n"


def test_real_helper_pipe_stop_and_error_exit_codes() -> None:
    command = [sys.executable, "-m", "booruflow", "--booruflow-module",
               "booruflow.cli.helper_io_probe"]
    stopped = subprocess.run(
        [*command, "--mode", "stop"], input="STOP\n", text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert stopped.returncode == 2
    assert "HELPER_STDOUT_READY" in stopped.stdout and "STOP_ACK" in stopped.stdout
    assert "HELPER_STDERR_READY" in stopped.stderr
    failed = subprocess.run(
        [*command, "--mode", "fail"], text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert failed.returncode == 1
    assert "ERROR: OSError" in failed.stderr
