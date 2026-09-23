# Windows portable database bootstrap

Run `powershell -File tools/build_portable.ps1` from the repository root. The
script reads the two explicit database paths from `config/booruflow_settings.json`,
validates the sources read only, and uses SQLite's online backup to make
consistent temporary copies. It places one database in each ZIP and writes a
manifest containing relative destinations, sizes, hashes, schema versions, and
the tag `after_id`. No configuration, credentials, or original SQLite files are
copied into the bundle. The script builds a PyInstaller onedir application and
copies the three bootstrap files beside `BooruFlow.exe` in
`dist/BooruFlow/bootstrap`.

The frozen app reads the archives beside its executable. At startup, after the
window is exposed, a worker extracts missing databases to
`data/databases/*.db.bootstrap.tmp`, checks the ZIP hash and member, verifies
the extracted hash, SQLite integrity, schema, and checkpoint, then atomically
activates each database. Successful installation removes its ZIP. The manifest
may remain. A missing archive follows the normal first-run options; an invalid
existing database remains untouched and is reported. The source checkout does
not automatically use distribution archives.

Keep the onedir folder writable for first-run installation. To rebuild the
application with existing validated archives, use
`powershell -File tools/build_portable.ps1 -SkipSnapshotGeneration`.
