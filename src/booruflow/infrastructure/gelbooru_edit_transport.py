"""Isolated, authenticated Gelbooru web-form submission.

Cookies belong to the injected session and are never persisted by BooruFlow.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

EDIT_ENDPOINT = "https://gelbooru.com/public/edit_post.php"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]


class GelbooruTransportError(RuntimeError):
    """A Gelbooru edit was not confirmed."""


class GelbooruPublishDeferredError(GelbooruTransportError):
    """A safe preflight deliberately stopped before any remote submission."""


class GelbooruSessionValidationError(GelbooruTransportError):
    """A global session condition blocked publication before a remote edit."""


class GelbooruSessionExpiredError(GelbooruSessionValidationError):
    """The authenticated web session is absent or no longer valid."""


class GelbooruSessionUnknownError(GelbooruSessionValidationError):
    """The page supplied neither proof of authentication nor proof of logout."""


class GelbooruAuthenticatedSession(Protocol):
    """Authenticated browser/session adapter, deliberately outside UI and DB."""

    def read_edit_form(self, post_id: str) -> Mapping[str, str]: ...

    def post_urlencoded(
        self, url: str, fields: Mapping[str, str], *, follow_redirects: bool
    ) -> HttpResponse: ...


class _EditFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.fields: dict[str, str] = {}; self._textarea: str | None = None; self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "textarea" and values.get("name"):
            self._textarea = values["name"]; self._chunks = []
        elif tag == "input" and values.get("name"):
            kind = values.get("type", "text").casefold()
            if kind not in {"submit", "button", "image", "reset"} and (kind not in {"checkbox", "radio"} or "checked" in values):
                self.fields[values["name"]] = values.get("value", "")

    def handle_data(self, data: str) -> None:
        if self._textarea is not None: self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea" and self._textarea is not None:
            self.fields[self._textarea] = "".join(self._chunks)
            self._textarea = None; self._chunks = []


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class UrllibGelbooruAuthenticatedSession:
    """Concrete in-memory session adapter; callers supply the authenticated cookie jar.

    It neither discovers browser profiles nor writes cookies anywhere.  A UI integration
    must deliberately provide a cookie jar/session object before a real publication.
    """

    def __init__(self, cookie_jar, *, opener=None) -> None:
        self.opener = opener or build_opener(HTTPCookieProcessor(cookie_jar), _NoRedirect())

    def read_edit_form(self, post_id: str) -> Mapping[str, str]:
        url = f"https://gelbooru.com/index.php?page=post&s=edit&id={post_id}"
        response = self.opener.open(Request(url, method="GET"))
        parser = _EditFormParser(); parser.feed(response.read().decode("utf-8", errors="replace"))
        return parser.fields

    def validate_authenticated(self) -> None:
        """Perform one safe GET and reject the anonymous/login page."""
        request = Request("https://gelbooru.com/index.php?page=account&s=home", method="GET")
        try:
            response = self.opener.open(request)
            status = int(response.getcode())
            location = next((value for key, value in response.headers.items() if key.casefold() == "location"), "")
            body = response.read().decode("utf-8", errors="replace").casefold()
        except HTTPError as exc:
            status, location, body = int(exc.code), str(exc.headers.get("Location", "")), ""
        if status in {401, 403} or "login" in location.casefold():
            raise GelbooruSessionExpiredError("Session Gelbooru non authentifiée ou expirée.")
        if status != 200 or "logout" not in body:
            raise GelbooruSessionExpiredError("La session Gelbooru n'a pas pu être confirmée.")

    def post_urlencoded(
        self, url: str, fields: Mapping[str, str], *, follow_redirects: bool
    ) -> HttpResponse:
        if follow_redirects:
            raise ValueError("Gelbooru success requires observing the initial 302")
        request = Request(
            url, data=encode_form(fields), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            response = self.opener.open(request)
            return HttpResponse(int(response.getcode()), dict(response.headers.items()))
        except HTTPError as exc:
            return HttpResponse(int(exc.code), dict(exc.headers.items()))


class GelbooruEditTransport:
    """Replace only ``tags`` in the current edit form and validate Gelbooru's 302."""

    endpoint = EDIT_ENDPOINT

    def submit(
        self, session: GelbooruAuthenticatedSession, post_id: str, tags: tuple[str, ...]
    ) -> None:
        try:
            fields = dict(session.read_edit_form(post_id))
        except GelbooruSessionExpiredError:
            raise
        except (OSError, ValueError, KeyError) as exc:
            raise GelbooruTransportError(f"could not read current Gelbooru edit form: {exc}") from exc
        if not fields:
            raise GelbooruSessionExpiredError("Gelbooru session is not authenticated or edit form is empty")
        fields["tags"] = " ".join(tags)
        try:
            response = session.post_urlencoded(self.endpoint, fields, follow_redirects=False)
        except GelbooruSessionExpiredError:
            raise
        except OSError as exc:
            raise GelbooruTransportError(f"Gelbooru network error: {exc}") from exc
        if response.status in {401, 403}:
            raise GelbooruSessionExpiredError(f"Gelbooru session expired (HTTP {response.status})")
        if response.status != 302:
            raise GelbooruTransportError(f"unexpected Gelbooru edit response: HTTP {response.status}")
        location = next((value for key, value in response.headers.items() if key.casefold() == "location"), "")
        if not self._is_expected_location(location, post_id):
            if "login" in location.casefold() or not location:
                raise GelbooruSessionExpiredError("Gelbooru session is not authenticated or expired")
            raise GelbooruTransportError(f"unexpected Gelbooru edit redirect: {location!r}")

    @staticmethod
    def _is_expected_location(location: str, post_id: str) -> bool:
        parsed = urlparse(location)
        query = parse_qs(parsed.query)
        return (
            parsed.path.endswith("index.php")
            and query.get("page") == ["post"]
            and query.get("s") == ["view"]
            and query.get("id") == [str(post_id)]
        )


def encode_form(fields: Mapping[str, str]) -> bytes:
    """Small testable helper used by concrete session adapters."""
    return urlencode(fields).encode("utf-8")
