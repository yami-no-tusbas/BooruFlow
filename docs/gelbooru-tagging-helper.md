# Gelbooru Tagging Helper

This standalone edition is intended for contributors who only need to find and
manually tag under-tagged Gelbooru posts. It does not include BooruFlow's local
databases, taxonomy, wiki, cleanup, Grabber, or multi-site workflows.

## Start on Windows

1. Install Python 3.12 or newer.
2. Install the GUI dependencies with `python -m pip install -e .[gui]`.
3. Double-click `Lancer-Gelbooru-Tagging.bat`.

An installed checkout can also run `gelbooru-tagging` from a terminal.

## Build a portable Windows folder

Install the distribution dependency and run the build script:

```powershell
python -m pip install -e ".[gui,distribution]"
.\tools\build_tagging_standalone.ps1
```

Share the resulting `dist/Gelbooru-Tagging-Helper` folder as a ZIP. Recipients
can launch `Gelbooru-Tagging-Helper.exe` without installing Python. Never put a
populated `config` directory into the shared archive.

## Gelbooru access

Enter the numeric Gelbooru user ID and API key shown in the account options on
Gelbooru. The key is masked in the interface. Both values are stored only in
`config/gelbooru_tagging_credentials.json` on the contributor's computer. The
entire `config/*.json` area is ignored by Git.

Never send this credentials file to another person and never include it in a
shared archive.

## Workflow and safety

- Enter a Gelbooru query and choose the page and tag-count thresholds.
- Start the scan; progress shows pages examined and retained posts.
- Click a thumbnail to open the post in the normal browser.
- Tagging and submission remain manual in the authenticated browser.
- The application never modifies Gelbooru posts automatically.

The next-page field advances after a scan, allowing contributors to continue
without rescanning the same block.
