"""Run the desktop application or a small first-party command group."""

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "similar":
        from booruflow.cli.similar_artists import main as similar_main
        return similar_main(sys.argv[2:])
    from booruflow.infrastructure.crash_diagnostics import start_crash_diagnostics

    diagnostics = start_crash_diagnostics(Path(__file__).resolve().parents[2])
    clean_exit = False
    # QtWebEngine remote debugging must be configured before importing PySide6.
    from booruflow.infrastructure.gelbooru_cdp_diagnostic import (
        configure_embedded_cdp_startup,
    )
    try:
        sys.argv[:] = configure_embedded_cdp_startup(sys.argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        diagnostics.close(clean=True)
        return 2
    try:
        from booruflow.presentation.pyside6 import run
        result = run(diagnostics=diagnostics)
        clean_exit = True
        return result
    except BaseException:
        diagnostics.record_exception(*sys.exc_info(), thread="main")
        raise
    finally:
        diagnostics.close(clean=clean_exit)


if __name__ == "__main__":
    raise SystemExit(main())
