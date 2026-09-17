# GUI parity checklist

This matrix records the replacement state of the retired Tkinter interface.
“Implemented” means the workflow exists in PySide6 and has automated coverage
where the boundary is testable. It does not replace a live authenticated or
network check.

| Workflow | PySide6 | Automated evidence | Live validation still required |
| --- | --- | --- | --- |
| Review queries, count, autocomplete, progress, stop and results | Implemented | Shell, workflow and review-state tests | Compare Gelbooru/e621 output on the same queries |
| Send reviewed results to Grabber | Implemented | Coordinator and command-building tests | Launch the configured local Grabber executable |
| Browser-assisted tagging review | Implemented | Request, progress and presentation tests | Thumbnail loading and authenticated browser flow |
| Taxonomy browse, search, edit, import and save preview | Implemented | Repository, page and coordinator tests | Confirm a representative taxonomy edit manually |
| Local tag database browser and copy actions | Implemented | Search/filter and page tests | Check large real databases interactively |
| Gelbooru wiki draft editor and preview | Implemented | Wiki rendering, validation and page tests | Manual authenticated publication remains intentional |
| Retro-cleanup audit and Corbeille confirmation | Implemented | Audit and controller tests | Confirm a disposable sample through the Windows Recycle Bin |
| Database paths, credentials and update jobs | Implemented | Settings and update-controller tests | Run each selected real database update |
| Grabber batch generation, progress, stop and persisted state | Implemented | Page and controller tests | Complete one real sequential batch |
| Localization, logs and top-level navigation | Implemented | Shell tests in the supported languages | Native-font visual pass on each packaged platform |

## Replacement gate

The non-functional legacy GUI was removed after user approval. Remaining live
validation is a release gate for packaged builds, not a reason to retain dead
UI code. Destructive and authenticated actions remain explicitly user-confirmed.
