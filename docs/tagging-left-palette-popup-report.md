# Tagging: transient left-palette windows and focus

Date: 2026-10-08. Scope: current dirty `refactor/pyside6-mvc` checkout. No commit, push or packaging. Existing changes were preserved.

## Confirmed cause, observed before changing behavior

An application event filter was installed **before constructing TaggingPage**. It recorded `Show`, `Hide`, `FocusIn`, `FocusOut`, and `WindowActivate`, including Python/Qt class, objectName, parent class/name, window flags, `isWindow()`, visibility, focus policy/current focus, tag, phase and Python stack. The filter observes events synchronously: checking `topLevelWidgets()` only after insertion would miss these windows because Qt has already reparented them. The observer lives exclusively in test/probe code; production has no new event filter or popup suppression.

The exact per-tag windows are:

| Control | Python / Qt class | objectName | Parent at Show | Flags at Show | Focus policy |
| --- | --- | --- | --- | --- | --- |
| Chip operation `+/−` | QToolButton / QToolButton | empty | None | 134279169 (`0x0800f001`, Window) | TabFocus |
| Chip WD14 analysis `?` | QPushButton / QPushButton | empty | None | 134279169 (`0x0800f001`, Window) | StrongFocus |

Both are ordinary widgets temporarily promoted to native top-level windows by initialization, rather than a menu or tooltip. The code path is:

1. Manual input: Return → `commit_input()` → `_commit_suggestion()` → `add_tag()`. Persisted loading: `_restore_bulk_palette()` → `add_tag(..., emit=False)`.
2. Canonical tag is inserted into `_tags` and `_operations`; a chip and its controls are constructed.
3. `chip.operation = QToolButton()` → `setVisible(self.mixed_operations)`; the left palette has `mixed_operations=True`. This emits a top-level Show while the button has no parent.
4. `chip_row.addWidget(chip.operation)` reparents it, producing Hide.
5. `chip.analysis = QPushButton("?")` → `setVisible(self.confidence_actions)`; the left palette also has `confidence_actions=True`. A second Show/Hide pair follows when the layout reparents this button.

The empty editor constructor additionally showed/reparented a parentless `ChipFlowWidget` and `QScrollArea` when `expand_chips=True`. These are separate from the two flashes per added/persisted tag.

A second root cause was reproduced with cached suggestions and a focused, empty palette input:

`add_tag()` → `input.clear()` → `_refresh_suggestions()` → `show_tag_completer()` → `QCompleter.complete()` → **QListView Show**. Its parent was None, flags 134279177 (`0x0800f009`, Popup). Rendering the remaining suggestions was incorrectly also an instruction to open autocomplete, even after the query was cleared.

Evidence is saved in `var/tmp/tagging-left-palette-popup-baseline/`. `before.json` captures empty → one → 24 → persisted reconstruction; `before-cached.json` captures the unintended QListView. `before-native-add.json` and `before-native-reload.json` additionally record the relevant tag from the live `add_tag()` stack frame. The pre-fix source was saved as `tagging_page.py` before any production edit.

## Lifecycle audit and fix

All passive TagTokenEditor widgets now receive a parent at construction: chip container and scroll area/input belong to the editor; each chip belongs to the chip container; checkbox and operation/analysis/removal buttons belong to the chip. Existing geometry, styles, visibility rules and actions are retained.

`_refresh_suggestions()` now only renders the suggestion model. Interactive completion is requested by `set_suggestions()` for a focused input with a query meeting the existing autocomplete minimum. Adding/removing/loading chips and late responses after the input was cleared no longer request completion.

The left column uses actual chip widgets in a FlowLayout, not item-view delegates, embedded editors, persistent editors or index widgets. QCompleter and its model already have explicit QObject ownership; its intentional popup remains lazy. Chip context menus are parented to the chip and created/executed only by an explicit custom-context-menu event. Tooltips are assigned as text and are not explicitly displayed. There is no focus call, popup flag assignment or delegate/editor opening in passive chip initialization.

`setChecked()` initializes the checkbox **before** connecting its toggle handlers; it does not launch analysis or act as a user click. Initial label text does not invoke an interactive handler. Persisted reconstruction uses `emit=False`, retaining legitimate `contents_changed` updates without emitting `tag_added` / `tags_changed`. No broad signal blocking was introduced.

## A/B results and restart verification

`tests/unit/test_tagging_palette_windows.py` covers empty construction → one tag → 24 tags → persisted JSON reconstruction, with direct insertion while either the palette input or another search input owns focus, and with actual Return-key insertion. It checks focus after every insertion, rejects FocusOut during insertion, rejects every unintended top-level Show/Hide, checks the visible top-level inventory and verifies persisted operation/checked/category state. Passive repopulation while another input owns focus also preserves focus and emits no user-action signals.

Additional cases cover cached suggestions, late lookup responses to an empty input, removal, deliberate autocomplete selection, operation/checkbox/analysis/removal clicks and a real context-menu event. Existing double-click and broader workflow coverage is also exercised.

The retained `native_probe.py` was run using Windows Qt in separate fresh processes for addition and restart. It uses only an isolated palette JSON file and presentation widgets, without controllers, network activity or application databases. The pre-fix comparison loads the saved source as a separate module; production files are never swapped.

| Separate Windows process | Before: unintended Show / Hide | After: unintended Show / Hide |
| --- | --- | --- |
| Empty construction | 2 / 2 | 0 / 0 |
| Add 24 tags via Return, excluding construction | 48 / 48 | **0 / 0** |
| Reconstruct 24 persisted tags, excluding construction | 48 / 48 | **0 / 0** |

The pre-fix Return-key probe lost input focus after the first insertion and remained unfocused at subsequent checkpoints. The fixed probe retained input focus at all 24 checkpoints. The fixed addition and restart processes had only the intended Tagging window visible. **Adding 20+ tags produced no unexpected top-level Show or Hide event.** This is validation of the current checkout with native Qt; no replacement portable was built.

## Validation

- First focused Windows Qt group: 51 passed (palette events, stabilization, scrolling/filters, presentation, preview and Curated UI).
- Expanded Windows Qt group: 223 passed, 12 subtests passed; one pre-existing shell geometry failure. `test_tagging_review_actions_have_nonzero_geometry_in_main_viewport` expects no vertical overflow at the small viewport but observes scrollbar maximum 10. The exact same failure (10 vs 0) occurs independently with the saved **pre-fix** source. No unrelated geometry changes were made. Logs: `native-qt.txt` and `native-shell-before.txt` in the evidence directory.
- Final full suite with offscreen Qt: **1457 passed, 2 skipped, 14 subtests passed** in 134.63 s. This includes all five new palette regression cases. Log: `full-suite-final.txt` in the evidence directory.
- Ruff passed on both changed/new Python files (`tagging_page.py`, `test_tagging_palette_windows.py`). `git diff --check` passed.

The four protected real SQLite files (analysis state, image cache, entity profiles, post metadata) retain their original SHA-256 hashes. Tests use their existing real-database guard and temporary roots; restart probes do not open any database.
