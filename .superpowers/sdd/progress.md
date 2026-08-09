# Slice 18 — Quick panel — progress ledger

Environment note: implementing on a Linux sandbox, not the Windows dev box.
`.venv-build` is a Windows venv (WinDLL/pywebview) and cannot run here. Built
`.venv-linux` for verification of platform-independent modules. Anything
importing `avg.config.secrets` (WinDLL crypt32) or `avg.shell` (pywebview)
cannot be collected by pytest on this machine — pre-existing baseline issue,
not introduced by this slice. Full suite must be re-run on the Windows box
before this is considered verified end-to-end.

## Baseline (before Task 1)
- `tests` collection on Linux: 29 errors (all pre-existing: WinDLL / webview
  import failures), unrelated files unaffected.
- Task 1's own test file has zero platform dependencies and is fully
  verifiable here.

## Tasks
- [ ] Task 1: quick_settings.json backend document
- [ ] Task 2: routes
- [ ] Task 3: useSettingsData extraction
- [ ] Task 4: Settings.tsx cleanup
- [ ] Task 5: LiveList.tsx adapted + tag filter
- [ ] Task 6: PreviewTab.tsx
- [ ] Task 7: RecentErrorsTab.tsx
- [ ] Task 8: QuickSettingsTab.tsx
- [ ] Task 9: QuickPanel.tsx shell + wiring
- [ ] Task 10: design language / motion
