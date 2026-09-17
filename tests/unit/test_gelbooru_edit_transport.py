import pytest

from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruEditTransport,
    GelbooruSessionExpiredError,
    GelbooruTransportError,
    HttpResponse,
    UrllibGelbooruAuthenticatedSession,
)


class FakeSession:
    def __init__(self, response, form=None):
        self.response = response; self.form = form or {"rating": "e", "title": "old", "source": "src", "csrf": "token", "tags": "old"}; self.calls = []
    def read_edit_form(self, post_id): return self.form
    def post_urlencoded(self, url, fields, *, follow_redirects):
        self.calls.append((url, dict(fields), follow_redirects)); return self.response


def response(status=302, location="../index.php?page=post&s=view&id=123"):
    return HttpResponse(status, {"Location": location})


def test_submit_preserves_current_form_and_replaces_only_tags():
    session = FakeSession(response())
    GelbooruEditTransport().submit(session, "123", ("a", "new_tag"))
    url, fields, redirects = session.calls[0]
    assert url.endswith("/public/edit_post.php") and not redirects
    assert fields == {"rating": "e", "title": "old", "source": "src", "csrf": "token", "tags": "a new_tag"}


@pytest.mark.parametrize("result", [response(200), response(302, "../index.php?page=post&s=view&id=999")])
def test_submit_rejects_non_confirming_response(result):
    with pytest.raises(GelbooruTransportError):
        GelbooruEditTransport().submit(FakeSession(result), "123", ("a",))


@pytest.mark.parametrize("result", [response(401), response(403), response(302, "../index.php?page=account&s=login")])
def test_submit_detects_expired_session(result):
    with pytest.raises(GelbooruSessionExpiredError):
        GelbooruEditTransport().submit(FakeSession(result), "123", ("a",))


def test_network_error_is_not_success():
    class Broken(FakeSession):
        def post_urlencoded(self, *_args, **_kwargs): raise OSError("offline")
    with pytest.raises(GelbooruTransportError, match="network"):
        GelbooruEditTransport().submit(Broken(response()), "123", ("a",))


def test_concrete_session_encodes_form_with_required_content_type():
    class Response:
        def __init__(self):
            self.headers = {"Location": "../index.php?page=post&s=view&id=123"}
        def getcode(self): return 302
    class Opener:
        def __init__(self): self.request = None
        def open(self, request): self.request = request; return Response()
    opener = Opener()
    session = UrllibGelbooruAuthenticatedSession(object(), opener=opener)
    result = session.post_urlencoded("https://gelbooru.com/public/edit_post.php", {"tags": "a b", "source": "x"}, follow_redirects=False)
    assert result.status == 302
    assert opener.request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert opener.request.data == b"tags=a+b&source=x"
