"""Persistent, embedded QtWebEngine session and Gelbooru edit transport.

All QWebEngine objects live on the Qt GUI thread.  Publication workers only see
``EmbeddedGelbooruSession``, a blocking proxy whose requests are delivered to
the GUI thread through a queued signal.  No cookie or token leaves WebEngine.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from booruflow.infrastructure.gelbooru_cdp_diagnostic import (
    EmbeddedCdpNetworkCapture,
    embedded_cdp_configuration,
)
from booruflow.infrastructure.gelbooru_edit_prototype import (
    EDIT_WORKFLOW_CLICK_EDIT_SCRIPT,
    EDIT_WORKFLOW_STATE_SCRIPT,
    build_apply_real_form_deltas_script,
)
from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruPublishDeferredError,
    GelbooruSessionExpiredError,
    GelbooruSessionUnknownError,
    GelbooruTransportError,
)
from booruflow.infrastructure.gelbooru_http_diagnostic import (
    GelbooruEditRequestInterceptor,
    HttpDiagnosticExpectation,
)

GELBOORU_HOME = "https://gelbooru.com/"
GELBOORU_ACCOUNT = "https://gelbooru.com/index.php?page=account&s=home"
E621_HOME = "https://e621.net/"
E621_ACCOUNT = "https://e621.net/users/home"

_SAFE_FIELD_RE = re.compile(r"[^A-Za-z0-9_-]")
_SENSITIVE_FORM_FIELDS = frozenset({"csrf-token", "uid", "uname", "lupdated"})

SESSION_DIAGNOSTIC_SCRIPT = """(() => {
    try {
        const text = (document.body && document.body.innerText || '').toLowerCase();
        const url = location.href;
        return JSON.stringify({
            url,
            readyState: document.readyState,
            bodyPresent: Boolean(document.body),
            bodyTextLength: text.trim().length,
            titlePresent: Boolean((document.title || '').trim()),
            accountHome: /[?&]page=account(?:&|$)/.test(url) && /[?&]s=home(?:&|$)/.test(url),
            accountTitle: /my account/i.test(document.title || ''),
            accountContent: Boolean(document.body) && text.trim().length > 40,
            loginForm: Boolean(document.querySelector(
                'form[action*="s=login"], form[action*="page=account"] input[type="password"], input[name="pass"]'
            )),
            loginLink: Boolean(document.querySelector('a[href*="s=login"]')),
            logoutMarker: Boolean(document.querySelector(
                'a[href*="logout"], form[action*="logout"], [name="logout"], #logout'
            )),
            loggedOutText: /you are not logged in|not logged in/.test(text),
            challengeMarker: Boolean(document.querySelector(
                '#challenge-form, .cf-challenge, [id^="cf-chl"], [class*="cf-chl"]'
            )) || /just a moment|verify you are human|checking your browser/.test(
                (document.title || '') + ' ' + text.slice(0, 2000)
            )
        });
    } catch (error) {
        return JSON.stringify({
            url: location.href,
            readyState: document.readyState,
            probeError: error && error.name || 'Error'
        });
    }
})()"""

EDIT_FORM_DIAGNOSTIC_SCRIPT = """(() => {
    try {
        const editForm = document.getElementById('edit_form');
        const forms = Array.from(document.forms).map((form, index) => {
            const isEditForm = form === editForm;
            const tags = isEditForm
                ? form.querySelector('textarea[name="tags"], input[name="tags"]')
                : null;
            const postId = isEditForm ? form.elements.namedItem('id') : null;
            const expectedId = new URL(location.href).searchParams.get('id');
            return {
                index,
                isEditForm,
                methodPost: form.method.toLowerCase() === 'post',
                action: form.getAttribute('action') || '',
                tagsField: Boolean(tags),
                tagsKind: tags ? tags.tagName.toLowerCase() : '',
                ratingField: Boolean(form.querySelector('[name="rating"]')),
                sourceField: Boolean(form.querySelector('[name="source"]')),
                titleField: Boolean(form.querySelector('[name="title"]')),
                postIdField: Boolean(postId),
                postIdMatches: Boolean(postId) && String(postId.value) === String(expectedId),
                submitControl: Boolean(form.querySelector(
                    'button[type="submit"], input[type="submit"], button:not([type])'
                ))
            };
        });
        return JSON.stringify({
            url: location.href,
            readyState: document.readyState,
            titlePresent: Boolean((document.title || '').trim()),
            bodyPresent: Boolean(document.body),
            loginForm: Boolean(document.querySelector(
                'input[name="login"], input[type="password"], form[action*="login"]'
            )),
            globalTagsFields: document.querySelectorAll('[name="tags"]').length,
            forms
        });
    } catch (error) {
        return JSON.stringify({
            url: location.href,
            readyState: document.readyState,
            probeError: error && error.name || 'Error'
        });
    }
})()"""

POST_SUBMIT_DIAGNOSTIC_SCRIPT = """(() => {
    try {
        const text = (document.body && document.body.innerText || '').toLowerCase();
        const url = location.href;
        return JSON.stringify({
            url,
            readyState: document.readyState,
            bodyPresent: Boolean(document.body),
            accountPage: /[?&]page=account(?:&|$)/.test(url),
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
        });
    } catch (error) {
        return JSON.stringify({
            url: location.href,
            readyState: document.readyState,
            probeError: error && error.name || 'Error'
        });
    }
})()"""

EMBEDDED_SAVE_CLICK_SCRIPT = r"""(() => {
    try {
        const form = document.getElementById('edit_form');
        const visible = node => Boolean(node) && getComputedStyle(node).display !== 'none'
            && getComputedStyle(node).visibility !== 'hidden' && node.getClientRects().length > 0;
        const save = form && form.querySelector(
            'input[type="submit"][name="submit"][value="Save changes"]'
        );
        if (!form || !visible(form)) return JSON.stringify({status: 'form_not_visible'});
        if (!save) return JSON.stringify({status: 'save_missing'});
        if (save.disabled) return JSON.stringify({status: 'save_disabled'});
        save.click();
        return JSON.stringify({status: 'save_clicked'});
    } catch (error) {
        return JSON.stringify({status: 'javascript_error'});
    }
})()"""


class SessionState(StrEnum):
    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SessionDiagnostic:
    state: SessionState
    url: str
    login_form: bool
    login_link: bool
    logout_marker: bool
    challenge_marker: bool
    account_home: bool
    account_content: bool
    ready_state: str
    body_present: bool
    body_text_length: int
    title_present: bool
    probe_error: str = ""

    @property
    def technically_complete(self) -> bool:
        return self.ready_state == "complete"

    @property
    def dom_usable(self) -> bool:
        return self.account_home and self.body_present and self.body_text_length > 40

    def safe_log(self) -> str:
        parsed = urlparse(self.url)
        safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        query = parse_qs(parsed.query)
        safe_query = "&".join(
            f"{key}={query[key][0]}" for key in ("page", "s", "id") if query.get(key)
        )
        if safe_query:
            safe_url += "?" + safe_query
        return (
            "Gelbooru session check: "
            f"url={safe_url} login_form={str(self.login_form).lower()} "
            f"login_link={str(self.login_link).lower()} "
            f"logout_marker={str(self.logout_marker).lower()} "
            f"challenge_marker={str(self.challenge_marker).lower()} "
            f"account_home={str(self.account_home).lower()} "
            f"account_content={str(self.account_content).lower()} "
            f"ready_state={self.ready_state or '[missing]'} "
            f"body_present={str(self.body_present).lower()} "
            f"body_text_length={self.body_text_length} "
            f"title_present={str(self.title_present).lower()} "
            f"dom_usable={str(self.dom_usable).lower()} "
            f"probe_error={self.probe_error or '[none]'} "
            f"result={self.state.value}"
        )

    def dom_log(self) -> str:
        return (
            f"ready_state={self.ready_state or '[missing]'} "
            f"body_present={str(self.body_present).lower()} "
            f"body_text_length={self.body_text_length} "
            f"title_present={str(self.title_present).lower()} "
            f"account_home={str(self.account_home).lower()} "
            f"account_content={str(self.account_content).lower()} "
            f"login_form={str(self.login_form).lower()} "
            f"login_link={str(self.login_link).lower()} "
            f"logout_marker={str(self.logout_marker).lower()} "
            f"challenge_marker={str(self.challenge_marker).lower()} "
            f"probe_error={self.probe_error or '[none]'}"
        )


def classify_session(values: object) -> SessionDiagnostic:
    if isinstance(values, str):
        try:
            decoded = json.loads(values)
        except (TypeError, ValueError):
            decoded = {}
        data = decoded if isinstance(decoded, dict) else {}
    else:
        data = values if isinstance(values, dict) else {}
    login_form = data.get("loginForm") is True
    login_link = data.get("loginLink") is True
    logout_marker = data.get("logoutMarker") is True
    logged_out = data.get("loggedOutText") is True
    challenge = data.get("challengeMarker") is True
    account_home = data.get("accountHome") is True
    account_content = data.get("accountContent") is True
    ready_state = str(data.get("readyState", ""))
    body_present = data.get("bodyPresent") is True
    try:
        body_text_length = max(0, int(data.get("bodyTextLength", 0)))
    except (TypeError, ValueError):
        body_text_length = 0
    title_present = data.get("titlePresent") is True
    probe_error = str(data.get("probeError", ""))[:80]
    dom_usable = account_home and body_present and body_text_length > 40
    if challenge:
        state = SessionState.UNKNOWN
    elif login_form or logged_out:
        state = SessionState.UNAUTHENTICATED
    elif logout_marker or (dom_usable and account_content):
        # Gelbooru's account home explicitly renders "You are not logged in" for anonymous
        # users. A usable account response without that marker is positive evidence of the
        # authenticated variant even when readyState stays interactive and no logout link shows.
        state = SessionState.AUTHENTICATED
    else:
        state = SessionState.UNKNOWN
    return SessionDiagnostic(
        state, str(data.get("url", "")), login_form, login_link, logout_marker,
        challenge, account_home, account_content, ready_state, body_present,
        body_text_length, title_present, probe_error,
    )


@dataclass(frozen=True, slots=True)
class EditFormSummary:
    index: int
    is_edit_form: bool
    method_post: bool
    action: str
    tags_field: bool
    tags_kind: str
    rating_field: bool
    source_field: bool
    title_field: bool
    post_id_field: bool
    post_id_matches: bool
    submit_control: bool
    edit_action: bool


@dataclass(frozen=True, slots=True)
class EditFormDiagnostic:
    url: str
    expected_post_id: str
    ready_state: str
    title_present: bool
    body_present: bool
    login_form: bool
    tags_field: bool
    rating_field: bool
    source_field: bool
    title_field: bool
    forms: tuple[EditFormSummary, ...]
    probe_error: str = ""

    @property
    def page_expected(self) -> bool:
        parsed = urlparse(self.url)
        query = parse_qs(parsed.query)
        return (
            parsed.scheme == "https"
            and parsed.netloc.casefold() == "gelbooru.com"
            and parsed.path.endswith("/index.php")
            and query.get("page") == ["post"]
            and query.get("s") == ["view"]
            and query.get("id") == [self.expected_post_id]
        )

    @property
    def selected_form(self) -> EditFormSummary | None:
        return next((form for form in self.forms if form.is_edit_form), None)

    @property
    def status(self) -> str:
        if self.probe_error:
            return "javascript_error"
        if self.login_form:
            return "login"
        if not self.page_expected or not self.body_present:
            return "page_not_loaded"
        if not self.forms:
            return "form_absent"
        if self.selected_form is None:
            return "edit_form_absent"
        if not self.selected_form.tags_field:
            return "tags_absent"
        if not (
            self.selected_form.method_post
            and self.selected_form.edit_action
            and self.selected_form.post_id_field
            and self.selected_form.post_id_matches
            and self.selected_form.submit_control
        ):
            return "invalid_structure"
        return "form_ready"

    def safe_log(self) -> str:
        selected = self.selected_form
        return (
            "Gelbooru edit DOM: "
            f"url={EmbeddedGelbooruBridge._safe_url(self.url)} "
            f"ready_state={self.ready_state or '[missing]'} "
            f"title_present={str(self.title_present).lower()} "
            f"body={str(self.body_present).lower()} "
            f"forms={len(self.forms)} "
            f"edit_form={str(selected is not None).lower()} "
            f"method_post={str(bool(selected and selected.method_post)).lower()} "
            f"tags_field={str(bool(selected and selected.tags_field)).lower()} "
            f"tags_kind={(selected.tags_kind if selected else '[none]')} "
            f"rating_field={str(bool(selected and selected.rating_field)).lower()} "
            f"source_field={str(bool(selected and selected.source_field)).lower()} "
            f"title_field={str(bool(selected and selected.title_field)).lower()} "
            f"post_id_field={str(bool(selected and selected.post_id_field)).lower()} "
            f"edit_action={str(bool(selected and selected.edit_action)).lower()} "
            f"submit_control={str(bool(selected and selected.submit_control)).lower()} "
            f"probe_error={self.probe_error or '[none]'} "
            f"result={self.status}"
        )


@dataclass(frozen=True, slots=True)
class FormFieldSnapshot:
    name: str
    field_type: str
    present: bool
    length: int | None = None


@dataclass(frozen=True, slots=True)
class FormSnapshot:
    source: str
    method: str
    action: str
    fields: tuple[FormFieldSnapshot, ...]
    tags_entries: int
    tag_count: int
    additions_present: tuple[bool, ...]
    removals_present: tuple[bool, ...]
    submitter_name: str
    submitter_type: str
    lupdated_present: bool
    tags_searched_present: bool
    tags_searched_kind: str
    post_id_present: bool
    url_page: str
    url_s: str
    url_post_id: str
    url_tags_present: bool
    url_tags_kind: str
    prevented: bool
    added_from_initial_count: int = 0
    removed_from_initial_count: int = 0
    probe_error: str = ""
    enctype: str = ""
    serialized_length: int = 0
    serialized_sha256: str = ""
    tags_sha256: str = ""
    field_order: tuple[str, ...] = ()
    unique_field_count: int = 0
    duplicate_field_names: tuple[str, ...] = ()
    tags_duplicate_count: int = 0
    tags_leading_space: bool = False
    tags_trailing_space: bool = False
    tags_double_space: bool = False
    encoding_markers: tuple[bool, ...] = ()
    tags_searched_count: int = 0
    tags_searched_index: int = -1
    tags_searched_length: int | None = None
    tags_searched_sha256: str = ""
    tags_dom_matches_formdata: bool = False
    tags_control_disabled: bool = False
    tags_control_readonly: bool = False
    submitter_included: bool = False
    diagnostic_only: bool = False
    post_blocked: bool = False

    @property
    def expected_shape(self) -> bool:
        return (
            self.tags_entries == 1
            and bool(self.submitter_name)
            and self.lupdated_present
            and self.post_id_present
            and all(self.additions_present)
            and not any(self.removals_present)
            and not self.probe_error
        )

    def normalized_shape(self) -> tuple[object, ...]:
        """Return the non-sensitive form contract, excluding tag intent/content."""
        return (
            self.method,
            urlparse(self.action).path,
            tuple((item.name, item.field_type) for item in self.fields),
            self.tags_entries,
            self.tags_searched_present,
            self.tags_searched_kind,
            self.lupdated_present,
            self.submitter_name,
            self.submitter_type,
            self.post_id_present,
            self.expected_shape,
        )

    def safe_log(self) -> str:
        parsed = urlparse(self.action)
        safe_action = parsed.path if parsed.path.startswith("/") else "/[invalid]"
        field_parts = []
        for item in self.fields:
            part = f"{item.name}:{item.field_type}:present={str(item.present).lower()}"
            if item.length is not None and item.name not in _SENSITIVE_FORM_FIELDS:
                part += f":length={item.length}"
            field_parts.append(part)
        return (
            f"Gelbooru {self.source} form snapshot: "
            f"method={self.method or '[missing]'} action={safe_action} "
            f"fields={','.join(field_parts) or '[none]'} "
            f"tags_entries={self.tags_entries} tag_count={self.tag_count} "
            f"additions_present={sum(self.additions_present)}/{len(self.additions_present)} "
            f"removals_present={sum(self.removals_present)}/{len(self.removals_present)} "
            f"submitter={self.submitter_name or '[missing]'}:{self.submitter_type or '[missing]'} "
            f"lupdated_present={str(self.lupdated_present).lower()} "
            f"tagsSearched_present={str(self.tags_searched_present).lower()} "
            f"tagsSearched_kind={self.tags_searched_kind} "
            f"post_id_present={str(self.post_id_present).lower()} "
            f"url_page={self.url_page or '[missing]'} "
            f"url_s={self.url_s or '[missing]'} "
            f"url_id={self.url_post_id or '[missing]'} "
            f"url_tags_present={str(self.url_tags_present).lower()} "
            f"url_tags_kind={self.url_tags_kind} "
            f"prevented={str(self.prevented).lower()} "
            f"manual_added={self.added_from_initial_count} "
            f"manual_removed={self.removed_from_initial_count} "
            f"shape_ok={str(self.expected_shape).lower()} "
            f"enctype={self.enctype or '[missing]'} "
            f"serialized_length={self.serialized_length} "
            f"serialized_sha256={self.serialized_sha256 or '[missing]'} "
            f"tags_sha256={self.tags_sha256 or '[missing]'} "
            f"field_order={','.join(self.field_order) or '[none]'} "
            f"unique_field_count={self.unique_field_count} "
            f"duplicate_field_names={','.join(self.duplicate_field_names) or '[none]'} "
            f"tags_duplicate_count={self.tags_duplicate_count} "
            f"tags_leading_space={str(self.tags_leading_space).lower()} "
            f"tags_trailing_space={str(self.tags_trailing_space).lower()} "
            f"tags_double_space={str(self.tags_double_space).lower()} "
            f"encoding_markers={','.join(str(value).lower() for value in self.encoding_markers)} "
            f"tagsSearched_count={self.tags_searched_count} "
            f"tagsSearched_index={self.tags_searched_index} "
            f"tagsSearched_length={self.tags_searched_length if self.tags_searched_length is not None else '[hidden]'} "
            f"tagsSearched_sha256={self.tags_searched_sha256 or '[missing]'} "
            f"tags_dom_matches_formdata={str(self.tags_dom_matches_formdata).lower()} "
            f"tags_control_disabled={str(self.tags_control_disabled).lower()} "
            f"tags_control_readonly={str(self.tags_control_readonly).lower()} "
            f"submitter_included={str(self.submitter_included).lower()} "
            f"diagnostic_only={str(self.diagnostic_only).lower()} "
            f"post_blocked={str(self.post_blocked).lower()} "
            f"probe_error={self.probe_error or '[none]'}"
        )


@dataclass(frozen=True, slots=True)
class FormSerializationComparison:
    same_serialized_hash: bool
    same_tags_hash: bool
    same_length: bool
    same_field_order: bool
    same_field_counts: bool
    same_duplicate_fields: bool
    same_encoding_markers: bool
    same_submitter: bool
    same_tags_searched: bool
    differences: tuple[str, ...]

    def safe_log(self) -> str:
        return (
            "Gelbooru form serialization comparison: "
            f"same_serialized_hash={str(self.same_serialized_hash).lower()} "
            f"same_tags_hash={str(self.same_tags_hash).lower()} "
            f"same_length={str(self.same_length).lower()} "
            f"same_field_order={str(self.same_field_order).lower()} "
            f"same_field_counts={str(self.same_field_counts).lower()} "
            f"same_duplicate_fields={str(self.same_duplicate_fields).lower()} "
            f"same_encoding_markers={str(self.same_encoding_markers).lower()} "
            f"same_submitter={str(self.same_submitter).lower()} "
            f"same_tagsSearched={str(self.same_tags_searched).lower()} "
            f"differences={','.join(self.differences) or '[none]'}"
        )


def _safe_form_identifier(value: object) -> str:
    return _SAFE_FIELD_RE.sub("_", str(value))[:64]


def parse_form_snapshot(values: object, source: str) -> FormSnapshot:
    """Reduce a browser snapshot to metadata that cannot contain field values."""
    if isinstance(values, str):
        try:
            decoded = json.loads(values)
        except (TypeError, ValueError):
            decoded = {}
    else:
        decoded = values if isinstance(values, dict) else {}
    data = decoded if isinstance(decoded, dict) else {}
    raw_fields = data.get("fields", ())
    fields: list[FormFieldSnapshot] = []
    if isinstance(raw_fields, list):
        for raw in raw_fields[:64]:
            if not isinstance(raw, dict):
                continue
            name = _safe_form_identifier(raw.get("name", ""))
            field_type = _safe_form_identifier(raw.get("type", "unknown"))
            if not name:
                continue
            length: int | None = None
            if name not in _SENSITIVE_FORM_FIELDS and name != "tags":
                try:
                    raw_length = raw.get("length")
                    length = None if raw_length is None else max(0, int(raw_length))
                except (TypeError, ValueError):
                    length = None
            fields.append(FormFieldSnapshot(
                name=name,
                field_type=field_type or "unknown",
                present=raw.get("present") is True,
                length=length,
            ))

    def safe_count(key: str) -> int:
        try:
            return max(0, int(data.get(key, 0)))
        except (TypeError, ValueError):
            return 0

    def bool_tuple(key: str) -> tuple[bool, ...]:
        raw = data.get(key, ())
        return tuple(value is True for value in raw[:128]) if isinstance(raw, list) else ()

    def safe_hash(key: str) -> str:
        value = str(data.get(key, "")).casefold()
        return value if re.fullmatch(r"[0-9a-f]{64}", value) else ""

    def safe_names(key: str) -> tuple[str, ...]:
        raw = data.get(key, ())
        if not isinstance(raw, list):
            return ()
        return tuple(
            name for item in raw[:64]
            if (name := _safe_form_identifier(item))
        )

    def safe_optional_length(key: str) -> int | None:
        value = data.get(key)
        if value is None:
            return None
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    method = str(data.get("method", "")).upper()
    if method not in {"GET", "POST"}:
        method = ""
    diagnostic_only = data.get("diagnosticOnly") is True
    post_blocked = data.get("postBlocked") is True
    probe_error = _safe_form_identifier(data.get("probeError", ""))[:80]
    if diagnostic_only and not post_blocked:
        probe_error = "diagnostic_not_blocked"
    return FormSnapshot(
        source="manual" if source == "manual" else "embedded",
        method=method,
        action=str(data.get("action", "")),
        fields=tuple(fields),
        tags_entries=safe_count("tagsEntries"),
        tag_count=safe_count("tagCount"),
        additions_present=bool_tuple("additionsPresent"),
        removals_present=bool_tuple("removalsPresent"),
        submitter_name=_safe_form_identifier(data.get("submitterName", "")),
        submitter_type=_safe_form_identifier(data.get("submitterType", "")),
        lupdated_present=data.get("lupdatedPresent") is True,
        tags_searched_present=data.get("tagsSearchedPresent") is True,
        tags_searched_kind=(
            str(data.get("tagsSearchedKind", "other"))
            if data.get("tagsSearchedKind") in {"empty", "post_id_query", "other"}
            else "other"
        ),
        post_id_present=data.get("postIdPresent") is True,
        url_page=_safe_form_identifier(data.get("urlPage", "")),
        url_s=_safe_form_identifier(data.get("urlS", "")),
        url_post_id=(
            str(data.get("urlPostId", ""))[:32]
            if str(data.get("urlPostId", "")).isdigit() else ""
        ),
        url_tags_present=data.get("urlTagsPresent") is True,
        url_tags_kind=(
            str(data.get("urlTagsKind", "other"))
            if data.get("urlTagsKind") in {"empty", "post_id_query", "other"}
            else "other"
        ),
        prevented=data.get("prevented") is True,
        added_from_initial_count=safe_count("addedFromInitialCount"),
        removed_from_initial_count=safe_count("removedFromInitialCount"),
        probe_error=probe_error,
        enctype=_safe_form_identifier(data.get("enctype", ""))[:64],
        serialized_length=safe_count("serializedLength"),
        serialized_sha256=safe_hash("serializedSha256"),
        tags_sha256=safe_hash("tagsSha256"),
        field_order=safe_names("fieldOrder"),
        unique_field_count=safe_count("uniqueFieldCount"),
        duplicate_field_names=safe_names("duplicateFieldNames"),
        tags_duplicate_count=safe_count("tagsDuplicateCount"),
        tags_leading_space=data.get("tagsLeadingSpace") is True,
        tags_trailing_space=data.get("tagsTrailingSpace") is True,
        tags_double_space=data.get("tagsDoubleSpace") is True,
        encoding_markers=bool_tuple("encodingMarkers"),
        tags_searched_count=safe_count("tagsSearchedCount"),
        tags_searched_index=(
            int(data.get("tagsSearchedIndex"))
            if str(data.get("tagsSearchedIndex", "")).lstrip("-").isdigit() else -1
        ),
        tags_searched_length=safe_optional_length("tagsSearchedLength"),
        tags_searched_sha256=safe_hash("tagsSearchedSha256"),
        tags_dom_matches_formdata=data.get("tagsDomMatchesFormData") is True,
        tags_control_disabled=data.get("tagsControlDisabled") is True,
        tags_control_readonly=data.get("tagsControlReadonly") is True,
        submitter_included=data.get("submitterIncluded") is True,
        diagnostic_only=diagnostic_only,
        post_blocked=post_blocked,
    )


def compare_form_serializations(
    manual: FormSnapshot, embedded: FormSnapshot,
) -> FormSerializationComparison:
    """Compare two safe FormData serializations without retaining their values."""
    same_serialized_hash = bool(manual.serialized_sha256) and (
        manual.serialized_sha256 == embedded.serialized_sha256
    )
    same_tags_hash = bool(manual.tags_sha256) and manual.tags_sha256 == embedded.tags_sha256
    same_length = manual.serialized_length == embedded.serialized_length
    same_field_order = manual.field_order == embedded.field_order
    same_field_counts = (
        len(manual.field_order) == len(embedded.field_order)
        and manual.unique_field_count == embedded.unique_field_count
    )
    same_duplicate_fields = manual.duplicate_field_names == embedded.duplicate_field_names
    same_encoding_markers = manual.encoding_markers == embedded.encoding_markers
    same_submitter = (
        manual.submitter_name, manual.submitter_type, manual.submitter_included
    ) == (
        embedded.submitter_name, embedded.submitter_type, embedded.submitter_included
    )
    same_tags_searched = (
        manual.tags_searched_count, manual.tags_searched_index,
        manual.tags_searched_length, manual.tags_searched_sha256,
    ) == (
        embedded.tags_searched_count, embedded.tags_searched_index,
        embedded.tags_searched_length, embedded.tags_searched_sha256,
    )
    differences = tuple(name for name, matches in (
        ("serialized_hash_diff", same_serialized_hash),
        ("tags_value_diff", same_tags_hash),
        ("serialized_length_diff", same_length),
        ("field_order_diff", same_field_order),
        ("field_count_diff", same_field_counts),
        ("duplicate_field_diff", same_duplicate_fields),
        ("encoding_diff", same_encoding_markers),
        ("submitter_diff", same_submitter),
        ("tagsSearched_diff", same_tags_searched),
    ) if not matches)
    return FormSerializationComparison(
        same_serialized_hash, same_tags_hash, same_length, same_field_order,
        same_field_counts, same_duplicate_fields, same_encoding_markers,
        same_submitter, same_tags_searched, differences,
    )


def parse_edit_form_diagnostic(values: object, expected_post_id: str) -> EditFormDiagnostic:
    if isinstance(values, str):
        try:
            decoded = json.loads(values)
        except (TypeError, ValueError):
            decoded = {}
    else:
        decoded = values if isinstance(values, dict) else {}
    data = decoded if isinstance(decoded, dict) else {}
    url = str(data.get("url", ""))
    forms: list[EditFormSummary] = []
    raw_forms = data.get("forms", [])
    if isinstance(raw_forms, list):
        for position, raw in enumerate(raw_forms):
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", ""))
            resolved = urlparse(urljoin(url, action))
            edit_action = (
                resolved.scheme == "https"
                and resolved.netloc.casefold() == "gelbooru.com"
                and resolved.path == "/public/edit_post.php"
            )
            try:
                index = max(0, int(raw.get("index", position)))
            except (TypeError, ValueError):
                index = position
            forms.append(EditFormSummary(
                index=index,
                is_edit_form=raw.get("isEditForm") is True,
                method_post=raw.get("methodPost") is True,
                action=action,
                tags_field=raw.get("tagsField") is True,
                tags_kind=str(raw.get("tagsKind", ""))[:20].casefold(),
                rating_field=raw.get("ratingField") is True,
                source_field=raw.get("sourceField") is True,
                title_field=raw.get("titleField") is True,
                post_id_field=raw.get("postIdField") is True,
                post_id_matches=raw.get("postIdMatches") is True,
                submit_control=raw.get("submitControl") is True,
                edit_action=edit_action,
            ))
    return EditFormDiagnostic(
        url=url,
        expected_post_id=str(expected_post_id),
        ready_state=str(data.get("readyState", "")),
        title_present=data.get("titlePresent") is True,
        body_present=data.get("bodyPresent") is True,
        login_form=data.get("loginForm") is True,
        tags_field=any(form.tags_field for form in forms if form.is_edit_form),
        rating_field=any(form.rating_field for form in forms if form.is_edit_form),
        source_field=any(form.source_field for form in forms if form.is_edit_form),
        title_field=any(form.title_field for form in forms if form.is_edit_form),
        forms=tuple(forms),
        probe_error=str(data.get("probeError", ""))[:80],
    )


@dataclass(slots=True)
class _BridgeRequest:
    operation: str
    payload: dict[str, Any]
    timeout_seconds: float
    completed: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None
    cancelled: bool = False
    request_id: int = 0


def is_expected_post_url(url: str, post_id: str) -> bool:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "gelbooru.com"
        and parsed.path == "/index.php"
        and query.get("page") == ["post"]
        and query.get("s") == ["view"]
        and query.get("id") == [str(post_id)]
    )


_FORM_SNAPSHOT_JAVASCRIPT = r"""
    const sha256 = text => {
        const bytes = new TextEncoder().encode(text);
        const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6);
        padded.set(bytes); padded[bytes.length] = 0x80;
        const bits = bytes.length * 8;
        const view = new DataView(padded.buffer);
        view.setUint32(padded.length - 8, Math.floor(bits / 0x100000000));
        view.setUint32(padded.length - 4, bits >>> 0);
        const k = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
        let h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
        const rot = (value, amount) => (value >>> amount) | (value << (32 - amount));
        for (let offset = 0; offset < padded.length; offset += 64) {
            const w = new Uint32Array(64);
            for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4);
            for (let i = 16; i < 64; i++) {
                const a = w[i - 15], b = w[i - 2];
                w[i] = (((rot(a, 7) ^ rot(a, 18) ^ (a >>> 3)) + w[i - 7]) + ((rot(b, 17) ^ rot(b, 19) ^ (b >>> 10)) + w[i - 16])) >>> 0;
            }
            let [a,b,c,d,e,f,g,x] = h;
            for (let i = 0; i < 64; i++) {
                const s1 = rot(e, 6) ^ rot(e, 11) ^ rot(e, 25);
                const choice = (e & f) ^ (~e & g);
                const temp1 = (x + s1 + choice + k[i] + w[i]) >>> 0;
                const s0 = rot(a, 2) ^ rot(a, 13) ^ rot(a, 22);
                const majority = (a & b) ^ (a & c) ^ (b & c);
                const temp2 = (s0 + majority) >>> 0;
                x = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
            }
            h = [a,b,c,d,e,f,g,x].map((value, index) => (value + h[index]) >>> 0);
        }
        return h.map(value => value.toString(16).padStart(8, '0')).join('');
    };
    const snapshotForm = (
        form, submitter, expectedAdditions, expectedRemovals, source,
        prevented, initialTags, diagnosticOnly, postBlocked
    ) => {
        const formData = new FormData(form, submitter);
        const entries = Array.from(formData.entries());
        const tagsControl = form.querySelector('textarea[name="tags"], input[name="tags"]');
        const serialized = new URLSearchParams(formData).toString();
        const serializedLower = serialized.toLowerCase();
        const occurrences = Object.create(null);
        const sensitive = new Set(['csrf-token', 'uid', 'uname', 'lupdated']);
        const fields = entries.map(([name, value]) => {
            const controls = Array.from(form.elements).filter(
                control => control.name === name && !control.disabled
            );
            const position = occurrences[name] || 0;
            occurrences[name] = position + 1;
            const control = controls[Math.min(position, Math.max(0, controls.length - 1))];
            const type = control
                ? String(control.type || control.tagName || 'unknown').toLowerCase()
                : 'unknown';
            let length = null;
            if (name !== 'tags' && !sensitive.has(name)) {
                length = typeof value === 'string' ? value.length : Number(value.size || 0);
            }
            return {name, type, present: true, length};
        });
        const tagValues = entries.filter(([name]) => name === 'tags').map(([, value]) => value);
        const tagText = tagValues.length === 1 && typeof tagValues[0] === 'string'
            ? tagValues[0]
            : '';
        const tags = new Set(tagText.trim().split(/\s+/).filter(Boolean));
        const tagTokens = tagText.trim().split(/\s+/).filter(Boolean);
        const initial = initialTags instanceof Set ? initialTags : new Set();
        const postIdValues = entries.filter(([name]) => name === 'id').map(([, value]) => value);
        const postId = postIdValues.length === 1 && typeof postIdValues[0] === 'string'
            ? postIdValues[0]
            : '';
        const searchedValues = entries.filter(
            ([name]) => name === 'tagsSearched'
        ).map(([, value]) => value);
        const searched = searchedValues.length === 1 && typeof searchedValues[0] === 'string'
            ? searchedValues[0]
            : '';
        const classifyTagsQuery = value => {
            if (!value) return 'empty';
            if (/^id:\d+$/.test(value) && (!postId || value === `id:${postId}`)) {
                return 'post_id_query';
            }
            return 'other';
        };
        const pageUrl = new URL(location.href);
        const urlTagsPresent = pageUrl.searchParams.has('tags');
        const urlTags = urlTagsPresent ? pageUrl.searchParams.get('tags') || '' : '';
        const action = new URL(form.action || form.getAttribute('action') || '', location.href);
        const fieldOrder = entries.map(([name]) => name);
        const duplicateFieldNames = Array.from(new Set(fieldOrder.filter(
            (name, index) => fieldOrder.indexOf(name) !== index
        )));
        const tagsSearchedIndex = fieldOrder.indexOf('tagsSearched');
        const submitterIncluded = Boolean(submitter && entries.some(
            ([name, value]) => name === submitter.name && value === submitter.value
        ));
        return {
            source,
            method: String(form.method || 'get').toUpperCase(),
            action: action.origin === location.origin ? action.pathname : '/[cross-origin]',
            enctype: String(form.enctype || form.getAttribute('enctype') || '').toLowerCase(),
            fields,
            fieldOrder,
            uniqueFieldCount: new Set(fieldOrder).size,
            duplicateFieldNames,
            serializedLength: serialized.length,
            serializedSha256: sha256(serialized),
            tagsEntries: tagValues.length,
            tagCount: tags.size,
            tagsDuplicateCount: Math.max(0, tagTokens.length - tags.size),
            tagsSha256: sha256(tagText),
            tagsLeadingSpace: /^\s/.test(tagText),
            tagsTrailingSpace: /\s$/.test(tagText),
            tagsDoubleSpace: /\s{2,}/.test(tagText),
            encodingMarkers: [
                serialized.includes('+'), serializedLower.includes('%20'),
                serializedLower.includes('%28'), serializedLower.includes('%29'),
                serializedLower.includes('%0d'), serializedLower.includes('%0a'),
                serialized.includes('\r'), serialized.includes('\n')
            ],
            additionsPresent: expectedAdditions.map(tag => tags.has(tag)),
            removalsPresent: expectedRemovals.map(tag => tags.has(tag)),
            submitterName: submitter && submitter.name || '',
            submitterType: submitter && String(submitter.type || submitter.tagName || '').toLowerCase() || '',
            submitterIncluded,
            lupdatedPresent: entries.some(([name]) => name === 'lupdated'),
            tagsSearchedPresent: searchedValues.length === 1,
            tagsSearchedCount: searchedValues.length,
            tagsSearchedIndex,
            tagsSearchedLength: searched.length,
            tagsSearchedSha256: sha256(searched),
            tagsSearchedKind: classifyTagsQuery(searched),
            postIdPresent: postIdValues.length === 1,
            urlPage: pageUrl.searchParams.get('page') || '',
            urlS: pageUrl.searchParams.get('s') || '',
            urlPostId: pageUrl.searchParams.get('id') || '',
            urlTagsPresent,
            urlTagsKind: urlTagsPresent ? classifyTagsQuery(urlTags) : 'empty',
            prevented: Boolean(prevented),
            tagsDomMatchesFormData: Boolean(tagsControl) && String(tagsControl.value) === tagText,
            tagsControlDisabled: Boolean(tagsControl && tagsControl.disabled),
            tagsControlReadonly: Boolean(tagsControl && tagsControl.readOnly),
            diagnosticOnly: Boolean(diagnosticOnly),
            postBlocked: Boolean(postBlocked),
            addedFromInitialCount: Array.from(tags).filter(tag => !initial.has(tag)).length,
            removedFromInitialCount: Array.from(initial).filter(tag => !tags.has(tag)).length
        };
    };
"""


def build_embedded_form_snapshot_script(
    tags: tuple[str, ...],
    *,
    additions: tuple[str, ...] = (),
    removals: tuple[str, ...] = (),
) -> str:
    """Build a non-submitting FormData snapshot after applying the proposed tags."""
    joined_tags = " ".join(tags)
    return ("""((newTags, expectedAdditions, expectedRemovals) => {
        try {
            const form = document.getElementById('edit_form');
            if (!form) return JSON.stringify({status: 'form'});
            const field = form.querySelector(
                'textarea[name="tags"], input[name="tags"]'
            );
            if (!field) return JSON.stringify({status: 'tags'});
            const submitter = form.querySelector(
                'button[type="submit"], input[type="submit"], button:not([type])'
            );
            if (!submitter) return JSON.stringify({status: 'submitter'});
            field.value = newTags;
            const values = new Set(
                field.value.trim().split(/\\s+/).filter(Boolean)
            );
            const removalsStillPresent = expectedRemovals.filter(
                tag => values.has(tag)
            );
            const additionsMissing = expectedAdditions.filter(
                tag => !values.has(tag)
            );
            if (removalsStillPresent.length || additionsMissing.length) {
                return JSON.stringify({
                    status: 'mismatch',
                    count: values.size,
                    removalsStillPresent,
                    additionsMissing
                });
            }
            __BOORUFLOW_SNAPSHOT_HELPER__
            const snapshot = snapshotForm(
                form, submitter, expectedAdditions, expectedRemovals,
                'embedded', false, null, true, true
            );
            if (snapshot.tagsEntries !== 1 || !snapshot.lupdatedPresent) {
                return JSON.stringify({status: 'formdata_mismatch', snapshot});
            }
            return JSON.stringify({
                status: 'snapshot',
                count: values.size,
                removalsStillPresent,
                additionsMissing,
                snapshot
            });
        } catch (error) {
            return JSON.stringify({
                status: 'javascript_error',
                probeError: error && error.name || 'Error'
            });
        }
    })(__BOORUFLOW_TAGS__, __BOORUFLOW_ADDITIONS__, __BOORUFLOW_REMOVALS__)""").replace(
        "__BOORUFLOW_SNAPSHOT_HELPER__", _FORM_SNAPSHOT_JAVASCRIPT
    ).replace(
        "__BOORUFLOW_TAGS__", json.dumps(joined_tags)
    ).replace(
        "__BOORUFLOW_ADDITIONS__", json.dumps(list(additions))
    ).replace(
        "__BOORUFLOW_REMOVALS__", json.dumps(list(removals))
    )


MANUAL_FORM_DIAGNOSTIC_INSTALL_SCRIPT = (r"""(() => {
    try {
        const form = document.getElementById('edit_form');
        if (!form) return JSON.stringify({status: 'form'});
        const field = form.querySelector('textarea[name="tags"], input[name="tags"]');
        if (!field) return JSON.stringify({status: 'tags'});
        if (window.__booruflowManualSubmitGuard) {
            return JSON.stringify({status: 'armed'});
        }
        __BOORUFLOW_SNAPSHOT_HELPER__
        const initialTags = new Set(field.value.trim().split(/\s+/).filter(Boolean));
        const guard = event => {
            event.preventDefault();
            const submitter = event.submitter || form.querySelector(
                'button[type="submit"], input[type="submit"], button:not([type])'
            );
            queueMicrotask(() => {
                try {
                    window.__booruflowManualSnapshot = JSON.stringify({
                        status: 'snapshot',
                        snapshot: snapshotForm(
                            form, submitter, [], [], 'manual',
                            event.defaultPrevented, initialTags, true, event.defaultPrevented
                        )
                    });
                } catch (error) {
                    window.__booruflowManualSnapshot = JSON.stringify({
                        status: 'javascript_error',
                        snapshot: {probeError: error && error.name || 'Error'}
                    });
                }
            });
        };
        window.__booruflowManualSubmitGuard = guard;
        form.addEventListener('submit', guard);
        return JSON.stringify({status: 'armed'});
    } catch (error) {
        return JSON.stringify({status: 'javascript_error'});
    }
})()""").replace("__BOORUFLOW_SNAPSHOT_HELPER__", _FORM_SNAPSHOT_JAVASCRIPT)

MANUAL_FORM_DIAGNOSTIC_TAKE_SCRIPT = r"""(() => {
    const value = window.__booruflowManualSnapshot || '';
    window.__booruflowManualSnapshot = '';
    return value;
})()"""

MANUAL_FORM_DIAGNOSTIC_DISABLE_SCRIPT = r"""(() => {
    const form = document.getElementById('edit_form');
    const guard = window.__booruflowManualSubmitGuard;
    if (form && guard) form.removeEventListener('submit', guard);
    window.__booruflowManualSubmitGuard = null;
    window.__booruflowManualSnapshot = '';
    return true;
})()"""


class EmbeddedGelbooruProfile(QObject):
    """Own one persistent WebEngine profile for the entire application run."""

    _cdp_log = Signal(str)
    _cdp_finished = Signal(str)
    http_diagnostic_finished = Signal(str)

    def __init__(self, data_root: Path, parent: QObject | None = None, *, log=None) -> None:
        super().__init__(parent)
        self.log = log or (lambda _message: None)
        self._cdp_configuration = embedded_cdp_configuration()
        self.last_http_diagnostic_error = ""
        self._cdp_captures: dict[str, EmbeddedCdpNetworkCapture] = {}
        self._form_serialization_snapshots: dict[str, FormSnapshot] = {}
        self._cdp_log.connect(self.log)
        self._cdp_finished.connect(self._finish_cdp_diagnostic)
        self.root = data_root / "BrowserProfiles" / "embedded" / "gelbooru"
        self.storage_path = self.root / "storage"
        self.cache_path = self.root / "cache"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.profile = QWebEngineProfile("booruflow-gelbooru", self)
        self.profile.setPersistentStoragePath(str(self.storage_path))
        self.profile.setCachePath(str(self.cache_path))
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.http_diagnostic = GelbooruEditRequestInterceptor(self)
        self.http_diagnostic.captured.connect(self.log)
        self.profile.setUrlRequestInterceptor(self.http_diagnostic)
        if self._cdp_configuration.enabled:
            self.log(
                "BooruFlow startup: embedded_cdp_diagnostic=true "
                f"bind={self._cdp_configuration.host} "
                f"port={self._cdp_configuration.port} "
                "configured_before_qapplication=true"
            )

    def record_form_serialization_snapshot(self, snapshot: FormSnapshot) -> None:
        """Keep only safe one-shot metadata and compare matching manual/Embedded captures."""
        if not snapshot.diagnostic_only or not snapshot.post_blocked:
            return
        self._form_serialization_snapshots[snapshot.source] = snapshot
        other_source = "embedded" if snapshot.source == "manual" else "manual"
        other = self._form_serialization_snapshots.get(other_source)
        if other is None or not snapshot.url_post_id or other.url_post_id != snapshot.url_post_id:
            return
        self.log(compare_form_serializations(
            self._form_serialization_snapshots["manual"],
            self._form_serialization_snapshots["embedded"],
        ).safe_log())

    def arm_http_diagnostic(
        self,
        source: str,
        *,
        page: QWebEnginePage,
        additions: tuple[str, ...] = (),
        removals: tuple[str, ...] = (),
    ) -> bool:
        safe_source = source if source in {"manual", "embedded"} else "unknown"
        self.last_http_diagnostic_error = ""
        if not self._cdp_configuration.enabled:
            reason = self._cdp_configuration.error or "startup_mode_disabled"
            self.last_http_diagnostic_error = reason
            self.log(
                "Gelbooru CDP Network diagnostic unavailable: "
                f"reason={reason} restart_with=--embedded-cdp-diagnostic"
            )
            return False
        target_id = str(page.devToolsId())
        if not target_id:
            self.last_http_diagnostic_error = "missing_page_target"
            self.log(
                "Gelbooru CDP Network diagnostic unavailable: reason=missing_page_target"
            )
            return False
        # One profile-level diagnostic at a time: an Embedded launch replaces
        # the visible-page arm created by the shared UI toggle.
        self.disarm_http_diagnostic()
        expectation = HttpDiagnosticExpectation(
            safe_source, tuple(additions), tuple(removals)
        )
        capture = EmbeddedCdpNetworkCapture(
            self._cdp_configuration,
            target_id,
            expectation,
            emit_log=self._cdp_log.emit,
            emit_finished=self._cdp_finished.emit,
        )
        self._cdp_captures[safe_source] = capture
        self.http_diagnostic.arm(
            safe_source, additions=tuple(additions), removals=tuple(removals)
        )
        if not capture.start():
            self._cdp_captures.pop(safe_source, None)
            self.http_diagnostic.disarm(safe_source)
            phase = capture.failure_phase or "unknown"
            reason = capture.failure_reason or "unknown"
            self.last_http_diagnostic_error = f"{phase}:{reason}"
            self.log(
                "Gelbooru CDP Network diagnostic unavailable: "
                f"source={safe_source} phase={phase} reason={reason}"
            )
            return False
        self.log(
            "Gelbooru HTTP diagnostic armed via CDP Network: "
            f"source={safe_source} additions={len(additions)} removals={len(removals)} "
            "one_shot=true"
        )
        return True

    def disarm_http_diagnostic(self, source: str | None = None) -> None:
        self.http_diagnostic.disarm(source)
        sources = tuple(self._cdp_captures) if source is None else (source,)
        for item in sources:
            capture = self._cdp_captures.pop(item, None)
            if capture is not None:
                capture.stop()

    @Slot(str)
    def _finish_cdp_diagnostic(self, payload: str) -> None:
        source, _separator, outcome = payload.partition(":")
        self._cdp_captures.pop(source, None)
        self.http_diagnostic.disarm(source)
        self.log(
            "Gelbooru CDP Network diagnostic finished: "
            f"source={source or 'unknown'} result={outcome or 'unknown'}"
        )
        if outcome != "stopped":
            self.http_diagnostic_finished.emit(source)

    def create_page(self, parent: QObject | None = None) -> QWebEnginePage:
        return QWebEnginePage(self.profile, parent)

    def reset_session(self) -> None:
        """Clear only embedded Gelbooru web session data, never other BooruFlow data."""
        self.profile.cookieStore().deleteAllCookies()
        self.profile.clearAllVisitedLinks()
        self.profile.clearHttpCache()


class EmbeddedGelbooruBridge(QObject):
    """Serialize worker requests onto one hidden WebEngine working page."""

    request_received = Signal(object)

    def __init__(self, profile: EmbeddedGelbooruProfile, parent: QObject | None = None,
                 *, default_timeout_seconds: float = 20.0,
                 pre_save_delay_seconds: float = 0.0, page=None, log=None) -> None:
        super().__init__(parent)
        self.profile = profile
        self.page = page or profile.create_page(self)
        self.default_timeout_seconds = default_timeout_seconds
        self.pre_save_delay_seconds = max(0.0, pre_save_delay_seconds)
        self.log = log or (lambda _message: None)
        self._active: _BridgeRequest | None = None
        self._phase = ""
        self._request_counter = 0
        self._navigation_started = False
        self._target_url_seen = False
        self._load_finished_seen = False
        self._dom_probe_started = False
        self._dom_probe_finished = False
        self._dom_probe_count = 0
        self._last_diagnostic: SessionDiagnostic | None = None
        self._dom_summary_logged = False
        self._submit_dispatched = False
        self._post_submit_probe_started = False
        self._edit_wait_count = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self.page.loadFinished.connect(self._load_finished)
        self.page.loadStarted.connect(self._load_started)
        self.page.urlChanged.connect(self._url_changed)
        renderer_signal = getattr(self.page, "renderProcessTerminated", None)
        if renderer_signal is not None:
            renderer_signal.connect(self._render_process_terminated)
        self.request_received.connect(self._begin_request)

    def invoke(self, operation: str, payload: dict[str, Any] | None = None,
               timeout_seconds: float | None = None) -> Any:
        if QThread.currentThread() is self.thread():
            raise RuntimeError("Embedded WebEngine requests must originate outside the GUI thread")
        request = _BridgeRequest(
            operation, dict(payload or {}),
            timeout_seconds or self.default_timeout_seconds,
        )
        self.request_received.emit(request)
        phase_count = {
            "validate": 2, "inspect_edit": 2, "snapshot_submit": 2, "submit": 4,
        }.get(operation, 1)
        wait_seconds = (request.timeout_seconds * phase_count) + 2.0
        if not request.completed.wait(wait_seconds):
            request.cancelled = True
            raise GelbooruTransportError("Le bridge QtWebEngine n'a pas répondu dans le délai imparti.")
        if request.error is not None:
            raise request.error
        return request.result

    @Slot(object)
    def _begin_request(self, request: _BridgeRequest) -> None:
        if request.cancelled:
            request.completed.set(); return
        if self._active is not None:
            request.error = GelbooruTransportError("Une opération WebEngine est déjà en cours.")
            request.completed.set(); return
        self._active = request
        self._request_counter += 1
        request.request_id = self._request_counter
        self._navigation_started = False
        self._target_url_seen = False
        self._load_finished_seen = False
        self._dom_probe_started = False
        self._dom_probe_finished = False
        self._dom_probe_count = 0
        self._last_diagnostic = None
        self._dom_summary_logged = False
        self._submit_dispatched = False
        self._post_submit_probe_started = False
        self._edit_wait_count = 0
        self._trace(
            "request_created",
            page_exists=self.page is not None,
            gui_thread=QThread.currentThread() is self.thread(),
            profile_shared=self._profile_is_shared(),
        )
        if request.operation == "validate":
            self._phase = "validate-load"
            self._restart_phase_timeout()
            self._trace("load_requested", target=self._safe_url(GELBOORU_ACCOUNT))
            self.page.load(QUrl(GELBOORU_ACCOUNT))
        elif request.operation in {"inspect_edit", "snapshot_submit", "submit"}:
            self._phase = "edit-load"
            self._restart_phase_timeout()
            post_id = str(request.payload["post_id"])
            target = f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
            if (
                request.operation == "snapshot_submit"
                and request.payload.get("diagnostic_post_id_query") is True
            ):
                target += "&" + urlencode({"tags": f"id:{post_id}"})
            self.page.load(QUrl(target))
        else:
            self._finish(error=ValueError(f"unknown WebEngine operation: {request.operation}"))

    def _restart_phase_timeout(self) -> None:
        request = self._active
        if request is not None:
            self._timer.start(max(1, int(request.timeout_seconds * 1000)))

    @Slot()
    def _load_started(self) -> None:
        if self._active is None:
            return
        self._navigation_started = True

    @Slot(QUrl)
    def _url_changed(self, url: QUrl) -> None:
        if self._active is None:
            return
        value = url.toString()
        if self._phase in {"validate-load", "edit-load"} and self._url_is_expected(value):
            self._target_url_seen = True

    def _page_url(self, *, requested: bool = False) -> str:
        getter = getattr(self.page, "requestedUrl", None) if requested else None
        value = getter() if callable(getter) else self.page.url()
        return value.toString()

    def _expected_load_url(self, *, requested: bool = True) -> bool:
        return self._url_is_expected(self._page_url(requested=requested))

    def _url_is_expected(self, url: str) -> bool:
        request = self._active
        if request is None:
            return False
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != "gelbooru.com":
            return False
        if self._phase.startswith("validate"):
            return query.get("page") == ["account"] and query.get("s") == ["home"]
        if self._phase == "edit-load":
            return (
                query.get("page") == ["post"] and query.get("s") == ["view"]
                and query.get("id") == [str(request.payload["post_id"])]
            )
        return True

    @staticmethod
    def _safe_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "[empty-or-non-http]"
        safe = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        query = parse_qs(parsed.query)
        allowed = "&".join(
            f"{key}={query[key][0]}" for key in ("page", "s", "id") if query.get(key)
        )
        return safe + ("?" + allowed if allowed else "")

    def _profile_is_shared(self) -> bool:
        page_profile = getattr(self.page, "profile", None)
        owner_profile = getattr(self.profile, "profile", None)
        return callable(page_profile) and owner_profile is not None and page_profile() is owner_profile

    def _trace(self, event: str, **fields: object) -> None:
        request = self._active
        if request is None or request.operation != "validate":
            return
        details = " ".join(
            f"{key}={str(value).lower() if isinstance(value, bool) else value}"
            for key, value in fields.items()
        )
        suffix = f" {details}" if details else ""
        self.log(f"Gelbooru WebEngine check [{request.request_id}]: {event}{suffix}")

    @Slot(bool)
    def _load_finished(self, ok: bool) -> None:
        request = self._active
        if request is None or request.cancelled:
            return
        current_url = self._page_url(requested=False)
        self._trace(
            "load_finished", success=ok, url=self._safe_url(current_url),
            load_started=self._navigation_started,
        )
        if self._phase in {"validate-load", "edit-load"} and not self._url_is_expected(current_url):
            # QWebEngine can deliver about:blank's residual loadFinished after load().
            # It belongs to no current navigation and must not trigger DOM inspection.
            if self._phase == "edit-load" and current_url.startswith(("http://", "https://")):
                if "login" in current_url.casefold():
                    self._finish(error=GelbooruSessionExpiredError(
                        "Session Gelbooru expirée : page de connexion reçue à la place de l'édition."
                    ))
                else:
                    self._finish(error=GelbooruTransportError(
                        "Page d'édition du post non chargée : " + self._safe_url(current_url)
                    ))
            return
        self._target_url_seen = True
        self._load_finished_seen = True
        if not ok:
            self._finish(error=GelbooruTransportError("Erreur de chargement Gelbooru."))
            return
        if self._phase == "validate-load":
            self._phase = "validate-dom"
            self._restart_phase_timeout()
            self._inspect_validation(request.request_id)
        elif self._phase == "edit-load" and request.operation == "submit":
            self._phase = "edit-click"
            self._restart_phase_timeout()
            self.page.runJavaScript(
                EDIT_WORKFLOW_CLICK_EDIT_SCRIPT,
                lambda result: self._edit_clicked(result, request.request_id),
            )
        elif self._phase == "edit-load":
            self._phase = "edit-dom"
            self._restart_phase_timeout()
            self.page.runJavaScript(
                EDIT_FORM_DIAGNOSTIC_SCRIPT,
                lambda result, request_id=request.request_id: self._inspected_edit(
                    result, request_id
                ),
            )
        elif self._phase == "submit-navigation":
            self._inspect_post_submit(request.request_id)

    def _edit_clicked(self, result: object, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id or self._phase != "edit-click":
            return
        if self._decoded_mapping(result).get("status") not in {"edit_clicked", "already_visible"}:
            self._finish(error=GelbooruTransportError(
                "Impossible d'ouvrir le vrai formulaire Edit Gelbooru."
            ))
            return
        self._phase = "edit-wait"
        self._restart_phase_timeout()
        self._inspect_visible_edit(request_id)

    def _inspect_visible_edit(self, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id or self._phase != "edit-wait":
            return
        self._edit_wait_count += 1
        self.page.runJavaScript(
            EDIT_WORKFLOW_STATE_SCRIPT,
            lambda result: self._visible_edit_inspected(result, request_id),
        )

    def _visible_edit_inspected(self, result: object, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id or self._phase != "edit-wait":
            return
        values = self._decoded_mapping(result)
        ready = all(values.get(key) is True for key in (
            "editFormVisible", "tagsFieldPresent", "savePresent", "postIdMatches",
        ))
        writable = values.get("tagsFieldDisabled") is not True and (
            values.get("tagsFieldReadonly") is not True
        ) and values.get("saveDisabled") is not True
        if ready and writable:
            self._execute_submit(request_id)
        elif self._edit_wait_count < 20:
            QTimer.singleShot(100, lambda: self._inspect_visible_edit(request_id))
        else:
            self._finish(error=GelbooruTransportError(
                "Le vrai formulaire Edit Gelbooru n'est pas devenu visible et modifiable."
            ))
    def _inspect_validation(self, request_id: int) -> None:
        request = self._active
        if (
            request is None or request.request_id != request_id
            or self._phase != "validate-dom" or not self._expected_load_url(requested=False)
        ):
            return
        self._dom_probe_started = True
        self._dom_probe_count += 1
        self.page.runJavaScript(
            SESSION_DIAGNOSTIC_SCRIPT,
            lambda result: self._validated(result, request_id),
        )

    def _validated(self, result: object, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id or self._phase != "validate-dom":
            return
        self._dom_probe_finished = True
        diagnostic = classify_session(result)
        self._last_diagnostic = diagnostic
        if not diagnostic.dom_usable:
            if self._dom_probe_count < 5:
                QTimer.singleShot(250, lambda: self._inspect_validation(request_id))
            else:
                self._timed_out()
            return
        self._log_dom_summary(diagnostic)
        self.log(
            f"Gelbooru session check [{request_id}]: result={diagnostic.state.value}"
        )
        if diagnostic.state is SessionState.AUTHENTICATED:
            self._finish(result=True)
        elif diagnostic.state is SessionState.UNAUTHENTICATED:
            self._finish(error=GelbooruSessionExpiredError("Session Gelbooru non connectée."))
        else:
            self._finish(error=GelbooruSessionUnknownError(
                "État de session Gelbooru indéterminé. Ouvrez la session Gelbooru et "
                "vérifiez que la page est chargée et connectée."
            ))

    def _log_dom_summary(self, diagnostic: SessionDiagnostic) -> None:
        if self._dom_summary_logged:
            return
        request = self._active
        if request is None:
            return
        self._dom_summary_logged = True
        self.log(f"Gelbooru session DOM [{request.request_id}]: {diagnostic.dom_log()}")

    def _inspected_edit(self, result: object, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id or self._phase != "edit-dom":
            return
        post_id = str(request.payload["post_id"])
        diagnostic = parse_edit_form_diagnostic(result, post_id)
        self.log(diagnostic.safe_log())
        if diagnostic.status == "login":
            self._finish(error=GelbooruSessionExpiredError(
                "Session Gelbooru expirée : formulaire de connexion détecté."
            ))
        elif diagnostic.status == "page_not_loaded":
            self._finish(error=GelbooruTransportError(
                f"Page d'édition du post #{post_id} non chargée ou URL inattendue."
            ))
        elif diagnostic.status == "form_absent":
            self._finish(error=GelbooruTransportError(
                "Page d'édition chargée, mais aucun formulaire n'est présent."
            ))
        elif diagnostic.status == "edit_form_absent":
            self._finish(error=GelbooruTransportError(
                "Page du post chargée, mais formulaire #edit_form absent."
            ))
        elif diagnostic.status == "tags_absent":
            self._finish(error=GelbooruTransportError(
                "Formulaire Gelbooru présent, mais champ [name=tags] absent."
            ))
        elif diagnostic.status == "invalid_structure":
            self._finish(error=GelbooruTransportError(
                "#edit_form trouvé, mais méthode, action, post id ou contrôle submit invalide."
            ))
        elif diagnostic.status == "javascript_error":
            self._finish(error=GelbooruTransportError(
                f"Sonde JavaScript du formulaire en erreur ({diagnostic.probe_error})."
            ))
        elif request.operation == "inspect_edit":
            self._finish(result=diagnostic)
        elif request.operation == "snapshot_submit":
            self._execute_snapshot(diagnostic, request_id)
        else:
            self._execute_submit(request_id)

    def _execute_snapshot(self, diagnostic: EditFormDiagnostic, request_id: int) -> None:
        request = self._active
        if (
            request is None or request.request_id != request_id
            or diagnostic.selected_form is None
        ):
            return
        self._phase = "formdata-snapshot"
        self._restart_phase_timeout()
        self.page.runJavaScript(
            build_embedded_form_snapshot_script(
                tuple(request.payload["tags"]),
                additions=tuple(request.payload.get("additions", ())),
                removals=tuple(request.payload.get("removals", ())),
            ),
            lambda result: self._snapshot_finished(result, request_id),
        )

    def _snapshot_finished(self, result: object, request_id: int) -> None:
        request = self._active
        if (
            request is None or request.request_id != request_id
            or self._phase != "formdata-snapshot"
        ):
            return
        values = self._decoded_mapping(result)
        snapshot = parse_form_snapshot(values.get("snapshot"), "embedded")
        self.log(snapshot.safe_log())
        record = getattr(self.profile, "record_form_serialization_snapshot", None)
        if callable(record):
            record(snapshot)
        status = values.get("status")
        if status == "snapshot" and snapshot.expected_shape:
            self._finish(result=snapshot)
        elif status == "javascript_error":
            self._finish(error=GelbooruTransportError(
                "Erreur JavaScript pendant le snapshot FormData Embedded."
            ))
        else:
            self._finish(error=GelbooruTransportError(
                "Snapshot FormData Embedded invalide : "
                f"status={str(status)[:40]} tags_entries={snapshot.tags_entries} "
                f"lupdated_present={str(snapshot.lupdated_present).lower()}."
            ))

    def _execute_submit(self, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id:
            return
        if self._submit_dispatched:
            self.log(
                f"Publish Gelbooru #{request.payload['post_id']}: "
                "duplicate submit prevented"
            )
            return
        additions = tuple(request.payload.get("additions", ()))
        removals = tuple(request.payload.get("removals", ()))
        post_id = str(request.payload["post_id"])
        self.log(
            f"Publish Gelbooru #{post_id}: visible edit form ready"
        )
        self._submit_dispatched = True
        self._navigation_started = False
        self._load_finished_seen = False
        self._phase = "submit-prepare"
        self._restart_phase_timeout()
        if request.payload.get("http_diagnostic") is True:
            armed = self.profile.arm_http_diagnostic(
                "embedded", page=self.page, additions=additions, removals=removals
            )
            if not armed:
                self._finish(error=GelbooruTransportError(
                    "Diagnostic CDP Embedded indisponible : relancez BooruFlow avec "
                    "--embedded-cdp-diagnostic. Aucun POST n'a été envoyé."
                ))
                return
        self.page.runJavaScript(
            build_apply_real_form_deltas_script(additions, removals),
            lambda result: self._submitted(result, request_id),
        )

    def _submitted(self, result: object, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id or self._phase != "submit-prepare":
            return
        values = self._decoded_mapping(result)
        status = values.get("status")
        self.log(
            f"Publish Gelbooru #{request.payload['post_id']}: delta_check "
            f"tag_count={int(values.get('tagCount', 0) or 0)} "
            f"additions_present={str(values.get('additionsPresent') is True).lower()} "
            f"removals_absent={str(values.get('removalsAbsent') is True).lower()} "
            f"unrelated_preserved={str(values.get('unrelatedPreserved') is True).lower()}"
        )
        if status == "tags_missing":
            self._finish(error=GelbooruTransportError(
                "Le champ tags a disparu avant la soumission."
            ))
        elif status == "not_writable":
            self._finish(error=GelbooruTransportError(
                "Le formulaire Edit n'est plus modifiable avant Save."
            ))
        elif status == "invariant_failed":
            self._finish(error=GelbooruTransportError(
                "publish_payload_mismatch: les invariants du delta ciblé ont échoué."
            ))
        elif status == "javascript_error":
            self._finish(error=GelbooruTransportError(
                "Erreur JavaScript avant soumission ("
                f"{str(values.get('probeError', 'Error'))[:80]})."
            ))
        elif status != "prepared":
            self._finish(error=GelbooruTransportError(
                "Le formulaire d'édition a disparu pendant la préparation du delta."
            ))
        else:
            self._phase = "submit-rate-limit"
            self._restart_phase_timeout()
            delay_ms = int(self.pre_save_delay_seconds * 1000)
            self.log(f"Publish Gelbooru #{request.payload['post_id']}: rate limit before Save delay_ms={delay_ms}")
            if delay_ms:
                QTimer.singleShot(delay_ms, lambda: self._click_save(request_id))
            else:
                self._click_save(request_id)

    def _click_save(self, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id or self._phase != "submit-rate-limit":
            return
        self._navigation_started = False
        self._load_finished_seen = False
        self._phase = "submit-click"
        self._restart_phase_timeout()
        self.page.runJavaScript(EMBEDDED_SAVE_CLICK_SCRIPT, lambda result: self._save_clicked(result, request_id))

    def _save_clicked(self, result: object, request_id: int) -> None:
        request = self._active
        if request is None or request.request_id != request_id or self._phase != "submit-click":
            return
        if self._decoded_mapping(result).get("status") != "save_clicked":
            self._finish(error=GelbooruTransportError(
                "Le vrai bouton Save changes n'a pas pu être cliqué."
            ))
            return
        self.log(f"Publish Gelbooru #{request.payload['post_id']}: Save clicked")
        self._phase = "submit-navigation"
        self._restart_phase_timeout()
        if self._navigation_started and self._load_finished_seen:
            self._inspect_post_submit(request_id)

    @staticmethod
    def _decoded_mapping(result: object) -> dict[str, Any]:
        if isinstance(result, str):
            try:
                decoded = json.loads(result)
            except (TypeError, ValueError):
                decoded = {}
            return decoded if isinstance(decoded, dict) else {}
        return result if isinstance(result, dict) else {}

    def _inspect_post_submit(self, request_id: int) -> None:
        request = self._active
        if (
            request is None or request.request_id != request_id
            or self._phase != "submit-navigation" or self._post_submit_probe_started
        ):
            return
        self._post_submit_probe_started = True
        self._phase = "submit-confirm"
        final_url = self._page_url(requested=False)
        query = parse_qs(urlparse(final_url).query)
        if query.get("page") == ["post"] and query.get("s") == ["list"]:
            self._finish(error=GelbooruTransportError(
                "Gelbooru a redirigé Save vers la liste globale au lieu du post attendu."
            ))
            return
        self.page.runJavaScript(
            POST_SUBMIT_DIAGNOSTIC_SCRIPT,
            lambda result: self._confirmed_submit(result, request_id, final_url),
        )

    def _confirmed_submit(self, result: object, request_id: int, final_url: str) -> None:
        request = self._active
        if (
            request is None or request.request_id != request_id
            or self._phase != "submit-confirm"
        ):
            return
        if isinstance(result, str):
            try:
                decoded = json.loads(result)
            except (TypeError, ValueError):
                decoded = {}
            values = decoded if isinstance(decoded, dict) else {}
        else:
            values = result if isinstance(result, dict) else {}
        observed_url = str(values.get("url", "")) or final_url
        post_id = str(request.payload["post_id"])
        login = (
            values.get("loginForm") is True
            or values.get("loggedOutText") is True
            or "login" in observed_url.casefold()
        )
        challenge = values.get("challengeMarker") is True
        account_unknown = (
            values.get("accountPage") is True
            and values.get("logoutMarker") is not True
            and not login
        )
        if login:
            self._finish(error=GelbooruSessionExpiredError(
                "Session Gelbooru expirée après submit : reconnectez-vous dans "
                "le navigateur intégré."
            ))
        elif challenge or account_unknown:
            self._finish(error=GelbooruSessionUnknownError(
                "État de session Gelbooru indéterminé après submit (challenge ou "
                "page compte sans preuve d'authentification)."
            ))
        elif values.get("probeError"):
            self._finish(error=GelbooruTransportError(
                "Impossible de confirmer la page Gelbooru après submit (sonde DOM en erreur)."
            ))
        elif not is_expected_post_url(observed_url, post_id):
            self._finish(error=GelbooruTransportError(
                f"Gelbooru n'a pas confirmé le post attendu #{post_id}."
            ))
        else:
            self.log(
                f"Publish Gelbooru #{post_id}: navigation finished "
                f"url={self._safe_url(observed_url)}"
            )
            self._finish(result=observed_url)

    @Slot()
    def _timed_out(self) -> None:
        self.page.triggerAction(QWebEnginePage.WebAction.Stop)
        if self._last_diagnostic is not None:
            self._log_dom_summary(self._last_diagnostic)
        if self._phase == "edit-load":
            detail = "chargement de la page du post sans réponse finale"
        elif self._phase == "edit-dom":
            detail = "préparation JavaScript du formulaire sans réponse"
        elif self._phase in {"edit-click", "edit-wait"}:
            detail = "ouverture du vrai formulaire Edit sans confirmation"
        elif self._phase in {"submit-prepare", "submit-rate-limit", "submit-click"}:
            detail = "préparation ou clic du vrai bouton Save sans confirmation"
        elif self._phase == "formdata-snapshot":
            detail = "snapshot FormData sans réponse JavaScript"
        elif self._phase in {"submit-navigation", "submit-confirm"}:
            detail = "soumission sans navigation finale confirmée"
        elif self._phase == "validate-dom":
            detail = (
                "page chargée mais DOM non prêt"
                if self._dom_probe_finished else "page chargée mais sonde DOM sans réponse"
            )
        elif not self._navigation_started and not self._target_url_seen:
            detail = "aucun loadStarted ni urlChanged cible reçu"
        elif not self._load_finished_seen:
            detail = "navigation démarrée mais aucun loadFinished cible reçu"
        else:
            detail = f"phase inattendue {self._phase}"
        self._trace("timeout", phase=self._phase, detail=detail)
        self._finish(error=GelbooruTransportError(f"Timeout QtWebEngine : {detail}."))

    @Slot(object, int)
    def _render_process_terminated(self, status, exit_code: int) -> None:
        self._trace(
            "render_process_terminated", status=str(status), exit_code=exit_code,
        )

    @Slot()
    def cancel(self) -> None:
        if self._active is not None:
            if self._submit_dispatched:
                self.log(
                    "Publication Embedded : annulation reçue après submit ; "
                    "attente de la confirmation finale."
                )
                return
            self._active.cancelled = True
            self.page.triggerAction(QWebEnginePage.WebAction.Stop)
            self._finish(error=GelbooruTransportError("Publication WebEngine annulée."))

    def _finish(self, result: Any = None, error: BaseException | None = None) -> None:
        request = self._active
        if request is None:
            return
        self._timer.stop()
        self._active = None
        self._phase = ""
        self._navigation_started = False
        self._target_url_seen = False
        self._load_finished_seen = False
        self._dom_probe_started = False
        self._dom_probe_finished = False
        self._dom_probe_count = 0
        self._last_diagnostic = None
        self._dom_summary_logged = False
        self._submit_dispatched = False
        self._post_submit_probe_started = False
        self._edit_wait_count = 0
        request.result = result
        request.error = error
        request.completed.set()


class EmbeddedGelbooruSession:
    """Thread-safe worker-side handle; contains no cookie material."""

    def __init__(self, bridge: EmbeddedGelbooruBridge) -> None:
        self.bridge = bridge

    def validate_authenticated(self) -> None:
        self.bridge.invoke("validate")

    def submit(
        self,
        post_id: str,
        tags: tuple[str, ...],
        *,
        additions: tuple[str, ...] = (),
        removals: tuple[str, ...] = (),
        fresh_tags: tuple[str, ...] = (),
        http_diagnostic: bool = False,
    ) -> None:
        self.bridge.invoke("submit", {
            "post_id": str(post_id),
            "tags": tuple(tags),
            "additions": tuple(additions),
            "removals": tuple(removals),
            "fresh_tags": tuple(fresh_tags),
            "http_diagnostic": bool(http_diagnostic),
        })

    def inspect_edit_form(self, post_id: str) -> EditFormDiagnostic:
        return self.bridge.invoke("inspect_edit", {"post_id": str(post_id)})

    def snapshot_submit(
        self,
        post_id: str,
        tags: tuple[str, ...],
        *,
        additions: tuple[str, ...] = (),
        removals: tuple[str, ...] = (),
        diagnostic_post_id_query: bool = False,
    ) -> FormSnapshot:
        return self.bridge.invoke("snapshot_submit", {
            "post_id": str(post_id),
            "tags": tuple(tags),
            "additions": tuple(additions),
            "removals": tuple(removals),
            "diagnostic_post_id_query": bool(diagnostic_post_id_query),
        })


class EmbeddedGelbooruSessionFactory:
    def __init__(self, bridge: EmbeddedGelbooruBridge) -> None:
        self.bridge = bridge

    def create(self) -> EmbeddedGelbooruSession:
        return EmbeddedGelbooruSession(self.bridge)

    def validate(self) -> None:
        self.create().validate_authenticated()


class EmbeddedGelbooruEditTransport:
    """Publisher transport backed by the shared QtWebEngine working page."""

    def __init__(
        self, *, diagnostic_only: bool = False, http_diagnostic: bool = False
    ) -> None:
        self.diagnostic_only = diagnostic_only
        self.http_diagnostic = http_diagnostic

    def submit(self, session: EmbeddedGelbooruSession, post_id: str,
               tags: tuple[str, ...]) -> None:
        if self.diagnostic_only:
            session.snapshot_submit(
                post_id, tags, diagnostic_post_id_query=True
            )
            raise GelbooruPublishDeferredError(
                "Snapshot FormData terminé ; submit Embedded désactivé."
            )
        http_diagnostic = self.http_diagnostic
        self.http_diagnostic = False
        session.submit(post_id, tags, http_diagnostic=http_diagnostic)

    def submit_prepared(self, session: EmbeddedGelbooruSession, prepared) -> None:
        if self.diagnostic_only:
            session.snapshot_submit(
                prepared.post_id,
                prepared.publish_tags,
                additions=prepared.additions,
                removals=prepared.removals,
                diagnostic_post_id_query=True,
            )
            raise GelbooruPublishDeferredError(
                "Snapshot FormData terminé ; submit Embedded désactivé."
            )
        http_diagnostic = self.http_diagnostic
        self.http_diagnostic = False
        session.submit(
            prepared.post_id,
            prepared.publish_tags,
            additions=prepared.additions,
            removals=prepared.removals,
            fresh_tags=prepared.fresh_tags,
            http_diagnostic=http_diagnostic,
        )


class LazyGelbooruEditTransport:
    """Create and validate the selected browser session only for the first real edit."""

    def __init__(self, factory, transport) -> None:
        self.factory = factory
        self.transport = transport
        self.session = None

    def submit(self, _unused_session, post_id: str, tags: tuple[str, ...]) -> None:
        if self.session is None:
            self.session = self.factory.create()
            self.session.validate_authenticated()
        self.transport.submit(self.session, post_id, tags)

    def submit_prepared(self, _unused_session, prepared) -> None:
        if self.session is None:
            self.session = self.factory.create()
            self.session.validate_authenticated()
        submit_prepared = getattr(self.transport, "submit_prepared", None)
        if callable(submit_prepared):
            submit_prepared(self.session, prepared)
        else:
            self.transport.submit(self.session, prepared.post_id, prepared.publish_tags)


class GelbooruEditPrototypeDialog(QDialog):
    """Visible real-Edit experiment; Save remains a separate user action."""

    def __init__(self, profile: EmbeddedGelbooruProfile, catalog, parent=None, *, log=None) -> None:
        super().__init__(parent)
        self.profile = profile; self.catalog = catalog; self.log = log or (lambda _message: None)
        self.setWindowTitle(catalog.text("browser.prototype.title"))
        self.resize(1000, 720)
        self.view = QWebEngineView(self); self.view.setPage(profile.create_page(self.view))
        self.post_id = QLineEdit("14795705"); self.additions = QLineEdit(); self.removals = QLineEdit()
        self.status = QLabel(catalog.text("browser.prototype.ready"))
        self.open_button = QPushButton(catalog.text("browser.prototype.open"))
        self.apply_button = QPushButton(catalog.text("browser.prototype.apply"))
        self.save_button = QPushButton(catalog.text("browser.prototype.save"))
        self.apply_button.setEnabled(False); self.save_button.setEnabled(False)
        self.open_button.clicked.connect(self._open); self.apply_button.clicked.connect(self._apply)
        self.save_button.clicked.connect(self._save_test); self.view.loadFinished.connect(self._loaded)
        form = QHBoxLayout(); form.addWidget(QLabel(catalog.text("browser.prototype.post"))); form.addWidget(self.post_id)
        form.addWidget(QLabel(catalog.text("browser.prototype.additions"))); form.addWidget(self.additions)
        form.addWidget(QLabel(catalog.text("browser.prototype.removals"))); form.addWidget(self.removals)
        controls = QHBoxLayout(); controls.addWidget(self.open_button); controls.addWidget(self.apply_button)
        controls.addWidget(self.save_button); controls.addStretch(1); controls.addWidget(self.status)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(self.view, 1); layout.addLayout(controls)
        self._waiting = 0; self._opening = False

    @staticmethod
    def _tags(value: str) -> tuple[str, ...]:
        from booruflow.application.tagging import normalize_booru_tag
        return tuple(dict.fromkeys(normalize_booru_tag(tag) for tag in value.split() if tag.strip()))

    def _open(self) -> None:
        post_id = self.post_id.text().strip()
        if not post_id.isdigit():
            self.status.setText(self.catalog.text("browser.prototype.invalid_id")); return
        self.apply_button.setEnabled(False); self.save_button.setEnabled(False)
        self._opening = True; self._waiting = 0
        self.status.setText(self.catalog.text("browser.prototype.loading"))
        self.view.load(QUrl(f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"))

    def _loaded(self, ok: bool) -> None:
        if not self._opening: return
        if not ok:
            self.status.setText(self.catalog.text("browser.prototype.load_error")); self._opening = False; return
        self.view.page().runJavaScript(EDIT_WORKFLOW_CLICK_EDIT_SCRIPT, self._edit_clicked)

    def _edit_clicked(self, result: object) -> None:
        values = EmbeddedGelbooruBridge._decoded_mapping(result)
        if values.get("status") not in {"edit_clicked", "already_visible"}:
            self.status.setText(self.catalog.text("browser.prototype.edit_missing")); self._opening = False; return
        self._poll_visible()

    def _poll_visible(self) -> None:
        self.view.page().runJavaScript(EDIT_WORKFLOW_STATE_SCRIPT, self._state_received)

    def _state_received(self, result: object) -> None:
        values = EmbeddedGelbooruBridge._decoded_mapping(result)
        if values.get("editFormVisible") is not True and self._waiting < 20:
            self._waiting += 1; QTimer.singleShot(150, self._poll_visible); return
        self._opening = False
        ready = all(values.get(key) is True for key in ("editFormVisible", "tagsFieldPresent", "savePresent", "postIdMatches"))
        writable = ready and values.get("tagsFieldDisabled") is False and values.get("tagsFieldReadonly") is False and values.get("saveDisabled") is False
        self.apply_button.setEnabled(writable)
        self.status.setText(self.catalog.text("browser.prototype.edit_ready" if writable else "browser.prototype.edit_not_ready"))
        self.log("Gelbooru real Edit prototype: " + " ".join(
            f"{key}={str(values.get(key) is True).lower()}" for key in (
                "editFormVisible", "tagsFieldPresent", "savePresent", "postIdMatches"
            )
        ))

    def _apply(self) -> None:
        self.view.page().runJavaScript(build_apply_real_form_deltas_script(
            self._tags(self.additions.text()), self._tags(self.removals.text()),
        ), self._applied)

    def _applied(self, result: object) -> None:
        values = EmbeddedGelbooruBridge._decoded_mapping(result)
        ready = values.get("status") == "prepared" and all(values.get(key) is True for key in (
            "additionsPresent", "removalsAbsent", "unrelatedPreserved"
        )) and values.get("saveDisabled") is False
        self.save_button.setEnabled(ready)
        self.status.setText(self.catalog.text("browser.prototype.prepared" if ready else "browser.prototype.refused"))

    def _save_test(self) -> None:
        if QMessageBox.question(self, self.catalog.text("browser.prototype.confirm_title"), self.catalog.text("browser.prototype.confirm")) != QMessageBox.StandardButton.Yes:
            return
        self.view.page().runJavaScript(r"""(() => {
            const save = document.querySelector('#edit_form input[type="submit"][name="submit"][value="Save changes"]');
            if (save && !save.disabled) save.click();
        })()""")


class GelbooruSessionDialog(QDialog):
    """Visible tabbed booru browser sharing authentication, never publisher pages."""

    http_diagnostic_state_changed = Signal(bool)

    def __init__(self, profile: EmbeddedGelbooruProfile, catalog, parent=None, *, log=None) -> None:
        super().__init__(parent)
        self.profile = profile
        self.catalog = catalog
        self.log = log or (lambda _message: None)
        self.setWindowTitle(self.catalog.text("browser.title"))
        self.resize(1000, 720)
        self.tabs = QTabWidget(self); self.tabs.setTabsClosable(True); self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.view: QWebEngineView | None = None
        self.address = QLineEdit(); self.address.returnPressed.connect(self._navigate_address)
        self.status = QLabel()
        self.manual_diagnostic = QCheckBox()
        self.http_additions = QLineEdit()
        self.http_removals = QLineEdit()
        self.http_diagnostic = QCheckBox()
        self.prototype_button = QPushButton()
        self.back_button = QPushButton("←"); self.forward_button = QPushButton("→")
        self.reload_button = QPushButton(); self.home_button = QPushButton()
        self.account_button = QPushButton(); self.new_tab_button = QPushButton("+")
        self.close_button = QPushButton()
        self.back_button.clicked.connect(lambda: self.view and self.view.back())
        self.forward_button.clicked.connect(lambda: self.view and self.view.forward())
        self.reload_button.clicked.connect(lambda: self.view and self.view.reload())
        self.home_button.clicked.connect(lambda: self.open_url(self._site_urls()[0]))
        self.account_button.clicked.connect(lambda: self.open_url(self._site_urls()[1]))
        self.new_tab_button.clicked.connect(lambda: self.new_tab(self._site_urls()[0]))
        self.close_button.clicked.connect(self.close)
        self.manual_diagnostic.toggled.connect(self._toggle_manual_diagnostic)
        self.http_diagnostic.toggled.connect(self._toggle_manual_http_diagnostic)
        self.prototype_button.clicked.connect(self._open_real_edit_prototype)
        self.profile.http_diagnostic_finished.connect(self._http_diagnostic_finished)
        self._manual_probe_running = False
        self._pending_http_expectation: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._manual_probe_timer = QTimer(self)
        self._manual_probe_timer.setInterval(250)
        self._manual_probe_timer.timeout.connect(self._poll_manual_snapshot)
        controls = QHBoxLayout()
        for widget in (self.back_button, self.forward_button, self.reload_button, self.home_button, self.account_button, self.address, self.new_tab_button): controls.addWidget(widget)
        layout = QVBoxLayout(self); layout.addLayout(controls); layout.addWidget(self.tabs, 1)
        http_controls = QHBoxLayout()
        self.http_targets_label = QLabel(); http_controls.addWidget(self.http_targets_label)
        http_controls.addWidget(self.http_additions)
        http_controls.addWidget(self.http_removals)
        http_controls.addWidget(self.http_diagnostic)
        self.diagnostics_group = QGroupBox()
        self.diagnostics_group.setCheckable(True); self.diagnostics_group.setChecked(False)
        diagnostics_layout = QVBoxLayout(self.diagnostics_group); diagnostics_layout.addLayout(http_controls)
        diagnostic_actions = QHBoxLayout(); diagnostic_actions.addStretch(1)
        diagnostic_actions.addWidget(self.manual_diagnostic); diagnostic_actions.addWidget(self.prototype_button)
        diagnostics_layout.addLayout(diagnostic_actions); layout.addWidget(self.diagnostics_group)
        footer = QHBoxLayout(); footer.addWidget(self.status); footer.addStretch(1); footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.diagnostics_group.toggled.connect(self._set_diagnostics_visible)
        self.new_tab(GELBOORU_HOME)
        self.focus_address_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        self.new_tab_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.close_tab_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        self.focus_address_shortcut.activated.connect(self._focus_address)
        self.new_tab_shortcut.activated.connect(lambda: self.new_tab(self._site_urls()[0]))
        self.close_tab_shortcut.activated.connect(lambda: self.close_tab(self.tabs.currentIndex()))
        self._set_diagnostics_visible(False)
        self.retranslate()

    def retranslate(self) -> None:
        text = self.catalog.text
        self.setWindowTitle(text("browser.title"))
        self.reload_button.setText(text("browser.reload"))
        self.home_button.setText(text("browser.home"))
        self.account_button.setText(text("browser.account"))
        self.close_button.setText(text("browser.close"))
        self.new_tab_button.setToolTip(text("browser.new_tab"))
        self.address.setPlaceholderText(text("browser.address"))
        self.diagnostics_group.setTitle(text("browser.developer_diagnostics"))
        self.status.setText(text("browser.status_not_tested"))
        self.manual_diagnostic.setText(text("browser.diagnostic_form"))
        self.http_additions.setPlaceholderText(text("browser.diagnostic_additions"))
        self.http_removals.setPlaceholderText(text("browser.diagnostic_removals"))
        self.http_diagnostic.setText(text("browser.diagnostic_http"))
        self.prototype_button.setText(text("browser.diagnostic_prototype"))
        self.http_targets_label.setText(text("browser.diagnostic_targets"))
        self.manual_diagnostic.setToolTip(text("browser.diagnostic_form_tip"))
        self.http_diagnostic.setToolTip(text("browser.diagnostic_http_tip"))

    def _set_diagnostics_visible(self, visible: bool) -> None:
        for child in self.diagnostics_group.findChildren(QWidget):
            child.setVisible(visible)

    def new_tab(self, url: str | QUrl = GELBOORU_HOME) -> QWebEngineView:
        view = QWebEngineView(self.tabs)
        view.setPage(self.profile.create_page(view))
        index = self.tabs.addTab(view, "New tab")
        view.urlChanged.connect(lambda value, tab=view: self._url_changed(tab, value))
        view.titleChanged.connect(lambda title, tab=view: self._title_changed(tab, title))
        view.loadFinished.connect(lambda ok, tab=view: self._tab_loaded(tab, ok))
        self.tabs.setCurrentIndex(index)
        view.load(url if isinstance(url, QUrl) else QUrl.fromUserInput(url))
        return view

    def close_tab(self, index: int) -> None:
        if self.tabs.count() <= 1:
            self.tabs.widget(0).load(QUrl(self._site_urls()[0]))
            return
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        widget.deleteLater()

    def open_url(self, url: str, *, new_tab: bool = False) -> None:
        if new_tab or self.view is None:
            self.new_tab(url)
        else:
            self.view.load(QUrl.fromUserInput(url))

    def _site_urls(self) -> tuple[str, str]:
        host = self.view.url().host().casefold() if self.view is not None else ""
        if host in {"e621.net", "www.e621.net", "e926.net", "www.e926.net"}:
            return E621_HOME, E621_ACCOUNT
        return GELBOORU_HOME, GELBOORU_ACCOUNT

    def _tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        self.view = widget if isinstance(widget, QWebEngineView) else None
        if self.view is not None:
            self.address.setText(self.view.url().toString())

    def _navigate_address(self) -> None:
        if self.view is not None:
            self.view.load(QUrl.fromUserInput(self.address.text().strip()))

    def _focus_address(self) -> None:
        self.address.setFocus(); self.address.selectAll()

    def _url_changed(self, view: QWebEngineView, url: QUrl) -> None:
        if view is self.view:
            self.address.setText(url.toString())

    def _title_changed(self, view: QWebEngineView, title: str) -> None:
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabText(index, (title.strip() or view.url().host() or "New tab")[:40])

    def _tab_loaded(self, view: QWebEngineView, ok: bool) -> None:
        if view is self.view:
            self._loaded(ok)

    def _open_real_edit_prototype(self) -> None:
        self._prototype_dialog = GelbooruEditPrototypeDialog(self.profile, self.catalog, self, log=self.log)
        self._prototype_dialog.show(); self._prototype_dialog.raise_(); self._prototype_dialog.activateWindow()

    def _loaded(self, ok: bool) -> None:
        if not ok:
            self.status.setText(self.catalog.text("browser.status_load_error"))
            return
        self.view.page().runJavaScript(SESSION_DIAGNOSTIC_SCRIPT, self._show_session_state)
        if self.manual_diagnostic.isChecked():
            self._arm_manual_diagnostic()

    def _show_session_state(self, result: object) -> None:
        state = classify_session(result).state
        if state is SessionState.AUTHENTICATED:
            self.status.setText(self.catalog.text("browser.status_ready"))
        elif state is SessionState.UNAUTHENTICATED:
            self.status.setText(self.catalog.text("browser.status_login_required"))
        else:
            self.status.setText(self.catalog.text("browser.status_unknown"))

    def _toggle_manual_diagnostic(self, enabled: bool) -> None:
        if enabled:
            if self.http_diagnostic.isChecked():
                self.http_diagnostic.setChecked(False)
            self.log("Gelbooru manual form diagnostic: enabled no_post=true")
            self._manual_probe_timer.start()
            self._arm_manual_diagnostic()
        else:
            self._manual_probe_timer.stop()
            self.view.page().runJavaScript(MANUAL_FORM_DIAGNOSTIC_DISABLE_SCRIPT)
            self.status.setText(self.catalog.text("browser.diagnostic_disabled"))
            self.log("Gelbooru manual form diagnostic: disabled")

    @staticmethod
    def _diagnostic_tags(value: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(part for part in value.split() if part))

    def _toggle_manual_http_diagnostic(self, enabled: bool) -> None:
        if enabled:
            if self.manual_diagnostic.isChecked():
                self.manual_diagnostic.setChecked(False)
            additions = self._diagnostic_tags(self.http_additions.text())
            removals = self._diagnostic_tags(self.http_removals.text())
            self._pending_http_expectation = (additions, removals)
            self.status.setText(self.catalog.text("browser.diagnostic_arming"))
            self.view.page().runJavaScript(
                MANUAL_FORM_DIAGNOSTIC_INSTALL_SCRIPT,
                self._http_submit_guard_ready,
            )
        else:
            self._pending_http_expectation = None
            self.profile.disarm_http_diagnostic("manual")
            self.view.page().runJavaScript(MANUAL_FORM_DIAGNOSTIC_DISABLE_SCRIPT)
            self.http_diagnostic_state_changed.emit(False)

    def _http_submit_guard_ready(self, result: object) -> None:
        values = EmbeddedGelbooruBridge._decoded_mapping(result)
        if str(values.get("status", "")) != "armed":
            self.status.setText(self.catalog.text("browser.diagnostic_unavailable"))
            self._pending_http_expectation = None
            return
        expectation = self._pending_http_expectation
        if expectation is None or not self.http_diagnostic.isChecked():
            return
        additions, removals = expectation
        armed = self.profile.arm_http_diagnostic(
            "manual", page=self.view.page(), additions=additions, removals=removals
        )
        if not armed:
            reason = self.profile.last_http_diagnostic_error or "unknown"
            self.status.setText(self.catalog.text("browser.diagnostic_cdp_error", reason=reason))
            self._pending_http_expectation = None
            return
        self._pending_http_expectation = None
        self.view.page().runJavaScript(MANUAL_FORM_DIAGNOSTIC_DISABLE_SCRIPT)
        self.http_diagnostic_state_changed.emit(True)
        self.status.setText(self.catalog.text("browser.diagnostic_http_armed"))

    def _http_diagnostic_finished(self, source: str) -> None:
        if self.http_diagnostic.isChecked():
            self.http_diagnostic.setChecked(False)
        self.status.setText(self.catalog.text("browser.diagnostic_http_done", source=source))

    def _arm_manual_diagnostic(self) -> None:
        self.view.page().runJavaScript(
            MANUAL_FORM_DIAGNOSTIC_INSTALL_SCRIPT, self._manual_diagnostic_armed
        )

    def _manual_diagnostic_armed(self, result: object) -> None:
        values = EmbeddedGelbooruBridge._decoded_mapping(result)
        status = str(values.get("status", ""))
        if status == "armed":
            self.status.setText(self.catalog.text("browser.diagnostic_form_armed"))
        elif status not in {"form", "tags"}:
            self.status.setText(self.catalog.text("browser.diagnostic_form_unavailable"))

    def _poll_manual_snapshot(self) -> None:
        if self._manual_probe_running or not self.manual_diagnostic.isChecked():
            return
        self._manual_probe_running = True
        self.view.page().runJavaScript(
            MANUAL_FORM_DIAGNOSTIC_TAKE_SCRIPT, self._manual_snapshot_received
        )

    def _manual_snapshot_received(self, result: object) -> None:
        self._manual_probe_running = False
        if not result:
            return
        values = EmbeddedGelbooruBridge._decoded_mapping(result)
        snapshot = parse_form_snapshot(values.get("snapshot"), "manual")
        self.log(snapshot.safe_log())
        record = getattr(getattr(self, "profile", None), "record_form_serialization_snapshot", None)
        if callable(record):
            record(snapshot)
        if values.get("status") == "snapshot" and snapshot.prevented:
            self._manual_probe_timer.stop()
            self.view.page().runJavaScript(MANUAL_FORM_DIAGNOSTIC_DISABLE_SCRIPT)
            set_checked = getattr(self.manual_diagnostic, "setChecked", None)
            if callable(set_checked):
                set_checked(False)
            self.status.setText(self.catalog.text("browser.diagnostic_snapshot_done"))
        else:
            self.status.setText(self.catalog.text("browser.diagnostic_snapshot_error"))
