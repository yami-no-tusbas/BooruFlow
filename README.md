# BooruFlow

BooruFlow is a Windows desktop toolkit for Gelbooru tagging, image analysis,
batch review and publishing, taxonomy maintenance, wiki auditing, and related
Booru workflows.

> **Current status: Alpha**
>
> BooruFlow is under active development. Bugs, incomplete behavior and rough
> edges are expected.

## Download

The latest Windows x64 portable Alpha build is available here:

**[Download BooruFlow Alpha](https://github.com/yami-no-tusbas/BooruFlow/releases/tag/v0.1.0-alpha.1)**

Current release: **v0.1.0-alpha.1**

No installer is required. Extract the archive and run `BooruFlow.exe`.

The first-run wizard can configure Gelbooru access, local databases and the
optional WD14 image-analysis model.

## Main features

### Tagging

- Search Gelbooru directly from the application.
- Thumbnail grid with multi-selection and checkbox selection modes.
- Add and remove tags from multiple posts at once.
- Canonical tag and alias autocomplete.
- Existing tags available directly from image tooltips.
- Batch review before anything is published.
- No-op changes are detected and skipped automatically.

### WD14 image analysis

WD14 can be installed from inside BooruFlow and is used as a Gelbooru tagging
assistant.

- Analyze individual images.
- Search for a specific tag across the visible result set.
- Sort images by WD14 confidence.
- Cache completed analyses locally.
- Cancel long targeted scans.

WD14 suggestions do not automatically publish tags.

### Batch publishing

Changes prepared from the tagging grid are sent to a review queue before
publication.

Batch entries can be:

- Pending
- Skipped
- Failed
- Completed

Gelbooru authentication is handled through the embedded browser when required.

Tag changes remain under user control. BooruFlow is not intended to operate as
an autonomous tagging bot.

### Tag Browser

Browse the local Gelbooru tag database with categories, aliases and deprecated
tag information.

### Wiki Audit

Inspect the tags used by a Gelbooru post and check their wiki status, age,
version and related metadata.

### Artists of Folder

Scan a local image collection by artist and open matching files directly in
Everything when available.

### Image Finder

Search external artwork sources from inside BooruFlow.

Pixiv support currently includes artwork and artist search, pagination,
metadata, thumbnails and original-image downloads.

### Database maintenance

BooruFlow maintains local Gelbooru tag and alias databases and can update or
rebuild them from inside the application.

## Optional integrations

### Imgbrd-Grabber

Grabber integration is optional.

BooruFlow's main search, tagging, image-analysis, database and review workflows
remain usable without Grabber installed.

### Everything

Voidtools Everything can optionally be used by local collection tools such as
Artists of Folder.

## Reporting bugs

This is an Alpha release, so bug reports are particularly useful.

Please use **GitHub Issues** for bugs and reproducible problems:

**https://github.com/yami-no-tusbas/BooruFlow/issues**

Using Issues makes reports easier to track, discuss and close once fixed.

When possible, include:

- what you were trying to do;
- what happened;
- what you expected;
- steps to reproduce the problem;
- relevant logs or screenshots.

Please avoid including passwords, API keys or other credentials in reports.

## Development

BooruFlow is written in Python with PySide6.

The main application lives under:

`src/booruflow/`

Additional architecture and migration documentation is available under:

`docs/`

## Privacy and local data

Machine-local settings, credentials, databases, caches, logs and generated
results are excluded from the Git repository.

The portable release does not ship with user credentials or authenticated
sessions.

## License

See the repository license for details.
