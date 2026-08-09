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

## Money-rule check (Task 6 Step 2), run against Task 9's QuickPanel
No browser/DOM tooling exists in this sandbox at all (no playwright,
puppeteer, or chromium) — matches the spec's own §5 note that "the Browser
pane does not composite on this machine. Recorded as pending rather than
claimed." Same limitation, not a shortcut taken here.

Did the strongest available substitute: a full static trace of the call
graph from `QuickPanel`'s mount down to `confirm_paid`.
- `QuickPanel.tsx`: `PreviewTab` appears in exactly one place, a JSX
  conditional on `tab === "preview"` — no effect anywhere in the file
  reaches it or calls anything on it.
- `PreviewTab.tsx`: zero `useEffect`s (`grep -c useEffect` = 0). Switching
  `kind` only changes which child of `PreviewPanel` mounts.
- `PreviewPanel.tsx`: `confirm_paid: true` appears exactly twice, both
  inside `generateSample` functions bound only to a button's `onClick`.
  `VoicePreview`'s free-path effect is guarded `if (paid || !voiceId)
  return` before it can run. `ImagePreview`'s only effect is an unmount
  cleanup with an empty dependency array — no dependency on
  provider/model/aspectRatio, no network call.
- Conclusion: no render-on-mount, no render-on-tab-switch, no
  render-on-settings-change path reaches either paid call, at every layer
  from `QuickPanel` down. Live DOM click-through against a running dev
  server (localStorage-seeded tab, actual network tab inspection) still
  owed once this runs on a machine with a compositing browser — same
  status as the spec's own visual-pass note.

## Tasks
- [x] Task 1: quick_settings.json backend document
- [x] Task 2: routes
- [x] Task 3: useSettingsData extraction
- [x] Task 4: Settings.tsx cleanup
- [x] Task 5: LiveList.tsx adapted + tag filter
- [x] Task 6: PreviewTab.tsx (structural + call-graph money-rule check
      done; live DOM click-through still owed — no browser in this sandbox)
- [x] Task 7: RecentErrorsTab.tsx
- [x] Task 8: QuickSettingsTab.tsx
- [x] Task 9: QuickPanel.tsx shell + wiring
- [x] Task 10: design language / motion (mount lifecycle redesigned:
      stays mounted after first open so a slide-out transition has
      something to animate; found and avoided a pre-existing gap —
      `--font-mono`/`--ink` referenced by old code but never defined
      anywhere in tokens.css — by using a literal monospace stack and
      `--text`/`--muted` instead of repeating it)
- [ ] Task 3: useSettingsData extraction
- [ ] Task 4: Settings.tsx cleanup
- [ ] Task 5: LiveList.tsx adapted + tag filter
- [ ] Task 6: PreviewTab.tsx
- [x] Task 7: RecentErrorsTab.tsx
- [x] Task 8: QuickSettingsTab.tsx
- [ ] Task 9: QuickPanel.tsx shell + wiring
- [ ] Task 10: design language / motion
