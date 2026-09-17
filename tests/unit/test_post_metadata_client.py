from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from booruflow.infrastructure import post_metadata_client as client


class Response:
    def __init__(self,payload): self.payload=json.dumps(payload).encode(); self.headers=MagicMock(); self.headers.get_content_charset.return_value="utf-8"
    def __enter__(self): return self
    def __exit__(self,*_args): return None
    def read(self): return self.payload

def test_gelbooru_read_uses_dapi_and_configured_credentials():
    payload={"post":[{"id":9490613,"tags":"one two","md5":"abc","rating":"sensitive"}]}
    with patch.object(client.urllib.request,"urlopen",return_value=Response(payload)) as opened:
        post=client.fetch_post("gelbooru","9490613","7","secret")
    request=opened.call_args.args[0]; assert "page=dapi" in request.full_url and "s=post" in request.full_url
    assert "user_id=7" in request.full_url and "api_key=secret" in request.full_url and post.post_id=="9490613"

def test_http_error_exposes_diagnostic_but_redacts_credentials():
    error=urllib.error.HTTPError("https://gelbooru.com/index.php",401,"Unauthorized",{},None)
    with patch.object(client.urllib.request,"urlopen",side_effect=error),pytest.raises(client.MetadataFetchError) as caught:
        client.fetch_post("gelbooru","9490613","7","secret")
    failure=caught.value.failure; assert failure.status==401 and failure.stage=="remote_fetch"
    assert "api_key" not in failure.endpoint and "user_id" not in failure.endpoint and "id%3A9490613" in failure.endpoint

def test_e621_user_agent_and_response_shape():
    payload={"post":{"id":8,"rating":"s","tags":{"artist":["artist"],"species":["wolf"]},"file":{"md5":"abc"},"sources":[]}}
    with patch.object(client.urllib.request,"urlopen",return_value=Response(payload)) as opened:
        post=client.fetch_post("e621","8")
    request=opened.call_args.args[0]; assert request.full_url=="https://e621.net/posts/8.json"
    assert request.headers["User-agent"].startswith("BooruFlow/") and post.artists==("artist",) and post.species==("wolf",)

def test_invalid_json_is_classified_as_response_parsing():
    response=Response({}); response.payload=b"<html>blocked</html>"
    with patch.object(client.urllib.request,"urlopen",return_value=response),pytest.raises(client.MetadataFetchError) as caught:
        client.fetch_post("e621","8")
    assert caught.value.failure.stage=="response_parsing"

def test_http_404_is_normal_post_not_found_not_infrastructure():
    error=urllib.error.HTTPError("https://e621.net/posts/8.json",404,"Not Found",{},None)
    with patch.object(client.urllib.request,"urlopen",side_effect=error),pytest.raises(client.PostNotFoundError):
        client.fetch_post("e621","8")
