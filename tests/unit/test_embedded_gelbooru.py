import json
import threading
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtWidgets import QApplication

from booruflow.application.batch_publisher import PUBLISH_DELAY_SECONDS
from booruflow.infrastructure.embedded_gelbooru import (
    EMBEDDED_SAVE_CLICK_SCRIPT,
    MANUAL_FORM_DIAGNOSTIC_INSTALL_SCRIPT,
    EmbeddedGelbooruEditTransport,
    EmbeddedGelbooruProfile,
    EmbeddedGelbooruSession,
    EmbeddedGelbooruSessionFactory,
    LazyGelbooruEditTransport,
    SessionState,
    build_embedded_form_snapshot_script,
    classify_session,
    compare_form_serializations,
    is_expected_post_url,
    parse_edit_form_diagnostic,
    parse_form_snapshot,
)
from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruPublishDeferredError,
    GelbooruSessionExpiredError,
    GelbooruSessionUnknownError,
    GelbooruTransportError,
)


class Bridge:
    def __init__(self, result=True, error=None):
        self.result = result; self.error = error; self.calls = []

    def invoke(self, operation, payload=None, timeout_seconds=None):
        self.calls.append((operation, payload, timeout_seconds, threading.get_ident()))
        if self.error:
            raise self.error
        return self.result


def test_session_factory_reuses_the_same_bridge_and_profile_boundary():
    bridge = Bridge(); factory = EmbeddedGelbooruSessionFactory(bridge)
    first, second = factory.create(), factory.create()
    assert first.bridge is bridge and second.bridge is bridge


def test_non_destructive_session_validation_uses_only_validate_operation():
    bridge = Bridge(); EmbeddedGelbooruSession(bridge).validate_authenticated()
    assert bridge.calls == [("validate", None, None, threading.get_ident())]


def test_session_expiry_is_preserved_across_the_worker_proxy():
    session = EmbeddedGelbooruSession(Bridge(error=GelbooruSessionExpiredError("expired")))
    with pytest.raises(GelbooruSessionExpiredError, match="expired"):
        session.validate_authenticated()


def test_diagnostic_transport_builds_snapshot_without_submitting():
    bridge = Bridge(); session = EmbeddedGelbooruSession(bridge)
    with pytest.raises(GelbooruPublishDeferredError, match="submit Embedded désactivé"):
        EmbeddedGelbooruEditTransport(diagnostic_only=True).submit(
            session, "42", ("a", "new_tag")
        )
    assert bridge.calls[0][0:2] == (
        "snapshot_submit",
        {
            "post_id": "42", "tags": ("a", "new_tag"),
            "additions": (), "removals": (),
            "diagnostic_post_id_query": True,
        },
    )


def test_enabled_transport_passes_exact_tags_and_post_to_shared_session():
    bridge = Bridge(); session = EmbeddedGelbooruSession(bridge)
    EmbeddedGelbooruEditTransport().submit(session, "42", ("a", "new_tag"))
    assert bridge.calls[0][0:2] == (
        "submit", {
            "post_id": "42", "tags": ("a", "new_tag"),
            "additions": (), "removals": (), "fresh_tags": (),
            "http_diagnostic": False,
        }
    )


def test_prepared_transport_passes_only_fresh_publish_payload_and_diagnostics():
    bridge = Bridge(); session = EmbeddedGelbooruSession(bridge)
    prepared = SimpleNamespace(
        post_id="42",
        publish_tags=("a", "c", "d"),
        additions=("d",),
        removals=("b",),
        fresh_tags=("a", "b", "c"),
    )

    EmbeddedGelbooruEditTransport().submit_prepared(session, prepared)

    assert bridge.calls[0][0:2] == (
        "submit",
        {
            "post_id": "42", "tags": ("a", "c", "d"),
            "additions": ("d",), "removals": ("b",),
            "fresh_tags": ("a", "b", "c"),
            "http_diagnostic": False,
        },
    )


def test_http_diagnostic_transport_marks_only_the_real_submit_request():
    bridge = Bridge(); session = EmbeddedGelbooruSession(bridge)
    prepared = SimpleNamespace(
        post_id="42",
        publish_tags=("a", "new_tag"),
        additions=("new_tag",),
        removals=("highres",),
        fresh_tags=("a", "highres"),
    )

    transport = EmbeddedGelbooruEditTransport(http_diagnostic=True)
    transport.submit_prepared(session, prepared)

    operation, payload, _timeout, _thread = bridge.calls[0]
    assert operation == "submit"
    assert payload["http_diagnostic"] is True
    assert payload["additions"] == ("new_tag",)
    assert payload["removals"] == ("highres",)
    assert transport.http_diagnostic is False

    transport.submit_prepared(session, prepared)
    assert bridge.calls[1][1]["http_diagnostic"] is False


def test_lazy_transport_does_not_create_browser_until_submit_and_reuses_session():
    session = type("Session", (), {"validate_authenticated": lambda self: None})()
    created=[]; calls=[]
    factory=type("Factory", (), {"create": lambda self: created.append(True) or session})()
    transport=type("Transport", (), {
        "submit": lambda self, actual, post_id, tags: calls.append((actual, post_id, tags))
    })()
    lazy=LazyGelbooruEditTransport(factory, transport)
    assert created == []
    lazy.submit(object(), "1", ("a",)); lazy.submit(object(), "2", ("b",))
    assert created == [True]
    assert calls == [(session,"1",("a",)),(session,"2",("b",))]


def test_lazy_transport_forwards_prepared_context_only_when_backend_supports_it():
    session = type("Session", (), {"validate_authenticated": lambda self: None})()
    created=[]; calls=[]
    factory=type("Factory", (), {"create": lambda self: created.append(True) or session})()
    transport=type("Transport", (), {
        "submit_prepared": lambda self, actual, prepared: calls.append((actual, prepared))
    })()
    prepared=SimpleNamespace(post_id="42", publish_tags=("a","c","d"))
    lazy=LazyGelbooruEditTransport(factory, transport)

    lazy.submit_prepared(object(), prepared)

    assert created == [True]
    assert calls == [(session, prepared)]


@pytest.mark.parametrize("error", [
    GelbooruSessionExpiredError("login"),
    GelbooruTransportError("form"),
    GelbooruTransportError("timeout"),
    GelbooruTransportError("cancelled"),
])
def test_transport_preserves_auth_form_timeout_and_cancel_errors(error):
    with pytest.raises(type(error), match=str(error)):
        EmbeddedGelbooruEditTransport(diagnostic_only=False).submit(
            EmbeddedGelbooruSession(Bridge(error=error)), "1", ("a",)
        )


@pytest.mark.parametrize("url,expected", [
    ("https://gelbooru.com/index.php?page=post&s=view&id=12", True),
    ("https://gelbooru.com/index.php?page=post&s=view&id=13", False),
    ("https://gelbooru.com/index.php?page=post&s=edit&id=12", False),
    ("https://gelbooru.com/index.php?page=account&s=login", False),
    ("https://example.com/index.php?page=post&s=view&id=12", False),
    ("http://gelbooru.com/index.php?page=post&s=view&id=12", False),
    ("https://gelbooru.com:443/index.php?page=post&s=view&id=12", False),
])
def test_final_url_must_explicitly_identify_the_expected_post(url, expected):
    assert is_expected_post_url(url, "12") is expected


def test_profile_paths_and_persistence_policy_are_configured(monkeypatch, tmp_path: Path):
    import booruflow.infrastructure.embedded_gelbooru as module

    class CookiePolicy:
        ForcePersistentCookies = "persistent"

    class FakeProfile:
        PersistentCookiesPolicy = CookiePolicy
        def __init__(self, name, parent): self.name=name; self.parent=parent
        def setPersistentStoragePath(self, value): self.storage=value
        def setCachePath(self, value): self.cache=value
        def setPersistentCookiesPolicy(self, value): self.policy=value
        def setUrlRequestInterceptor(self, value): self.interceptor=value

    monkeypatch.setattr(module, "QWebEngineProfile", FakeProfile)
    owner = module.EmbeddedGelbooruProfile(tmp_path)
    expected = tmp_path / "BrowserProfiles" / "embedded" / "gelbooru"
    assert owner.root == expected
    assert owner.profile.storage == str(expected / "storage")
    assert owner.profile.cache == str(expected / "cache")
    assert owner.profile.policy == "persistent"
    assert owner.profile.interceptor is owner.http_diagnostic


def test_profile_cdp_capture_uses_the_exact_page_devtools_id(monkeypatch, tmp_path: Path):
    import booruflow.infrastructure.embedded_gelbooru as module

    qt_app()
    captures = []

    class FakeCapture:
        def __init__(self, configuration, target_id, expectation, **callbacks):
            self.configuration = configuration
            self.target_id = target_id
            self.expectation = expectation
            self.callbacks = callbacks
            self.stopped = False
            captures.append(self)

        def start(self):
            return True

        def stop(self):
            self.stopped = True

    configuration = SimpleNamespace(
        enabled=True, error="", host="127.0.0.1", port=9223
    )
    monkeypatch.setattr(module, "embedded_cdp_configuration", lambda: configuration)
    monkeypatch.setattr(module, "EmbeddedCdpNetworkCapture", FakeCapture)
    logs = []
    owner = module.EmbeddedGelbooruProfile(tmp_path, log=logs.append)
    page = SimpleNamespace(devToolsId=lambda: "exact-hidden-page-target")

    assert (
        "BooruFlow startup: embedded_cdp_diagnostic=true bind=127.0.0.1 "
        "port=9223 configured_before_qapplication=true"
    ) in logs

    assert owner.arm_http_diagnostic(
        "embedded", page=page, additions=("new",), removals=("old",)
    ) is True
    assert len(captures) == 1
    assert captures[0].configuration is configuration
    assert captures[0].target_id == "exact-hidden-page-target"
    assert captures[0].expectation.source == "embedded"
    assert captures[0].expectation.additions == ("new",)
    assert captures[0].expectation.removals == ("old",)
    owner.disarm_http_diagnostic("embedded")
    assert captures[0].stopped is True


class FakePage(QObject):
    loadFinished = Signal(bool)
    loadStarted = Signal()
    urlChanged = Signal(QUrl)
    class WebAction:
        Stop = "stop"

    def __init__(self, scripts):
        super().__init__(); self.scripts=list(scripts); self.executed_scripts=[]
        self.loads=[]; self.stopped=False
        self.current_url=QUrl("about:blank")
    def load(self, url):
        self.loads.append(url.toString()); self.current_url=url
        self.urlChanged.emit(url); self.loadStarted.emit()
    def runJavaScript(self, script, callback):
        self.executed_scripts.append(script)
        if "edit_control_missing" in script:
            if self.scripts:
                self.scripts.pop(0)  # obsolete hidden-form diagnostic fixture
            callback(json.dumps({"status": "edit_clicked"}))
        elif "editFormExists" in script:
            callback(json.dumps({
                "editFormVisible": True, "tagsFieldPresent": True,
                "savePresent": True, "postIdMatches": True,
                "tagsFieldDisabled": False, "tagsFieldReadonly": False,
                "saveDisabled": False,
            }))
        elif "const norm = value" in script:
            legacy = json.loads(self.scripts.pop(0)) if self.scripts else {}
            status = legacy.get("status")
            callback(json.dumps({
                "status": "invariant_failed" if status in {"mismatch", "prevented"} else "prepared",
                "tagCount": legacy.get("count", 1), "additionsPresent": status != "mismatch",
                "removalsAbsent": status != "mismatch", "unrelatedPreserved": status != "mismatch",
            }))
        elif "save.click()" in script:
            callback(json.dumps({"status": "save_clicked"}))
        else:
            callback(self.scripts.pop(0))
    def url(self): return self.current_url
    def requestedUrl(self): return self.current_url
    def triggerAction(self, _action): self.stopped=True


def qt_app():
    return QApplication.instance() or QApplication([])


def test_bridge_normal_validation_response_on_gui_thread():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    qt_app(); page=FakePage([{
        "url":"https://gelbooru.com/index.php?page=account&s=home",
        "readyState":"complete","accountHome":True,"accountTitle":True,
        "accountContent":True,"bodyPresent":True,"bodyTextLength":500,
        "titlePresent":True,
        "loginForm":False,"loginLink":False,"logoutMarker":False,
        "loggedOutText":False,"challengeMarker":False,
    }])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("validate",{},1)
    bridge._begin_request(request); page.loadFinished.emit(True)
    assert request.completed.is_set() and request.result is True and request.error is None


def test_successful_validation_emits_complete_safe_navigation_trace():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    logs=[]; qt_app(); page=FakePage([{
        "url":"https://gelbooru.com/index.php?page=account&s=home",
        "readyState":"complete","accountHome":True,"accountTitle":True,
        "accountContent":True,"bodyPresent":True,"bodyTextLength":500,
        "titlePresent":True,"logoutMarker":True,
    }])
    bridge=EmbeddedGelbooruBridge(object(),page=page,log=logs.append)
    request=_BridgeRequest("validate",{},1); bridge._begin_request(request); page.loadFinished.emit(True)
    trace="\n".join(logs)
    for event in ("request_created","load_requested","load_finished"):
        assert event in trace
    assert "dom_probe_started" not in trace and "dom_probe_finished" not in trace
    assert trace.count("Gelbooru session DOM") == 1
    assert "url=https://gelbooru.com/index.php?page=account&s=home" in trace


def test_target_url_and_load_finished_are_valid_without_observed_load_started():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    qt_app(); page=FakePage([{
        "url":"https://gelbooru.com/index.php?page=account&s=home",
        "readyState":"complete","accountHome":True,"accountTitle":True,
        "accountContent":True,"bodyPresent":True,"bodyTextLength":500,
        "titlePresent":True,"logoutMarker":True,
    }])
    bridge=EmbeddedGelbooruBridge(object(),page=page)
    request=_BridgeRequest("validate",{},1); bridge._begin_request(request)
    bridge._navigation_started=False
    page.loadFinished.emit(True)
    assert request.completed.is_set() and request.result is True


def test_timeout_trace_distinguishes_no_start_no_finish_and_dom_wait():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    qt_app()
    cases=[]
    for mode in ("no-start","no-finish","dom"):
        logs=[]; scripts=([{"readyState":"interactive"}] if mode=="dom" else [])
        page=FakePage(scripts); bridge=EmbeddedGelbooruBridge(object(),page=page,log=logs.append)
        request=_BridgeRequest("validate",{},1); bridge._begin_request(request)
        if mode=="no-start":
            bridge._navigation_started=False; bridge._target_url_seen=False
        elif mode=="dom":
            page.loadFinished.emit(True)
        bridge._timed_out(); cases.append((mode,str(request.error),"\n".join(logs)))
    assert "aucun loadStarted" in cases[0][1]
    assert "aucun loadFinished" in cases[1][1]
    assert "DOM non prêt" in cases[2][1]
    assert all("timeout phase=" in logs for _mode,_error,logs in cases)


def test_navigation_trace_filters_unapproved_query_parameters():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    logs=[]; qt_app(); page=FakePage([])
    bridge=EmbeddedGelbooruBridge(object(),page=page,log=logs.append)
    request=_BridgeRequest("validate",{},1); bridge._begin_request(request)
    page.urlChanged.emit(QUrl(
        "https://gelbooru.com/index.php?page=account&s=home&token=secret&PHPSESSID=hidden"
    ))
    trace="\n".join(logs)
    assert "token" not in trace and "secret" not in trace and "PHPSESSID" not in trace


def test_blank_residual_finish_never_inspects_dom_before_account_navigation_finishes():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    qt_app(); page=FakePage([{
        "url":"https://gelbooru.com/index.php?page=account&s=home",
        "readyState":"complete","accountHome":True,"accountTitle":True,
        "accountContent":True,"bodyPresent":True,"bodyTextLength":500,
        "titlePresent":True,"loginForm":False,"logoutMarker":True,
    }])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("validate",{},1); bridge._begin_request(request)
    page.current_url=QUrl("about:blank"); page.loadFinished.emit(True)
    assert not request.completed.is_set() and len(page.scripts) == 1
    page.current_url=QUrl("https://gelbooru.com/index.php?page=account&s=home")
    page.loadStarted.emit(); page.loadFinished.emit(True)
    assert request.completed.is_set() and request.result is True


def test_interactive_but_usable_account_dom_is_authenticated_immediately():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    base={
        "url":"https://gelbooru.com/index.php?page=account&s=home",
        "accountHome":True,"accountTitle":True,"accountContent":True,
        "bodyPresent":True,"bodyTextLength":500,"titlePresent":True,
        "loginForm":False,"loginLink":False,"logoutMarker":False,
        "loggedOutText":False,"challengeMarker":False,
    }
    qt_app(); page=FakePage([{**base,"readyState":"interactive"}])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("validate",{},1); bridge._begin_request(request); page.loadFinished.emit(True)
    assert request.completed.is_set() and request.result is True


def test_loading_without_body_retries_only_a_few_times_then_times_out():
    from PySide6.QtTest import QTest

    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    probe={
        "url":"https://gelbooru.com/index.php?page=account&s=home",
        "readyState":"loading","accountHome":True,"accountContent":False,
        "bodyPresent":False,"bodyTextLength":0,"titlePresent":False,
    }
    logs=[]; qt_app(); page=FakePage([probe.copy() for _ in range(5)])
    bridge=EmbeddedGelbooruBridge(object(),page=page,log=logs.append)
    request=_BridgeRequest("validate",{},5); bridge._begin_request(request); page.loadFinished.emit(True)
    QTest.qWait(1200)
    assert request.completed.is_set() and "DOM non prêt" in str(request.error)
    assert bridge._active is None and len(page.scripts) == 0
    assert "\n".join(logs).count("Gelbooru session DOM") == 1


@pytest.mark.parametrize("values,state", [
    ({"logoutMarker":True}, SessionState.AUTHENTICATED),
    ({"loginForm":True}, SessionState.UNAUTHENTICATED),
    ({"loggedOutText":True}, SessionState.UNAUTHENTICATED),
    ({"challengeMarker":True}, SessionState.UNKNOWN),
    ({"readyState":"complete"}, SessionState.UNKNOWN),
    ({
        "url":"https://gelbooru.com/index.php?page=account&s=home",
        "readyState":"complete","accountHome":True,"accountTitle":True,
        "accountContent":True,"bodyPresent":True,"bodyTextLength":500,
        "titlePresent":True,
        "loggedOutText":False,"challengeMarker":False,
    }, SessionState.AUTHENTICATED),
])
def test_session_classification_has_three_distinct_states(values, state):
    assert classify_session(values).state is state


def test_session_classification_decodes_qt_safe_json_result():
    result = json.dumps({
        "url": "https://gelbooru.com/index.php?page=account&s=home",
        "readyState": "complete",
        "accountHome": True,
        "accountContent": True,
        "bodyPresent": True,
        "bodyTextLength": 500,
        "logoutMarker": True,
    })

    diagnostic = classify_session(result)

    assert diagnostic.state is SessionState.AUTHENTICATED
    assert diagnostic.dom_usable is True


def test_session_probe_error_is_unknown_and_safely_named():
    diagnostic = classify_session(json.dumps({
        "url": "https://gelbooru.com/index.php?page=account&s=home&token=secret",
        "readyState": "complete",
        "probeError": "TypeError",
    }))

    assert diagnostic.state is SessionState.UNKNOWN
    assert diagnostic.probe_error == "TypeError"
    assert "token" not in diagnostic.safe_log()


def edit_payload(forms, **overrides):
    payload = {
        "url": "https://gelbooru.com/index.php?page=post&s=view&id=42",
        "readyState": "complete",
        "titlePresent": True,
        "bodyPresent": True,
        "loginForm": False,
        "forms": forms,
    }
    payload.update(overrides)
    return json.dumps(payload)


def real_edit_form(**overrides):
    form = {
        "index": 0,
        "isEditForm": True,
        "methodPost": True,
        "action": "./public/edit_post.php",
        "tagsField": True,
        "tagsKind": "textarea",
        "ratingField": True,
        "sourceField": True,
        "titleField": True,
        "postIdField": True,
        "postIdMatches": True,
        "submitControl": True,
    }
    form.update(overrides)
    return form


def post_submit_payload(url="https://gelbooru.com/index.php?page=post&s=view&id=12",
                        **overrides):
    payload = {
        "url": url,
        "readyState": "complete",
        "bodyPresent": True,
        "accountPage": False,
        "loginForm": False,
        "logoutMarker": True,
        "loggedOutText": False,
        "challengeMarker": False,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_edit_probe_is_scoped_to_edit_form_without_click_or_visibility_dependency():
    from booruflow.infrastructure.embedded_gelbooru import EDIT_FORM_DIAGNOSTIC_SCRIPT

    assert "document.getElementById('edit_form')" in EDIT_FORM_DIAGNOSTIC_SCRIPT
    assert "form.querySelector('textarea[name=\"tags\"], input[name=\"tags\"]')" in (
        EDIT_FORM_DIAGNOSTIC_SCRIPT
    )
    assert "showEditBox" not in EDIT_FORM_DIAGNOSTIC_SCRIPT
    assert ".click()" not in EDIT_FORM_DIAGNOSTIC_SCRIPT
    assert "display" not in EDIT_FORM_DIAGNOSTIC_SCRIPT


def test_save_script_clicks_only_the_real_save_control():
    assert "save.click()" in EMBEDDED_SAVE_CLICK_SCRIPT
    assert "requestSubmit" not in EMBEDDED_SAVE_CLICK_SCRIPT
    assert "form.submit" not in EMBEDDED_SAVE_CLICK_SCRIPT
    assert 'value=\"Save changes\"' in EMBEDDED_SAVE_CLICK_SCRIPT


def form_snapshot_payload(**overrides):
    payload = {
        "method": "POST",
        "action": "/public/edit_post.php",
        "fields": [
            {"name": "rating", "type": "select-one", "present": True, "length": 1},
            {"name": "title", "type": "text", "present": True, "length": 0},
            {"name": "source", "type": "text", "present": True, "length": 12},
            {"name": "tags", "type": "textarea", "present": True, "length": None},
            {"name": "tagsSearched", "type": "hidden", "present": True, "length": 0},
            {"name": "id", "type": "hidden", "present": True, "length": 2},
            {"name": "uid", "type": "hidden", "present": True, "length": None},
            {"name": "uname", "type": "hidden", "present": True, "length": None},
            {"name": "lupdated", "type": "hidden", "present": True, "length": None},
            {"name": "csrf-token", "type": "hidden", "present": True, "length": None},
            {"name": "submit", "type": "submit", "present": True, "length": 12},
        ],
        "tagsEntries": 1,
        "tagCount": 13,
        "additionsPresent": [True, True],
        "removalsPresent": [False, False],
        "submitterName": "submit",
        "submitterType": "submit",
        "lupdatedPresent": True,
        "tagsSearchedPresent": True,
        "tagsSearchedKind": "post_id_query",
        "postIdPresent": True,
        "urlPage": "post",
        "urlS": "view",
        "urlPostId": "42",
        "urlTagsPresent": True,
        "urlTagsKind": "post_id_query",
        "prevented": False,
    }
    payload.update(overrides)
    return payload


def test_formdata_snapshot_has_one_tags_submitter_lupdated_and_no_sensitive_values():
    secret = "never_log_csrf_uid_uname_or_lupdated_value"
    payload = form_snapshot_payload(secretValue=secret)
    snapshot = parse_form_snapshot(payload, "embedded")

    assert snapshot.tags_entries == 1
    assert [field.name for field in snapshot.fields].count("tags") == 1
    assert snapshot.submitter_name == "submit"
    assert snapshot.lupdated_present is True
    assert snapshot.tags_searched_kind == "post_id_query"
    assert snapshot.url_tags_kind == "post_id_query"
    assert snapshot.post_id_present is True
    assert snapshot.expected_shape is True
    assert secret not in snapshot.safe_log()
    for sensitive in ("csrf-token", "uid", "uname", "lupdated"):
        field = next(value for value in snapshot.fields if value.name == sensitive)
        assert field.length is None


def test_textarea_is_modified_before_formdata_and_snapshot_script_never_submits():
    script = build_embedded_form_snapshot_script(
        ("a", "c", "d"), additions=("d",), removals=("b",)
    )

    assert script.index("field.value = newTags") < script.index("new FormData(form, submitter)")
    assert "form.requestSubmit" not in script
    assert ".submit()" not in script
    assert "new FormData(form, submitter)" in script
    assert "'embedded', false, null, true, true" in script


def test_manual_and_embedded_snapshots_share_the_same_form_contract():
    embedded = parse_form_snapshot(form_snapshot_payload(), "embedded")
    manual = parse_form_snapshot(
        form_snapshot_payload(
            additionsPresent=[], removalsPresent=[], prevented=True,
            addedFromInitialCount=2, removedFromInitialCount=2,
        ),
        "manual",
    )

    assert (manual.method, manual.action, manual.fields, manual.tags_entries,
            manual.tag_count, manual.submitter_name, manual.lupdated_present) == (
        embedded.method, embedded.action, embedded.fields, embedded.tags_entries,
        embedded.tag_count, embedded.submitter_name, embedded.lupdated_present,
    )
    assert manual.prevented is True


def test_serialization_snapshot_preserves_order_duplicates_hashes_and_safe_metadata():
    secret = "never_log_this_form_value"
    snapshot = parse_form_snapshot(form_snapshot_payload(
        fieldOrder=["rating", "tags", "tagsSearched", "tags", "submit"],
        uniqueFieldCount=4,
        duplicateFieldNames=["tags"],
        serializedLength=321,
        serializedSha256="a" * 64,
        tagsSha256="b" * 64,
        tagsDuplicateCount=1,
        tagsLeadingSpace=True,
        tagsTrailingSpace=False,
        tagsDoubleSpace=True,
        encodingMarkers=[True, False, True, True, False, False, False, False],
        tagsSearchedCount=1,
        tagsSearchedIndex=2,
        tagsSearchedLength=5,
        tagsSearchedSha256="c" * 64,
        tagsDomMatchesFormData=True,
        submitterIncluded=True,
        diagnosticOnly=True,
        postBlocked=True,
        secretValue=secret,
    ), "manual")

    assert snapshot.field_order == ("rating", "tags", "tagsSearched", "tags", "submit")
    assert snapshot.duplicate_field_names == ("tags",)
    assert snapshot.serialized_sha256 == "a" * 64
    assert snapshot.tags_sha256 == "b" * 64
    assert snapshot.tags_searched_sha256 == "c" * 64
    assert snapshot.submitter_included and snapshot.diagnostic_only and snapshot.post_blocked
    assert secret not in snapshot.safe_log()


def test_diagnostic_snapshot_refuses_the_unsafe_diagnostic_only_without_blocked_pair():
    snapshot = parse_form_snapshot(form_snapshot_payload(
        diagnosticOnly=True, postBlocked=False,
    ), "embedded")

    assert snapshot.diagnostic_only and not snapshot.post_blocked
    assert snapshot.probe_error == "diagnostic_not_blocked"
    assert not snapshot.expected_shape


def test_serialization_comparison_is_strict_and_reports_only_safe_categories():
    shared = {
        "fieldOrder": ["rating", "tags", "tagsSearched", "submit"],
        "uniqueFieldCount": 4, "duplicateFieldNames": [], "serializedLength": 100,
        "serializedSha256": "a" * 64, "tagsSha256": "b" * 64,
        "encodingMarkers": [True] * 8, "tagsSearchedCount": 1,
        "tagsSearchedIndex": 2, "tagsSearchedLength": 5, "tagsSearchedSha256": "c" * 64,
        "submitterIncluded": True,
    }
    manual = parse_form_snapshot(form_snapshot_payload(
        prevented=True, additionsPresent=[], removalsPresent=[], **shared
    ), "manual")
    embedded = parse_form_snapshot(form_snapshot_payload(**shared), "embedded")
    identical = compare_form_serializations(manual, embedded)
    assert identical.differences == ()
    assert identical.same_serialized_hash and identical.same_tags_hash
    changed = parse_form_snapshot(form_snapshot_payload(
        **(shared | {"fieldOrder": ["rating", "tagsSearched", "tags", "submit"],
                     "serializedSha256": "d" * 64})
    ), "embedded")
    different = compare_form_serializations(manual, changed)
    assert "serialized_hash_diff" in different.differences
    assert "field_order_diff" in different.differences
    assert "a" * 64 not in different.safe_log()


def test_matching_manual_and_embedded_diagnostic_snapshots_are_compared_once_safely():
    payload = form_snapshot_payload(
        fieldOrder=["tags", "tagsSearched", "submit"], uniqueFieldCount=3,
        serializedLength=17, serializedSha256="a" * 64, tagsSha256="b" * 64,
        encodingMarkers=[False] * 8, tagsSearchedCount=1, tagsSearchedIndex=1,
        tagsSearchedLength=5, tagsSearchedSha256="c" * 64, submitterIncluded=True,
        diagnosticOnly=True, postBlocked=True,
    )
    manual = parse_form_snapshot(
        form_snapshot_payload(**(payload | {"prevented": True})), "manual"
    )
    embedded = parse_form_snapshot(payload, "embedded")
    logs = []
    profile = SimpleNamespace(_form_serialization_snapshots={}, log=logs.append)

    EmbeddedGelbooruProfile.record_form_serialization_snapshot(profile, manual)
    EmbeddedGelbooruProfile.record_form_serialization_snapshot(profile, embedded)

    assert len(logs) == 1
    assert "same_serialized_hash=true" in logs[0]
    assert "a" * 64 not in logs[0]


@pytest.mark.parametrize(
    "hidden_kind,url_present,url_kind",
    [
        ("empty", False, "empty"),
        ("post_id_query", True, "post_id_query"),
        ("other", True, "other"),
    ],
)
def test_snapshot_classifies_safe_query_contexts(
    hidden_kind, url_present, url_kind,
):
    snapshot = parse_form_snapshot(form_snapshot_payload(
        tagsSearchedKind=hidden_kind,
        urlTagsPresent=url_present,
        urlTagsKind=url_kind,
    ), "manual")

    assert snapshot.tags_searched_kind == hidden_kind
    assert snapshot.url_tags_present is url_present
    assert snapshot.url_tags_kind == url_kind
    assert f"tagsSearched_kind={hidden_kind}" in snapshot.safe_log()
    assert f"url_tags_kind={url_kind}" in snapshot.safe_log()


def test_normalized_diff_ignores_tag_intent_but_compares_safe_form_context():
    manual = parse_form_snapshot(form_snapshot_payload(
        tagCount=14, additionsPresent=[], removalsPresent=[], prevented=True,
    ), "manual")
    embedded = parse_form_snapshot(form_snapshot_payload(
        tagCount=13, additionsPresent=[True] * 7,
        removalsPresent=[False, False],
    ), "embedded")

    assert manual.normalized_shape() == embedded.normalized_shape()


def test_snapshot_does_not_mutate_tags_searched_hidden_field():
    script = build_embedded_form_snapshot_script(("a", "b"))

    assert script.count("field.value = newTags") == 1
    assert "tagsSearched.value" not in script
    assert "[name=\"tagsSearched\"]" not in script


def test_manual_guard_blocks_submit_and_snapshots_after_synchronous_handlers():
    script = MANUAL_FORM_DIAGNOSTIC_INSTALL_SCRIPT

    assert "event.preventDefault()" in script
    assert "queueMicrotask" in script
    assert script.index("event.preventDefault()") < script.index("queueMicrotask")
    assert "event.defaultPrevented" in script
    assert "new FormData(form, submitter)" in script


class DialogPage:
    def __init__(self, results=()):
        self.results = list(results)
        self.scripts = []

    def runJavaScript(self, script, callback=None):
        self.scripts.append(script)
        if callback is not None:
            callback(self.results.pop(0) if self.results else None)


class DialogTimer:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


def dialog_harness(page, *, checked=True):
    from booruflow.infrastructure.embedded_gelbooru import GelbooruSessionDialog

    status = SimpleNamespace(text="", setText=lambda value: setattr(status, "text", value))
    dialog = SimpleNamespace(
        log_messages=[],
        log=lambda value: dialog.log_messages.append(value),
        view=SimpleNamespace(page=lambda: page),
        manual_diagnostic=SimpleNamespace(isChecked=lambda: checked),
        http_diagnostic=SimpleNamespace(isChecked=lambda: False),
        _manual_probe_timer=DialogTimer(),
        _manual_probe_running=False,
        status=status,
    )
    for name in (
        "_arm_manual_diagnostic", "_manual_diagnostic_armed",
        "_poll_manual_snapshot", "_manual_snapshot_received",
    ):
        setattr(dialog, name, MethodType(getattr(GelbooruSessionDialog, name), dialog))
    return dialog


def test_manual_diagnostic_ui_arms_and_disarms_the_submit_guard():
    from booruflow.infrastructure.embedded_gelbooru import (
        MANUAL_FORM_DIAGNOSTIC_DISABLE_SCRIPT,
        MANUAL_FORM_DIAGNOSTIC_INSTALL_SCRIPT,
        GelbooruSessionDialog,
    )

    page = DialogPage([json.dumps({"status": "armed"})])
    dialog = dialog_harness(page)

    GelbooruSessionDialog._toggle_manual_diagnostic(dialog, True)

    assert dialog._manual_probe_timer.started is True
    assert page.scripts == [MANUAL_FORM_DIAGNOSTIC_INSTALL_SCRIPT]
    assert "Save changes sera bloqué" in dialog.status.text
    assert dialog.log_messages == [
        "Gelbooru manual form diagnostic: enabled no_post=true"
    ]

    GelbooruSessionDialog._toggle_manual_diagnostic(dialog, False)

    assert dialog._manual_probe_timer.started is False
    assert page.scripts[-1] == MANUAL_FORM_DIAGNOSTIC_DISABLE_SCRIPT
    assert dialog.log_messages[-1] == "Gelbooru manual form diagnostic: disabled"


def test_manual_diagnostic_ui_polls_and_logs_only_the_safe_snapshot():
    from booruflow.infrastructure.embedded_gelbooru import (
        MANUAL_FORM_DIAGNOSTIC_TAKE_SCRIPT,
        GelbooruSessionDialog,
    )

    secret = "sensitive_value_must_not_escape"
    result = json.dumps({
        "status": "snapshot",
        "snapshot": form_snapshot_payload(
            prevented=True, additionsPresent=[], removalsPresent=[],
            secretValue=secret,
        ),
    })
    page = DialogPage([result])
    dialog = dialog_harness(page)

    GelbooruSessionDialog._poll_manual_snapshot(dialog)

    assert page.scripts[0] == MANUAL_FORM_DIAGNOSTIC_TAKE_SCRIPT
    assert len(page.scripts) == 2
    assert dialog._manual_probe_running is False
    assert "snapshot manuel capturé" in dialog.status.text
    assert len(dialog.log_messages) == 1
    assert dialog.log_messages[0].startswith("Gelbooru manual form snapshot:")
    assert secret not in dialog.log_messages[0]


def test_manual_http_diagnostic_blocks_save_until_cdp_network_is_confirmed():
    from booruflow.infrastructure.embedded_gelbooru import GelbooruSessionDialog

    page = DialogPage([json.dumps({"status": "armed"})])
    calls = []
    status = SimpleNamespace(text="", setText=lambda value: setattr(status, "text", value))
    dialog = SimpleNamespace(
        manual_diagnostic=SimpleNamespace(isChecked=lambda: False),
        http_diagnostic=SimpleNamespace(isChecked=lambda: True),
        http_additions=SimpleNamespace(text=lambda: "new_tag"),
        http_removals=SimpleNamespace(text=lambda: "highres"),
        view=SimpleNamespace(page=lambda: page),
        profile=SimpleNamespace(
            arm_http_diagnostic=lambda source, **values: calls.append((source, values)) or True,
            disarm_http_diagnostic=lambda _source: None,
        ),
        status=status,
        _pending_http_expectation=None,
        http_diagnostic_state_changed=SimpleNamespace(emit=lambda value: calls.append(("state", value))),
    )
    dialog._diagnostic_tags = GelbooruSessionDialog._diagnostic_tags
    dialog._http_submit_guard_ready = lambda result: GelbooruSessionDialog._http_submit_guard_ready(dialog, result)

    GelbooruSessionDialog._toggle_manual_http_diagnostic(dialog, True)

    assert "trace HTTP armée" in status.text
    assert calls[0] == ("manual", {"page": page, "additions": ("new_tag",), "removals": ("highres",)})
    assert calls[1] == ("state", True)
    assert len(page.scripts) == 2


def test_manual_http_diagnostic_keeps_save_blocked_when_cdp_cannot_arm():
    from booruflow.infrastructure.embedded_gelbooru import GelbooruSessionDialog

    page = DialogPage([json.dumps({"status": "armed"})])
    status = SimpleNamespace(text="", setText=lambda value: setattr(status, "text", value))
    dialog = SimpleNamespace(
        manual_diagnostic=SimpleNamespace(isChecked=lambda: False),
        http_diagnostic=SimpleNamespace(isChecked=lambda: True),
        http_additions=SimpleNamespace(text=lambda: ""),
        http_removals=SimpleNamespace(text=lambda: "highres"),
        view=SimpleNamespace(page=lambda: page),
        profile=SimpleNamespace(
            arm_http_diagnostic=lambda *_args, **_values: False,
            disarm_http_diagnostic=lambda _source: None,
            last_http_diagnostic_error="waiting_port:timeout",
        ),
        status=status,
        _pending_http_expectation=None,
        http_diagnostic_state_changed=SimpleNamespace(emit=lambda _value: None),
    )
    dialog._diagnostic_tags = GelbooruSessionDialog._diagnostic_tags
    dialog._http_submit_guard_ready = lambda result: GelbooruSessionDialog._http_submit_guard_ready(dialog, result)

    GelbooruSessionDialog._toggle_manual_http_diagnostic(dialog, True)

    assert "Save changes reste bloqué" in status.text
    assert len(page.scripts) == 1


@pytest.mark.parametrize("action", [
    "/public/edit_post.php",
    "./public/edit_post.php",
    "public/edit_post.php",
    "https://gelbooru.com/public/edit_post.php",
])
@pytest.mark.parametrize("tags_kind", ["input", "textarea"])
def test_edit_diagnostic_accepts_safe_action_variants_and_tags_fields(action, tags_kind):
    diagnostic = parse_edit_form_diagnostic(edit_payload([
        real_edit_form(action=action, tagsKind=tags_kind)
    ]), "42")

    assert diagnostic.status == "form_ready"
    assert diagnostic.selected_form.tags_kind == tags_kind


def test_edit_diagnostic_scopes_tags_to_hidden_edit_form_among_search_and_comment_forms():
    diagnostic = parse_edit_form_diagnostic(edit_payload([
        {"index": 0, "isEditForm": False, "methodPost": True,
         "action": "index.php?page=search", "tagsField": True,
         "tagsKind": "input", "submitControl": True},
        real_edit_form(index=1, hidden=True),
        {"index": 2, "isEditForm": False, "methodPost": True,
         "action": "/public/add_comment.php", "tagsField": False,
         "tagsKind": "", "submitControl": True},
    ]), "42")

    assert diagnostic.status == "form_ready"
    assert diagnostic.selected_form.index == 1
    assert diagnostic.selected_form.tags_kind == "textarea"


@pytest.mark.parametrize("forms,status", [
    ([], "form_absent"),
    ([{"index": 0, "isEditForm": False, "methodPost": True,
       "action": "/search", "tagsField": True, "submitControl": True}],
     "edit_form_absent"),
    ([real_edit_form(tagsField=False, tagsKind="")], "tags_absent"),
    ([real_edit_form(action="/wrong")], "invalid_structure"),
    ([real_edit_form(methodPost=False)], "invalid_structure"),
    ([real_edit_form(postIdMatches=False)], "invalid_structure"),
])
def test_edit_diagnostic_distinguishes_missing_and_invalid_structures(forms, status):
    assert parse_edit_form_diagnostic(edit_payload(forms), "42").status == status


def test_edit_diagnostic_rejects_login_or_wrong_post_and_never_logs_field_contents():
    secret = "do_not_log_this_tag_value"
    login = parse_edit_form_diagnostic(edit_payload([], loginForm=True), "42")
    wrong = parse_edit_form_diagnostic(edit_payload([], url=(
        "https://gelbooru.com/index.php?page=post&s=view&id=99"
    )), "42")
    valid = parse_edit_form_diagnostic(edit_payload([
        real_edit_form(fieldValue=secret, csrfToken="secret_csrf", uid="secret_uid")
    ]), "42")

    assert login.status == "login"
    assert wrong.status == "page_not_loaded"
    assert secret not in valid.safe_log()


def test_edit_diagnostic_json_error_is_distinct_and_safe():
    diagnostic = parse_edit_form_diagnostic(json.dumps({
        "url": "https://gelbooru.com/index.php?page=post&s=view&id=42&token=secret",
        "readyState": "complete",
        "probeError": "TypeError",
    }), "42")

    assert diagnostic.status == "javascript_error"
    assert diagnostic.probe_error == "TypeError"
    assert "token" not in diagnostic.safe_log() and "secret" not in diagnostic.safe_log()


def test_bridge_diagnostic_logs_compact_structure_and_stops_before_submit():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    logs=[]; qt_app(); page=FakePage([edit_payload([
        real_edit_form(fieldValue="secret_tag", hidden=True)
    ])])
    bridge=EmbeddedGelbooruBridge(object(),page=page,log=logs.append)
    request=_BridgeRequest("inspect_edit",{"post_id":"42"},1)
    bridge._begin_request(request); page.loadFinished.emit(True)

    assert request.completed.is_set() and request.error is None
    assert page.loads == ["https://gelbooru.com/index.php?page=post&s=view&id=42"]
    assert len(page.scripts) == 0
    line = next(message for message in logs if message.startswith("Gelbooru edit DOM:"))
    assert "forms=1" in line and "edit_form=true" in line
    assert "method_post=true" in line and "tags_field=true" in line
    assert "post_id_field=true" in line
    assert "edit_action=true" in line and "submit_control=true" in line
    assert "result=form_ready" in line
    assert "secret_tag" not in line


def test_bridge_embedded_diagnostic_stops_after_snapshot_without_submit_or_navigation():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    logs=[]; qt_app(); page=FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({"status": "snapshot", "snapshot": form_snapshot_payload(
            diagnosticOnly=True, postBlocked=True,
        )}),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page, log=logs.append)
    request=_BridgeRequest("snapshot_submit", {
        "post_id": "42", "tags": ("a", "c", "d"),
        "additions": ("d",), "removals": ("b",),
    }, 1)

    bridge._begin_request(request); page.loadFinished.emit(True)

    assert request.completed.is_set() and request.error is None
    assert request.result.tags_entries == 1
    assert request.result.diagnostic_only and request.result.post_blocked
    assert page.loads == ["https://gelbooru.com/index.php?page=post&s=view&id=42"]
    assert len(page.executed_scripts) == 2
    assert "form.requestSubmit" not in page.executed_scripts[-1]
    assert all("submit started" not in value for value in logs)
    line = next(value for value in logs if value.startswith(
        "Gelbooru embedded form snapshot:"
    ))
    assert "tags_entries=1" in line and "lupdated_present=true" in line
    assert "diagnostic_only=true" in line and "post_blocked=true" in line
    assert "csrf-token:hidden:present=true:length=" not in line


def test_bridge_rejects_an_embedded_diagnostic_snapshot_that_is_not_blocked():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page = FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({"status": "snapshot", "snapshot": form_snapshot_payload(
            diagnosticOnly=True, postBlocked=False,
        )}),
    ])
    bridge = EmbeddedGelbooruBridge(object(), page=page)
    request = _BridgeRequest("snapshot_submit", {"post_id": "42", "tags": ("a", "b")}, 1)

    bridge._begin_request(request); page.loadFinished.emit(True)

    assert request.completed.is_set() and isinstance(request.error, GelbooruTransportError)
    assert "form.requestSubmit" not in page.executed_scripts[-1]


def test_bridge_diagnostic_post_id_query_changes_only_the_loaded_url():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page=FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({"status": "snapshot", "snapshot": form_snapshot_payload()}),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("snapshot_submit", {
        "post_id": "42", "tags": ("a", "b"),
        "diagnostic_post_id_query": True,
    }, 1)

    bridge._begin_request(request); page.loadFinished.emit(True)

    assert request.completed.is_set() and request.error is None
    assert page.loads == [
        "https://gelbooru.com/index.php?page=post&s=view&id=42&tags=id%3A42"
    ]
    script = page.executed_scripts[-1]
    assert "tagsSearched.value" not in script
    assert "form.requestSubmit" not in script


def test_bridge_stops_on_delta_invariant_failure_before_clicking_save():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page=FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({
            "status": "prevented",
            "count": 2,
            "additionsMissing": [],
            "removalsStillPresent": [],
            "snapshot": form_snapshot_payload(prevented=True),
        }),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("submit", {"post_id": "42", "tags": ("a", "b")}, 1)

    bridge._begin_request(request); page.loadFinished.emit(True)

    assert request.completed.is_set()
    assert isinstance(request.error, GelbooruTransportError)
    assert "publish_payload_mismatch" in str(request.error)
    assert all("save.click()" not in script for script in page.executed_scripts)


def test_bridge_edit_redirect_to_login_is_session_expired_without_dom_script():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page=FakePage([]); bridge=EmbeddedGelbooruBridge(object(),page=page)
    request=_BridgeRequest("inspect_edit",{"post_id":"42"},1)
    bridge._begin_request(request)
    page.current_url=QUrl("https://gelbooru.com/index.php?page=account&s=login")
    page.loadFinished.emit(True)

    assert request.completed.is_set()
    assert isinstance(request.error, GelbooruSessionExpiredError)


def test_unknown_diagnostic_is_not_session_expired_and_log_is_safe():
    diagnostic=classify_session({
        "url":"https://gelbooru.com/index.php?page=account&s=home&token=secret",
        "challengeMarker":True,
    })
    assert diagnostic.state is SessionState.UNKNOWN
    assert "token" not in diagnostic.safe_log() and "secret" not in diagnostic.safe_log()


def test_bridge_submit_reuses_page_and_requires_final_expected_url():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    qt_app(); page=FakePage([
        edit_payload(
            [real_edit_form()],
            url="https://gelbooru.com/index.php?page=post&s=view&id=12",
        ),
        json.dumps({"status":"submitted"}),
        post_submit_payload(),
    ])
    logs=[]; bridge=EmbeddedGelbooruBridge(object(), page=page, log=logs.append)
    request=_BridgeRequest("submit",{"post_id":"12","tags":("a","b")},1)
    bridge._begin_request(request); page.loadFinished.emit(True)
    assert not request.completed.is_set()
    page.current_url=QUrl("https://gelbooru.com/index.php?page=post&s=view&id=12")
    page.loadFinished.emit(True)
    assert request.completed.is_set() and request.error is None
    assert len(page.executed_scripts) == 5
    assert "visible edit form ready" in "\n".join(logs)
    assert "Save clicked" in "\n".join(logs)
    assert all("requestSubmit" not in script and "form.submit" not in script
               for script in page.executed_scripts)
    assert "navigation finished url=https://gelbooru.com/index.php?page=post&s=view&id=12" in (
        "\n".join(logs)
    )
    assert "a b" not in "\n".join(logs)


def test_bridge_rate_limit_defers_the_real_save_click(monkeypatch):
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    deferred = []
    monkeypatch.setattr(
        "booruflow.infrastructure.embedded_gelbooru.QTimer.singleShot",
        lambda delay, callback: deferred.append((delay, callback)),
    )
    qt_app(); page = FakePage([
        edit_payload([real_edit_form()]), json.dumps({"status": "submitted"}),
    ])
    bridge = EmbeddedGelbooruBridge(
        object(), page=page, pre_save_delay_seconds=PUBLISH_DELAY_SECONDS
    )
    request = _BridgeRequest("submit", {
        "post_id": "42", "tags": ("a",), "additions": ("new",),
    }, 1)

    bridge._begin_request(request); page.loadFinished.emit(True)

    assert bridge._phase == "submit-rate-limit"
    assert deferred[0][0] >= 10_000
    assert all("save.click()" not in script for script in page.executed_scripts)
    deferred[0][1]()
    assert "save.click()" in page.executed_scripts[-1]


def test_bridge_rejects_global_post_list_redirect_before_dom_confirmation():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page = FakePage([
        edit_payload([real_edit_form()]), json.dumps({"status": "submitted"}),
    ])
    bridge = EmbeddedGelbooruBridge(object(), page=page)
    request = _BridgeRequest("submit", {"post_id": "42", "tags": ("a",)}, 1)
    bridge._begin_request(request); page.loadFinished.emit(True)
    page.current_url = QUrl(
        "https://gelbooru.com/index.php?page=post&s=list&tags=all"
    )
    page.loadFinished.emit(True)

    assert isinstance(request.error, GelbooruTransportError)
    assert "liste globale" in str(request.error)
    assert len(page.scripts) == 0


def test_bridge_prevents_double_submit_for_one_request():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    logs=[]; qt_app(); page=FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({"status":"submitted"}),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page, log=logs.append)
    request=_BridgeRequest("submit",{"post_id":"42","tags":("a",)},1)
    bridge._begin_request(request); page.loadFinished.emit(True)
    bridge._execute_submit(request.request_id)

    assert len(page.executed_scripts) == 4
    assert "duplicate submit prevented" in "\n".join(logs)


def test_bridge_arms_http_capture_before_preparing_delta_and_clicking_save():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    armed = []
    profile = SimpleNamespace(
        profile=object(),
        arm_http_diagnostic=lambda source, **values: (
            armed.append((source, values)) or True
        ),
    )
    qt_app(); page = FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({"status": "submitted"}),
    ])
    bridge = EmbeddedGelbooruBridge(profile, page=page)
    request = _BridgeRequest("submit", {
        "post_id": "42", "tags": ("a", "new_tag"),
        "additions": ("new_tag",), "removals": ("highres",),
        "http_diagnostic": True,
    }, 1)

    bridge._begin_request(request); page.loadFinished.emit(True)

    assert armed == [("embedded", {
        "page": page, "additions": ("new_tag",), "removals": ("highres",),
    })]
    assert "save.click()" in page.executed_scripts[-1]
    assert all("requestSubmit" not in script and "form.submit" not in script
               for script in page.executed_scripts)


def test_bridge_never_runs_submit_script_when_cdp_network_enable_is_not_confirmed():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    profile = SimpleNamespace(
        profile=object(),
        arm_http_diagnostic=lambda _source, **_values: False,
    )
    qt_app(); page = FakePage([edit_payload([real_edit_form()])])
    bridge = EmbeddedGelbooruBridge(profile, page=page)
    request = _BridgeRequest("submit", {
        "post_id": "42", "tags": ("a", "new_tag"),
        "additions": ("new_tag",), "removals": ("highres",),
        "http_diagnostic": True,
    }, 1)

    bridge._begin_request(request); page.loadFinished.emit(True)

    assert len(page.executed_scripts) == 2
    assert all("save.click()" not in script for script in page.executed_scripts)
    assert isinstance(request.error, GelbooruTransportError)
    assert "Aucun POST" in str(request.error)


def test_bridge_textarea_mismatch_stops_before_navigation_or_submit_confirmation():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page=FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({
            "status": "mismatch", "count": 3,
            "additionsMissing": ["d"], "removalsStillPresent": ["b"],
        }),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("submit",{
        "post_id":"42", "tags":("a","c","d"),
        "additions":("d",), "removals":("b",), "fresh_tags":("a","b","c"),
    },1)
    bridge._begin_request(request); page.loadFinished.emit(True)

    assert request.completed.is_set()
    assert isinstance(request.error, GelbooruTransportError)
    assert "publish_payload_mismatch" in str(request.error)
    assert page.loads == ["https://gelbooru.com/index.php?page=post&s=view&id=42"]


def test_bridge_logs_successful_delta_invariants_without_tag_values():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    final_url="https://gelbooru.com/index.php?page=post&s=view&id=42"
    logs=[]; qt_app(); page=FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({
            "status": "submitted", "count": 3,
            "additionsMissing": [], "removalsStillPresent": [],
        }),
        post_submit_payload(final_url),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page, log=logs.append)
    request=_BridgeRequest("submit",{
        "post_id":"42", "tags":("a","c","d"),
        "additions":("d",), "removals":("highres","irene_(arknights)"),
        "fresh_tags":("a","c","highres","irene_(arknights)"),
    },1)
    bridge._begin_request(request); page.loadFinished.emit(True)
    page.current_url=QUrl(final_url); page.loadFinished.emit(True)

    joined="\n".join(logs)
    assert request.error is None and request.completed.is_set()
    assert "delta_check tag_count=3 additions_present=true removals_absent=true unrelated_preserved=true" in joined
    assert "highres" not in joined and "irene_(arknights)" not in joined


@pytest.mark.parametrize("final_url", [
    "https://gelbooru.com/index.php?page=post&s=view&id=99",
    "https://example.com/index.php?page=post&s=view&id=12",
    "http://gelbooru.com/index.php?page=post&s=view&id=12",
])
def test_bridge_rejects_wrong_final_post_host_or_scheme(final_url):
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page=FakePage([
        edit_payload([real_edit_form()], url=(
            "https://gelbooru.com/index.php?page=post&s=view&id=12"
        )),
        json.dumps({"status":"submitted"}),
        post_submit_payload(final_url),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("submit",{"post_id":"12","tags":("a",)},1)
    bridge._begin_request(request); page.loadFinished.emit(True)
    page.current_url=QUrl(final_url); page.loadFinished.emit(True)

    assert request.completed.is_set()
    assert isinstance(request.error, GelbooruTransportError)


@pytest.mark.parametrize("overrides,error_type", [
    ({"loginForm": True}, GelbooruSessionExpiredError),
    ({"loggedOutText": True}, GelbooruSessionExpiredError),
    ({"challengeMarker": True}, GelbooruSessionUnknownError),
    ({"accountPage": True, "logoutMarker": False}, GelbooruSessionUnknownError),
])
def test_bridge_post_submit_session_loss_pauses_as_retryable(overrides, error_type):
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    final_url="https://gelbooru.com/index.php?page=post&s=view&id=12"
    qt_app(); page=FakePage([
        edit_payload([real_edit_form()], url=final_url),
        json.dumps({"status":"submitted"}),
        post_submit_payload(final_url, **overrides),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("submit",{"post_id":"12","tags":("a",)},1)
    bridge._begin_request(request); page.loadFinished.emit(True)
    page.current_url=QUrl(final_url); page.loadFinished.emit(True)

    assert request.completed.is_set()
    assert isinstance(request.error, error_type)


def test_bridge_post_submit_login_redirect_is_session_expired():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    final_url="https://gelbooru.com/index.php?page=account&s=login"
    qt_app(); page=FakePage([
        edit_payload([real_edit_form()], url=(
            "https://gelbooru.com/index.php?page=post&s=view&id=12"
        )),
        json.dumps({"status":"submitted"}),
        post_submit_payload(final_url),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("submit",{"post_id":"12","tags":("a",)},1)
    bridge._begin_request(request); page.loadFinished.emit(True)
    page.current_url=QUrl(final_url); page.loadFinished.emit(True)

    assert isinstance(request.error, GelbooruSessionExpiredError)


def test_cancel_after_submit_waits_for_final_observation_instead_of_stopping_page():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    final_url="https://gelbooru.com/index.php?page=post&s=view&id=12"
    qt_app(); page=FakePage([
        edit_payload([real_edit_form()], url=final_url),
        json.dumps({"status":"submitted"}),
        post_submit_payload(final_url),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("submit",{"post_id":"12","tags":("a",)},1)
    bridge._begin_request(request); page.loadFinished.emit(True)

    bridge.cancel()

    assert not request.completed.is_set()
    assert page.stopped is False
    page.current_url=QUrl(final_url); page.loadFinished.emit(True)
    assert request.completed.is_set() and request.error is None


def test_bridge_submit_navigation_timeout_never_reports_success():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page=FakePage([
        edit_payload([real_edit_form()]),
        json.dumps({"status":"submitted"}),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    request=_BridgeRequest("submit",{"post_id":"42","tags":("a",)},1)
    bridge._begin_request(request); page.loadFinished.emit(True)

    bridge._timed_out()

    assert request.completed.is_set()
    assert isinstance(request.error, GelbooruTransportError)
    assert "navigation finale" in str(request.error)


def test_bridge_reuses_one_page_for_multiple_posts():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest

    qt_app(); page=FakePage([
        edit_payload([real_edit_form()], url=(
            "https://gelbooru.com/index.php?page=post&s=view&id=12"
        )),
        json.dumps({"status":"submitted"}),
        post_submit_payload(),
        edit_payload([real_edit_form()], url=(
            "https://gelbooru.com/index.php?page=post&s=view&id=13"
        )),
        json.dumps({"status":"submitted"}),
        post_submit_payload("https://gelbooru.com/index.php?page=post&s=view&id=13"),
    ])
    bridge=EmbeddedGelbooruBridge(object(), page=page)
    first=_BridgeRequest("submit",{"post_id":"12","tags":("a",)},1)
    bridge._begin_request(first); page.loadFinished.emit(True)
    page.current_url=QUrl("https://gelbooru.com/index.php?page=post&s=view&id=12")
    page.loadFinished.emit(True)
    second=_BridgeRequest("submit",{"post_id":"13","tags":("b",)},1)
    bridge._begin_request(second); page.loadFinished.emit(True)
    page.current_url=QUrl("https://gelbooru.com/index.php?page=post&s=view&id=13")
    page.loadFinished.emit(True)

    assert first.error is None and second.error is None
    assert first.completed.is_set() and second.completed.is_set()
    assert page.loads == [
        "https://gelbooru.com/index.php?page=post&s=view&id=12",
        "https://gelbooru.com/index.php?page=post&s=view&id=13",
    ]


def test_bridge_load_error_timeout_and_cancel_finish_request():
    from booruflow.infrastructure.embedded_gelbooru import EmbeddedGelbooruBridge, _BridgeRequest
    qt_app()
    for action in ("load", "timeout", "cancel"):
        page=FakePage([]); bridge=EmbeddedGelbooruBridge(object(), page=page)
        request=_BridgeRequest("validate",{},1); bridge._begin_request(request)
        if action == "load": page.loadFinished.emit(False)
        elif action == "timeout": bridge._timed_out()
        else: bridge.cancel()
        assert request.completed.is_set() and isinstance(request.error, GelbooruTransportError)


def test_profile_create_page_always_uses_the_same_logical_webengine_profile(monkeypatch, tmp_path):
    import booruflow.infrastructure.embedded_gelbooru as module
    created=[]
    class Policy: ForcePersistentCookies="persistent"
    class Profile:
        PersistentCookiesPolicy=Policy
        def __init__(self,*_args): pass
        def setPersistentStoragePath(self,_value): pass
        def setCachePath(self,_value): pass
        def setPersistentCookiesPolicy(self,_value): pass
        def setUrlRequestInterceptor(self, value): self.interceptor=value
    monkeypatch.setattr(module,"QWebEngineProfile",Profile)
    monkeypatch.setattr(module,"QWebEnginePage",lambda profile,parent=None: created.append(profile) or object())
    owner=module.EmbeddedGelbooruProfile(tmp_path)
    owner.create_page(); owner.create_page()
    assert created == [owner.profile, owner.profile]
