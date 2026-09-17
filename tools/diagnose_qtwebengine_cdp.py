"""Probe QtWebEngine's local DevTools server without loading any network page."""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

from PySide6.QtCore import QTimer
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from booruflow.infrastructure.gelbooru_cdp_diagnostic import (
    EmbeddedCdpConfiguration,
    EmbeddedCdpNetworkCapture,
)
from booruflow.infrastructure.gelbooru_http_diagnostic import HttpDiagnosticExpectation


def port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def request_json(port: int, path: str) -> object:
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
        return json.loads(response.read())


def _read_exact(connection: socket.socket, count: int) -> bytes:
    result = bytearray()
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise ConnectionError("closed")
        result.extend(chunk)
    return bytes(result)


def _masked_text_frame(payload: bytes) -> bytes:
    mask = os.urandom(4)
    size = len(payload)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    if size < 126:
        header = bytes((0x81, 0x80 | size))
    elif size <= 0xFFFF:
        header = bytes((0x81, 0x80 | 126)) + size.to_bytes(2, "big")
    else:
        raise ValueError("diagnostic payload unexpectedly large")
    return header + mask + masked


def raw_websocket_probe(websocket_url: str, command: dict[str, object]) -> dict[str, object]:
    """Perform one RFC 6455 request and expose only frame structure."""
    parsed = urlsplit(websocket_url)
    if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise ValueError("non-local websocket URL")
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    connection = socket.create_connection((parsed.hostname, parsed.port), timeout=3)
    connection.settimeout(4)
    try:
        request = (
            f"GET {parsed.path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Origin: http://127.0.0.1:{parsed.port}\r\n\r\n"
        ).encode("ascii")
        connection.sendall(request)
        header = bytearray()
        while b"\r\n\r\n" not in header:
            header.extend(_read_exact(connection, 1))
            if len(header) > 16_384:
                raise ValueError("handshake header too large")
        lines = header.decode("iso-8859-1").split("\r\n")
        status = lines[0].split(maxsplit=2)[1] if lines else "unknown"
        connection.sendall(_masked_text_frame(json.dumps(command).encode("utf-8")))
        first, second = _read_exact(connection, 2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(_read_exact(connection, 2), "big")
        elif length == 127:
            length = int.from_bytes(_read_exact(connection, 8), "big")
        if second & 0x80:
            mask = _read_exact(connection, 4)
        else:
            mask = b""
        payload = _read_exact(connection, length)
        if mask:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        kind = {1: "text", 2: "binary", 8: "close", 9: "ping", 10: "pong"}.get(opcode, "other")
        result: dict[str, object] = {
            "handshake_status": status,
            "frame_received": True,
            "frame_type": kind,
            "frame_length": len(payload),
        }
        if kind == "text":
            message = json.loads(payload)
            result["has_id"] = isinstance(message, dict) and "id" in message
            result["id"] = message.get("id") if isinstance(message, dict) else None
            result["method"] = message.get("method") if isinstance(message, dict) else None
        return result
    finally:
        connection.close()


def _raw_websocket_connection(websocket_url: str) -> tuple[socket.socket, str]:
    """Open one local RFC 6455 connection for the structural CDP probe."""
    parsed = urlsplit(websocket_url)
    if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise ValueError("non-local websocket URL")
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    connection = socket.create_connection((parsed.hostname, parsed.port), timeout=3)
    connection.settimeout(4)
    request = (
        f"GET {parsed.path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"Origin: http://127.0.0.1:{parsed.port}\r\n\r\n"
    ).encode("ascii")
    connection.sendall(request)
    header = bytearray()
    while b"\r\n\r\n" not in header:
        header.extend(_read_exact(connection, 1))
        if len(header) > 16_384:
            raise ValueError("handshake header too_large")
    lines = header.decode("iso-8859-1").split("\r\n")
    return connection, lines[0].split(maxsplit=2)[1] if lines else "unknown"


def _raw_websocket_message(connection: socket.socket) -> dict[str, object] | None:
    first, second = _read_exact(connection, 2)
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(_read_exact(connection, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(connection, 8), "big")
    mask = _read_exact(connection, 4) if second & 0x80 else b""
    payload = _read_exact(connection, length)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    if opcode == 9:
        connection.sendall(bytes((0x8A, 0x80 | len(payload))) + os.urandom(4))
        return None
    if opcode != 1:
        return None
    value = json.loads(payload)
    return value if isinstance(value, dict) else None


def raw_flat_session_probe(browser_url: str, target_id: str) -> dict[str, object]:
    """Verify Browser -> Target(flat) -> Runtime/Network on one socket only."""
    connection, status = _raw_websocket_connection(browser_url)
    frames = 0
    phase = "attach_flat"
    attached_ok = False
    session_received = False

    def call(command: dict[str, object], expected_id: int, session: str | None = None) -> dict[str, object]:
        nonlocal frames
        connection.sendall(_masked_text_frame(json.dumps(command).encode("utf-8")))
        while True:
            message = _raw_websocket_message(connection)
            frames += 1
            if message is None or message.get("id") != expected_id:
                continue
            if session is not None and message.get("sessionId") != session:
                continue
            return message

    try:
        attached = call(
            {"id": 1, "method": "Target.attachToTarget", "params": {"targetId": target_id, "flatten": True}},
            1,
        )
        attached_ok = "error" not in attached
        result = attached.get("result")
        session = result.get("sessionId") if isinstance(result, dict) else None
        if not isinstance(session, str) or not session:
            return {"handshake_status": status, "attach_flat": False, "session_received": False, "frames": frames}
        session_received = True
        phase = "runtime_evaluate"
        evaluated = call(
            {"id": 2, "sessionId": session, "method": "Runtime.evaluate", "params": {"expression": "1+1", "returnByValue": True}},
            2, session,
        )
        evaluated_result = evaluated.get("result")
        remote = evaluated_result.get("result") if isinstance(evaluated_result, dict) else None
        value_is_2 = isinstance(remote, dict) and remote.get("value") == 2
        phase = "runtime_enable"
        runtime = call({"id": 3, "sessionId": session, "method": "Runtime.enable", "params": {}}, 3, session)
        phase = "network_enable"
        network = call({"id": 4, "sessionId": session, "method": "Network.enable", "params": {}}, 4, session)
        return {
            "handshake_status": status,
            "attach_flat": True,
            "session_received": True,
            "flat_runtime_evaluate": "error" not in evaluated,
            "value_is_2": value_is_2,
            "flat_runtime_enable": "error" not in runtime,
            "flat_network_enable": "error" not in network,
            "frames": frames,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic result, not application behavior
        return {
            "handshake_status": status,
            "phase": phase,
            "error": type(exc).__name__,
            "attach_flat": attached_ok,
            "session_received": session_received,
            "frames": frames,
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--without-origin", action="store_true")
    args = parser.parse_args(argv)
    app = QApplication([])
    profile = QWebEngineProfile("booruflow-cdp-probe", app)
    page = QWebEnginePage(profile, app)
    view = QWebEngineView()
    view.setPage(page)
    page.setHtml("<title>BooruFlow local CDP probe</title>")
    view.show()
    deadline = time.monotonic() + 5

    outcome: dict[str, object] = {}

    def request_in_background() -> None:
        try:
            outcome["version"] = request_json(args.port, "/json/version")
            outcome["targets"] = request_json(args.port, "/json/list")
            targets = outcome["targets"]
            page_target = next(
                target for target in targets
                if isinstance(target, dict) and str(target.get("id", "")) == str(page.devToolsId())
            )
            for name, endpoint, command in (
                ("raw_browser", outcome["version"]["webSocketDebuggerUrl"], {"id": 1, "method": "Browser.getVersion"}),
                ("raw_page", page_target["webSocketDebuggerUrl"], {"id": 1, "method": "Runtime.enable"}),
            ):
                try:
                    outcome[name] = raw_websocket_probe(str(endpoint), command)
                except Exception as exc:  # noqa: BLE001 - structural diagnostic only
                    outcome[name] = {"error": type(exc).__name__}
            try:
                outcome["raw_flat"] = raw_flat_session_probe(
                    str(outcome["version"]["webSocketDebuggerUrl"]), str(page.devToolsId())
                )
            except Exception as exc:  # noqa: BLE001 - structural diagnostic only
                outcome["raw_flat"] = {"error": type(exc).__name__}
            logs: list[str] = []
            capture = EmbeddedCdpNetworkCapture(
                EmbeddedCdpConfiguration(True, port=args.port, configured_before_qapplication=True),
                str(page.devToolsId()),
                HttpDiagnosticExpectation("manual"),
                emit_log=logs.append,
                emit_finished=lambda _result: None,
                timeout_seconds=1,
            )
            if args.without_origin:
                import websocket
                capture.websocket_factory = lambda url, **options: websocket.create_connection(
                    url, timeout=options["timeout"], suppress_origin=True
                )
            outcome["network_enable"] = capture.start(wait_seconds=8)
            outcome["network_failure"] = f"{capture.failure_phase}:{capture.failure_reason}"
            outcome["network_log"] = " | ".join(logs[-8:])
            capture.stop()
        except Exception as exc:  # noqa: BLE001 - diagnostic output only
            outcome["error"] = type(exc).__name__
        finally:
            outcome["finished"] = True

    def probe() -> None:
        listening = port_is_listening(args.port)
        print(f"QTWEBENGINE_CDP port_listening={str(listening).lower()}", flush=True)
        if not listening and time.monotonic() < deadline:
            QTimer.singleShot(150, probe)
            return
        if not listening:
            app.exit(2)
            return
        if "started" not in outcome:
            outcome["started"] = True
            threading.Thread(target=request_in_background, daemon=True).start()
            QTimer.singleShot(50, probe)
            return
        if "finished" not in outcome:
            QTimer.singleShot(50, probe)
            return
        if "error" not in outcome:
            version = outcome["version"]
            targets = outcome["targets"]
            print(
                "QTWEBENGINE_CDP "
                f"json_version={isinstance(version, dict)} json_list={isinstance(targets, list)} "
                f"target_count={len(targets) if isinstance(targets, list) else 0} "
                f"network_enable={str(outcome['network_enable']).lower()} "
                f"network_failure={outcome['network_failure']}",
                flush=True,
            )
            for name in ("raw_page", "raw_browser"):
                value = outcome[name]
                if "error" in value:
                    print(f"QTWEBENGINE_CDP {name} error={value['error']}", flush=True)
                    continue
                print(
                    "QTWEBENGINE_CDP "
                    f"{name} handshake={value['handshake_status']} "
                    f"frame_received={str(value['frame_received']).lower()} "
                    f"frame_type={value['frame_type']} frame_length={value['frame_length']} "
                    f"has_id={str(value.get('has_id', False)).lower()} "
                    f"id={value.get('id', 'none')} method={value.get('method', 'none')}",
                    flush=True,
                )
            flat = outcome["raw_flat"]
            print(
                "QTWEBENGINE_CDP raw_flat "
                + " ".join(f"{key}={str(value).lower()}" for key, value in flat.items()),
                flush=True,
            )
            if not bool(outcome["network_enable"]):
                print(f"QTWEBENGINE_CDP network_log={outcome['network_log']}", flush=True)
            app.exit(0)
            return
        print(f"QTWEBENGINE_CDP http_error={outcome['error']}", flush=True)
        app.exit(3)

    page.loadFinished.connect(lambda _ok: QTimer.singleShot(150, probe))
    QTimer.singleShot(5_000, probe)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
