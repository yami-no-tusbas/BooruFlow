import json

import pytest

from booruflow.infrastructure.gelbooru_cdp_diagnostic import (
    DIAGNOSTIC_ENV,
    DIAGNOSTIC_PORT_ENV,
    DIAGNOSTIC_PRE_QAPP_ENV,
    QT_REMOTE_DEBUGGING_ENV,
    CdpHandshakeError,
    EmbeddedCdpConfiguration,
    EmbeddedCdpNetworkCapture,
    analyze_request_will_be_sent,
    configure_embedded_cdp_startup,
    embedded_cdp_configuration,
    select_exact_page_target,
    validate_local_websocket_url,
)
from booruflow.infrastructure.gelbooru_http_diagnostic import HttpDiagnosticExpectation


def event(*, post_data="tags=alpha+new_tag&submit=Save+changes", url=None):
    request = {
        "method": "POST",
        "url": url or "https://gelbooru.com/public/edit_post.php",
        "headers": {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Cookie": "must-not-escape",
        },
    }
    if post_data is not None:
        request["postData"] = post_data
    return {"requestId": "safe-opaque-id", "request": request}


def test_startup_flag_is_consumed_and_binds_qt_debugging_to_loopback():
    env = {}
    remaining = configure_embedded_cdp_startup(
        ["booruflow", "--embedded-cdp-diagnostic=19333", "--style", "Fusion"],
        environ=env,
    )

    assert remaining == ["booruflow", "--style", "Fusion"]
    assert env == {
        DIAGNOSTIC_ENV: "1",
        DIAGNOSTIC_PORT_ENV: "19333",
        DIAGNOSTIC_PRE_QAPP_ENV: "1",
        QT_REMOTE_DEBUGGING_ENV: "127.0.0.1:19333",
    }
    configuration = embedded_cdp_configuration(env)
    assert configuration.enabled is True
    assert configuration.configured_before_qapplication is True


@pytest.mark.parametrize("port", ["0", "80", "70000", "abc"])
def test_startup_flag_rejects_invalid_port(port):
    with pytest.raises(ValueError):
        configure_embedded_cdp_startup(
            ["booruflow", f"--embedded-cdp-diagnostic={port}"], environ={}
        )


def test_configuration_refuses_a_non_matching_or_non_loopback_qt_endpoint():
    env = {
        DIAGNOSTIC_ENV: "1",
        DIAGNOSTIC_PORT_ENV: "9223",
        QT_REMOTE_DEBUGGING_ENV: "0.0.0.0:9223",
    }
    configuration = embedded_cdp_configuration(env)
    assert configuration.enabled is False
    assert configuration.error == "qt_debugging_not_configured"


def test_configuration_requires_the_pre_qapplication_startup_marker():
    configuration = embedded_cdp_configuration({
        DIAGNOSTIC_ENV: "1",
        DIAGNOSTIC_PORT_ENV: "9223",
        QT_REMOTE_DEBUGGING_ENV: "127.0.0.1:9223",
    })
    assert configuration.enabled is False
    assert configuration.error == "not_configured_before_qapplication"


def target(target_id="expected", *, target_type="page", url="https://gelbooru.com/"):
    return {
        "id": target_id,
        "type": target_type,
        "url": url,
        "webSocketDebuggerUrl": f"ws://127.0.0.1:9223/devtools/page/{target_id}",
    }


def version():
    return {"Browser": "QtWebEngine", "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/test"}


def test_exact_target_is_selected_instead_of_first_or_blank_target():
    selected = select_exact_page_target([
        target("other", url="about:blank"),
        target("expected"),
    ], "expected")
    assert selected.target_id == "expected"
    assert selected.target_type == "page"
    assert selected.url_kind == "gelbooru"


def test_json_list_without_expected_target_is_rejected():
    with pytest.raises(CdpHandshakeError) as caught:
        select_exact_page_target([target("other")], "expected")
    assert caught.value.phase == "target"
    assert caught.value.reason == "expected_devtools_id_missing"


def test_matching_non_page_target_is_rejected():
    with pytest.raises(CdpHandshakeError) as caught:
        select_exact_page_target([target(target_type="worker")], "expected")
    assert caught.value.reason == "expected_target_not_page"


def test_matching_target_without_websocket_url_is_rejected():
    value = target()
    value.pop("webSocketDebuggerUrl")
    with pytest.raises(CdpHandshakeError) as caught:
        select_exact_page_target([value], "expected")
    assert caught.value.reason == "websocket_url_missing"


def test_websocket_url_must_remain_on_the_configured_loopback_port():
    configuration = EmbeddedCdpConfiguration(True, port=9223)
    assert validate_local_websocket_url(
        "ws://localhost:9223/devtools/page/expected", configuration
    ).startswith("ws://localhost:9223/")
    with pytest.raises(CdpHandshakeError):
        validate_local_websocket_url(
            "ws://192.0.2.1:9223/devtools/page/expected", configuration
        )


def test_direct_event_post_data_uses_safe_shared_parser_and_never_calls_fallback():
    calls = []
    snapshot = analyze_request_will_be_sent(
        event(post_data=(
            "tags=alpha+irene_%28arknights%29+alpha&"
            "csrf-token=secret&uid=secret&uname=secret&lupdated=secret"
        )),
        HttpDiagnosticExpectation(
            "manual", additions=("alpha", "new_tag"),
            removals=("irene_(arknights)", "highres"),
        ),
        get_request_post_data=lambda request_id: calls.append(request_id),
    )

    assert calls == []
    assert snapshot is not None
    assert snapshot.post_data_source == "event"
    assert snapshot.tags_entries == 1
    assert snapshot.tag_count == 3
    assert snapshot.duplicate_tag_count == 1
    assert snapshot.additions_present == (True, False)
    assert snapshot.removals_present == (True, False)
    assert snapshot.plus_count == 2
    assert snapshot.percent28_count == 1
    assert snapshot.percent29_count == 1
    safe = snapshot.safe_log()
    assert "source=manual" in safe
    assert "post_data_source=event" in safe
    for forbidden in (
        "secret", "Cookie", "csrf-token", "uid", "uname", "lupdated",
        "alpha", "new_tag", "irene", "highres",
    ):
        assert forbidden not in safe


def test_missing_event_post_data_uses_get_request_post_data_fallback():
    calls = []

    def fallback(request_id):
        calls.append(request_id)
        return {"postData": "tags=first%20tag%0D%0Athird&tags=fourth_tag"}

    snapshot = analyze_request_will_be_sent(
        event(post_data=None),
        HttpDiagnosticExpectation("embedded"),
        get_request_post_data=fallback,
    )

    assert calls == ["safe-opaque-id"]
    assert snapshot is not None
    assert snapshot.source == "embedded"
    assert snapshot.post_data_source == "fallback"
    assert snapshot.tags_entries == 2
    assert snapshot.tag_count == 4
    assert snapshot.percent20_count == 1
    assert snapshot.encoded_crlf_count == 1
    assert snapshot.underscore_count == 1


def test_non_target_event_is_ignored_without_requesting_post_data():
    calls = []
    snapshot = analyze_request_will_be_sent(
        event(post_data=None, url="https://gelbooru.com/public/add_comment.php"),
        HttpDiagnosticExpectation("embedded"),
        get_request_post_data=lambda request_id: calls.append(request_id),
    )
    assert snapshot is None
    assert calls == []


def test_manual_and_embedded_use_the_same_output_shape():
    params = event(post_data="tags=a+b&submit=Save")
    manual = analyze_request_will_be_sent(
        params,
        HttpDiagnosticExpectation("manual"),
        get_request_post_data=lambda _request_id: None,
    ).safe_log()
    embedded = analyze_request_will_be_sent(
        params,
        HttpDiagnosticExpectation("embedded"),
        get_request_post_data=lambda _request_id: None,
    ).safe_log()

    assert manual.replace("source=manual", "source=embedded") == embedded


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class FakeSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.timeouts = []
        self.closed = False

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        if not self.messages:
            raise WebSocketTimeoutException()
        return json.dumps(self.messages.pop(0))

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        self.closed = True


class WebSocketTimeoutException(TimeoutError):
    pass


def make_capture(*, socket=None, opener=None, command_timeout=0.02):
    logs = []
    finished = []
    configuration = EmbeddedCdpConfiguration(
        True, port=9223, configured_before_qapplication=True
    )
    capture = EmbeddedCdpNetworkCapture(
        configuration,
        "expected",
        HttpDiagnosticExpectation("manual"),
        emit_log=logs.append,
        emit_finished=finished.append,
        websocket_factory=(lambda *_args, **_kwargs: socket) if socket else None,
        urlopen_function=opener,
        timeout_seconds=0,
        command_timeout_seconds=command_timeout,
        startup_timeout_seconds=1,
        startup_retry_seconds=0,
        port_probe=lambda _host, _port, _timeout: True,
        sleep=lambda _seconds: None,
    )
    return capture, logs, finished


def test_complete_handshake_uses_http_discovery_runtime_probe_and_network_enable():
    urls = []

    def opener(url, timeout):
        urls.append((url, timeout))
        if url.endswith("/json/version"):
            return FakeHttpResponse(version())
        return FakeHttpResponse([target("other", url="about:blank"), target("expected")])

    socket = FakeSocket([
        {"method": "Runtime.executionContextCreated", "params": {"secret": "ignored"}},
        {"id": 1, "result": {}},
        {"id": 2, "result": {"result": {"type": "number", "value": 2}}},
        {"method": "Network.loadingFinished", "params": {"secret": "ignored"}},
        {"id": 3, "result": {}},
    ])
    factory_calls = []

    def factory(url, **options):
        factory_calls.append((url, options))
        return socket

    capture, logs, finished = make_capture(socket=socket, opener=opener)
    capture.websocket_factory = factory
    capture._run()

    assert [path.rsplit("/", 2)[-2:] for path, _timeout in urls] == [
        ["json", "version"], ["json", "list"]
    ]
    assert factory_calls == [(
        "ws://127.0.0.1:9223/devtools/page/expected",
        {"timeout": 4.0, "origin": "http://127.0.0.1:9223"},
    )]
    assert [(item["id"], item["method"]) for item in socket.sent] == [
        (1, "Runtime.enable"),
        (2, "Runtime.evaluate"),
        (3, "Network.enable"),
    ]
    assert capture._armed is True
    joined = "\n".join(logs)
    for marker in (
        "http_endpoint=true", "json_version=true", "json_list=true", "targets=2",
        "target_match=true", "expected_devtools_id_present=true",
        "selected_target_type=page", "selected_target_url_kind=gelbooru",
        "websocket_url=true", "websocket_connected=true",
        "probe_command=true", "network_enable=true",
    ):
        assert marker in joined
    assert "secret" not in joined
    assert "response_id=none method=Runtime.executionContextCreated" in joined
    assert "response_id=3 method=none" in joined
    assert finished == ["manual:timeout"]


def test_dispatcher_ignores_events_and_old_responses_until_matching_id():
    socket = FakeSocket([
        {"id": 98, "result": {"stale": True}},
        {"method": "Runtime.consoleAPICalled", "params": {"secret": "ignored"}},
        {"id": 7, "result": {"ok": True}},
    ])
    capture, logs, _finished = make_capture(socket=socket, opener=lambda *_a, **_k: None)
    capture._socket = socket

    result = capture._await_call(7, "probe")

    assert result == {"ok": True}
    assert "response_id=98 method=none" in "\n".join(logs)
    assert "response_id=none method=Runtime.consoleAPICalled" in "\n".join(logs)
    assert "secret" not in "\n".join(logs)


def test_runtime_evaluate_probe_requires_the_numeric_result_two():
    socket = FakeSocket([
        {"id": 1, "result": {}},
        {"id": 2, "result": {"result": {"value": 3}}},
    ])
    capture, _logs, _finished = make_capture(socket=socket, opener=lambda *_a, **_k: None)
    capture._socket = socket

    with pytest.raises(CdpHandshakeError) as caught:
        capture._probe_runtime()

    assert caught.value.phase == "runtime_evaluate"
    assert caught.value.reason == "unexpected_result"


def test_timeout_is_reported_for_the_exact_command_phase():
    socket = FakeSocket([])
    capture, _logs, _finished = make_capture(
        socket=socket, opener=lambda *_a, **_k: None, command_timeout=0.001
    )
    capture._socket = socket

    with pytest.raises(CdpHandshakeError) as caught:
        capture._call("Network.enable", None, "network_enable")

    assert caught.value.phase == "network_enable"
    assert caught.value.reason == "timeout"


def test_http_timeout_stops_before_websocket_connection():
    websocket_calls = []

    def opener(_url, timeout):
        raise TimeoutError(f"local endpoint did not answer in {timeout}")

    capture, logs, finished = make_capture(opener=opener)
    capture.websocket_factory = lambda *_args, **_kwargs: websocket_calls.append(True)
    capture._run()

    assert websocket_calls == []
    assert capture.failure_phase == "waiting_json_version"
    assert capture.failure_reason == "timeout"
    assert "phase=waiting_json_version result=false reason=timeout" in "\n".join(logs)
    assert finished == ["manual:error"]


def test_port_not_listening_is_reported_before_any_http_request():
    opened = []
    capture, logs, _finished = make_capture(opener=lambda *args: opened.append(args))
    capture.port_probe = lambda _host, _port, _timeout: False
    capture._run()

    assert opened == []
    assert capture.failure_phase == "waiting_port"
    assert capture.failure_reason == "timeout"
    assert "port_listening=false" in "\n".join(logs)


def test_json_version_retries_after_port_becomes_available():
    attempts = []

    def opener(url, timeout):
        _ = timeout
        attempts.append(url)
        if url.endswith("/json/version") and attempts.count(url) == 1:
            raise TimeoutError()
        if url.endswith("/json/version"):
            return FakeHttpResponse(version())
        return FakeHttpResponse([target("expected")])

    socket = FakeSocket([
        {"id": 1, "result": {"sessionId": "safe-session"}},
        {"id": 2, "result": {}},
        {"id": 3, "result": {"result": {"value": 2}}},
        {"id": 4, "result": {}},
    ])
    capture, logs, _finished = make_capture(socket=socket, opener=opener)
    capture._run()

    assert attempts.count("http://127.0.0.1:9223/json/version") == 2
    assert "port_listening=true" in "\n".join(logs)
    assert "json_version=true" in "\n".join(logs)


def test_websocket_connect_timeout_is_distinct_from_command_timeout():
    def opener(url, timeout):
        assert timeout == 2.0
        if url.endswith("/json/version"):
            return FakeHttpResponse(version())
        return FakeHttpResponse([target("expected")])

    capture, logs, _finished = make_capture(opener=opener)
    capture.websocket_factory = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(WebSocketTimeoutException())
    )
    capture._run()

    assert capture.failure_phase == "websocket_connect"
    assert capture.failure_reason == "timeout"
    assert "phase=websocket_connect result=false reason=timeout" in "\n".join(logs)
    assert "network_enable=true" not in "\n".join(logs)


def test_missing_exact_target_stops_before_websocket_connection():
    def opener(url, timeout):
        assert timeout == 2.0
        if url.endswith("/json/version"):
            return FakeHttpResponse(version())
        return FakeHttpResponse([target("other")])

    capture, logs, _finished = make_capture(opener=opener)
    capture.websocket_factory = lambda *_args, **_kwargs: pytest.fail(
        "websocket must not be opened for the wrong target"
    )
    capture._run()

    assert capture.failure_phase == "target"
    assert capture.failure_reason == "expected_devtools_id_missing"
    joined = "\n".join(logs)
    assert "target_match=false" in joined
    assert "expected_devtools_id_present=false" in joined
