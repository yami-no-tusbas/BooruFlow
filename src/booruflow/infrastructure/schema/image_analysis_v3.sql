ALTER TABLE model_runs ADD COLUMN runtime TEXT NOT NULL DEFAULT '';
ALTER TABLE tag_observations ADD COLUMN category TEXT;
ALTER TABLE tag_observations ADD COLUMN raw_tag_name TEXT;
ALTER TABLE tag_observations ADD COLUMN source_present INTEGER NOT NULL DEFAULT 0
    CHECK (source_present IN (0, 1));

CREATE INDEX idx_tag_observations_review
ON tag_observations(item_id, source, category, decision, confidence DESC);

PRAGMA user_version = 3;
