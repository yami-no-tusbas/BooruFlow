# Incremental migration plan

## Stage 0 - Frozen baseline (completed)

- Characterize the historical GUI before replacement.
- Characterize pure behavior before extraction.
- Do not move local databases or settings yet.

## Stage 1 - Extract boundaries

1. [x] Settings and credentials repositories
2. [x] Gelbooru and e621 clients
3. [x] Search/count CLI engines
4. [x] Taxonomy repository and wiki import service
5. [x] Cleanup audit service
6. [x] Optional Grabber adapter
7. [x] Shared SQLite cache and entity-type specifications

Compatibility modules remain available under `legacy` while external callers
migrate. Modern code must not import from `legacy`; an architecture test
enforces this rule.

## Stage 2 - PySide6 shell

- [x] Add the main window and top-level navigation.
- [x] Implement logs, progress and cancellation through Qt signals.
- [x] Migrate one functional page at a time.
- [x] Launch review and database-update engines through `python -m booruflow.cli...`.
- [x] Extract Review, Tagging, Cleanup, database-update and Grabber coordinators.
- [x] Extract the taxonomy/organization orchestration while keeping confirmation in the shell.
- [x] Add a persistent task center shared by long-running coordinators.
- [x] Remove the non-functional Tkinter launcher after PySide6 replacement.

## Stage 3 - Parity and replacement

- Track workflow status in [gui-parity.md](gui-parity.md).
- Compare outputs from both interfaces on the same fixtures.
- Test Windows, Linux and macOS packaging.
- Switch the default launcher only after visual and behavioral parity.
- [x] Archive the Tkinter interface in Git history.
