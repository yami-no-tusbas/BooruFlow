from pathlib import Path

from legacy.retro_cleanup import match_file, parse_blacklist


MD5 = "0123456789abcdef0123456789abcdef"
BLACKLIST = parse_blacklist(
    [
        "plain_artist",
        "website:e621.net scoped_artist",
        "website:gelbooru.com gel_artist",
        "yoshi_tama huge_breasts",
        "2boys 2girls rating:sensitive",
        "width:<479",
        "height:<479",
    ]
)


def tags(path: str, mode: str = "artists") -> set[str]:
    return {match.tag for match in match_file(Path(path), BLACKLIST, mode)}


def test_blacklist_categories():
    assert len(BLACKLIST.rules) == 3
    assert BLACKLIST.ignored_compound == 2
    assert BLACKLIST.ignored_non_tag == 2


def test_unscoped_rule_matches_every_site():
    filename = f"plain_artist - 123 - sensitive - {MD5}.jpg"
    assert tags(rf"D:\Tags (e621)\{filename}") == {"plain_artist"}
    assert tags(rf"D:\Tags (Gelbooru)\{filename}") == {"plain_artist"}


def test_website_rule_requires_matching_detected_site():
    filename = f"scoped_artist - 123 - sensitive - {MD5}.jpg"
    assert tags(rf"D:\Tags (e621)\{filename}") == {"scoped_artist"}
    assert tags(rf"D:\Tags (Gelbooru)\{filename}") == set()


def test_website_rule_falls_back_when_site_unknown():
    filename = f"scoped_artist - 123 - sensitive - {MD5}.jpg"
    assert tags(rf"D:\Avatars\{filename}") == {"scoped_artist"}


def test_path_modes_use_parent_directories():
    filename = f"other_artist - 123 - safe - {MD5}.png"
    path = rf"D:\Copyrights\plain_artist\Character\{filename}"
    assert tags(path, "copyrights") == {"plain_artist"}


def test_all_mode_combines_filename_and_path():
    filename = f"scoped_artist - 123 - safe - {MD5}.png"
    path = rf"D:\Copyrights\plain_artist\{filename}"
    assert tags(path, "all") == {"plain_artist", "scoped_artist"}


def test_compound_and_dimensions_never_match():
    filename = f"yoshi_tama - 123 - sensitive - {MD5}.jpg"
    assert tags(rf"D:\2boys 2girls\{filename}") == set()
