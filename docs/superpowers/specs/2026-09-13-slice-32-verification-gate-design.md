# Slice 32 (sub-slice 4) — Release Verification Gate & Secrets Hygiene: Design

**Source:** ports `AI_Video_Gen`'s B2.22 Part 4
(`spec_B2.22_part4_final_verification_secrets.md`). Fourth priority,
matching B2.22's own explicit statement that this part "runs LAST by
design" — it verifies sub-slices 1-3, so it has nothing to check until
they exist.

**This shrinks a lot in the port.** Legacy needed `Final_Verification.ps1`
built from scratch because the PowerShell app had no test framework at all
— every "does it actually work" question was answered by hand or not at
all. AVG Studio already has 671+ passing pytest tests and, more directly
on point, `avg/core/health.py`'s `_deep_checks` (Stage 4, `report(deep=True)`)
is **already** a from-scratch-verified, no-network, no-cost, "do the thing
and undo it" check registry — the exact shape B2.22 Part 4 asked legacy to
build. This sub-slice extends that existing registry with checks for what
sub-slices 1-3 added, and wraps the project's *existing* verification tools
(pytest, mypy, oxlint, `health.py`) into one release-gate entry point —
it does not invent a new verification mechanism.

## 1. Current state (verified in code, `development` @ `4d48ec8`)

- `avg/core/health.py`'s `report()` already runs three stages always
  (`dependencies`, `warmup`, `safeguards`) and a fourth (`deep`) on demand.
  `_deep_checks`' own docstring states the exact invariant this sub-slice
  must keep: **"No provider is called and nothing is generated, so this
  costs nothing and cannot spend money"** — every check does the real
  thing against throwaway/local data (a temp settings file, a throwaway
  DPAPI string, a temp output-folder probe, a real one-second ffmpeg
  encode) and undoes it. This is a hard existing boundary, not a soft
  guideline — no new deep check in this sub-slice may call a network
  provider, even a free/keyless one like Pollinations.
- `avg/core/health.py`'s `_safeguard_checks` (Stage 3) is the *other*
  kind: reports on **real, current** state (e.g. "Provider keys" — which
  providers actually have a key configured right now) rather than proving
  a mechanism against throwaway data. Sub-slice 1's "Provider cooldowns"
  check (already specced there) belongs here, not in `_deep_checks` — it's
  reporting on real cooldown state, not proving the persistence mechanism
  works.
- `CLAUDE.md`'s own documented commands are the project's actual
  verification tools today: `.venv-build\Scripts\python.exe -m pytest
  tests -q`, `-m mypy`, and (frontend) `npm run lint` (oxlint) — none of
  these are wired together into one gate; each is run by hand.
- The release flow (`CLAUDE.md` §Commands) is `Build Installer.bat` →
  `tools/make_release.ps1` → `tools/build.py pyinstaller|installer|all`.
  `make_release.ps1` already has a documented escape-hatch flag pattern
  (`-SkipInstaller`) — the natural place for a verification gate to live
  is a step in this exact flow, with the same kind of opt-out flag, not a
  disconnected script nobody remembers to run.
- **No secrets ever reached this repo's git history** — checked directly
  (`git log --all --diff-filter=A --name-only`, filtered for
  `secrets.dat`/`.env`/`settings.json`/`*api*key*`): the only match is
  `fastapi`'s own vendored `api_key.py` inside `.venv-build/`, a
  false-positive from a third-party library file, not a real credential.
  `.gitignore` already excludes `.env`. Every real secret
  (`secrets.dat`, `settings.json`, `channels/`) lives under
  `%APPDATA%/AVG Studio/`, **outside the repository directory entirely** —
  there is no path by which a `git add` could ever pick one up, unlike
  legacy's PowerShell project, whose config lived inside the working tree
  it committed from. Legacy's Part 4 motivation (a messy history needing
  cleanup, plus this project being about to go from private tooling toward
  something publishable) does not describe a problem that exists here
  today. The repo is currently private (cloning required a PAT), which
  further lowers the stakes relative to legacy's own situation.
- `.github/workflows/` contains four stock template files
  (`c-cpp.yml`, `django.yml`, `jekyll-docker.yml`, `python-app.yml`) —
  none configured for a Python/React desktop app, almost certainly
  unedited defaults from repo creation rather than real CI. Noted because
  a release-gate script needs *somewhere* to eventually run unattended if
  that's ever wanted, and one of these is an available, currently-unused
  slot — not because fixing this was asked for or is in scope here.

## 2. Decisions

1. **No new verification mechanism — orchestrate the existing ones.** A
   new `tools/verify_release.py` runs, in order, and stops at the first
   failure: (a) `pytest tests -q`, comparing the failure count against the
   documented baseline in `.superpowers/sdd/progress.md` (matching this
   project's own already-stated rule, `CLAUDE.md`: "Test baseline = zero
   regressions vs documented baseline, not zero failures" — this script
   enforces exactly that rule mechanically instead of by memory), (b)
   `mypy`, (c) `npm run lint` (oxlint) in `web/`, (d)
   `avg.core.health.report(deep=True)` against a throwaway output root,
   asserting every `required=True` component and every deep check is
   `ok=True`. Each step's real output is captured and shown — this script
   does not re-implement what any of these four already check, it calls
   them and interprets the result.
2. **Wired into the existing release flow, with the existing opt-out
   pattern.** `tools/make_release.ps1` gains a verification step before
   packaging, with a `-SkipVerification` flag alongside its existing
   `-SkipInstaller` — matching the flag naming and "explicit, not implicit,
   opt-out" convention already established there. Running it stand-alone
   (`tools/verify_release.py`) stays possible for a mid-slice check, same
   as `pytest`/`mypy` already are.
3. **`_deep_checks` gains checks for sub-slices 1-3, staying inside the
   existing "no network, no cost, do-and-undo" invariant** (see §3) — new
   rows, not a new stage, since the existing stage's purpose already
   matches exactly what's needed.
4. **Secrets hygiene is a standing check, not a cleanup project** — see
   §1's finding. What B2.22 Part 4 asked legacy to *clean up*, this
   sub-slice only needs to *keep true*: a small check
   (`tools/verify_release.py`, cheap, first step, cheaper than even the
   test suite) that greps the currently-staged/tracked file list for
   secrets-shaped filenames (`*.dat`, `.env*`, anything matching
   `*secret*`/`*apikey*` case-insensitively) and fails loudly if one is
   ever about to be committed — a tripwire, not a remediation, because
   there is nothing to remediate today.

## 3. Design

### A. New `_deep_checks` rows (sub-slices 1-3, throwaway data only)

- **"Provider health round-trip"** (sub-slice 1): write a throwaway
  cooldown entry for a fake provider name (e.g. `"__avg_deep_check__"`)
  via `avg.config.provider_health.record_cooldown`, confirm
  `is_in_cooldown` reports it, then `clear_cooldown` and confirm it no
  longer does — a temp-path-scoped round trip, exactly mirroring
  "Settings round-trip"'s own `tempfile.TemporaryDirectory()` pattern, so
  a real machine's actual cooldown state is never touched by running this
  check.
- **Seed/reference-consistency plumbing** (sub-slice 2): deferred to
  implementation time, with one hard constraint carried over from §1 — it
  may prove the *request-building* code path locally (e.g., that a given
  seed value ends up in the query string / request body a provider call
  would send) but must never make a live call to Pollinations or
  StableHorde, free or not. If sub-slice 2's implementation doesn't
  already expose a pure "build the request for this seed" function to
  check against, factoring one out is preparation work for this check,
  not a blocker for sub-slice 2 itself.
- **"Word-timing remap round-trip"** (sub-slice 3): a tiny synthetic audio
  segment (a few seconds, generated in-check with `pydub.AudioSegment.
  silent`/`.overlay`, no provider involved) with a known word-timing list
  run through the real `avg.pipeline.phases.voice._clean_segment` remap
  path, asserting the remapped spans land inside the expected post-clean
  window — local ffmpeg/pydub only, matching the existing "FFmpeg encode"
  check's own precedent of doing real local audio/video work with no
  network involved.

### B. `_safeguard_checks` addition

- "Provider cooldowns" (specced already under sub-slice 1 — restated here
  only because it's the one addition that belongs to Stage 3, not Stage 4,
  and this document is where that distinction is made explicit).

### C. `tools/verify_release.py`

```
tools/verify_release.py [--skip-tests] [--skip-mypy] [--skip-lint] [--skip-deep]
```

Each flag exists for the same reason `make_release.ps1`'s own flags do —
a fast iteration loop while working on one piece, never the default for an
actual release. No flag disables the secrets tripwire (decision #4) — that
one is not optional. Exit code is the OR of every step's own exit code;
output is each tool's real stdout/stderr, not a re-summarized version of
it, so a failure is exactly as debuggable as running the tool by hand.

### D. `make_release.ps1` integration

One new step, before the PyInstaller/installer stage, calling
`tools/verify_release.py`; `-SkipVerification` skips it, printed as a
visible warning (matching how a skipped stage should never be silent) —
mirrors `-SkipInstaller`'s own existing treatment exactly.

## 4. Data/schema summary

| File | Change |
|---|---|
| `avg/core/health.py` | `_deep_checks`: +2-3 new rows (provider-health round-trip, word-timing remap round-trip, seed-plumbing check if a pure function exists to check by implementation time); `_safeguard_checks`: +1 row (cross-referenced from sub-slice 1) |
| `tools/verify_release.py` | New — orchestrates pytest/mypy/oxlint/`health.report(deep=True)`, plus the secrets-filename tripwire |
| `tools/make_release.ps1` | +1 verification step, +1 `-SkipVerification` flag |

No settings-schema change — this sub-slice adds no user-facing configurable
behavior; it's build/release tooling.

## 5. Testing

`tools/verify_release.py` itself is a thin orchestrator — its own test
coverage is a handful of cases with each underlying tool mocked/stubbed
(pytest returns non-zero → script fails with that step named; mypy clean +
lint clean + deep checks green → script exits 0), not a re-test of
pytest/mypy/oxlint themselves. The new `_deep_checks` rows get the same
kind of test every existing deep check already has: call the check
function directly, assert `Component(ok=True, ...)` on the happy path and
a graceful `ok=False` (never a raised exception) when the thing it's
checking is broken.

## 6. Out of scope

- Any change to what mypy/oxlint/pytest themselves check — this only
  orchestrates them.
- Actual CI wiring (turning `.github/workflows/`'s stub files into
  something real) — noted as available, not undertaken; no one asked for
  CI and it's a genuinely separate decision (hosted runners, secrets in
  CI, etc.) from a local release-gate script.
- A live-call seed-determinism proof against Pollinations/StableHorde —
  deliberately never in scope, per the existing `_deep_checks` invariant.
- Sub-slices 1-3 — separate documents; this one only verifies them.
