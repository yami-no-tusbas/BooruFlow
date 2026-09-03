"""Opt-in CDP Network capture for QtWebEngine Gelbooru diagnostics.

This module never creates or modifies an HTTP request.  It attaches only to a
specific QtWebEngine page target on a loopback debugging endpoint and reduces
the selected POST body to the safe structural snapshot shared by manual and
Embedded diagnostics.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Callable, Mapping, MutableSequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from booruflow.infrastructure.gelbooru_http_diagnostic import (
    HttpDiagnosticExpectation,
    OutgoingEditRequestSnapshot,
    analyze_urlencoded_edit_request,
    is_gelbooru_edit_request,
)

DIAGNOSTIC_ARGUMENT = "--embedded-cdp-diagnostic"
DIAGNOSTIC_ENV = "BOORUFLOW_EMBEDDED_CDP_DIAGNOSTIC"
DIAGNOSTIC_PORT_ENV = "BOORUFLOW_EMBEDDED_CDP_PORT"
DIAGNOSTIC_PRE_QAPP_ENV = "BOORUFLOW_EMBEDDED_CDP_CONFIGURED_BEFORE_QAPPLICATION"
QT_REMOTE_DEBUGGING_ENV = "QTWEBENGINE_REMOTE_DEBUGGING"
DEFAULT_DIAGNOSTIC_PORT = 9223
_MAX_BODY_BYTES = 1024 * 1024


def _local_port_is_listening(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class EmbeddedCdpConfiguration:
    enabled: bool
    host: str = "127.0.0.1"
    port: int = DEFAULT_DIAGNOSTIC_PORT
    error: str = ""
    configured_before_qapplication: bool = False

    @property
    def http_origin(self) -> str:
        return f"http://{self.host}:{self.port}"

@dataclass(frozen=True, slots=True)
class CdpPageTarget:
    target_id: str
    target_type: str
    url_kind: str
    websocket_url: str


class CdpHandshakeError(RuntimeError):
    def __init__(self, phase: str, reason: str) -> None:
        super().__init__(f"{phase}:{reason}")
        self.phase = phase
        self.reason = reason


class CdpHandshakeTimeout(CdpHandshakeError):
    pass


def _target_url_kind(value: object) -> str:
    url = str(value)
    if url == "about:blank":
        return "blank"
    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() == "gelbooru.com":
        return "gelbooru"
    return "other"


def select_exact_page_target(targets: object, expected_target_id: str) -> CdpPageTarget:
    """Select one exact page target; never fall back to another WebView."""
    if not isinstance(targets, list):
        raise CdpHandshakeError("json_list", "invalid_targets_payload")
    matching = [
        target for target in targets
        if isinstance(target, Mapping) and str(target.get("id", "")) == expected_target_id
    ]
    if not matching:
        raise CdpHandshakeError("target", "expected_devtools_id_missing")
    target = matching[0]
    target_type = str(target.get("type", ""))
    if target_type != "page":
        raise CdpHandshakeError("target", "expected_target_not_page")
    websocket_url = str(target.get("webSocketDebuggerUrl", ""))
    if not websocket_url:
        raise CdpHandshakeError("target", "websocket_url_missing")
    return CdpPageTarget(
        target_id=expected_target_id,
        target_type=target_type,
        url_kind=_target_url_kind(target.get("url", "")),
        websocket_url=websocket_url,
    )


def validate_local_websocket_url(
    websocket_url: str, configuration: EmbeddedCdpConfiguration
) -> str:
    parsed = urlparse(websocket_url)
    if (
        parsed.scheme != "ws"
        or (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port != configuration.port
        or not parsed.path.startswith("/devtools/page/")
    ):
        raise CdpHandshakeError("target", "non_local_websocket_url")
    return websocket_url


def _valid_port(value: object) -> int | None:
    try:
        port = int(str(value))
    except (TypeError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def configure_embedded_cdp_startup(
    argv: MutableSequence[str], *, environ: dict[str, str] | None = None
) -> list[str]:
    """Consume the diagnostic CLI flag before PySide6/QtWebEngine is imported."""
    env = os.environ if environ is None else environ
    remaining: list[str] = []
    requested_port: int | None = None
    for argument in argv:
        if argument == DIAGNOSTIC_ARGUMENT:
            requested_port = _valid_port(env.get(DIAGNOSTIC_PORT_ENV)) or DEFAULT_DIAGNOSTIC_PORT
            continue
        prefix = f"{DIAGNOSTIC_ARGUMENT}="
        if argument.startswith(prefix):
            requested_port = _valid_port(argument[len(prefix):])
            if requested_port is None:
                raise ValueError("Le port du diagnostic Embedded CDP est invalide.")
            continue
        remaining.append(argument)
    if requested_port is not None:
        env[DIAGNOSTIC_ENV] = "1"
        env[DIAGNOSTIC_PORT_ENV] = str(requested_port)
        env[DIAGNOSTIC_PRE_QAPP_ENV] = "1"
        env[QT_REMOTE_DEBUGGING_ENV] = f"127.0.0.1:{requested_port}"
    return remaining


def embedded_cdp_configuration(
    environ: Mapping[str, str] | None = None,
) -> EmbeddedCdpConfiguration:
    env = os.environ if environ is None else environ
    if str(env.get(DIAGNOSTIC_ENV, "")).casefold() not in {"1", "true", "yes", "on"}:
        return EmbeddedCdpConfiguration(False)
    port = _valid_port(env.get(DIAGNOSTIC_PORT_ENV))
    expected = f"127.0.0.1:{port}" if port is not None else ""
    if port is None:
        return EmbeddedCdpConfiguration(False, error="invalid_port")
    if str(env.get(QT_REMOTE_DEBUGGING_ENV, "")).strip() != expected:
        return EmbeddedCdpConfiguration(False, port=port, error="qt_debugging_not_configured")
    configured_before = str(env.get(DIAGNOSTIC_PRE_QAPP_ENV, "")) == "1"
    if not configured_before:
        return EmbeddedCdpConfiguration(
            False, port=port, error="not_configured_before_qapplication"
        )
    return EmbeddedCdpConfiguration(
        True, port=port, configured_before_qapplication=True
    )


def _content_type(headers: object) -> str:
    if not isinstance(headers, Mapping):
        return ""
    for name, value in headers.items():
        if str(name).casefold() == "content-type":
            return str(value)
    return ""


def analyze_request_will_be_sent(
    params: object,
    expectation: HttpDiagnosticExpectation,
    *,
    get_request_post_data: Callable[[str], object],
) -> OutgoingEditRequestSnapshot | None:
    """Reduce one CDP Network event, using the protocol fallback when needed."""
    if not isinstance(params, Mapping):
        return None
    request = params.get("request")
    if not isinstance(request, Mapping):
        return None
    method = str(request.get("method", ""))
    url = str(request.get("url", ""))
    if not is_gelbooru_edit_request(method, url):
        return None

    source = "event"
    if isinstance(request.get("postData"), str):
        post_data = str(request["postData"])
    else:
        source = "fallback"
        result = get_request_post_data(str(params.get("requestId", "")))
        if isinstance(result, Mapping):
            post_data = result.get("postData")
        else:
            post_data = result
        if not isinstance(post_data, str):
            raise TypeError("missing_post_data")

    body = post_data.encode("utf-8")
    truncated = len(body) > _MAX_BODY_BYTES
    if truncated:
        body = body[:_MAX_BODY_BYTES]
    return analyze_urlencoded_edit_request(
        source=expectation.source,
        method=method,
        url=url,
        content_type=_content_type(request.get("headers")),
        body=body,
        additions=expectation.additions,
        removals=expectation.removals,
        body_truncated=truncated,
        post_data_source=source,
    )


class EmbeddedCdpNetworkCapture:
    """Background, one-shot Network listener for one exact QtWebEngine page."""

    def __init__(
        self,
        configuration: EmbeddedCdpConfiguration,
        target_id: str,
        expectation: HttpDiagnosticExpectation,
        *,
        emit_log: Callable[[str], None],
        emit_finished: Callable[[str], None],
        websocket_factory=None,
        urlopen_function=None,
        timeout_seconds: float = 600.0,
        http_timeout_seconds: float = 2.0,
        websocket_timeout_seconds: float = 4.0,
        command_timeout_seconds: float = 4.0,
        startup_timeout_seconds: float = 4.0,
        startup_retry_seconds: float = 0.15,
        port_probe: Callable[[str, int, float], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.configuration = configuration
        self.target_id = str(target_id)
        self.expectation = expectation
        self.emit_log = emit_log
        self.emit_finished = emit_finished
        self.websocket_factory = websocket_factory
        self.urlopen_function = urlopen_function or urlopen
        self.timeout_seconds = timeout_seconds
        self.http_timeout_seconds = http_timeout_seconds
        self.websocket_timeout_seconds = websocket_timeout_seconds
        self.command_timeout_seconds = command_timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.startup_retry_seconds = startup_retry_seconds
        self.port_probe = port_probe or _local_port_is_listening
        self.sleep = sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket = None
        self._next_id = 0
        self._ready = threading.Event()
        self._armed = False
        self.failure_phase = ""
        self.failure_reason = ""
        self._websocket_url = ""

    def start(self, *, wait_seconds: float = 24.0) -> bool:
        self._thread = threading.Thread(
            target=self._run,
            name=f"booruflow-embedded-cdp-{self.expectation.source}",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(wait_seconds):
            self.failure_phase = "startup_wait"
            self.failure_reason = "timeout"
            self.stop()
            return False
        return self._armed

    def stop(self) -> None:
        self._stop.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception:  # noqa: BLE001, S110 - best-effort local shutdown
                pass

    def _log_phase(self, name: str, value: object) -> None:
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        self.emit_log(
            "Gelbooru Embedded CDP: "
            f"source={self.expectation.source} {name}={rendered}"
        )

    def _fetch_json(self, path: str, phase: str) -> object:
        endpoint = f"{self.configuration.http_origin}{path}"
        try:
            response = self.urlopen_function(endpoint, timeout=self.http_timeout_seconds)
            with response:
                return json.loads(response.read())
        except Exception as exc:
            if "timeout" in type(exc).__name__.casefold():
                raise CdpHandshakeTimeout(phase, "timeout") from exc
            raise CdpHandshakeError(phase, type(exc).__name__) from exc

    def _wait_for_devtools(self) -> None:
        """Wait briefly for the local server before making its HTTP handshake."""
        deadline = time.monotonic() + self.startup_timeout_seconds
        while not self._stop.is_set() and time.monotonic() < deadline:
            if self.port_probe(self.configuration.host, self.configuration.port, 0.2):
                self._log_phase("port_listening", True)
                break
            self.sleep(self.startup_retry_seconds)
        else:
            self._log_phase("port_listening", False)
            raise CdpHandshakeTimeout("waiting_port", "timeout")

        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                version = self._fetch_json("/json/version", "json_version")
            except CdpHandshakeError:
                self.sleep(self.startup_retry_seconds)
                continue
            if isinstance(version, Mapping):
                self._log_phase("json_version", True)
                return
            self.sleep(self.startup_retry_seconds)
        raise CdpHandshakeTimeout("waiting_json_version", "timeout")

    def _discover_target(self) -> CdpPageTarget:
        self._wait_for_devtools()
        self._log_phase("http_endpoint", True)

        targets = self._fetch_json("/json/list", "json_list")
        if not isinstance(targets, list):
            raise CdpHandshakeError("json_list", "invalid_payload")
        self._log_phase("json_list", True)
        self._log_phase("targets", len(targets))
        try:
            selected = select_exact_page_target(targets, self.target_id)
        except CdpHandshakeError:
            self._log_phase("expected_devtools_id_present", False)
            self._log_phase("target_match", False)
            raise
        self._log_phase("expected_devtools_id_present", True)
        self._log_phase("target_match", True)
        self._log_phase("selected_target_type", selected.target_type)
        self._log_phase("selected_target_url_kind", selected.url_kind)
        self._websocket_url = validate_local_websocket_url(
            selected.websocket_url, self.configuration
        )
        self._log_phase("websocket_url", True)
        return selected

    def _connect(self, websocket_url: str):
        factory = self.websocket_factory
        if factory is None:
            import websocket  # type: ignore[import-not-found]
            factory = websocket.create_connection
        try:
            socket = factory(
                websocket_url,
                timeout=self.websocket_timeout_seconds,
                origin=self.configuration.http_origin,
            )
        except Exception as exc:
            name = type(exc).__name__
            reason = "timeout" if "timeout" in name.casefold() else name
            error = CdpHandshakeTimeout if reason == "timeout" else CdpHandshakeError
            raise error("websocket_connect", reason) from exc
        set_timeout = getattr(socket, "settimeout", None)
        if callable(set_timeout):
            set_timeout(self.command_timeout_seconds)
        self._log_phase("websocket_connected", True)
        return socket

    def _send(self, method: str, params: Mapping[str, object] | None = None) -> int:
        self._next_id += 1
        call_id = self._next_id
        self._socket.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
        return call_id

    def _receive_json(self) -> Mapping[str, Any] | None:
        try:
            value = json.loads(self._socket.recv())
        except Exception as exc:
            if "timeout" in type(exc).__name__.casefold():
                return None
            raise
        return value if isinstance(value, Mapping) else None

    def _log_message_type(self, message: Mapping[str, Any]) -> None:
        response_id = message.get("id")
        safe_response_id = response_id if isinstance(response_id, int) else "none"
        raw_method = message.get("method")
        safe_method = "none"
        if isinstance(raw_method, str):
            safe_method = "".join(
                character for character in raw_method[:80]
                if character.isalnum() or character in {".", "_", "-"}
            ) or "none"
        self.emit_log(
            "Gelbooru Embedded CDP websocket message: "
            f"response_id={safe_response_id} method={safe_method}"
        )

    def _await_call(self, call_id: int, phase: str) -> object:
        deadline = time.monotonic() + self.command_timeout_seconds
        while not self._stop.is_set() and time.monotonic() < deadline:
            message = self._receive_json()
            if message is None:
                continue
            self._log_message_type(message)
            if message.get("id") == call_id:
                if "error" in message:
                    raise CdpHandshakeError(phase, "cdp_error_response")
                return message.get("result", {})
        if self._stop.is_set():
            raise CdpHandshakeError(phase, "capture_stopped")
        raise CdpHandshakeTimeout(phase, "timeout")

    def _call(
        self, method: str, params: Mapping[str, object] | None, phase: str
    ) -> object:
        return self._await_call(self._send(method, params), phase)

    def _probe_runtime(self) -> None:
        self._call("Runtime.enable", None, "runtime_enable")
        result = self._call(
            "Runtime.evaluate",
            {"expression": "1+1", "returnByValue": True},
            "runtime_evaluate",
        )
        value = None
        if isinstance(result, Mapping):
            remote = result.get("result")
            if isinstance(remote, Mapping):
                value = remote.get("value")
        if value != 2:
            raise CdpHandshakeError("runtime_evaluate", "unexpected_result")
        self._log_phase("probe_command", True)

    def _get_post_data(self, request_id: str) -> object:
        if not request_id:
            raise ValueError("missing_request_id")
        return self._call(
            "Network.getRequestPostData",
            {"requestId": request_id},
            "get_request_post_data",
        )

    def _run(self) -> None:
        outcome = "error"
        try:
            selected = self._discover_target()
            self._socket = self._connect(selected.websocket_url)
            self._probe_runtime()
            self._call("Network.enable", None, "network_enable")
            self._log_phase("network_enable", True)
            self._armed = True
            self._ready.set()
            self.emit_log(
                "Gelbooru CDP Network diagnostic armed: "
                f"source={self.expectation.source} target=exact-page one_shot=true"
            )
            deadline = time.monotonic() + self.timeout_seconds
            while not self._stop.is_set() and time.monotonic() < deadline:
                message = self._receive_json()
                if message is None or message.get("method") != "Network.requestWillBeSent":
                    continue
                snapshot = analyze_request_will_be_sent(
                    message.get("params"),
                    self.expectation,
                    get_request_post_data=self._get_post_data,
                )
                if snapshot is not None:
                    self.emit_log(snapshot.safe_log())
                    outcome = "captured"
                    return
            outcome = "stopped" if self._stop.is_set() else "timeout"
            if outcome == "timeout":
                self.emit_log(
                    "Gelbooru CDP Network diagnostic: "
                    f"source={self.expectation.source} result=timeout no_matching_post=true"
                )
        except ModuleNotFoundError:
            self.failure_phase = "websocket_connect"
            self.failure_reason = "dependency_missing"
            self.emit_log(
                "Gelbooru CDP Network diagnostic: result=unavailable dependency=browser-cdp"
            )
        except CdpHandshakeError as exc:
            self.failure_phase = exc.phase
            self.failure_reason = exc.reason
            if not self._stop.is_set():
                self.emit_log(
                    "Gelbooru Embedded CDP: "
                    f"source={self.expectation.source} phase={exc.phase} "
                    f"result=false reason={exc.reason}"
                )
        except Exception as exc:  # noqa: BLE001 - isolated diagnostic thread boundary
            if self._stop.is_set():
                outcome = "stopped"
            else:
                self.failure_phase = "unexpected"
                self.failure_reason = type(exc).__name__
                self.emit_log(
                    "Gelbooru CDP Network diagnostic: "
                    f"source={self.expectation.source} result=error type={type(exc).__name__}"
                )
        finally:
            self._ready.set()
            socket = self._socket
            if socket is not None:
                try:
                    socket.close()
                except Exception:  # noqa: BLE001, S110 - best-effort local shutdown
                    pass
            self.emit_finished(f"{self.expectation.source}:{outcome}")


def target_path(url: object) -> str:
    """Small test/debug helper that never exposes query or form data."""
    return urlparse(str(url)).path
