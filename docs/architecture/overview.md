# Architecture target

BooruFlow uses a layered architecture inspired by MVC and Qt Model/View.

```text
PySide6 views and Qt models
            |
Controllers / presentation view-models
            |
Application use cases and ports
            |
Pure domain models
            |
Infrastructure adapters
```

## Dependency rules

- `domain` imports only the Python standard library.
- `application` may import `domain`, but never a GUI or concrete adapter.
- `infrastructure` implements application ports for Booru sites, SQLite,
  filesystem settings and optional external applications.
- `presentation` invokes application services and converts results into Qt
  models. It never performs HTTP, SQLite or subprocess work directly.
- Grabber is an optional infrastructure adapter. No core use case depends on
  Grabber being installed.

## Functional areas

- Search and category review
- Tagging review
- Taxonomy organization
- Local database maintenance
- Retroactive cleanup
- Optional Grabber batch generation and session control

## PySide6 coordination

`MainWindow` is the composition shell. Long-running or stateful workflows are
owned by dedicated Review, Tagging, Cleanup, Organization, database-update and
Grabber coordinators. The shell retains navigation and explicit confirmation
boundaries for destructive cleanup and taxonomy replacement.

Controllers publish lifecycle and progress updates through `TaskManager`.
Its repository is an application port: production uses bounded atomic JSON
history while tests use an in-memory implementation. Running records recovered
after an application restart become `interrupted`, so stale work is never shown
as active indefinitely.

The future navigation should use one top-level page per functional area. It
must not reproduce nested notebooks merely to mirror the old implementation.
