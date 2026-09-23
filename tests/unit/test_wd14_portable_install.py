from __future__ import annotations

import io
from pathlib import Path

import pytest

from booruflow.cli import wd14_model


class Response(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def test_wd14_install_stages_then_activates_and_reports_progress(tmp_path: Path, monkeypatch, capsys) -> None:
    payloads = iter((b"model bytes", b"tag,value\n"))
    monkeypatch.setattr(wd14_model.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(next(payloads)))
    target = tmp_path / "wd14"

    wd14_model.install(target, "test/model")

    assert (target / wd14_model.MODEL_FILENAME).read_bytes() == b"model bytes"
    assert (target / wd14_model.TAGS_FILENAME).read_bytes() == b"tag,value\n"
    assert (target / wd14_model.METADATA_FILENAME).is_file()
    assert not list(tmp_path.glob(".wd14-install-*"))
    output = capsys.readouterr().out
    assert "DOWNLOAD model.onnx" in output
    assert "VERIFYING" in output and "INSTALLING" in output and "INSTALLED" in output


def test_wd14_failed_download_preserves_previous_installation(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "wd14"
    target.mkdir()
    (target / wd14_model.MODEL_FILENAME).write_bytes(b"previous model")
    calls = 0

    def fail_second(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("download failed")
        return Response(b"new model")

    monkeypatch.setattr(wd14_model.urllib.request, "urlopen", fail_second)
    with pytest.raises(OSError, match="download failed"):
        wd14_model.install(target, "test/model")
    assert (target / wd14_model.MODEL_FILENAME).read_bytes() == b"previous model"
    assert not list(tmp_path.glob(".wd14-install-*"))


def test_wd14_rejects_truncated_response_before_activation(tmp_path: Path, monkeypatch) -> None:
    def truncated(*_args, **_kwargs):
        response = Response(b"short")
        response.headers["Content-Length"] = "100"
        return response

    monkeypatch.setattr(wd14_model.urllib.request, "urlopen", truncated)
    target = tmp_path / "wd14"
    with pytest.raises(ValueError, match="Incomplete WD14 download"):
        wd14_model.install(target, "test/model")
    assert not target.exists()
