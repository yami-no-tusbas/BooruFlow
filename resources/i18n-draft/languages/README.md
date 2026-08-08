# Language files

The interface discovers every `*.json` file in this directory at startup.

To add a translation:

1. Copy `en.json` to `<language-code>.json` (for example `de.json`).
2. Set `_meta.name` to the name displayed in Options.
3. Translate values only. Keep keys and placeholders such as `{count}` unchanged.
4. Save as UTF-8 and restart Artist by Tag. The new language appears automatically.

Missing keys fall back to English, so a translation can be completed gradually.
