# Slice 32 (sub-slice 1) — Provider Resilience: Design

**Source:** ports `AI_Video_Gen`'s B2.22 Part 1 (`spec_B2.22_part1_provider_resilience.md`,
commit `bff265e`). Highest priority of Slice 32's four sub-slices, same
ranking B2.22 gave it, for the same reason: every other sub-slice's
providers inherit whatever this one gets right or wrong.

**This is not a straight port.** `AI_Video_Gen` needed Part 1 because one
`QUOTA_RATE` bucket covered three different situations and three independent
hand-rolled fallback stacks (text/image/voice) had already drifted out of
sync once (the ElevenLabs backup-key bug `avg/providers/errors.py`'s own
docstring cites). AVG Studio never had that problem: `avg/core/errors.py`'s
`ErrorKind` already separates `rate_limit` / `quota` / `billing`, and
`avg/providers/errors.py`'s `classify()` + `POLICY` is already the one
choke point every provider goes through. What B2.22 Part 1 actually
contributes here is two things Studio's taxonomy doesn't do yet: **use the
real wait time a provider tells you** instead of a guessed backoff, and
**remember a dead key across calls** instead of re-trying it immediately.

## 1. Current state (verified in code, `development` @ `4d48ec8`)

- `avg/providers/errors.py`: `ErrorKind.rate_limit` / `.quota` / `.billing`
  already exist as distinct values (lines 5-9, `avg/core/errors.py`).
  `POLICY[rate_limit]` is `retryable=True, max_attempts=5,
  backoff_seconds=2.0, on_exhausted="switch_provider"`; `quota`/`billing`
  are both `retryable=False, max_attempts=1, on_exhausted="switch_provider"`
  (lines 71-81). `classify()`'s signature is `classify(status, body, exc,
  provider)` — no header access, ever.
- `avg/providers/transport.py`'s `_send_with_retry` (lines 376-424) is the
  ONE retry loop every HTTP-based provider shares. Its backoff is
  `rule.backoff_seconds * (2 ** (attempt - 1))` — a fixed formula, never a
  provider-reported wait. No `Retry-After` header is ever read; no JSON
  body (`error.details[].retryDelay`) is ever parsed. `response.headers` is
  available at the call site (line ~412) but nothing downstream sees it.
- **No persisted state exists at all.** A `quota`/`billing` failure raises
  immediately (`on_exhausted="switch_provider"`, `max_attempts=1`) and the
  caller falls back for *that run* — but the very next call, in the same
  run or a brand-new session, tries the same dead provider again from
  scratch. `avg/pipeline/phases/visuals.py`'s `_should_fall_back` (fallback
  gate) and `avg/providers/text/router.py`'s `generate()` (1-deep
  primary→backup→default) both re-discover the same failure every time.
- `avg.config.secrets` is **one key per provider name**, no fallback-key
  slot concept (`avg/providers/text/router.py`'s own docstring: "Deliberately
  NOT ported: the legacy per-provider fallback key... `avg.config.secrets`
  exposes one key per provider"). ElevenLabs is the one exception — it has
  its own primary/backup key pair, handled entirely inside
  `ElevenLabsVoiceProvider` (`avg/providers/voice/providers.py`), not through
  `avg.config.secrets`'s shape. This means B2.22's "per-key, not
  per-provider" granularity (decision #4) doesn't apply the same way here:
  Studio's cooldown state is naturally **per-provider**, with ElevenLabs'
  own key-switch logic staying exactly as it is (untouched by this
  sub-slice — a key-switch failure still propagates to the router layer
  as one `AvgError`, which is what gets a provider-level cooldown).
- `avg/pipeline/phases/visuals.py`'s image generation runs scenes through a
  `ThreadPoolExecutor` (`_generate_all`, lines 300-322) — several worker
  threads in **one process**, not separate OS processes or runspaces. A
  shared cooldown state file therefore needs an in-process
  `threading.Lock`, not a cross-process mutex (legacy needed a named
  `System.Threading.Mutex` because PowerShell's `Start-ThreadJob` and the
  render engine's child process are genuinely separate OS processes;
  neither is true here — `avg.pipeline` and `avg.providers` share one
  Python interpreter for the whole app's lifetime).
- Every config document in this codebase (`avg/config/settings.py`,
  `channels.py`, `secrets.py`, `channel_defaults.py`, `quick_settings.py`,
  `avg/pipeline/create.py`) writes atomically: `tempfile.mkstemp(dir=...)`
  then `os.replace(...)`. A new persisted-state file follows this
  convention exactly, not a new one.
- `avg.config.secrets.set_key`/`get_key` have no hook mechanism (no
  observer, no callback) — `secrets.py`'s docstring is explicit that it is
  "the ONLY module that encrypts/decrypts/persists keys," full stop. A
  billing-cooldown auto-clear-on-key-change (B2.22 decision #3) therefore
  cannot be a hook *inside* `secrets.py`; it has to be a fingerprint
  comparison done lazily, at read time, by the new module — exactly
  legacy's own approach, just without a `set_key` hook either app ever had.
- `avg/core/health.py`'s `_safeguard_checks` (Stage 3, lines 315-375) is
  where "a key at zero quota, found before a run starts" already lives in
  spirit (`Provider keys` check) — a natural place to add a `Provider
  cooldowns` row once cooldown state exists. No such row exists today.

## 2. Decisions

Mostly B2.22's own four decisions, adapted to the two structural
differences above (per-provider not per-key, in-process lock not a mutex):

1. **Rate-limit → wait it out using the real number, then retry the same
   provider.** `_send_with_retry` uses `Retry-After` (header) or a parsed
   body delay (Gemini's `error.details[].retryDelay`) *instead of* the
   fixed exponential formula, when one is present — capped at a sane
   ceiling so a provider claiming a 6-hour wait doesn't hang a pipeline run.
   This still spends attempts out of `POLICY[rate_limit].max_attempts`
   (already 5) — **no separate retry-budget carve-out**, unlike legacy.
   Legacy needed one because a rate-limit wait sat inside a *different*,
   stingier per-phase retry budget than the fallback decision; Studio's
   `POLICY` table is already the only budget there is, and 5 attempts with
   real wait times is already generous enough to reach a genuine fallback
   decision without a second bookkeeping concept.
2. **Quota/billing exhausted → persist a per-provider cooldown, skip the
   provider on every subsequent call (including a new session) until it
   clears.** Quota gets a `cooldown_until` (an actual timer); billing gets
   none — cleared only by a key-fingerprint mismatch (decision #3 below).
3. **Billing cooldown clears when the configured key changes.** Checked
   lazily against `avg.config.secrets.get_key(provider)`'s current value at
   query time — see "Current state" above for why this can't be a
   `secrets.py` hook.
4. **Granularity is per-provider**, not per-key-slot (Studio has no
   key-slot concept outside ElevenLabs' own internal pair, which this
   sub-slice does not touch — see above).
5. **Applies at the one existing choke point** (`Transport`), so text,
   image, and every `httpx`-based voice call inherit it automatically with
   one change, not three. Edge TTS (the one voice provider that bypasses
   `Transport` entirely — no HTTP request/response, it's a websocket
   library) gets the identical pre-flight-check/record-on-failure calls
   made explicitly at its own `classify()` call site
   (`avg/providers/voice/providers.py`), since there is no shared choke
   point to inherit from for that one provider.
6. **Fully automatic, no prompt** — matches existing behavior; nothing in
   Studio ever prompted `[Y/N]` for a provider switch, so there is no
   existing UX to preserve here.
7. **Visible, not silent.** A cooldown starting is folded into the
   `ProgressEvent` message the calling phase already emits when it falls
   back (`visuals.py`/`router.py`/`voice.py` already emit one on fallback);
   `avg/core/health.py` gets a new `Provider cooldowns` safeguard row.

## 3. Design

### A. `classify()` and `_send_with_retry` gain header/body awareness

- `avg.providers.errors.classify()` signature grows one optional parameter:
  `classify(status, body, exc, provider, headers=None)`. Existing callers
  (there are several across `visuals.py`, `voice.py`, `providers.py`) are
  unaffected — `headers` defaults to `None`, and `classify()`'s return
  shape doesn't change; header-derived precision is best-effort only, same
  principle B2.22's own spec states for the legacy port.
- New pure function, `avg.providers.errors.retry_after_seconds(headers,
  body) -> float | None`: checks a `Retry-After` header first (seconds, or
  an HTTP-date — only the seconds form is expected from Groq/OpenAI-shaped
  APIs, but an HTTP-date is parsed rather than crashing on it), then falls
  back to parsing `error.details[].retryDelay` out of a Gemini-shaped JSON
  body (`"15s"` → `15.0`). Returns `None` when neither is present — the
  existing fixed-backoff formula is exactly that fallback, unchanged.
- `_send_with_retry`'s `response.status_code >= 400` branch (the only
  branch with a real HTTP response to read) passes `response.headers` and
  `body_text` through to `classify()`, and when `kind == ErrorKind.
  rate_limit`, uses `retry_after_seconds(...)` in place of the exponential
  formula when it returns a value, clamped to a config-driven ceiling (see
  §4). The `httpx.RequestError` branch (no response at all) is unaffected
  — there is nothing to parse a delay out of.

### B. Persisted per-provider cooldown state (new module)

`avg/config/provider_health.py`, following `channels.py`'s file layout
exactly (module-level `threading.Lock`, atomic `tempfile.mkstemp` +
`os.replace`, "corrupt file → treat as empty, never hard-lock" — the same
tolerance `avg.config.settings.load_settings` already has for a corrupt
`settings.json`):

```json
{
  "version": 1,
  "providers": {
    "groq":       {"status": "quota",   "since": "2026-09-13T14:22:00Z", "cooldown_until": "2026-09-14T00:00:00Z", "note": "daily quota"},
    "elevenlabs": {"status": "billing", "since": "2026-09-12T10:00:00Z", "cooldown_until": null, "key_fingerprint": "a1b2c3d4e5f6", "note": "insufficient balance"}
  }
}
```

Public functions:

- `is_in_cooldown(provider: str) -> bool` — `False` if no entry; for
  `status="quota"`, compares `cooldown_until` to now (UTC); for
  `status="billing"`, compares the entry's stored `key_fingerprint` against
  `avg.config.secrets.get_key(provider)`'s current value, hashed the same
  way — a mismatch (including "key now unset") returns `False`, implementing
  decision #3 without touching `secrets.py`.
- `record_cooldown(provider: str, status: Literal["quota","billing"], *,
  cooldown_until: datetime | None = None, note: str = "") -> None` —
  writes/overwrites the provider's entry. `key_fingerprint` is computed
  internally from the *current* key at write time (SHA-256, first 12 hex
  chars, never the raw key — matches `avg.config.transport`'s own
  redaction philosophy of never letting a credential reach disk unhashed).
- `clear_cooldown(provider: str) -> None` — explicit removal, used by
  `is_in_cooldown` itself when it detects a stale billing entry (opportunistic
  cleanup, not required for correctness since the check is already lazy).
- `default_quota_cooldown_until(provider: str, *, hours: float) -> datetime`
  — `now + hours`, called by whoever catches the failure (see §C), not by
  this module — `provider_health.py` stores state, it doesn't own policy
  about how long a cooldown should last.

File location: `user_assets_root() / "Config" / "provider_health.json"`
(`avg.config.user_assets.user_assets_root()`, currently `Documents/AVG
Studio/UserAssets` — the README's data-documents table still says
`%APPDATA%/AVG Studio/`, which predates the Slice 20 UserAssets
consolidation and is stale) — the same `Config/` directory `settings.json`,
`channel_defaults.json`, `quick_settings.json`, and `model_prefs.json`
already share; `secrets.dat` lives in the separate `Secrets/` subdirectory
and this is not a credential, so it belongs with the former group, not the
latter. Following the "one flat file per concern" pattern every sibling
module already uses — no new subdirectory needed.

### C. Where the pre-flight check and the write happen

- **Pre-flight (skip the call):** `Transport.request()` gains one check at
  the top, before anything touches the network: if `provider` is
  non-empty and `avg.config.provider_health.is_in_cooldown(provider)` is
  `True`, raise the equivalent `AvgError` immediately (`ErrorKind.quota` or
  `.billing`, matching the recorded `status`) — no request is sent, exactly
  legacy's "skip the call entirely" rule. `replay` mode is unaffected: a
  test replaying a fixture never touches `provider_health.json` in the
  first place (module-level state a test doesn't set up stays empty, so
  `is_in_cooldown` is always `False` for every existing test — no fixture
  or test needs to change).
- **Write (record the failure):** `_send_with_retry`'s non-retryable branch
  — where it currently just raises `AvgError` for `quota`/`billing` — calls
  `record_cooldown` first. For `quota`, `cooldown_until` comes from
  `default_quota_cooldown_until(provider, hours=<config>)`; for `billing`,
  `cooldown_until=None` (decision #3's timerless case).
- **Edge TTS's own path** (`avg/providers/voice/providers.py`,
  `EdgeVoiceProvider.synthesize`/`list_voices`): the same pre-flight check
  and the same `record_cooldown` call, made explicitly at its `classify(None,
  "", exc, self.name)` call sites, since this provider never goes through
  `Transport`. In practice Edge TTS has no authenticated quota/billing
  concept at all (it's Microsoft's free, keyless endpoint) — this wiring
  exists for uniformity and for the rare case Microsoft's endpoint itself
  starts rate-limiting by IP, not because a billing failure is expected
  here.

### D. Surfacing

- `avg/pipeline/phases/visuals.py` (image fallback), `avg/providers/text/
  router.py` (text fallback — its `generate()` currently has no
  `ctx.emit`, being below the pipeline layer; the calling phase, e.g.
  `avg/pipeline/phases/director.py`, already emits its own progress events
  and gains one more line when `used_backup` is `True` and the primary's
  `AvgError.kind` was quota/billing), and `avg/pipeline/phases/voice.py`
  each get their existing fallback `ProgressEvent` message extended with
  the cooldown detail when applicable — e.g. "Falling back to
  gemini/gemini-2.5-flash... (groq: daily quota reached, back ~00:00 UTC)."
  No new event type — this is strictly a message-text addition to events
  that already fire.
- `avg/core/health.py`'s `_safeguard_checks` gains one new check,
  `Provider cooldowns`: lists every entry currently in
  `provider_health.json` with `is_in_cooldown(...) is True`, `ok=(no active
  cooldowns)`, `detail` naming which provider(s) and until when. Mirrors
  the existing `Provider keys` check immediately above it in that function
  — same category, same non-fatal `ok=False` treatment (a cooldown is
  informative, not a broken install).

## 4. Config additions

Two new `SettingSpec` entries, group `"app"` (cooldown behavior is a
process-wide policy, not a per-channel creative choice — matches every
existing `group="keys"` entry's `scope="app"`), `invalidates=None` (nothing
about a resume/re-run needs to change because this changed):

- `resilience_rate_limit_wait_ceiling_sec` — `int`, default `120`. The cap
  applied to a provider-reported `Retry-After`/body delay before it's ever
  used as a real sleep duration.
- `resilience_quota_cooldown_hours` — `float`, default `24.0`. Used by
  `default_quota_cooldown_until` when a provider has no better-documented
  reset window (no per-provider override table exists yet — Groq's/
  Gemini's own documented windows can be layered in later without a schema
  change, since this is only ever the *fallback* value).

## 5. Data/schema summary

| File | Change |
|---|---|
| `avg/core/errors.py` | Untouched — `ErrorKind` already has the three values needed |
| `avg/providers/errors.py` | `classify()` gains optional `headers` param; new `retry_after_seconds()` |
| `avg/config/provider_health.py` | New — `is_in_cooldown`/`record_cooldown`/`clear_cooldown`/`default_quota_cooldown_until` |
| `avg/providers/transport.py` | `Transport.request()`: pre-flight cooldown check. `_send_with_retry`: real wait time when available; records cooldown on quota/billing before raising |
| `avg/providers/voice/providers.py` | `EdgeVoiceProvider`: same pre-flight check + `record_cooldown` at its own `classify()` sites |
| `avg/pipeline/phases/visuals.py`, `voice.py`, `director.py` | Existing fallback `ProgressEvent` messages gain the cooldown detail when applicable |
| `avg/core/health.py` | `_safeguard_checks`: new `Provider cooldowns` row |
| `avg/core/settings_schema.py` | +2 `SettingSpec`s (`resilience_rate_limit_wait_ceiling_sec`, `resilience_quota_cooldown_hours`) |
| `Config/provider_health.json` (under `user_assets_root()`) | New, user-machine state, not committed — same treatment as every sibling file in that directory |

## 6. Testing

`retry_after_seconds`: header seconds form, header HTTP-date form, Gemini
body `retryDelay` form, absent-in-both → `None`. `provider_health.py`:
round-trip (write/read), corrupt-file tolerance, `is_in_cooldown` for
active/expired quota, billing with matching/mismatched/absent key
fingerprint, concurrent writes from a small thread pool (mirrors
`visuals.py`'s own `ThreadPoolExecutor` shape — a lock test, not a mutex
test, since this is one process). `Transport`: a fixture-driven test
confirming a cooldown-active provider never reaches `_send_with_retry` at
all (pre-flight short-circuit), and a quota/billing failure writes the
expected entry before raising. Fallback message text: cooldown detail
appears when the primary's failure was quota/billing, absent otherwise (no
regression to the existing fallback message tests). No live provider call
in any of this — same `AVG_HTTP_RECORD`-gated convention as every other
provider test in this codebase.

## 7. Out of scope

- Per-provider documented reset windows beyond the one configurable
  fallback (`resilience_quota_cooldown_hours`) — captured as real data
  during implementation if a provider's own docs are checked, not guessed
  here.
- Any change to ElevenLabs' own primary/backup key-switch logic — that
  stays exactly as it is; a cooldown applies to the *provider* (both keys
  exhausted, in effect), not to which of ElevenLabs' two keys tried first.
- Parts 2-4 (visual/motion consistency, captions, verification gate) —
  separate sub-slices.

## 8. Open risks / to verify during implementation

- Groq's/Gemini's exact current header and body shapes should be
  re-confirmed against a real captured response at implementation time
  (B2.22's own spec already did this research once against those two
  providers' documented APIs — worth a fresh check since provider APIs do
  shift, per that spec's own "model naming has shifted before" caution
  elsewhere).
- Whether an HTTP-date `Retry-After` value ever actually appears on any
  provider this app calls — parsed defensively either way, but not a
  confirmed real case.
