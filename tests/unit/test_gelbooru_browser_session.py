import sqlite3
from pathlib import Path

import pytest

from booruflow.infrastructure.browser_launcher import (
    DEDICATED_PROFILE,
    BrowserLaunchSettings,
    DetectedBrowser,
)
from booruflow.infrastructure.gelbooru_browser_session import (
    BrowserSessionUnavailable,
    ChromiumSessionFactory,
    load_chromium_gelbooru_cookies,
)


def write_cookie_database(profile: Path, *, value: str = "session-value") -> None:
    path = profile / "Default" / "Network"; path.mkdir(parents=True)
    with sqlite3.connect(path / "Cookies") as connection:
        connection.execute("CREATE TABLE cookies(host_key,name,value,encrypted_value,path,is_secure,expires_utc)")
        connection.execute("INSERT INTO cookies VALUES(?,?,?,?,?,?,?)", (".gelbooru.com", "PHPSESSID", value, b"", "/", 1, 0))
        connection.execute("INSERT INTO cookies VALUES(?,?,?,?,?,?,?)", (".example.com", "other", "hidden", b"", "/", 1, 0))


class Launcher:
    def __init__(self, root: Path, mode: str = DEDICATED_PROFILE, browser_id: str = "chrome"):
        self.settings = BrowserLaunchSettings(mode=mode)
        browser = DetectedBrowser(browser_id, browser_id, Path("browser.exe"))
        self.detector = type("Detector", (), {"detect": lambda _self: browser})()
        self.root = root
    def profile_dir(self, browser_id: str) -> Path: return self.root / f"gelbooru-{browser_id}"


def test_dedicated_chromium_factory_filters_to_gelbooru_and_validates(tmp_path: Path):
    launcher = Launcher(tmp_path); write_cookie_database(launcher.profile_dir("chrome"))
    captured = {}
    class Session:
        def __init__(self, jar): captured["cookies"] = list(jar)
        def validate_authenticated(self): captured["valid"] = True
    factory = ChromiumSessionFactory(launcher, session_type=Session)
    factory.validate()
    assert captured["valid"] and [(cookie.domain, cookie.name) for cookie in captured["cookies"]] == [(".gelbooru.com", "PHPSESSID")]


@pytest.mark.parametrize("mode,browser_id", [("system", "chrome"), (DEDICATED_PROFILE, "firefox")])
def test_factory_refuses_non_dedicated_or_unsupported_browser(tmp_path: Path, mode: str, browser_id: str):
    with pytest.raises(BrowserSessionUnavailable):
        ChromiumSessionFactory(Launcher(tmp_path, mode, browser_id)).create()


def test_missing_or_unreadable_profile_is_a_safe_error(tmp_path: Path):
    with pytest.raises(BrowserSessionUnavailable, match="Cookies"):
        ChromiumSessionFactory(Launcher(tmp_path)).create()


def test_active_profile_copy_and_encrypted_cookie_failure_are_explicit(tmp_path: Path):
    profile = tmp_path / "profile"; write_cookie_database(profile, value="")
    with sqlite3.connect(profile / "Default" / "Network" / "Cookies") as connection:
        connection.execute("UPDATE cookies SET encrypted_value=? WHERE host_key LIKE ?", (b"v20opaque", "%gelbooru.com"))
    with pytest.raises(BrowserSessionUnavailable, match="chiffrés"):
        load_chromium_gelbooru_cookies(profile, lambda _value, _profile: (_ for _ in ()).throw(RuntimeError("locked")))
