# Development and maintenance tools

These scripts are kept outside the application package because they are not part of the BooruFlow GUI runtime.
Run them from the repository root as modules so project-relative files remain stable:

```powershell
python -m tools.gallery.exporter_listings_galerie
python -m tools.maintenance.compact_booru_cache --help
python -m tools.benchmarks.grabber_sweetspot_benchmark --help
```

- `gallery`: one-off gallery and download-list generation utilities.
- `maintenance`: explicit database/cache maintenance commands.
- `benchmarks`: Imgbrd-Grabber measurement tools; Grabber remains optional for BooruFlow.
