# BooruFlow

BooruFlow is a desktop workflow toolkit for Booru search, review, tagging,
taxonomy maintenance, local databases and recoverable cleanup.

Imgbrd-Grabber integration is optional. Search, tagging, taxonomy, database
maintenance and cleanup must remain usable when Grabber is not installed.

## Current application

The working baseline is still the legacy Tkinter application:

```powershell
.\Lancer-Artist-by-Tag-GUI.bat
```

The new `src/booruflow` package is an incremental PySide6 migration. It does
not replace the working interface until feature parity has been verified.
## Repository layout

- `legacy`: working Tkinter application and compatibility modules.
- `src/booruflow`: new layered application under construction.
- `data`: local databases, imported source data and tracked taxonomy.
- `config`: machine-local settings and credentials; ignored by Git.
- `var`: generated results, lists and benchmarks; ignored by Git.
- `resources/i18n-draft`: unused localization prototype retained for reference.
- `tools`: standalone gallery, maintenance and benchmark commands.

## Safety rules

Before every migration batch:

1. Start from a clean Git state.
2. Create a dated local backup of every existing file in scope.
3. Keep one behavioral change per commit.
4. Run characterization and unit tests.
5. Verify the GUI visually before replacing the legacy entry point.

Secrets, machine-local settings, databases, caches and generated results are
excluded from Git.

## Architecture

See [docs/architecture/overview.md](docs/architecture/overview.md) and
[docs/architecture/migration-plan.md](docs/architecture/migration-plan.md).

