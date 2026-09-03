"""Redacted structural diagnostics for Gelbooru edit POST requests.

The Qt interceptor is deliberately limited to metadata.  QtWebEngine does not
guarantee that ``requestBody()`` is readable at this boundary, so the request
body is analysed by the opt-in CDP Network diagnostic instead.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote_plus, urlparse

from PySide6.QtCore import QObject, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)

_EDIT_PATH = "/public/edit_post.php"
_SENSITIVE_FIELDS = frozenset({"csrf-token", "uid", "uname", "lupdated"})
_SAFE_FIELD_RE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True, slots=True)
class HttpDiagnosticExpectation:
    source: str
    additions: tuple[str, ...] = ()
    removals: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LowLevelEditRequestSnapshot:
    source: str
    method: str
    path: str
    content_type: str
    resource_type: str
    navigation_type: str

    def safe_log(self) -> str:
        return (
            "Gelbooru outgoing edit request (Qt metadata): "
            f"source={self.source} method={self.method or '[missing]'} "
            f"path={self.path or '[missing]'} "
            f"content_type={self.content_type or '[missing]'} "
            f"resource_type={self.resource_type or '[unknown]'} "
            f"navigation_type={self.navigation_type or '[unknown]'} "
            "post_initiated=true body=unavailable_by_design"
        )


@dataclass(frozen=True, slots=True)
class OutgoingEditRequestSnapshot:
    source: str
    method: str
    path: str
    content_type: str
    body_present: bool
    body_length: int
    body_truncated: bool
    fields: tuple[str, ...]
    tags_entries: int
    tag_count: int
    duplicate_tag_count: int
    additions_present: tuple[bool, ...]
    removals_present: tuple[bool, ...]
    plus_count: int
    percent20_count: int
    percent28_count: int
    percent29_count: int
    encoded_crlf_count: int
    literal_crlf_count: int
    underscore_count: int
    parse_status: str
    post_data_source: str = "unknown"

    def safe_log(self) -> str:
        return (
            "Gelbooru outgoing edit request (CDP): "
            f"source={self.source} method={self.method or '[missing]'} "
            f"path={self.path or '[missing]'} "
            f"content_type={self.content_type or '[missing]'} "
            f"body_present={str(self.body_present).lower()} "
            f"body_length={self.body_length} "
            f"body_truncated={str(self.body_truncated).lower()} "
            f"fields={','.join(self.fields) or '[none]'} "
            f"tags_entries={self.tags_entries} tag_count={self.tag_count} "
            f"duplicate_tag_count={self.duplicate_tag_count} "
            f"additions_present={sum(self.additions_present)}/{len(self.additions_present)} "
            f"removals_present={sum(self.removals_present)}/{len(self.removals_present)} "
            f"plus={self.plus_count} percent20={self.percent20_count} "
            f"percent28={self.percent28_count} percent29={self.percent29_count} "
            f"encoded_crlf={self.encoded_crlf_count} "
            f"literal_crlf={self.literal_crlf_count} "
            f"underscores={self.underscore_count} "
            f"parse_status={self.parse_status} "
            f"post_data_source={self.post_data_source}"
        )


def is_gelbooru_edit_request(method: object, url: object) -> bool:
    """Match only the exact HTTPS Gelbooru edit endpoint."""
    parsed = urlparse(str(url))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        str(method).upper() == "POST"
        and parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == "gelbooru.com"
        and port in {None, 443}
        and parsed.path == _EDIT_PATH
    )


def analyze_urlencoded_edit_request(
    *,
    source: str,
    method: object,
    url: object,
    content_type: object,
    body: bytes,
    additions: tuple[str, ...] = (),
    removals: tuple[str, ...] = (),
    body_truncated: bool = False,
    post_data_source: str = "unknown",
) -> OutgoingEditRequestSnapshot:
    """Reduce a raw request to non-sensitive structural and tag booleans."""
    parsed_url = urlparse(str(url))
    safe_source = source if source in {"manual", "embedded"} else "unknown"
    media_type = str(content_type).split(";", 1)[0].strip().casefold()
    raw_text = body.decode("ascii", errors="replace")
    pairs: list[tuple[str, str]] = []
    parse_status = "ok"
    if body_truncated:
        parse_status = "truncated"
    elif media_type and media_type != "application/x-www-form-urlencoded":
        parse_status = "unsupported_content_type"
    else:
        try:
            pairs = parse_qsl(raw_text, keep_blank_values=True, strict_parsing=False)
        except (UnicodeError, ValueError):
            parse_status = "invalid_urlencoded"

    fields = tuple(
        "[redacted]" if name.casefold() in _SENSITIVE_FIELDS
        else (_SAFE_FIELD_RE.sub("_", name)[:64] or "[unnamed]")
        for name, _value in pairs
    )
    tag_values = [value for name, value in pairs if name == "tags"]
    tokens: list[str] = []
    for value in tag_values:
        tokens.extend(part for part in re.split(r"\s+", value.strip()) if part)
    token_set = set(tokens)

    encoded_tag_values = []
    for component in raw_text.split("&"):
        raw_name, separator, raw_value = component.partition("=")
        if separator and unquote_plus(raw_name) == "tags":
            encoded_tag_values.append(raw_value)
    encoded_tags = "&".join(encoded_tag_values)
    encoded_tags_upper = encoded_tags.upper()
    return OutgoingEditRequestSnapshot(
        source=safe_source,
        method=str(method).upper() if str(method).upper() in {"GET", "POST"} else "",
        path=parsed_url.path if parsed_url.path == _EDIT_PATH else "/[other]",
        content_type=media_type,
        body_present=bool(body),
        body_length=len(body),
        body_truncated=body_truncated,
        fields=fields,
        tags_entries=len(tag_values),
        tag_count=len(tokens),
        duplicate_tag_count=max(0, len(tokens) - len(token_set)),
        additions_present=tuple(tag in token_set for tag in additions),
        removals_present=tuple(tag in token_set for tag in removals),
        plus_count=encoded_tags.count("+"),
        percent20_count=encoded_tags_upper.count("%20"),
        percent28_count=encoded_tags_upper.count("%28"),
        percent29_count=encoded_tags_upper.count("%29"),
        encoded_crlf_count=len(re.findall(r"%0D%0A|%0D|%0A", encoded_tags_upper)),
        literal_crlf_count=encoded_tags.count("\r") + encoded_tags.count("\n"),
        underscore_count=encoded_tags.count("_") + encoded_tags_upper.count("%5F"),
        parse_status=parse_status,
        post_data_source=(
            post_data_source if post_data_source in {"event", "fallback"} else "unknown"
        ),
    )


def _enum_name(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    text = str(value)
    return text.rsplit(".", 1)[-1] if text else ""


class GelbooruEditRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Capture metadata for one explicitly armed edit request."""

    captured = Signal(str)
    finished = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._expectation: HttpDiagnosticExpectation | None = None

    @property
    def armed_source(self) -> str | None:
        with self._lock:
            return self._expectation.source if self._expectation is not None else None

    def arm(
        self,
        source: str,
        *,
        additions: tuple[str, ...] = (),
        removals: tuple[str, ...] = (),
    ) -> None:
        safe_source = source if source in {"manual", "embedded"} else "unknown"
        with self._lock:
            self._expectation = HttpDiagnosticExpectation(
                safe_source, tuple(additions), tuple(removals)
            )

    def disarm(self, source: str | None = None) -> None:
        with self._lock:
            if source is None or (
                self._expectation is not None and self._expectation.source == source
            ):
                self._expectation = None

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        method = bytes(info.requestMethod()).decode("ascii", errors="replace")
        url = info.requestUrl().toString()
        if not is_gelbooru_edit_request(method, url):
            return
        with self._lock:
            expectation = self._expectation
            if expectation is None:
                return
            self._expectation = None
        try:
            headers = info.httpHeaders()
            content_type = ""
            for raw_name, raw_value in headers.items():
                if bytes(raw_name).lower() == b"content-type":
                    content_type = bytes(raw_value).decode("ascii", errors="replace")
                    break
            snapshot = LowLevelEditRequestSnapshot(
                source=expectation.source,
                method=str(method).upper(),
                path=urlparse(url).path,
                content_type=content_type,
                resource_type=_enum_name(info.resourceType()),
                navigation_type=_enum_name(info.navigationType()),
            )
            self.captured.emit(snapshot.safe_log())
        except Exception as exc:  # noqa: BLE001 - passive browser boundary
            self.captured.emit(
                "Gelbooru outgoing edit request: "
                f"source={expectation.source} capture_error={type(exc).__name__}"
            )
        finally:
            self.finished.emit(expectation.source)
