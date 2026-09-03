import pytest

from booruflow.infrastructure.gelbooru_browser_transport import (
    BrowserGelbooruEditTransport,
    BrowserGelbooruSession,
    BrowserGelbooruSessionFactory,
    CdpBrowserAutomation,
    _is_post_url,
)
from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruSessionExpiredError,
    GelbooruSessionUnknownError,
    GelbooruTransportError,
)

AUTHENTICATED = {
    "accountHome": True,
    "bodyPresent": True,
    "bodyTextLength": 200,
    "loginForm": False,
    "logoutMarker": True,
    "loggedOutText": False,
    "challengeMarker": False,
}


class Browser:
    def __init__(
        self,
        result="submitted",
        final_url="https://gelbooru.com/index.php?page=post&s=view&id=12",
        session_result=None,
    ):
        self.result = result
        self.final_url = final_url
        self.session_result = AUTHENTICATED if session_result is None else session_result
        self.opened = 0
        self.urls = []
        self.expressions = []

    def open_working_tab(self):
        self.opened += 1

    def navigate(self, url):
        self.urls.append(url)

    def evaluate(self, expression, arguments=None):
        self.expressions.append((expression, arguments))
        if "accountHome" in expression:
            return self.session_result
        return self.result

    def wait_for_url(self, expected, _timeout):
        if expected(self.final_url):
            return self.final_url
        raise TimeoutError()


def test_browser_transport_reuses_one_tab_and_mutates_only_tags_field():
    browser = Browser()
    transport = BrowserGelbooruEditTransport()
    session = BrowserGelbooruSession(browser)

    transport.submit(session, "12", ("a", "new_tag"))
    transport.submit(session, "12", ("a", "second_tag"))

    assert browser.opened == 2  # The automation makes this idempotent and keeps one CDP socket.
    assert browser.urls == [
        "https://gelbooru.com/index.php?page=post&s=edit&id=12",
        "https://gelbooru.com/index.php?page=post&s=edit&id=12",
    ]
    assert browser.expressions[0][1] == {"0": "a new_tag"}
    assert browser.expressions[1][1] == {"0": "a second_tag"}
    script = browser.expressions[0][0]
    assert "field.value" in script and "form.submit" in script
    assert "rating" not in script and "source" not in script


@pytest.mark.parametrize(
    "result,error",
    [("auth", GelbooruSessionExpiredError), ("form", GelbooruTransportError)],
)
def test_browser_transport_rejects_expired_or_unexpected_form(result, error):
    with pytest.raises(error):
        BrowserGelbooruEditTransport().submit(
            BrowserGelbooruSession(Browser(result)), "12", ("a",)
        )


def test_browser_transport_requires_confirmed_final_post_url():
    browser = Browser(final_url="https://gelbooru.com/index.php?page=account&s=login")
    with pytest.raises(GelbooruTransportError, match="confirmé"):
        BrowserGelbooruEditTransport().submit(
            BrowserGelbooruSession(browser), "12", ("a",)
        )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://gelbooru.com/index.php?page=post&s=view&id=12", True),
        ("https://gelbooru.com/index.php?s=view&id=12&page=post", True),
        ("https://gelbooru.com/index.php?page=post&s=view&id=123", False),
        ("https://evil.example/index.php?page=post&s=view&id=12", False),
        ("http://gelbooru.com/index.php?page=post&s=view&id=12", False),
    ],
)
def test_final_post_url_is_parsed_exactly(url, expected):
    assert _is_post_url(url, "12") is expected


def test_session_validation_is_get_only_browser_navigation():
    browser = Browser()
    BrowserGelbooruSession(browser).validate_authenticated()
    assert browser.urls == ["https://gelbooru.com/index.php?page=account&s=home"]
    assert all("submit" not in expression for expression, _args in browser.expressions)


@pytest.mark.parametrize(
    "diagnostic,error",
    [
        ({**AUTHENTICATED, "logoutMarker": False, "loginForm": True}, GelbooruSessionExpiredError),
        ({**AUTHENTICATED, "logoutMarker": False, "loggedOutText": True}, GelbooruSessionExpiredError),
        ({**AUTHENTICATED, "logoutMarker": False, "challengeMarker": True}, GelbooruSessionUnknownError),
        ({}, GelbooruSessionUnknownError),
    ],
)
def test_session_validation_distinguishes_expired_and_unknown(diagnostic, error):
    with pytest.raises(error):
        BrowserGelbooruSession(Browser(session_result=diagnostic)).validate_authenticated()


def test_cdp_navigation_waits_for_a_usable_document(monkeypatch):
    automation = CdpBrowserAutomation(9222)
    calls = []
    states = iter(
        [
            {"url": "about:blank", "readyState": "complete", "body": True},
            {"url": "https://gelbooru.com/", "readyState": "interactive", "body": True},
        ]
    )

    def call(method, params=None):
        calls.append((method, params))
        if method == "Page.navigate":
            return {}
        return {"result": {"value": next(states)}}

    automation._call = call
    monkeypatch.setattr("booruflow.infrastructure.gelbooru_browser_transport.time.sleep", lambda _delay: None)

    automation.navigate("https://gelbooru.com/")

    assert calls[0] == ("Page.navigate", {"url": "https://gelbooru.com/"})
    assert [method for method, _params in calls].count("Runtime.evaluate") == 2


def test_cdp_reuses_existing_gelbooru_target_without_creating_about_blank(monkeypatch):
    opened = []
    socket = object()

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.payload

    def open_url(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        opened.append((url, timeout))
        if "/json/close/" in url:
            return Response(b"Target is closing")
        assert url.endswith("/json/list")
        return Response(
            b'[{"id":"gel","type":"page","url":"https://gelbooru.com/",'
            b'"webSocketDebuggerUrl":"ws://127.0.0.1/devtools/page/1"},'
            b'{"id":"blank","type":"page","url":"about:blank",'
            b'"webSocketDebuggerUrl":"ws://127.0.0.1/devtools/page/2"}]'
        )

    monkeypatch.setattr(
        "booruflow.infrastructure.gelbooru_browser_transport.urlopen",
        open_url,
    )
    automation = CdpBrowserAutomation(
        9222,
        websocket_factory=lambda endpoint, timeout: (
            opened.append((endpoint, timeout)) or socket
        ),
    )

    automation.open_working_tab()
    automation.open_working_tab()

    assert automation.socket is socket
    assert opened == [
        ("http://127.0.0.1:9222/json/list", 1.0),
        ("http://127.0.0.1:9222/json/close/blank", 1.0),
        ("ws://127.0.0.1/devtools/page/1", 10.0),
    ]


def test_cdp_creates_one_gelbooru_target_only_when_no_page_exists(monkeypatch):
    opened = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.payload

    def open_url(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        opened.append(url)
        if url.endswith("/json/list"):
            return Response(b"[]")
        assert "about:blank" not in url
        return Response(b'{"webSocketDebuggerUrl":"ws://127.0.0.1/devtools/page/1"}')

    monkeypatch.setattr(
        "booruflow.infrastructure.gelbooru_browser_transport.urlopen", open_url
    )
    automation = CdpBrowserAutomation(
        9222, websocket_factory=lambda _endpoint, timeout: object()
    )

    automation.open_working_tab()

    assert len(opened) == 2
    assert opened[0].endswith("/json/list")
    assert "/json/new?https%3A%2F%2Fgelbooru.com%2F" in opened[1]


def test_cdp_navigation_timeout_is_reported_without_submitting(monkeypatch):
    automation = CdpBrowserAutomation(9222, timeout_seconds=0.1)
    moments = iter([0.0, 0.0, 1.0])
    automation._call = lambda method, _params=None: (
        {} if method == "Page.navigate" else {
            "result": {"value": {"url": "about:blank", "readyState": "loading", "body": False}}
        }
    )
    monkeypatch.setattr(
        "booruflow.infrastructure.gelbooru_browser_transport.time.monotonic",
        lambda: next(moments),
    )
    monkeypatch.setattr("booruflow.infrastructure.gelbooru_browser_transport.time.sleep", lambda _delay: None)

    with pytest.raises(GelbooruTransportError, match="fini de charger"):
        automation.navigate("https://gelbooru.com/")


def test_cdp_arguments_are_local_to_the_single_expression():
    automation = CdpBrowserAutomation(9222)
    expressions = []
    automation._call = lambda _method, params=None: (
        expressions.append(params["expression"]) or {"result": {"value": "submitted"}}
    )

    assert automation.evaluate("(() => arguments[0])()", {"0": "safe_tag"}) == "submitted"
    assert "const __booruflow_args" in expressions[0]
    assert "window.__booruflow_args" not in expressions[0]


def test_factory_requires_a_successful_strict_dedicated_launch():
    class Launcher:
        debugging_port = 9222

        def __init__(self):
            self.calls = []

        def ensure_dedicated(self, url):
            self.calls.append(url)
            return False

    launcher = Launcher()
    with pytest.raises(GelbooruTransportError, match="Impossible de lancer"):
        BrowserGelbooruSessionFactory(launcher).create()
    assert launcher.calls == ["https://gelbooru.com/"]


def test_factory_reuses_the_same_session_and_target_automation():
    class Launcher:
        debugging_port = 9222

        def __init__(self):
            self.calls = []

        def ensure_dedicated(self, url):
            self.calls.append(url)
            return True

    launcher = Launcher()
    automations = []
    factory = BrowserGelbooruSessionFactory(
        launcher,
        automation_factory=lambda port: automations.append(port) or Browser(),
    )

    first = factory.create()
    second = factory.create()

    assert first is second
    assert launcher.calls == ["https://gelbooru.com/"]
    assert automations == [9222]
