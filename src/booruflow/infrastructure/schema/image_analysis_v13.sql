CREATE TABLE tag_review_entries (
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    tag_name TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('existing','manual')),
    decision TEXT NOT NULL CHECK (decision IN ('keep','remove','add')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (item_id, tag_name, origin)
);
PRAGMA user_version=13;
