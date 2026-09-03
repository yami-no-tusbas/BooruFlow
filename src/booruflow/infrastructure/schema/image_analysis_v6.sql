CREATE TABLE tag_mappings (
    id INTEGER PRIMARY KEY,
    source_namespace TEXT NOT NULL,
    source_tag TEXT NOT NULL COLLATE NOCASE,
    target_site TEXT NOT NULL,
    target_tag TEXT NOT NULL COLLATE NOCASE,
    provenance TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_namespace, source_tag, target_site)
);
CREATE INDEX idx_tag_mappings_target ON tag_mappings(target_site, target_tag);
PRAGMA user_version=6;
