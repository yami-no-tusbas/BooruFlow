ALTER TABLE analysis_items ADD COLUMN analysis_requested INTEGER NOT NULL DEFAULT 0
    CHECK (analysis_requested IN (0, 1));

UPDATE analysis_items SET analysis_requested=1
WHERE state IN ('pending','processing') AND source_state IN ('unresolved','resolved');

CREATE INDEX idx_analysis_scheduler
ON analysis_items(analysis_requested,state,source_state,priority,created_at);

PRAGMA user_version=7;
