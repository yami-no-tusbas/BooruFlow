"""Gelbooru edit transport controlled through a dedicated Chromium CDP session.

No browser cookie, password, or token leaves Chromium.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import urlopen

from booruflow.infrastructure.browser_launcher import BrowserLauncher
from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruSessionExpiredError,
    GelbooruSessionUnknownError,
    GelbooruTransportError,
)

GELBOORU_ACCOUNT = "https://gelbooru.com/index.php?page=account&s=home"
CDP_POLL_INTERVAL_SECONDS = 0.15
DEFAULT_CDP_TIMEOUT_SECONDS = 10.0
MINIMUM_USABLE_ACCOUNT_TEXT_LENGTH = 40
PUBLISH_NAVIGATION_TIMEOUT_SECONDS = 20.0

SESSION_DIAGNOSTIC_SCRIPT = """(() => {
    const text = (document.body && document.body.innerText || '').toLowerCase();
    const url = location.href;
    return {
        accountHome: /[?&]page=account(?:&|$)/.test(url) && /[?&]s=home(?:&|$)/.test(url),
        bodyPresent: Boolean(document.body),
        bodyTextLength: text.trim().length,
        loginForm: Boolean(document.querySelector(
            'form[action*="s=login"], form[action*="page=account"] input[type="password"], input[name="pass"]'
        )),
        logoutMarker: Boolean(document.querySelector(
            'a[href*="logout"], form[action*="logout"], [name="logout"], #logout'
        )),
        loggedOutText: /you are not logged in|not logged in/.test(text),
        challengeMarker: Boolean(document.querySelector(
            '#challenge-form, .cf-challenge, [id^="cf-chl"], [class*="cf-chl"]'
        )) || /just a moment|verify you are human|checking your browser/.test(
            (document.title || '') + ' ' + text.slice(0, 2000)
        )
    };
})()"""


class BrowserAutomation(Protocol):
    """Minimal browser control surface needed by Gelbooru publication."""

    def open_working_tab(self) -> None: ...
    def navigate(self, url: str) -> None: ...
    def evaluate(
        self, expression: str, arguments: Mapping[str, object] | None = None
    ) -> object: ...
    def wait_for_url(self, expected: Callable[[str], bool], timeout_seconds: float) -> str: ...


class BrowserGelbooruSession:
    """Validate authentication from page state without reading browser cookies."""

    def __init__(self, browser: BrowserAutomation) -> None:
        self.browser = browser

    def validate_authenticated(self) -> None:
        self.browser.open_working_tab()
        self.browser.navigate(GELBOORU_ACCOUNT)
        result = self.browser.evaluate(SESSION_DIAGNOSTIC_SCRIPT)
        values = result if isinstance(result, dict) else {}
        if values.get("loginForm") is True or values.get("loggedOutText") is True:
            raise GelbooruSessionExpiredError(
                "Session Gelbooru expirée : reconnectez-vous dans le profil dédié."
            )
        usable_account = (
            values.get("accountHome") is True
            and values.get("bodyPresent") is True
            and isinstance(values.get("bodyTextLength"), (int, float))
            and values["bodyTextLength"] > MINIMUM_USABLE_ACCOUNT_TEXT_LENGTH
        )
        if values.get("challengeMarker") is True or not (
            values.get("logoutMarker") is True or usable_account
        ):
            raise GelbooruSessionUnknownError(
                "La session Gelbooru n'a pas pu être confirmée ; aucune publication n'a été envoyée."
            )


class BrowserGelbooruEditTransport:
    """Mutate only the tags control of the live Gelbooru edit form, then submit it."""

    def submit(self, session: BrowserGelbooruSession, post_id: str, tags: tuple[str, ...]) -> None:
        browser = session.browser
        browser.open_working_tab()
        browser.navigate(f"https://gelbooru.com/index.php?page=post&s=edit&id={post_id}")
        result = browser.evaluate(
            """(() => {
                if (document.querySelector('input[name=login], form[action*=login]')) return 'auth';
                const form = document.querySelector('form[action*="edit_post.php"]');
                const field = form && form.querySelector('[name="tags"]');
                if (!form || !field) return 'form';
                field.value = arguments[0]; form.submit(); return 'submitted';
            })()""",
            {"0": " ".join(tags)},
        )
        if result == "auth":
            raise GelbooruSessionExpiredError(
                "Session Gelbooru expirée : reconnectez-vous dans le profil dédié."
            )
        if result != "submitted":
            raise GelbooruTransportError("Formulaire Gelbooru d'édition introuvable ou inattendu.")
        try:
            browser.wait_for_url(
                lambda url: _is_post_url(url, post_id), PUBLISH_NAVIGATION_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            raise GelbooruTransportError(
                "Gelbooru n'a pas confirmé l'édition dans le délai imparti."
            ) from exc


def _is_post_url(url: str, post_id: str) -> bool:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "gelbooru.com"
        and parsed.path.endswith("/index.php")
        and query.get("page") == ["post"]
        and query.get("s") == ["view"]
        and query.get("id") == [str(post_id)]
    )


class CdpBrowserAutomation:
    """Small synchronous CDP adapter for a dedicated Chromium profile."""

    def __init__(
        self,
        port: int,
        *,
        timeout_seconds: float = DEFAULT_CDP_TIMEOUT_SECONDS,
        websocket_factory=None,
    ) -> None:
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.websocket_factory = websocket_factory
        self.socket = None
        self._next_id = 0

    def open_working_tab(self) -> None:
        if self.socket is not None:
            return
        deadline = time.monotonic() + self.timeout_seconds
        endpoint = ""
        last_error: Exception | None = None
        from urllib.request import Request

        while time.monotonic() < deadline:
            try:
                with urlopen(
                    f"http://127.0.0.1:{self.port}/json/list",
                    timeout=min(1.0, self.timeout_seconds),
                ) as response:
                    targets = json.loads(response.read())
                pages = [target for target in targets if target.get("type") == "page"]
                target = next(
                    (
                        page
                        for page in pages
                        if urlparse(str(page.get("url", ""))).netloc.casefold() == "gelbooru.com"
                    ),
                    next(
                        (page for page in pages if page.get("url") == "about:blank"),
                        pages[0] if pages else None,
                    ),
                )
                if target is not None:
                    endpoint = str(target["webSocketDebuggerUrl"])
                    for page in pages:
                        if (
                            page is not target
                            and page.get("url") == "about:blank"
                            and page.get("id")
                        ):
                            blank_id = quote(str(page["id"]), safe="")
                            try:
                                with urlopen(
                                    f"http://127.0.0.1:{self.port}/json/close/{blank_id}",
                                    timeout=min(1.0, self.timeout_seconds),
                                ):
                                    pass
                            except OSError:
                                # A disappearing initial tab is already the desired outcome.
                                pass
                else:
                    target_url = quote("https://gelbooru.com/", safe="")
                    request = Request(
                        f"http://127.0.0.1:{self.port}/json/new?{target_url}", method="PUT"
                    )
                    with urlopen(request, timeout=min(1.0, self.timeout_seconds)) as response:
                        endpoint = str(json.loads(response.read())["webSocketDebuggerUrl"])
                break
            except (OSError, TypeError, ValueError, KeyError) as exc:
                last_error = exc
                time.sleep(CDP_POLL_INTERVAL_SECONDS)
        if not endpoint:
            raise GelbooruTransportError(
                "Le contrôle local du profil dédié BooruFlow n'a pas démarré dans le délai imparti."
            ) from last_error
        try:
            factory = self.websocket_factory
            if factory is None:
                import websocket  # type: ignore[import-not-found]

                factory = websocket.create_connection
            self.socket = factory(endpoint, timeout=self.timeout_seconds)
        except ModuleNotFoundError as exc:
            raise GelbooruTransportError(
                "Support CDP absent : installez l'option BooruFlow [browser-cdp]."
            ) from exc
        except Exception as exc:
            raise GelbooruTransportError(
                "Profil dédié non lancé avec le contrôle local BooruFlow."
            ) from exc

    def _call(self, method: str, params: Mapping[str, object] | None = None) -> object:
        if self.socket is None:
            raise RuntimeError("CDP tab is not open")
        self._next_id += 1
        request_id = self._next_id
        self.socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            response = json.loads(self.socket.recv())
            if response.get("id") == request_id:
                if "error" in response:
                    raise GelbooruTransportError("CDP Gelbooru request failed")
                return response.get("result", {})

    def navigate(self, url: str) -> None:
        result = self._call("Page.navigate", {"url": url})
        if isinstance(result, Mapping) and result.get("errorText"):
            raise GelbooruTransportError(f"Navigation Gelbooru impossible : {result['errorText']}")
        self._wait_for_document(self.timeout_seconds)

    def _wait_for_document(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            state = self.evaluate(
                "({url: location.href, readyState: document.readyState, body: Boolean(document.body)})"
            )
            if (
                isinstance(state, dict)
                and state.get("url") not in {None, "", "about:blank"}
                and state.get("readyState") in {"interactive", "complete"}
                and state.get("body") is True
            ):
                return
            time.sleep(CDP_POLL_INTERVAL_SECONDS)
        raise GelbooruTransportError(
            "La page Gelbooru n'a pas fini de charger dans le délai imparti."
        )

    def evaluate(self, expression: str, arguments: Mapping[str, object] | None = None) -> object:
        # Arguments remain local to this single expression and never become page globals.
        if arguments:
            local_expression = expression.replace("arguments", "__booruflow_args")
            expression = (
                f"(() => {{ const __booruflow_args = {json.dumps(arguments)}; "
                f"return ({local_expression}); }})()"
            )
        result = self._call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return result.get("result", {}).get("value")

    def wait_for_url(self, expected: Callable[[str], bool], timeout_seconds: float) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            url = str(self.evaluate("location.href"))
            if expected(url):
                return url
            time.sleep(CDP_POLL_INTERVAL_SECONDS)
        raise TimeoutError()


class BrowserGelbooruSessionFactory:
    """Reuse one authenticated CDP session for a publication run."""

    def __init__(
        self, launcher: BrowserLauncher, *, automation_factory=CdpBrowserAutomation
    ) -> None:
        self.launcher = launcher
        self.automation_factory = automation_factory
        self._session: BrowserGelbooruSession | None = None

    def open(self) -> None:
        if not self.launcher.ensure_dedicated("https://gelbooru.com/"):
            raise GelbooruTransportError(
                "Impossible de lancer le navigateur Chromium avec le profil dédié BooruFlow."
            )

    def create(self) -> BrowserGelbooruSession:
        if self._session is not None:
            return self._session
        self.open()
        port = self.launcher.debugging_port
        if port is None:
            raise GelbooruTransportError("Le profil dédié doit être relancé depuis BooruFlow.")
        self._session = BrowserGelbooruSession(self.automation_factory(port))
        return self._session

    def validate(self) -> None:
        self.create().validate_authenticated()
