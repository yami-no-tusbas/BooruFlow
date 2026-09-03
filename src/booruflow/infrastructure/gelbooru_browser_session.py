"""Deprecated cookie-extraction experiment; not used by normal publication.

Kept temporarily for migration history only.  Browser publication uses CDP and
never reads Chromium's Cookies database.
"""

from __future__ import annotations

import base64
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

from booruflow.infrastructure.browser_launcher import DEDICATED_PROFILE, BrowserLauncher
from booruflow.infrastructure.gelbooru_edit_transport import UrllibGelbooruAuthenticatedSession


class BrowserSessionUnavailable(RuntimeError):
    """The configured dedicated browser profile cannot safely provide a session."""


class ChromiumSessionFactory:
    """Factory for BooruFlow's own Chromium-family dedicated profiles only.

    It makes a temporary read-only copy of the Cookies DB, filters strictly to
    Gelbooru, and keeps the resulting jar in memory for one publication run.
    """

    supported_browsers = frozenset({"brave", "chrome", "chromium", "edge"})

    def __init__(
        self,
        launcher: BrowserLauncher,
        *,
        decrypt: Callable[[bytes, Path], bytes] | None = None,
        session_type=UrllibGelbooruAuthenticatedSession,
    ) -> None:
        self.launcher = launcher
        self.decrypt = decrypt or decrypt_chromium_cookie
        self.session_type = session_type

    def create(self):
        if self.launcher.settings.mode != DEDICATED_PROFILE:
            raise BrowserSessionUnavailable(
                "Une session navigateur dédiée/configurée est nécessaire pour la publication automatique."
            )
        browser = self.launcher.detector.detect()
        if browser is None or browser.browser_id not in self.supported_browsers:
            raise BrowserSessionUnavailable(
                "Le navigateur dédié configuré n'est pas un Chromium pris en charge."
            )
        profile = self.launcher.profile_dir(browser.browser_id)
        jar = load_chromium_gelbooru_cookies(profile, self.decrypt)
        if not list(jar):
            raise BrowserSessionUnavailable(
                "Aucun cookie Gelbooru exploitable dans le profil dédié."
            )
        return self.session_type(jar)

    def validate(self) -> None:
        session = self.create()
        session.validate_authenticated()


def load_chromium_gelbooru_cookies(
    profile: Path, decrypt: Callable[[bytes, Path], bytes]
) -> CookieJar:
    """Load Gelbooru cookies from a temporary read-only database copy."""
    cookie_db = next(
        (
            candidate
            for candidate in (
                profile / "Default" / "Network" / "Cookies",
                profile / "Default" / "Cookies",
            )
            if candidate.is_file()
        ),
        None,
    )
    if cookie_db is None:
        raise BrowserSessionUnavailable(
            "Profil Chromium dédié introuvable ou aucun fichier Cookies disponible."
        )
    with tempfile.TemporaryDirectory(prefix="booruflow-cookies-") as temporary:
        copied = Path(temporary) / "Cookies"
        try:
            shutil.copy2(cookie_db, copied)
            connection = sqlite3.connect(f"file:{copied.as_posix()}?mode=ro", uri=True)
            rows = connection.execute(
                "SELECT host_key,name,value,encrypted_value,path,is_secure,expires_utc FROM cookies WHERE host_key LIKE ?",
                ("%gelbooru.com",),
            ).fetchall()
            connection.close()
        except (OSError, sqlite3.Error) as exc:
            raise BrowserSessionUnavailable(
                "Impossible de lire sans risque les cookies du profil dédié."
            ) from exc
    jar = CookieJar()
    for host, name, value, encrypted, path, secure, expires in rows:
        try:
            content = str(value) if value else decrypt(bytes(encrypted), profile).decode("utf-8")
        except (OSError, ValueError, RuntimeError) as exc:
            raise BrowserSessionUnavailable(
                "Les cookies Chromium sont chiffrés et ne peuvent pas être déverrouillés dans cette session."
            ) from exc
        if content:
            jar.set_cookie(
                _cookie(
                    str(host), str(name), content, str(path or "/"), bool(secure), int(expires or 0)
                )
            )
    return jar


def _cookie(host: str, name: str, value: str, path: str, secure: bool, expires: int) -> Cookie:
    expiry = (
        None
        if not expires
        else int((datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=expires)).timestamp())
    )
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        host,
        host.startswith("."),
        host.startswith("."),
        path,
        True,
        secure,
        expiry,
        False,
        None,
        None,
        {},
        False,
    )


def decrypt_chromium_cookie(value: bytes, profile: Path) -> bytes:
    """Decrypt classic Chromium cookies; newer app-bound encryption is rejected safely."""
    if not value:
        return b""
    if value.startswith(b"v20"):
        raise RuntimeError("Chromium app-bound encryption is not supported")
    if value.startswith((b"v10", b"v11")):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise RuntimeError(
                "Le module cryptography est requis pour les cookies Chromium chiffrés"
            ) from exc
        key = _chromium_master_key(profile)
        return AESGCM(key).decrypt(value[3:15], value[15:], None)
    return _dpapi_unprotect(value)


def _chromium_master_key(profile: Path) -> bytes:
    try:
        local_state = json.loads((profile / "Local State").read_text(encoding="utf-8"))
        encrypted = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("La clé Chromium du profil dédié est indisponible") from exc
    return _dpapi_unprotect(encrypted[5:] if encrypted.startswith(b"DPAPI") else encrypted)


def _dpapi_unprotect(value: bytes) -> bytes:
    if __import__("os").name != "nt":
        raise RuntimeError("Le déchiffrement Chromium est pris en charge uniquement sous Windows")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    input_buffer = ctypes.create_string_buffer(value)
    output = Blob()
    input_blob = Blob(len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)))
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise OSError("DPAPI could not decrypt Chromium data")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
