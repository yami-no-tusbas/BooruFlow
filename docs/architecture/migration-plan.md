# Incremental migration plan

## Stage 0 - Frozen baseline

- Keep `artist_by_tag_gui.py` and its launcher operational.
- Characterize pure behavior before extraction.
- Do not move local databases or settings yet.

## Stage 1 - Extract boundaries

1. Settings and credentials repositories
2. Gelbooru and e621 clients
3. Search/count use cases
4. Taxonomy repository and service
5. Cleanup audit service
6. Optional Grabber adapter

Root-level compatibility modules remain available while callers migrate.

## Stage 2 - PySide6 shell

- Add the main window and top-level navigation.
- Implement logs, progress and cancellation through Qt signals.
- Migrate one functional page at a time.
- Keep the Tkinter launcher as a fallback.

## Stage 3 - Parity and replacement

- Compare outputs from both interfaces on the same fixtures.
- Test Windows, Linux and macOS packaging.
- Switch the default launcher only after visual and behavioral parity.
- Archive the Tkinter interface in a dedicated final commit.

