-- One source recovery per explicit analysis request, surviving worker restarts.
ALTER TABLE analysis_items ADD COLUMN source_recovery_count INTEGER NOT NULL DEFAULT 0;

-- Human review is attached to a site's canonical tag, independently of proposal provenance.
CREATE TABLE canonical_tag_decisions (
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted','rejected','unreviewed')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (item_id,site,tag_name)
) WITHOUT ROWID;

PRAGMA user_version = 27;
