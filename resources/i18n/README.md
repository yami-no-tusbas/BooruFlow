# BooruFlow language files

BooruFlow discovers every `*.json` file in this directory when it starts.
English is the default and the fallback for any missing translation.

To add a language:

1. Copy `en.json` to `<language-code>.json`, for example `de.json`.
2. Change `_meta.name` to the name displayed in Options.
3. Translate values only; keep keys and placeholders such as `{page}` unchanged.
4. Save the file as UTF-8 and restart BooruFlow.

Incomplete files are supported: missing values fall back to English.
