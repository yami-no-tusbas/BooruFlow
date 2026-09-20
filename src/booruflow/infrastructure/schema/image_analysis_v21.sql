-- Record completed, database-local maintenance so historical backfills do not
-- run on every application launch (or every feature activation).
CREATE TABLE IF NOT EXISTS feature_maintenance (
    maintenance_key TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

PRAGMA user_version = 21;
