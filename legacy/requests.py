"""Petit sous-ensemble compatible de requests, basé sur urllib.

Il existe uniquement pour le collecteur Gelbooru historique afin que
l'application reste autonome sans installation pip.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


JSONDecodeError = json.JSONDecodeError


class RequestException(Exception):
    pass


class Timeout(RequestException):
    pass


class ConnectionError(RequestException):
    pass


class Response:
    def __init__(self, status_code: int, content: bytes, headers: Any) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers
        charset = headers.get_content_charset() if headers else None
        self.text = content.decode(charset or "utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RequestException(f"Erreur HTTP {self.status_code}")


class Session:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Response:
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Response(
                    int(getattr(response, "status", 200)),
                    response.read(),
                    response.headers,
                )
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read(), error.headers)
        except (socket.timeout, TimeoutError) as error:
            raise Timeout(str(error)) from error
        except urllib.error.URLError as error:
            raise ConnectionError(str(error)) from error
