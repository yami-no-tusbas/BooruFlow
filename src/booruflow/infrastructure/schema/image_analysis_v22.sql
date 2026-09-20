ALTER TABLE tagging_review_batch_entries ADD COLUMN failure_reason TEXT;
ALTER TABLE tagging_review_batch_entries ADD COLUMN failure_retryable INTEGER;

PRAGMA user_version = 22;
