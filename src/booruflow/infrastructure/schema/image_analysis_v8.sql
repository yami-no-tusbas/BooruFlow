CREATE TABLE artist_profiles (
    site TEXT NOT NULL,
    artist_tag TEXT NOT NULL COLLATE NOCASE,
    profile_version TEXT NOT NULL,
    dependency_hash TEXT NOT NULL,
    source_embedding_versions_json TEXT NOT NULL DEFAULT '{}',
    image_count INTEGER NOT NULL,
    profile_json TEXT NOT NULL,
    dirty INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0, 1)),
    built_at TEXT NOT NULL,
    PRIMARY KEY (site, artist_tag, profile_version),
    CHECK (site IN ('gelbooru', 'e621')),
    CHECK (image_count >= 0)
) WITHOUT ROWID;

CREATE INDEX idx_artist_profiles_dirty ON artist_profiles(dirty, site, artist_tag);

CREATE TRIGGER dirty_profiles_embedding_insert AFTER INSERT ON embeddings BEGIN
    UPDATE artist_profiles SET dirty=1 WHERE (site,artist_tag) IN (
        SELECT site,artist_tag FROM item_artists WHERE item_id=NEW.item_id
    );
END;
CREATE TRIGGER dirty_profiles_embedding_update AFTER UPDATE ON embeddings BEGIN
    UPDATE artist_profiles SET dirty=1 WHERE (site,artist_tag) IN (
        SELECT site,artist_tag FROM item_artists WHERE item_id=NEW.item_id
    );
END;
CREATE TRIGGER dirty_profiles_artist_insert AFTER INSERT ON item_artists BEGIN
    UPDATE artist_profiles SET dirty=1
    WHERE site=NEW.site AND artist_tag=NEW.artist_tag COLLATE NOCASE;
END;
CREATE TRIGGER dirty_profiles_artist_delete AFTER DELETE ON item_artists BEGIN
    UPDATE artist_profiles SET dirty=1
    WHERE site=OLD.site AND artist_tag=OLD.artist_tag COLLATE NOCASE;
END;
CREATE TRIGGER dirty_profiles_decision AFTER UPDATE OF decision,reviewed_name
ON tag_observations BEGIN
    UPDATE artist_profiles SET dirty=1 WHERE (site,artist_tag) IN (
        SELECT site,artist_tag FROM item_artists WHERE item_id=NEW.item_id
    );
END;
CREATE TRIGGER dirty_profiles_source_tag_insert AFTER INSERT ON source_tags BEGIN
    UPDATE artist_profiles SET dirty=1 WHERE (site,artist_tag) IN (
        SELECT site,artist_tag FROM item_artists WHERE item_id=NEW.item_id
    );
END;
CREATE TRIGGER dirty_profiles_source_tag_delete AFTER DELETE ON source_tags BEGIN
    UPDATE artist_profiles SET dirty=1 WHERE (site,artist_tag) IN (
        SELECT site,artist_tag FROM item_artists WHERE item_id=OLD.item_id
    );
END;

PRAGMA user_version = 8;
