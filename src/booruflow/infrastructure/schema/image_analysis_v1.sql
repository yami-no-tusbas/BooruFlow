CREATE TABLE analysis_items (
    id INTEGER PRIMARY KEY,
    input_kind TEXT NOT NULL,
    source_site TEXT,
    source_post_id TEXT,
    original_path TEXT,
    cached_path TEXT,
    content_sha256 TEXT,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    source_state TEXT NOT NULL DEFAULT 'resolved',
    state TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    processing_started_at TEXT,
    processing_heartbeat_at TEXT,
    ready_at TEXT,
    reviewed_at TEXT,
    CHECK (input_kind IN ('local_file', 'gelbooru_post', 'e621_post')),
    CHECK (source_state IN ('unresolved', 'resolved', 'failed')),
    CHECK (state IN (
        'pending', 'processing', 'ready_for_review', 'reviewed', 'failed', 'skipped'
    )),
    CHECK ((width IS NULL AND height IS NULL) OR (width > 0 AND height > 0)),
    CHECK (
        (input_kind = 'local_file' AND original_path IS NOT NULL
            AND source_site IS NULL AND source_post_id IS NULL)
        OR
        (input_kind = 'gelbooru_post' AND source_site = 'gelbooru'
            AND source_post_id IS NOT NULL)
        OR
        (input_kind = 'e621_post' AND source_site = 'e621'
            AND source_post_id IS NOT NULL)
    )
);

CREATE INDEX idx_analysis_items_queue
ON analysis_items(state, priority DESC, created_at, id);
CREATE INDEX idx_analysis_items_source_queue
ON analysis_items(source_state, created_at, id);
CREATE INDEX idx_analysis_items_content_hash
ON analysis_items(content_sha256);
CREATE UNIQUE INDEX idx_analysis_remote_source
ON analysis_items(source_site, source_post_id)
WHERE source_site IS NOT NULL;

CREATE TABLE source_tags (
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    category TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (item_id, site, tag_name),
    CHECK (site IN ('gelbooru', 'e621'))
) WITHOUT ROWID;

CREATE TABLE model_runs (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    backend TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    configuration_hash TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    UNIQUE (item_id, backend, model_name, model_version, configuration_hash)
);

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
    CHECK (source IN ('gelbooru', 'e621', 'wd14', 'yolo', 'manual')),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    CHECK (decision IN ('unreviewed', 'accepted', 'rejected'))
);

CREATE TABLE embeddings (
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    model_run_id INTEGER NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
    vector BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    dtype TEXT NOT NULL,
    normalized INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (item_id, model_run_id),
    CHECK (dimensions > 0),
    CHECK (normalized IN (0, 1))
) WITHOUT ROWID;

CREATE TABLE object_detections (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    model_run_id INTEGER NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    confidence REAL NOT NULL,
    x_min REAL NOT NULL,
    y_min REAL NOT NULL,
    x_max REAL NOT NULL,
    y_max REAL NOT NULL,
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (x_min >= 0.0 AND x_min < x_max AND x_max <= 1.0),
    CHECK (y_min >= 0.0 AND y_min < y_max AND y_max <= 1.0)
);

CREATE TABLE image_statistics (
    item_id INTEGER PRIMARY KEY REFERENCES analysis_items(id) ON DELETE CASCADE,
    model_run_id INTEGER NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
    mean_saturation REAL,
    mean_luminance REAL,
    luminance_stddev REAL,
    contrast REAL,
    pastel_score REAL,
    dominant_colors_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE item_artists (
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    artist_tag TEXT NOT NULL,
    provenance TEXT NOT NULL,
    PRIMARY KEY (item_id, site, artist_tag, provenance),
    CHECK (site IN ('gelbooru', 'e621'))
) WITHOUT ROWID;

PRAGMA user_version = 1;
