ALTER TABLE analysis_items ADD COLUMN queue_visible INTEGER NOT NULL DEFAULT 1
    CHECK (queue_visible IN (0, 1));

CREATE TABLE image_provenances (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    local_path TEXT,
    site TEXT,
    post_id TEXT,
    created_at TEXT NOT NULL,
    CHECK (kind IN ('local_file', 'gelbooru_post', 'e621_post')),
    CHECK (
        (kind='local_file' AND local_path IS NOT NULL AND site IS NULL AND post_id IS NULL)
        OR
        (kind!='local_file' AND local_path IS NULL AND site IS NOT NULL AND post_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX idx_image_provenance_local
ON image_provenances(item_id, local_path) WHERE local_path IS NOT NULL;
CREATE UNIQUE INDEX idx_image_provenance_remote
ON image_provenances(site, post_id) WHERE site IS NOT NULL;

INSERT INTO image_provenances(item_id,kind,local_path,site,post_id,created_at)
SELECT id,input_kind,original_path,source_site,source_post_id,created_at FROM analysis_items;

PRAGMA user_version = 5;
