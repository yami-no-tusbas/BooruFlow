import json
from unittest.mock import MagicMock, patch

from booruflow.infrastructure import gelbooru_client


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()
        self.headers = MagicMock()
        self.headers.get_content_charset.return_value = "utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.payload


def test_fetch_result_count_requests_one_witness_post_without_mutating_state():
    payload = {"@attributes": {"count": "42"}, "post": {"id": 123}}
    with patch.object(
        gelbooru_client.urllib.request,
        "urlopen",
        return_value=FakeResponse(payload),
    ) as urlopen:
        count, posts = gelbooru_client.fetch_result_count("blue_hair solo", "7", "secret")

    request = urlopen.call_args.args[0]
    assert "limit=1" in request.full_url
    assert "pid=0" in request.full_url
    assert "user_id=7" in request.full_url
    assert "api_key=secret" in request.full_url
    assert count == 42
    assert posts == [{"id": 123}]


def test_normalize_posts_accepts_list_single_post_and_empty_shapes():
    assert gelbooru_client.normalize_posts([{"id": 1}, "invalid"]) == [{"id": 1}]
    assert gelbooru_client.normalize_posts({"post": {"id": 2}}) == [{"id": 2}]
    assert gelbooru_client.normalize_posts({"post": []}) == []
