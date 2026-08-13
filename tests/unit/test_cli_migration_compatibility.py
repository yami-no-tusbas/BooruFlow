from booruflow.cli import e621_scan, e621_tags_update, gelbooru_scan, gelbooru_tags_update
from legacy import (
    e621_artistes_par_tags,
    e621_tags_importer,
    gelbooru_artistes_par_tags_ignore,
    gelbooru_tags_updater,
)


def test_scanner_wrappers_reexport_the_migrated_entry_points():
    assert gelbooru_artistes_par_tags_ignore.main is gelbooru_scan.main
    assert e621_artistes_par_tags.main is e621_scan.main


def test_database_update_wrappers_reexport_the_migrated_entry_points():
    assert gelbooru_tags_updater.main is gelbooru_tags_update.main
    assert e621_tags_importer.main is e621_tags_update.main
