# Legacy Tkinter application

This package contains the Tkinter fallback retained during the incremental
PySide6 migration. The other modules are thin compatibility wrappers around
the implementations in `src/booruflow`; new code must import the modern paths.
Launch it from the repository root with `Lancer-Artist-by-Tag-GUI.bat` or:

```powershell
python -m legacy.artist_by_tag_gui
```

Do not remove it until the PySide6 interface has reached verified feature parity.
