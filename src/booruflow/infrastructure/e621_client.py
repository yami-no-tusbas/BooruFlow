"""Small authenticated e621 JSON API client."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from booruflow.infrastructure.http_config import e621_user_agent

API_ROOT = "https://e621.net"
ResponseOpener = Callable[[urllib.request.Request, float], Any]


class MalformedE621Response(ValueError):
    """The server response was not usable e621 JSON."""


class E621ApiError(RuntimeError):
    """Sanitized e621 HTTP failure suitable for durable batch state."""

    def __init__(self, status: int | None, reason: str) -> None:
        self.status = status
        self.reason = reason
        super().__init__(f"e621_api_error status={status or 'network'} reason={reason}")


class E621Client:
    """Read-only e621 client with site-specific HTTP Basic authentication."""

    def __init__(
        self,
        username: str = "",
        api_key: str = "",
        *,
        opener: ResponseOpener | None = None,
        timeout: float = 20,
        request_interval: float = 1.0,
    ) -> None:
        self.username = username
        self.api_key = api_key
        self.opener = opener or (lambda request, timeout: urllib.request.urlopen(request, timeout=timeout))
        self.timeout = timeout
        self.request_interval = max(0.0, request_interval)
        self._last_request_at: float | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": e621_user_agent(self.username), "Accept": "application/json"}
        if self.username and self.api_key:
            token = base64.b64encode(f"{self.username}:{self.api_key}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _wait_for_rate_limit(self) -> None:
        """Respect e621's recommended one-request-per-second client pace."""
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.request_interval:
                time.sleep(self.request_interval - elapsed)
        self._last_request_at = time.monotonic()

    def request_json(
        self,
        path: str,
        parameters: Mapping[str, str] | None = None,
        *,
        method: str = "GET",
        form: Mapping[str, str] | None = None,
    ) -> object:
        """Perform authenticated JSON I/O without putting credentials in the URL."""
        query = urllib.parse.urlencode(parameters or {})
        url = f"{API_ROOT}/{path.lstrip('/')}" + (f"?{query}" if query else "")
        headers = self._headers()
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        self._wait_for_rate_limit()
        try:
            with self.opener(request, self.timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
        except urllib.error.HTTPError as exc:
            reasons = {
                401: "invalid_credentials",
                403: "access_denied",
                404: "not_found",
                422: "validation_error",
                429: "rate_limited",
                500: "server_error",
                502: "server_error",
                503: "rate_limited",
            }
            raise E621ApiError(exc.code, reasons.get(exc.code, "http_error")) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            reason = "timeout" if isinstance(exc, TimeoutError) else "network_error"
            raise E621ApiError(None, reason) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode(charset, errors="replace"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MalformedE621Response("e621 returned malformed JSON") from exc

    def fetch_posts(
        self, *, tags: str = "", limit: int = 1, page: int | None = None
    ) -> list[dict[str, Any]]:
        parameters = {"limit": str(max(1, min(limit, 320)))}
        if tags:
            parameters["tags"] = tags
        if page is not None:
            parameters["page"] = str(max(1, page))
        payload = self.request_json("posts.json", parameters)
        posts = payload.get("posts") if isinstance(payload, dict) else None
        if not isinstance(posts, list) or any(not isinstance(post, dict) for post in posts):
            raise MalformedE621Response("e621 response does not contain a posts list")
        return posts

    def validate_credentials(self) -> None:
        """Prove that e621 accepts the Basic credentials with a cheap read."""
        self.fetch_posts(limit=1)

    def update_post_tags(
        self, post_id: str | int, additions: tuple[str, ...], removals: tuple[str, ...]
    ) -> object:
        """Apply only an explicit tag delta through e621's post update API."""
        diff = " ".join((*additions, *(f"-{tag}" for tag in removals)))
        if not diff:
            return {}
        return self.request_json(
            f"posts/{int(post_id)}.json",
            method="PATCH",
            form={"post[tag_string_diff]": diff},
        )
