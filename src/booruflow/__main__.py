"""Run the desktop application or a small first-party command group."""

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "similar":
        from booruflow.cli.similar_artists import main as similar_main
        return similar_main(sys.argv[2:])
    from booruflow.presentation.pyside6 import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
