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

## Verification note
Local-only pytest plugin at `/home/claude/verify_shims/_winstub_plugin.py`
(outside repo, never committed) stubs `ctypes.WinDLL` and `webview` so
Windows-only modules import on this Linux sandbox. With it: 904 passed, 26
failed — all 26 pre-existing and unrelated to this slice (DPAPI round-trip
needs the real Win32 API my stub fakes; edge_tts/startup_cost/font-family
failures predate this slice). Quick-settings tests: 11/11 green. Full suite
must be re-run for real on the Windows box; this only proves the new code
doesn't break anything platform-independent.

mypy on touched files: baseline (before this slice) 43 errors in
`avg/api/app.py` + `avg/api/routes_settings.py`, all pre-existing
unparameterized-`dict` returns. Task 2 adds 2 more of the exact same kind
(`-> dict:` on the two new routes) — matches the plan's own code sample and
this file's dominant existing style (25+ instances), not a new error
category.

## Tasks
- [x] Task 1: quick_settings.json backend document
- [x] Task 2: routes
- [x] Task 3: useSettingsData extraction
- [x] Task 4: Settings.tsx cleanup
- [x] Task 5: LiveList.tsx adapted + tag filter
- [x] Task 6: PreviewTab.tsx (structural money-rule check done; live DOM
      click-through deferred to Final Verification once Task 9 mounts
      QuickPanel — no page exists yet to run it against)
- [ ] Task 7: RecentErrorsTab.tsx
- [ ] Task 8: QuickSettingsTab.tsx
- [ ] Task 9: QuickPanel.tsx shell + wiring
- [ ] Task 10: design language / motion
- [ ] Task 3: useSettingsData extraction
- [ ] Task 4: Settings.tsx cleanup
- [ ] Task 5: LiveList.tsx adapted + tag filter
- [ ] Task 6: PreviewTab.tsx
- [ ] Task 7: RecentErrorsTab.tsx
- [ ] Task 8: QuickSettingsTab.tsx
- [ ] Task 9: QuickPanel.tsx shell + wiring
- [ ] Task 10: design language / motion
