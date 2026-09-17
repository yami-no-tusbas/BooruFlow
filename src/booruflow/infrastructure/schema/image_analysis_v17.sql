ALTER TABLE tagging_review_batch_entries
    ADD COLUMN published_final_tags_json TEXT;

UPDATE tagging_review_batch_entries
SET published_final_tags_json=reviewed_final_tags_json
WHERE publish_state='published'
  AND (published_at IS NULL OR reviewed_at <= published_at);

UPDATE tagging_review_batch_entries
SET publish_state='pending_publish'
WHERE publish_state='published'
  AND published_at IS NOT NULL
  AND reviewed_at > published_at;

PRAGMA user_version=17;
