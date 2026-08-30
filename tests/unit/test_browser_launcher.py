from pathlib import Path

from booruflow.infrastructure.browser_launcher import (
    CUSTOM_COMMAND,
    DEDICATED_PROFILE,
    BrowserDetector,
    BrowserLauncher,
    BrowserLaunchSettings,
)
from booruflow.infrastructure.settings import JsonSettingsRepository


def test_linux_detection_uses_documented_order() -> None:
    paths = {
        "google-chrome": "/opt/chrome",
        "chromium": "/usr/bin/chromium",
        "microsoft-edge": "/opt/edge",
    }
    detected = BrowserDetector(platform="posix", which=paths.get).detect_all()
    assert [browser.browser_id for browser in detected] == ["chrome", "chromium", "edge"]


def test_windows_detection_prefers_brave_then_chrome(tmp_path: Path) -> None:
    brave = tmp_path / "pf" / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
    chrome = tmp_path / "local" / "Google" / "Chrome" / "Application" / "chrome.exe"
    brave.parent.mkdir(parents=True); brave.touch()
    chrome.parent.mkdir(parents=True); chrome.touch()
    detector = BrowserDetector(platform="nt", environ={
        "PROGRAMFILES": str(tmp_path / "pf"),
        "PROGRAMFILES(X86)": str(tmp_path / "x86"),
        "LOCALAPPDATA": str(tmp_path / "local"),
    })
    assert [browser.browser_id for browser in detector.detect_all()] == ["brave", "chrome"]


def test_chromium_command_has_isolated_profile_and_expected_flags(tmp_path: Path) -> None:
    browser = BrowserDetector(platform="posix", which=lambda _name: "/usr/bin/brave").detect()
    launcher = BrowserLauncher(tmp_path, {"gelbooru_browser_mode": DEDICATED_PROFILE})
    command = launcher.chromium_command(browser, "https://gelbooru.com/post/1")
    assert command[0:2] == [
        str(Path("/usr/bin/brave")),
        f"--user-data-dir={tmp_path / 'BrowserProfiles' / 'gelbooru-brave'}",
    ]
    assert "--remote-debugging-address=127.0.0.1" in command
    assert any(flag.startswith("--remote-debugging-port=") for flag in command)
    assert command[-3:] == ["--disable-sync", "--new-window", "https://gelbooru.com/post/1"]
    assert "--disable-extensions" not in command


def test_dedicated_profile_reuses_session_without_forcing_a_new_window(tmp_path: Path) -> None:
    browser = BrowserDetector(platform="posix", which=lambda _name: "/usr/bin/brave").detect()
    commands = []
    launcher = BrowserLauncher(
        tmp_path,
        {"gelbooru_browser_mode": DEDICATED_PROFILE},
        detector=type("Detector", (), {"detect": lambda _self: browser})(),
        process_runner=lambda command: commands.append(list(command)),
    )

    assert launcher.open("https://gelbooru.com/post/1") is True
    assert launcher.open("https://gelbooru.com/post/2") is True

    profile_flag = f"--user-data-dir={tmp_path / 'BrowserProfiles' / 'gelbooru-brave'}"
    assert commands[0][0:2] == [str(Path("/usr/bin/brave")), profile_flag]
    assert "--remote-debugging-address=127.0.0.1" in commands[0]
    assert any(flag.startswith("--remote-debugging-port=") for flag in commands[0])
    assert commands[0][-3:] == ["--disable-sync", "--new-window", "https://gelbooru.com/post/1"]
    assert commands[1][0:2] == [str(Path("/usr/bin/brave")), profile_flag]
    assert "--remote-debugging-address=127.0.0.1" in commands[1]
    assert commands[1][-2:] == ["--disable-sync", "https://gelbooru.com/post/2"]
    assert "--new-window" not in commands[1]


def test_system_default_keeps_using_the_system_opener(tmp_path: Path) -> None:
    opened = []
    launcher = BrowserLauncher(tmp_path, default_opener=lambda url: opened.append(url) or True)
    assert launcher.open("https://gelbooru.com/post/1") is True
    assert launcher.open("https://gelbooru.com/post/2") is True
    assert opened == ["https://gelbooru.com/post/1", "https://gelbooru.com/post/2"]


def test_custom_command_requires_and_replaces_url(tmp_path: Path) -> None:
    valid = BrowserLauncher(tmp_path, BrowserLaunchSettings(CUSTOM_COMMAND, 'tor "{url}"'))
    assert valid.custom_command("https://gelbooru.com/?x=1") == ["tor", "https://gelbooru.com/?x=1"]
    invalid = BrowserLauncher(tmp_path, BrowserLaunchSettings(CUSTOM_COMMAND, "tor"))
    assert invalid.custom_command("https://gelbooru.com/") is None


def test_invalid_custom_command_falls_back_to_default(tmp_path: Path) -> None:
    opened = []
    launcher = BrowserLauncher(
        tmp_path,
        BrowserLaunchSettings(CUSTOM_COMMAND, "tor"),
        default_opener=lambda url: opened.append(url) or True,
    )
    assert launcher.open("https://gelbooru.com/") is True
    assert opened == ["https://gelbooru.com/"]


def test_strict_dedicated_launch_never_falls_back_to_regular_browser(tmp_path: Path) -> None:
    opened = []
    launcher = BrowserLauncher(
        tmp_path,
        {"gelbooru_browser_mode": DEDICATED_PROFILE},
        detector=type("Detector", (), {"detect": lambda _self: None})(),
        default_opener=lambda url: opened.append(url) or True,
    )

    assert launcher.open("https://gelbooru.com/", allow_default_fallback=False) is False
    assert opened == []


def test_publication_dedicated_session_is_independent_and_started_once(tmp_path: Path) -> None:
    browser = BrowserDetector(platform="posix", which=lambda _name: "/usr/bin/brave").detect()
    commands = []
    opened = []
    launcher = BrowserLauncher(
        tmp_path,
        {"gelbooru_browser_mode": "system"},
        detector=type("Detector", (), {"detect": lambda _self: browser})(),
        process_runner=lambda command: commands.append(list(command)),
        default_opener=lambda url: opened.append(url) or True,
    )

    assert launcher.ensure_dedicated("https://gelbooru.com/") is True
    assert launcher.ensure_dedicated("https://gelbooru.com/") is True
    assert len(commands) == 1
    assert "--remote-debugging-address=127.0.0.1" in commands[0]
    assert launcher.debugging_port is not None
    assert opened == []

    launcher.update_settings({"gelbooru_browser_mode": CUSTOM_COMMAND})
    launcher.update_settings({"gelbooru_browser_mode": "system"})
    assert launcher.ensure_dedicated("https://gelbooru.com/") is True
    assert len(commands) == 1


def test_reset_only_deletes_expected_dedicated_profile(tmp_path: Path) -> None:
    launcher = BrowserLauncher(tmp_path)
    profile = launcher.profile_dir("brave")
    profile.mkdir(parents=True); (profile / "state").write_text("x")
    unrelated = tmp_path / "BrowserProfiles" / "keep"; unrelated.mkdir()
    assert launcher.reset_dedicated_profile("brave") is True
    assert not profile.exists()
    assert unrelated.exists()
    assert launcher.reset_dedicated_profile("../keep") is False


def test_browser_settings_persist_in_json_repository(tmp_path: Path) -> None:
    repository = JsonSettingsRepository(tmp_path / "settings.json")
    values = BrowserLaunchSettings(DEDICATED_PROFILE, "", True).to_mapping()
    repository.save(values)
    assert BrowserLaunchSettings.from_mapping(repository.load()) == BrowserLaunchSettings(
        DEDICATED_PROFILE, "", True
    )
