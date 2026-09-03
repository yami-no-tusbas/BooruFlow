-- Hydra is an e621 analysis source. SQLite cannot alter a CHECK constraint,
-- so rebuild this small derived-data table while preserving every observation.
ALTER TABLE tag_observations RENAME TO tag_observations_v19;

CREATE TABLE tag_observations (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    model_run_id INTEGER REFERENCES model_runs(id) ON DELETE CASCADE,
    tag_name TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL,
    decision TEXT NOT NULL DEFAULT 'unreviewed',
    reviewed_name TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    category TEXT,
    raw_tag_name TEXT,
    source_present INTEGER NOT NULL DEFAULT 0,
    CHECK (source IN ('gelbooru', 'e621', 'wd14', 'hydra', 'yolo', 'manual')),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    CHECK (decision IN ('unreviewed', 'accepted', 'rejected')),
    CHECK (source_present IN (0, 1))
);

INSERT INTO tag_observations(
    id, item_id, model_run_id, tag_name, source, confidence, decision,
    reviewed_name, reviewed_at, created_at, category, raw_tag_name, source_present
)
SELECT
    id, item_id, model_run_id, tag_name, source, confidence, decision,
    reviewed_name, reviewed_at, created_at, category, raw_tag_name, source_present
FROM tag_observations_v19;

DROP TABLE tag_observations_v19;

CREATE INDEX idx_tag_observations_review
ON tag_observations(item_id, source, category, decision, confidence DESC);

CREATE TRIGGER dirty_profiles_decision AFTER UPDATE OF decision,reviewed_name
ON tag_observations BEGIN
    UPDATE artist_profiles SET dirty=1 WHERE (site,artist_tag) IN (
        SELECT site,artist_tag FROM item_artists WHERE item_id=NEW.item_id
    );
END;

PRAGMA user_version = 20;
