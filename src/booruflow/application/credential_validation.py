"""Site-scoped, read-only API credential validation."""

from __future__ import annotations

import urllib.error
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from booruflow.infrastructure.e621_client import E621ApiError, E621Client, MalformedE621Response
from booruflow.infrastructure.gelbooru_client import fetch_page


class CredentialStatus(StrEnum):
    NOT_TESTED = "not_tested"
    TESTING = "testing"
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    MALFORMED_RESPONSE = "malformed_response"


@dataclass(frozen=True, slots=True)
class CredentialValidationResult:
    site: str
    status: CredentialStatus


class E621ClientFactory(Protocol):
    def __call__(self, username: str, api_key: str) -> E621Client: ...


def _http_failure(site: str, code: int) -> CredentialValidationResult:
    if code == 401:
        status = CredentialStatus.INVALID
    elif code in {429, 503}:
        status = CredentialStatus.RATE_LIMITED
    elif code == 403 or 500 <= code:
        status = CredentialStatus.SERVER_ERROR
    else:
        status = CredentialStatus.NETWORK_ERROR
    return CredentialValidationResult(site, status)


def validate_e621_credentials(
    credentials: dict[str, str],
    *,
    client_factory: E621ClientFactory = E621Client,
) -> CredentialValidationResult:
    # `user_id` is the historical persisted key; e621 interprets it as a username.
    username = credentials.get("user_id", "").strip()
    api_key = credentials.get("api_key", "").strip()
    if not username or not api_key:
        return CredentialValidationResult("e621", CredentialStatus.MISSING)
    try:
        client_factory(username, api_key).validate_credentials()
    except E621ApiError as exc:
        return _http_failure("e621", exc.status) if exc.status else CredentialValidationResult(
            "e621", CredentialStatus.NETWORK_ERROR
        )
    except urllib.error.HTTPError as exc:
        return _http_failure("e621", exc.code)
    except MalformedE621Response:
        return CredentialValidationResult("e621", CredentialStatus.MALFORMED_RESPONSE)
    except (urllib.error.URLError, TimeoutError, OSError):
        return CredentialValidationResult("e621", CredentialStatus.NETWORK_ERROR)
    return CredentialValidationResult("e621", CredentialStatus.VALID)


def validate_gelbooru_credentials(credentials: dict[str, str]) -> CredentialValidationResult:
    user_id = credentials.get("user_id", "").strip()
    api_key = credentials.get("api_key", "").strip()
    if not user_id or not api_key:
        return CredentialValidationResult("gelbooru", CredentialStatus.MISSING)
    try:
        fetch_page("sort:id", 0, 1, user_id, api_key)
    except urllib.error.HTTPError as exc:
        return _http_failure("gelbooru", exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return CredentialValidationResult("gelbooru", CredentialStatus.NETWORK_ERROR)
    except (RuntimeError, ValueError):
        return CredentialValidationResult("gelbooru", CredentialStatus.MALFORMED_RESPONSE)
    return CredentialValidationResult("gelbooru", CredentialStatus.VALID)


def validate_site_credentials(site: str, credentials: dict[str, str]) -> CredentialValidationResult:
    if site == "gelbooru":
        return validate_gelbooru_credentials(credentials)
    if site == "e621":
        return validate_e621_credentials(credentials)
    raise ValueError(f"unsupported credential site: {site}")
