CREATE TABLE tagging_pool_items (item_id INTEGER PRIMARY KEY REFERENCES analysis_items(id) ON DELETE CASCADE, source TEXT NOT NULL, added_at TEXT NOT NULL);
PRAGMA user_version=14;
