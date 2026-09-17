ALTER TABLE library_index_jobs ADD COLUMN detected INTEGER NOT NULL DEFAULT 0;

CREATE TABLE library_index_paths (
    job_id TEXT NOT NULL REFERENCES library_index_jobs(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    item_id INTEGER REFERENCES analysis_items(id) ON DELETE SET NULL,
    outcome TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (job_id,path)
) WITHOUT ROWID;

PRAGMA user_version = 12;
