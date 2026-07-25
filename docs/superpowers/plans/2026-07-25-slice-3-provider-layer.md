# Slice 3 — The provider layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Everything that talks to an outside AI service — text, images and voice — behind one error taxonomy, one retry policy, and a test suite that never spends a token.

**Replaces:** `modules/ai.ps1` (4,872 lines) and `modules/visuals.ps1` (2,009 lines) from the PowerShell app, plus its TTS handling.

**Architecture:** Extends Slices 1–2. Providers are adapters behind a common interface; the router picks one per phase and falls back on failure. Nothing in this layer prints, prompts, or knows a UI exists.

## Execution mode

Carried over: speed over ceremony. One commit per task, suite run once at the end of a task, no per-step RED/GREEN, no front-end tests. Python tests stay — here they are the whole safety net.

## Global Constraints

- **Windows only.** Python 3.12. Nothing below `avg/api/` may print or prompt.
- **Tests never make a network call and never spend money.** Every provider test runs against recorded fixtures. A test that would hit a live API is a defect.
- **No hardcoded model catalogues.** Model lists come from the live API or from the on-disk cache — never from a list baked into the source. The PowerShell app deleted its built-in catalogues deliberately; do not reintroduce them.
- **A key value never appears** in a log, an exception, a repr, or an API response. Keys come from `avg.config.secrets`.
- **Every failure maps to `ErrorKind`** (`avg/core/errors.py`, already defined): `auth`, `quota`, `billing`, `rate_limit`, `model_missing`, `content_filter`, `network`, `timeout`, `resource`, `dependency`, `bug`. Nothing outside the classifier inspects an HTTP status or greps an error string.
- Reference app (read-only): `D:\AI_Video_Gen`.

---

## File structure

| Path | Responsibility |
|---|---|
| `avg/providers/errors.py` | Raw failure → `ErrorKind`; the retry policy table |
| `avg/providers/transport.py` | HTTP with record/replay, timeouts, backoff |
| `avg/providers/text/base.py` | `TextBackend` protocol |
| `avg/providers/text/{groq,gemini,huggingface,pollinations}.py` | One backend each |
| `avg/providers/text/router.py` | Per-phase model routing, fallback chain |
| `avg/providers/images/base.py` | `ImageProvider` protocol |
| `avg/providers/images/{openai,gemini,pollinations,huggingface,stablehorde}.py` | One provider each |
| `avg/providers/voice/{elevenlabs,edge}.py` | TTS |
| `avg/providers/models.py` | Model catalogue: live fetch + disk cache |
| `tests/fixtures/http/` | Recorded responses, one file per interaction |

---

### Task 1: Error taxonomy and retry policy

The PowerShell app spread this across **eleven overlapping functions in three modules** — `Get-ApiErrorClassification`, `Get-ApiErrorMessage`, `Get-CompactApiErrorMessage`, `Test-ImageQuotaError`, `Test-ImageBillingError`, `Test-HardImageProviderError`, `Get-ElevenLabsErrorMessage`, `Resolve-AppError`, `Write-AppError`, `Write-CompactError`, `Get-HttpErrorBody`. That fragmentation is why the ElevenLabs backup-key behaviour took two releases to settle.

**Files:** Create `avg/providers/errors.py`; test `tests/providers/test_errors.py`

**Interfaces:**
- `classify(status: int | None, body: str, exc: Exception | None, provider: str) -> ErrorKind`
- `RetryRule(retryable: bool, max_attempts: int, backoff_seconds: float, on_exhausted: str)` where `on_exhausted` is `"fail"` | `"switch_key"` | `"switch_provider"` | `"switch_model"`
- `POLICY: dict[ErrorKind, RetryRule]` — **a table, not code**

**The policy, stated so it is not re-derived per provider:**

| kind | retry | on exhausted |
|---|---|---|
| `rate_limit` | yes, exponential backoff | switch_provider |
| `network`, `timeout` | yes | switch_provider |
| `auth` | **no** | switch_key |
| `quota`, `billing` | **no** | switch_provider |
| `model_missing` | no | switch_model |
| `content_filter` | no | fail |
| `bug`, `dependency` | no | fail |

A 5xx is `network` (transient, retry). A 401/403 is `auth`. A 429 is `rate_limit`. Quota and billing are distinguished by body text per provider, because they mean different things: quota may reset, billing will not.

- [ ] Implement, test, commit. Tests must cover every row of the table and each provider's quota-vs-billing distinction.

---

### Task 2: Transport with record/replay

This is what makes every later task testable for free. Build it before any provider.

**Files:** Create `avg/providers/transport.py`; test `tests/providers/test_transport.py`

- `request(method, url, *, headers, json, timeout) -> Response` over `httpx`.
- **Record mode** (`AVG_HTTP_RECORD=1`): performs the real call and writes the interaction to `tests/fixtures/http/<slug>.json`.
- **Replay mode** (the default in tests): matches on method + URL + a stable hash of the body, and returns the recorded response. **An unmatched request in replay mode raises** — it must never silently fall through to the network.
- **Recorded fixtures are scrubbed of credentials on write.** Authorization headers, `api_key` query parameters and anything key-shaped is replaced before the file is written. A fixture is committed to git; a leaked key in one is a security incident.
- Retry/backoff comes from Task 1's policy — the transport applies it, providers do not implement their own.

- [ ] Implement, test, commit. A test must prove that an unmatched request in replay mode raises rather than hitting the network, and that a recorded fixture contains no credential.

---

### Task 3: Text backends and the router

**Files:** Create `avg/providers/text/base.py`, `groq.py`, `gemini.py`, `huggingface.py`, `pollinations.py`, `router.py`, and `avg/providers/models.py`; test `tests/providers/test_text.py`, `tests/providers/test_router.py`

The old app's four backends live at `modules/ai.ps1`: `Invoke-GroqBackend` (line 41), `Invoke-PollinationsTextBackend` (117), `Invoke-GeminiBackend` (194), `Invoke-HuggingFaceBackend` (272), behind `Invoke-AI` (400). Read them for the request shapes and quirks; do not port their structure.

- `TextBackend` protocol: `name`, `generate(prompt: str, model: str, *, max_tokens, temperature) -> str`, `list_models() -> list[str]`.
- **Router:** `generate(prompt, phase, *, config) -> TextResult`. Per-phase model routing — each pipeline phase (`brief`, `script`, `director1`, `director2`) may name its own provider+model plus a backup, which is how the old app's per-phase models feature worked.
- Fallback chain driven entirely by Task 1's policy. The router never inspects a status code itself.
- `models.py`: fetch the live catalogue when possible, cache to `%APPDATA%/AVG Studio/model_cache.json`, and fall back to the cache when the API is unreachable. **Never to a hardcoded list.**

- [ ] Implement, test against recorded fixtures, commit. Tests must cover: each backend's happy path, a rate-limited backend falling through to the backup, and a model list served from cache when the API is down.

---

### Task 4: Image providers

**Files:** Create `avg/providers/images/base.py` plus `openai.py`, `gemini.py`, `pollinations.py`, `huggingface.py`, `stablehorde.py`; test `tests/providers/test_images.py`

Old equivalents in `modules/visuals.ps1`: `Invoke-OpenAIImageGeneration` (720), `Invoke-GeminiImageGeneration` (775), `Invoke-PollinationsImageGeneration` (864), `Invoke-HuggingFaceImageGeneration` (906), `Invoke-StableHordeImageGeneration` (977).

- `ImageProvider` protocol: `name`, `generate(prompt: str, *, aspect_ratio: str, model: str) -> bytes`, `supports(aspect_ratio) -> bool`.
- StableHorde is **asynchronous** — submit, then poll. Its ETA handling and its very different failure modes are why it gets its own careful treatment.
- Aspect-ratio handling differs per provider; some take a size, some take a ratio, some only support squares and need a crop. Encode that per provider rather than in the caller.
- Fallback across providers uses Task 1's policy, same as text.

- [ ] Implement, test against recorded fixtures, commit. Tests must cover each provider's happy path, a billing failure switching provider, and StableHorde's poll loop reaching a result.

---

### Task 5: Voice providers

**Files:** Create `avg/providers/voice/elevenlabs.py`, `edge.py`; test `tests/providers/test_voice.py`

- `VoiceProvider` protocol: `name`, `synthesize(text: str, *, voice_id: str, model: str) -> bytes`, `list_voices()`.
- **ElevenLabs key fallback, stated precisely because the old app got it wrong twice:** on `auth` or `quota`, retry once with the configured backup key. On a **5xx, do NOT switch keys** — a server error says nothing about the key, and switching wastes the backup's quota. When both keys fail, fall back to Edge TTS.
- Edge TTS is free, local-ish and has no key; it is the last resort and must always work.

- [ ] Implement, test against recorded fixtures, commit. Tests must cover: primary key succeeding, auth failure switching to the backup key, a 5xx **not** switching keys, and both failing over to Edge.

---

## Out of scope for Slice 3

The eight pipeline phases, the create wizard, prompt construction, the learning system, trend discovery, app chat. Slice 4 assembles these providers into the pipeline.
