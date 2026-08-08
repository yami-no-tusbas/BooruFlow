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

The future navigation should use one top-level page per functional area. It
must not reproduce nested notebooks merely to mirror the old implementation.

