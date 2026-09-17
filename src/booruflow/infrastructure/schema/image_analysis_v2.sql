ALTER TABLE analysis_items ADD COLUMN review_active INTEGER NOT NULL DEFAULT 0
    CHECK (review_active IN (0, 1));
ALTER TABLE analysis_items ADD COLUMN source_resolution_started_at TEXT;

CREATE UNIQUE INDEX idx_analysis_single_active_review
ON analysis_items(review_active) WHERE review_active = 1;

CREATE TABLE worker_sessions (
    id TEXT PRIMARY KEY,
    process_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    stopped_at TEXT,
    last_error TEXT,
    CHECK (state IN ('running', 'stopped', 'failed'))
);

ALTER TABLE image_statistics RENAME TO image_statistics_v1;
CREATE TABLE image_statistics (
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    model_run_id INTEGER PRIMARY KEY REFERENCES model_runs(id) ON DELETE CASCADE,
    mean_saturation REAL,
    mean_luminance REAL,
    luminance_stddev REAL,
    contrast REAL,
    pastel_score REAL,
    dominant_colors_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX idx_image_statistics_item ON image_statistics(item_id, model_run_id);
INSERT INTO image_statistics SELECT * FROM image_statistics_v1;
DROP TABLE image_statistics_v1;

PRAGMA user_version = 2;
