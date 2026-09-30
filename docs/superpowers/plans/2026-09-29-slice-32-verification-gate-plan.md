# Slice 32 (sub-slice 4) - Release Verification Gate: Plan (`linux-port`)

Spec: `docs/superpowers/specs/2026-09-13-slice-32-verification-gate-design.md`
(written against `development` / Windows). This plan is the `linux-port`
translation, **written alongside the implementation rather than before it** -
the decisions below were made while finding out what the spec's assumptions
did to the real tree. Status: implemented, pushed.

## What it is

`tools/verify_release.py` - one command, five steps, cheapest first, stop at
the first failure (`--keep-going` for all), each tool's own output untouched:

| # | Step | Gate rule |
|---|---|---|
| 1 | secrets | no tracked path is key-material-shaped. **Not skippable.** |
| 2 | tests | `pytest tests`; failures <= `tools/verify_baseline.json` (0); any *error* fails |
| 3 | mypy | error count <= baseline (704 - strict mode over pre-existing debt) |
| 4 | lint | `npm run lint` in `web/`; runs `npm ci` first, loudly, if `node_modules` is missing |
| 5 | deep | `health.report(deep=True)` on throwaway data; every `required` row green, minus two reasoned exemptions |

Wired into `tools/build_flatpak.ps1 -BuildPackage` (before the web build, in its
own `.venv-verify` with `.[dev,media]` - NOT the dev venv, which is deliberately
never `media`); `-SkipVerification` bypasses it with a warning. First wired into
the `.sh` this branch used to have; re-applied to the PowerShell port when that
replaced it upstream. **The `.ps1` edit has never been executed** - there is no
`pwsh` in the sandbox; it mirrors the script's own helpers line for line.

## Deep Check additions (`avg/core/health.py`)

Three rows proving what Slice 32 added, all on throwaway data, none touching a
network or the real cooldown/secret state:

- **Provider health round-trip** - a cooldown records, is scoped to *its*
  credential, and clears. The scoping assertion exists because that exact bug
  shipped once (ElevenLabs primary-key cooldown blocked the backup key); a
  negative test re-creates the old behaviour and requires the row to go red.
- **Seed request parameters** - via the new pure `_pollinations_params`
  (factored out of `generate`, which the spec pre-authorised).
- **Word-timing remap** - tone/silence/tone clip through the real
  `_clean_segment`; the word after the gap must move earlier and keep its length.

## Deliberate deviations from the spec

1. **Baseline in a JSON file, not parsed from the ledger's prose.** "Zero
   regressions vs the documented baseline" is the project's real rule; a number
   in prose gets enforced by memory. `--update-baseline` ratchets it.
2. **Fail-fast, not OR-of-all.** The spec said both; fail-fast is what "cheapest
   first" is for. `--keep-going` covers the other.
3. **Two exempt rows, each with a printed reason.** `Provider keys` (an account
   property, not a build property) and `pywebview` (the `gui` extra is kept out
   of dev venvs on purpose; the Flatpak manifest installs it). Without the
   second, the gate fails on every build from the venv the script itself makes.
4. **`make_release.ps1` (the Windows script) not touched.** Cannot be run here, and
   it is not this branch's release route.
5. **The secrets tripwire exempts source/doc suffixes** (`.py .md .ts .tsx .js
   .css`); `avg/config/secrets.py` is legitimately named that.

## Verified

Full run in the sandbox: secrets / tests (1200+) / mypy (704 = baseline) / lint
(`npm ci` + oxlint) all PASS; deep fails only on *Secret round-trip* because the
sandbox has no keyring - and a run with a throwaway keyring backend exits 0.
Not verified: a real `--build-package` (no flatpak-builder here), and the
gate on the owner's CachyOS desktop.

## Out of scope

CI wiring; auto-fixing; a GUI. Also the README's Windows-era `%APPDATA%` tables.
