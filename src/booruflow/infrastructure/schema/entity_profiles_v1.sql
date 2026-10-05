CREATE TABLE profiles (
    id INTEGER PRIMARY KEY,
    entity_tag TEXT NOT NULL,
    query TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0 CHECK(sample_count >= 0),
    last_run_new INTEGER NOT NULL DEFAULT 0,
    last_run_state TEXT NOT NULL DEFAULT 'ready',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(entity_tag, query)
);
CREATE TABLE profile_posts (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    post_id TEXT NOT NULL,
    preview_url TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(profile_id, post_id)
);
CREATE TABLE profile_post_tags (
    profile_id INTEGER NOT NULL,
    post_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY(profile_id, post_id, tag),
    FOREIGN KEY(profile_id, post_id) REFERENCES profile_posts(profile_id, post_id) ON DELETE CASCADE
);
CREATE INDEX profile_matching_tags ON profile_post_tags(profile_id, tag);
CREATE TABLE recurring_tags (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'unknown',
    kind TEXT NOT NULL CHECK(kind IN ('attribute','relation')),
    occurrences INTEGER NOT NULL CHECK(occurrences >= 0),
    status TEXT NOT NULL DEFAULT 'review' CHECK(status IN ('keep','review','ignore')),
    PRIMARY KEY(profile_id, tag)
);
CREATE TABLE global_exclusions (tag TEXT PRIMARY KEY);
-- Only UI decisions live here. Accepted tags use the existing manual observation workflow.
-- No foreign key crosses into the separate image-analysis database.
CREATE TABLE tagging_profile_decisions (
    item_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('unreviewed','accepted','rejected')),
    observation_id INTEGER,
    PRIMARY KEY(item_id, tag)
);
PRAGMA user_version=1;
