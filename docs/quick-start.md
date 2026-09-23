# BooruFlow — Quick Start

BooruFlow is a Windows desktop application for Gelbooru tagging, image analysis,
batch review/publishing, wiki maintenance and related workflows.

This is currently an Alpha release. Bugs and rough edges are expected.

## 1. Installation

Download the latest Windows x64 release:

https://github.com/yami-no-tusbas/BooruFlow/releases/tag/v0.1.0-alpha.1

BooruFlow is portable and does not require an installer.

1. Download the ZIP archive.
2. Extract the complete `BooruFlow` folder somewhere writable.
3. Run `BooruFlow.exe`.

Do not run the application directly from inside the ZIP archive.

## 2. First-run setup

On first launch, BooruFlow will guide you through the initial setup.

You can configure your Gelbooru account information and install the resources
used by the application.

### Local tag databases

BooruFlow installs local Gelbooru tag and alias databases.

These databases are used for features such as:

- tag autocomplete;
- aliases;
- tag categories;
- post counts;
- local taxonomy information.

### WD14

WD14 is optional.

It is an image-analysis model used as a Gelbooru tagging assistant. It can
suggest tags and estimate confidence scores for an image.

WD14 never publishes tags automatically.

You can install it during first-run setup or later from the application
options.

## 3. Searching and tagging posts

Open the `Tagging` page.

Enter a Gelbooru search query and start the search.

The result grid shows matching posts and their thumbnails.

You can:

- select posts normally;
- enable `Checkbox selection` and click anywhere on a card to toggle it;
- hover over an image to see its existing tags;
- open an image with a double-click for individual review.

### Add / Remove tags

Use the `Add tags` and `Remove tags` fields.

Autocomplete uses the local Gelbooru database and resolves known aliases to
their canonical tag.

Tags already shown as chips remain active.

All listed tags are applied together when you press `Apply`.

For example:

    Add:
    1girl
    blonde_hair
    shorts

means that all three tags will be requested for the selected images.

BooruFlow displays a confirmation before adding the changes to the batch.

`Apply` does NOT immediately edit Gelbooru.

It only adds the requested changes to the local review batch.

## 4. No-op changes

BooruFlow compares requested changes with the tags already present on each post.

If nothing needs to change, the item is added to the batch as:

    Skipped — No tag changes needed

For example, requesting `1girl` on a post that already has `1girl` does not
create a Gelbooru edit.

If you request several tags and only some are missing, only the effective
changes will be published.

## 5. WD14-assisted tagging

### Individual analysis

Double-click a post to open the individual review page.

WD14 can analyze the image and display suggested tags with confidence scores.

You can inspect the results before deciding what to add.

### Targeted analysis

From the result grid, BooruFlow can also analyze the visible images for a
specific tag.

The results are grouped/sorted by confidence.

This is useful when reviewing many images for one particular tag.

Long targeted scans can be cancelled.

Previously completed analyses may be loaded from the local cache.

## 6. The Batch

After applying changes, the `Batch` button shows how many entries still need
attention.

Open the Batch page to review changes before publication.

Batch entries can have several states:

- `Pending` — ready to be published;
- `Skipped` — no effective tag change was required;
- `Failed` — publication failed;
- `Completed` / `Published` — publication succeeded.

The Additions and Removals columns show what was requested for review.

Skipped entries are never sent to Gelbooru.

## 7. Publishing changes

When you are satisfied with the batch, press `Publish batch`.

If BooruFlow does not have an authenticated Gelbooru browser session, the
embedded browser will open.

Log in to Gelbooru normally, return to the Batch page, and press
`Publish batch` again.

Before publication starts, BooruFlow asks for confirmation.

Publication is rate-limited. Progress and an estimated remaining time are
displayed while the batch is running.

You can continue using other parts of BooruFlow while publication is in
progress.

After successful publication, `Remove completed` removes completed entries from
the batch while leaving entries that still need attention.

## 8. Wiki Audit

The `Wiki Audit` page can inspect the tags used by a Gelbooru post.

It displays information such as:

- category;
- post count;
- aliases;
- wiki status;
- wiki date;
- version;
- author.

You can also read the remote wiki text directly inside BooruFlow.

## 9. Tag Browser

The `Tag Browser` provides access to the local Gelbooru tag database.

It can be used to inspect:

- tag names;
- categories;
- post counts;
- aliases;
- deprecated tags.

## 10. Image Finder and other tools

BooruFlow also contains experimental and local-library tools such as:

- Image Finder;
- Similar Artists;
- Artists of Folder;
- Auto organize;
- Cleanup by Blacklist;
- Grabber integration.

Some of these features are still experimental or marked Beta.

Imgbrd-Grabber is optional and is not required for the main Gelbooru tagging
workflow.

## 11. Reporting bugs

BooruFlow is currently an Alpha release, so bugs are expected.

Please report reproducible problems through GitHub Issues:

https://github.com/yami-no-tusbas/BooruFlow/issues

GitHub Issues is strongly preferred over Gelbooru private messages, live chat,
or scattered forum replies because reports can be tracked, discussed and
marked as fixed.

When possible, include:

- what you were trying to do;
- what happened;
- what you expected;
- steps to reproduce the problem;
- a screenshot if useful;
- relevant log messages.

Do not post passwords, API keys, cookies or other credentials.

## Important

BooruFlow is designed as a human-reviewed workflow tool.

Tag suggestions and requested edits remain under user control, and changes are
reviewed in the batch before publication.

BooruFlow is not intended to operate as an autonomous tagging bot.