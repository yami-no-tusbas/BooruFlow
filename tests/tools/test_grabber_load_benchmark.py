from tools.benchmarks.grabber_load_benchmark import LoadMeasurement, best_measurement, parsed_tab_keys
from tools.benchmarks.grabber_sweetspot_benchmark import configuration_grid
from artist_by_tag_gui import remaining_review_tabs


def test_parsed_tab_keys_deduplicates_html_api_pair():
    text = """
[10:00:01.000][Info] [gelbooru.com][Html] Parsed page `https://gelbooru.com/index.php?page=post&tags=alpha`: 42 images
[10:00:02.000][Info] [gelbooru.com][Xml] Parsed page `https://gelbooru.com/index.php?page=dapi&tags=alpha&limit=50`: 50 images
[10:00:03.000][Info] [e621.net][Json] Parsed page `https://e621.net/posts.json?tags=beta&limit=20`: 20 images
"""
    assert parsed_tab_keys(text) == {
        ("gelbooru.com", "alpha", "0"),
        ("e621.net", "beta", "0"),
    }


def test_parsed_tab_keys_distinguishes_pages():
    text = """
[10:00:01.000][Info] [gelbooru.com][Xml] Parsed page `https://gelbooru.com/index.php?tags=alpha&pid=2`: 50 images
[10:00:02.000][Info] [gelbooru.com][Xml] Parsed page `https://gelbooru.com/index.php?tags=alpha&pid=3`: 50 images
"""
    assert len(parsed_tab_keys(text)) == 2


def test_best_measurement_uses_throughput():
    slow = LoadMeasurement("a", 10, 20, 10, 1, 20, 10, 5, "a.log")
    fast = LoadMeasurement("b", 20, 20, 15, .75, 26.7, 20, 5, "b.log")
    assert best_measurement([slow, fast]) is fast


def test_default_grid_has_323_combinations():
    grid = configuration_grid()
    assert len(grid) == 323
    assert len(set(grid)) == len(grid)


def test_remaining_review_tabs_ignores_empty_home_tab():
    search = {"type": "tag", "tags": ["rating:general", "pikachu"]}
    data = {
        "tabs": [
            {"type": "tag", "tags": []},
            {"type": "home", "tags": ["ignored"]},
            search,
        ]
    }
    assert remaining_review_tabs(data) == [search]
