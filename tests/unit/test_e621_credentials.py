from __future__ import annotations

import base64
import io
import urllib.error

import pytest

from booruflow.application.credential_validation import (
    CredentialStatus,
    validate_e621_credentials,
    validate_site_credentials,
)
from booruflow.infrastructure.e621_client import E621Client, MalformedE621Response


class _Headers:
    @staticmethod
    def get_content_charset():
        return "utf-8"


class _Response:
    headers = _Headers()

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


def test_e621_client_uses_basic_auth_without_url_credentials() -> None:
    captured = {}

    def opener(request, timeout):
        captured.update(url=request.full_url, headers=dict(request.header_items()), timeout=timeout)
        return _Response(b'{"posts": []}')

    E621Client("alice", "top-secret", opener=opener).validate_credentials()

    expected = base64.b64encode(b"alice:top-secret").decode("ascii")
    assert captured["headers"]["Authorization"] == f"Basic {expected}"
    assert captured["headers"]["User-agent"].startswith("BooruFlow/0.1 (by alice on e621;")
    assert "top-secret" not in captured["headers"]["User-agent"]
    assert captured["url"] == "https://e621.net/posts.json?limit=1"
    assert "alice" not in captured["url"]
    assert "top-secret" not in captured["url"]


@pytest.mark.parametrize("credentials", [
    {"user_id": "", "api_key": "key"},
    {"user_id": "name", "api_key": ""},
])
def test_missing_e621_fields(credentials) -> None:
    assert validate_e621_credentials(credentials).status is CredentialStatus.MISSING


def _factory_raising(error):
    class Client:
        def validate_credentials(self):
            raise error

    return lambda _username, _api_key: Client()


def test_valid_e621_response() -> None:
    class Client:
        def validate_credentials(self):
            return None

    result = validate_e621_credentials(
        {"user_id": "name", "api_key": "key"}, client_factory=lambda *_args: Client()
    )
    assert result.status is CredentialStatus.VALID


@pytest.mark.parametrize(("code", "status"), [
    (401, CredentialStatus.INVALID),
    (403, CredentialStatus.SERVER_ERROR),
    (429, CredentialStatus.RATE_LIMITED),
    (503, CredentialStatus.RATE_LIMITED),
    (500, CredentialStatus.SERVER_ERROR),
])
def test_e621_http_failures_are_classified(code, status) -> None:
    error = urllib.error.HTTPError("https://e621.net/posts.json?limit=1", code, "failure", {}, io.BytesIO())
    result = validate_e621_credentials(
        {"user_id": "name", "api_key": "key"}, client_factory=_factory_raising(error)
    )
    assert result.status is status


def test_e621_network_failure_is_classified() -> None:
    result = validate_e621_credentials(
        {"user_id": "name", "api_key": "key"},
        client_factory=_factory_raising(urllib.error.URLError("offline")),
    )
    assert result.status is CredentialStatus.NETWORK_ERROR


def test_e621_malformed_response_is_safe() -> None:
    result = validate_e621_credentials(
        {"user_id": "name", "api_key": "key"},
        client_factory=_factory_raising(MalformedE621Response("bad shape")),
    )
    assert result.status is CredentialStatus.MALFORMED_RESPONSE


def test_e621_client_rejects_malformed_shape() -> None:
    client = E621Client("name", "key", opener=lambda *_args: _Response(b'{"posts": {}}'))
    with pytest.raises(MalformedE621Response):
        client.validate_credentials()


def test_site_router_keeps_gelbooru_and_e621_contracts_separate(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(
        "booruflow.application.credential_validation.validate_gelbooru_credentials",
        lambda credentials: calls.append(("gelbooru", credentials)) or "gel-result",
    )
    monkeypatch.setattr(
        "booruflow.application.credential_validation.validate_e621_credentials",
        lambda credentials: calls.append(("e621", credentials)) or "e621-result",
    )
    gel = {"user_id": "42", "api_key": "gel-key"}
    e621 = {"user_id": "wolf", "api_key": "e-key"}
    assert validate_site_credentials("gelbooru", gel) == "gel-result"
    assert validate_site_credentials("e621", e621) == "e621-result"
    assert calls == [("gelbooru", gel), ("e621", e621)]


def test_unsupported_site_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported credential site"):
        validate_site_credentials("other", {})
