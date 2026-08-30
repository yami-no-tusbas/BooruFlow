from types import SimpleNamespace

import pytest

from booruflow.infrastructure.gelbooru_http_diagnostic import (
    GelbooruEditRequestInterceptor,
    analyze_urlencoded_edit_request,
    is_gelbooru_edit_request,
)


@pytest.mark.parametrize(
    "method,url,expected",
    [
        ("POST", "https://gelbooru.com/public/edit_post.php", True),
        ("post", "https://gelbooru.com:443/public/edit_post.php", True),
        ("GET", "https://gelbooru.com/public/edit_post.php", False),
        ("POST", "http://gelbooru.com/public/edit_post.php", False),
        ("POST", "https://evil.example/public/edit_post.php", False),
        ("POST", "https://gelbooru.com/public/add_comment.php", False),
        ("POST", "https://gelbooru.com.evil.example/public/edit_post.php", False),
    ],
)
def test_filter_matches_only_the_exact_gelbooru_edit_post(method, url, expected):
    assert is_gelbooru_edit_request(method, url) is expected


def test_urlencoded_body_is_reduced_to_safe_structure_and_target_booleans():
    body = (
        b"rating=q&title=&source=https%3A%2F%2Fexample.test&"
        b"tags=alpha+irene_%28arknights%29+alpha&tagsSearched=&id=14772833&"
        b"uid=secret_uid&uname=secret_name&lupdated=secret_version&"
        b"csrf-token=secret_csrf&submit=Save+changes"
    )

    snapshot = analyze_urlencoded_edit_request(
        source="manual",
        method="POST",
        url="https://gelbooru.com/public/edit_post.php",
        content_type="application/x-www-form-urlencoded; charset=UTF-8",
        body=body,
        additions=("alpha", "new_tag"),
        removals=("irene_(arknights)", "highres"),
    )

    assert snapshot.tags_entries == 1
    assert snapshot.tag_count == 3
    assert snapshot.duplicate_tag_count == 1
    assert snapshot.additions_present == (True, False)
    assert snapshot.removals_present == (True, False)
    assert snapshot.plus_count == 2
    assert snapshot.percent28_count == 1
    assert snapshot.percent29_count == 1
    assert snapshot.underscore_count == 1
    assert snapshot.content_type == "application/x-www-form-urlencoded"
    assert snapshot.parse_status == "ok"
    safe = snapshot.safe_log()
    for forbidden in (
        "secret_uid", "secret_name", "secret_version", "secret_csrf",
        "csrf-token", "uid", "uname", "lupdated", "irene_(arknights)",
    ):
        assert forbidden not in safe
    assert safe.count("[redacted]") == 4


def test_multiple_tags_entries_and_whitespace_encodings_are_detected():
    body = b"tags=first%20tag%0D%0Athird&tags=fourth_tag&submit=Save"

    snapshot = analyze_urlencoded_edit_request(
        source="embedded",
        method="POST",
        url="https://gelbooru.com/public/edit_post.php",
        content_type="application/x-www-form-urlencoded",
        body=body,
    )

    assert snapshot.tags_entries == 2
    assert snapshot.tag_count == 4
    assert snapshot.percent20_count == 1
    assert snapshot.encoded_crlf_count == 1
    assert snapshot.underscore_count == 1


class FakeBody:
    def __init__(self, value: bytes):
        self.value = value
        self.peek_calls = 0
        self.read_calls = 0

    def peek(self, size: int):
        self.peek_calls += 1
        return self.value[:size]

    def readAll(self):  # pragma: no cover - proves the interceptor never consumes it
        self.read_calls += 1
        return self.value


class FakeInfo:
    def __init__(self, *, url: str, body: bytes, headers=None):
        self.url = url
        self.body = FakeBody(body)
        self.headers = headers or {}
        self.block_calls = []
        self.request_body_calls = 0

    def requestMethod(self):
        return b"POST"

    def requestUrl(self):
        return SimpleNamespace(toString=lambda: self.url)

    def httpHeaders(self):
        return self.headers

    def requestBody(self):
        self.request_body_calls += 1
        return self.body

    def resourceType(self):
        return SimpleNamespace(name="ResourceTypeMainFrame")

    def navigationType(self):
        return SimpleNamespace(name="NavigationTypeFormSubmitted")

    def block(self, value):
        self.block_calls.append(value)


def test_inactive_interceptor_does_not_read_or_change_a_normal_request():
    info = FakeInfo(
        url="https://gelbooru.com/public/edit_post.php",
        body=b"tags=alpha",
    )
    interceptor = GelbooruEditRequestInterceptor()
    captured = []
    interceptor.captured.connect(captured.append)

    interceptor.interceptRequest(info)

    assert captured == []
    assert info.request_body_calls == 0
    assert info.body.peek_calls == 0
    assert info.body.read_calls == 0
    assert info.block_calls == []


def test_armed_interceptor_ignores_other_posts_and_captures_one_target_request():
    other = FakeInfo(
        url="https://gelbooru.com/public/add_comment.php",
        body=b"comment=secret",
    )
    target = FakeInfo(
        url="https://gelbooru.com/public/edit_post.php",
        body=b"tags=alpha+new_tag&id=1&csrf-token=secret&uid=secret&uname=secret",
        headers={
            b"Content-Type": b"application/x-www-form-urlencoded",
            b"Cookie": b"session=must_never_escape",
        },
    )
    interceptor = GelbooruEditRequestInterceptor()
    captured = []
    finished = []
    interceptor.captured.connect(captured.append)
    interceptor.finished.connect(finished.append)
    interceptor.arm("embedded", additions=("new_tag",), removals=("highres",))

    interceptor.interceptRequest(other)
    interceptor.interceptRequest(target)
    interceptor.interceptRequest(target)

    assert other.body.peek_calls == 0
    assert target.request_body_calls == 0
    assert target.body.peek_calls == 0
    assert target.body.read_calls == 0
    assert target.block_calls == []
    assert len(captured) == 1
    assert finished == ["embedded"]
    assert "post_initiated=true" in captured[0]
    assert "body=unavailable_by_design" in captured[0]
    assert "resource_type=ResourceTypeMainFrame" in captured[0]
    assert "navigation_type=NavigationTypeFormSubmitted" in captured[0]
    for forbidden in (
        "secret", "Cookie", "session", "csrf-token", "uid", "uname",
        "new_tag", "highres", "body_length", "tags_entries",
    ):
        assert forbidden not in captured[0]


def test_capture_failure_is_redacted_and_never_changes_or_raises_from_request():
    info = FakeInfo(
        url="https://gelbooru.com/public/edit_post.php",
        body=b"tags=secret_tag",
    )
    info.resourceType = lambda: (_ for _ in ()).throw(RuntimeError("secret metadata"))
    interceptor = GelbooruEditRequestInterceptor()
    captured = []
    finished = []
    interceptor.captured.connect(captured.append)
    interceptor.finished.connect(finished.append)
    interceptor.arm("manual", removals=("secret_tag",))

    interceptor.interceptRequest(info)

    assert captured == [
        "Gelbooru outgoing edit request: source=manual capture_error=RuntimeError"
    ]
    assert finished == ["manual"]
    assert info.block_calls == []
