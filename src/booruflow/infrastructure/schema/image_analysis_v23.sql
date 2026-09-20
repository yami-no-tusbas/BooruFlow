CREATE TABLE IF NOT EXISTS wd14_score_vectors (
    model_run_id INTEGER PRIMARY KEY REFERENCES model_runs(id) ON DELETE CASCADE,
    scores_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

PRAGMA user_version = 23;
