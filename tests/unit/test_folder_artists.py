from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from booruflow.infrastructure.everything import (
    build_everything_command,
    everything_artist_query,
    find_everything_executable,
    launch_everything,
)
from booruflow.infrastructure.folder_artists import (
    extract_artist,
    parse_image_count_filter,
    scan_folder_artists,
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_extract_artist_uses_the_first_exact_separator() -> None:
    assert extract_artist("omoi_ryuugi - 123456 - explicit - abc.jpg") == "omoi_ryuugi"
    assert extract_artist("  artist name   - 1 - safe - aaa.webp") == "artist name"


def test_extract_artist_ignores_unrecognized_names() -> None:
    assert extract_artist("random.jpg") is None
    assert extract_artist("123456 - explicit - xxx.jpg") is None


def test_scan_counts_ratings_artists_anonymous_and_subfolders(tmp_path: Path) -> None:
    names = (
        "foo - 1 - safe - aaa.jpg",
        "foo - 2 - questionable - bbb.png",
        "foo - 3 - explicit - ccc.webp",
        "bar - 4 - explicit - ddd.gif",
        "anonymous - 5 - safe - eee.bmp",
        "Anonymous - 6 - explicit - fff.avif",
        "random.jpeg",
    )
    for index, name in enumerate(names):
        _touch(tmp_path / ("nested" if index % 2 else "") / name)

    result = scan_folder_artists(tmp_path)

    assert [(item.name, item.images) for item in result.artists] == [("foo", 3), ("bar", 1)]
    assert result.images_found == 7
    assert result.images_with_artist == 4
    assert result.anonymous_ignored == 2
    assert result.unrecognized_names == 1


def test_scan_groups_artist_case_insensitively(tmp_path: Path) -> None:
    _touch(tmp_path / "Foo - 1 - safe - aaa.jpg")
    _touch(tmp_path / "foo - 2 - safe - bbb.jpg")
    assert [(item.name, item.images) for item in scan_folder_artists(tmp_path).artists] == [
        ("Foo", 2)
    ]


def test_scan_skips_an_inaccessible_subfolder(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    _touch(tmp_path / "foo - 1 - safe - aaa.jpg")
    real_scandir = os.scandir

    def selective_scandir(path):
        if Path(path) == blocked:
            raise PermissionError("blocked")
        return real_scandir(path)

    with patch("booruflow.infrastructure.folder_artists.os.scandir", selective_scandir):
        result = scan_folder_artists(tmp_path)

    assert [(item.name, item.images) for item in result.artists] == [("foo", 1)]
    assert result.inaccessible_entries == 1


@pytest.mark.parametrize(
    ("expression", "matching", "excluded"),
    (
        ("500", (500,), (499, 501)),
        ("<500", (0, 499), (500,)),
        ("<=500", (0, 500), (501,)),
        (">500", (501, 900), (500,)),
        (">=500", (500, 900), (499,)),
        ("50 ~ 100", (50, 75, 100), (49, 101)),
        ("50~100", (50, 75, 100), (49, 101)),
    ),
)
def test_image_count_filter_expressions(expression, matching, excluded) -> None:
    count_filter = parse_image_count_filter(expression)
    assert count_filter is not None
    assert all(count_filter.matches(value) for value in matching)
    assert not any(count_filter.matches(value) for value in excluded)


def test_empty_image_count_filter_disables_filtering() -> None:
    assert parse_image_count_filter("  ") is None


@pytest.mark.parametrize("expression", ("foo", "<abc", "50 ~", "~100", "50 ~ abc", "<<500"))
def test_invalid_image_count_filter_is_rejected(expression) -> None:
    with pytest.raises(ValueError):
        parse_image_count_filter(expression)


def test_everything_command_has_only_path_scope_and_anchored_artist_query(tmp_path: Path) -> None:
    executable = Path("C:/Program Files/Everything/Everything.exe")
    command = build_everything_command(executable, tmp_path, "foo.bar")
    assert command[:2] == [str(executable), "-path"]
    assert command[2] == str(tmp_path.resolve())
    assert command[3] == "-search"
    assert command[4] == r"no-case:no-path:regex*:^foo\.bar\ \-\ "
    assert "file:" not in command[4]
    assert "ext:" not in command[4]
    assert everything_artist_query("foo") != everything_artist_query("foobar")


def test_everything_launcher_passes_an_argument_list() -> None:
    popen = Mock(return_value=object())
    command = ["Everything.exe", "-path", "D:/images", "-search", "query"]
    result = launch_everything(command, popen=popen)
    assert result is popen.return_value
    popen.assert_called_once_with(command, close_fds=True)


def test_everything_detection_prefers_configured_file(tmp_path: Path) -> None:
    executable = tmp_path / "Everything.exe"
    executable.touch()
    with patch(
        "booruflow.infrastructure.everything.shutil.which",
        side_effect=AssertionError("automatic detection must not run"),
    ):
        assert find_everything_executable(str(executable)) == executable.resolve()
