-- The old JSON vectors are disposable cache. Do not read or convert them.
DROP TABLE IF EXISTS wd14_score_vectors;

CREATE TABLE wd14_label_sets (
    id INTEGER PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    labels_hash TEXT NOT NULL,
    label_count INTEGER NOT NULL CHECK (label_count > 0),
    UNIQUE (model_name, model_version, configuration_hash, labels_hash)
);

CREATE TABLE wd14_labels (
    label_set_id INTEGER NOT NULL REFERENCES wd14_label_sets(id) ON DELETE CASCADE,
    tag_index INTEGER NOT NULL CHECK (tag_index >= 0 AND tag_index <= 4294967295),
    tag_name TEXT NOT NULL,
    PRIMARY KEY (label_set_id, tag_index),
    UNIQUE (label_set_id, tag_name)
) WITHOUT ROWID;

CREATE TABLE wd14_score_vectors (
    model_run_id INTEGER PRIMARY KEY REFERENCES model_runs(id) ON DELETE CASCADE,
    label_set_id INTEGER NOT NULL REFERENCES wd14_label_sets(id),
    scores_blob BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_wd14_vectors_created_at ON wd14_score_vectors(created_at);

PRAGMA user_version = 26;
