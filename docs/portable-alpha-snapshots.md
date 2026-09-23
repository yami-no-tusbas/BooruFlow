# Gelbooru database snapshots (portable alpha preparation)

No snapshot release or download URL is bundled yet. Set `snapshot_manifest_url` in
`config/booruflow_settings.json` when a trusted data release exists. On a missing
Gelbooru tag or alias database, **Update database** fetches that manifest and
the matching archive. **Full rebuild** remains available as an explicit fallback.
Existing databases keep their current update workflow.

The manifest format is:

```json
{
  "schema_version": 1,
  "databases": {
    "tags": {
      "generated_at": "2026-09-21T00:00:00Z",
      "filename": "gelbooru-tags.zip",
      "size": 123456,
      "sha256": "64 lowercase hexadecimal characters",
      "database_schema_version": "2.1-after-id-ascending",
      "last_tag_id": 1957517
    },
    "aliases": {
      "generated_at": "2026-09-21T00:00:00Z",
      "filename": "gelbooru-aliases.zip",
      "size": 12345,
      "sha256": "64 lowercase hexadecimal characters",
      "database_schema_version": "1"
    }
  }
}
```

Each ZIP contains exactly one database, named `tags.db` or `aliases.db`.
The existing database schema and import state must be present. Archives are
downloaded beside the destination, checked against size and SHA256, extracted
to a temporary file, and validated with SQLite integrity and schema checks.
Activation uses `os.replace`; an existing database is backed up under a unique
name. Temporary files are removed on failure. The tag importer then resumes
from `last_tag_id`; the alias synchronizer resumes from its stored checkpoint.
The manifest and archive should be served from the same directory.

The initial snapshot files and manifest must be produced from validated local
databases and reviewed before publishing a data release. This pass does not
publish one.
