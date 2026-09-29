# Slice 15 — Smart model picker: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn 566 catalogue entries into a short list with a reason attached to each — without the app ever stating as fact something it did not measure.

**Architecture:** Four tasks. One new provider-layer module that asks a text model to choose from the catalogue the app actually holds, storage for its answers beside the user's ticks, one route, and a fourth provenance chip on the Live List. A suggestion is always an opinion; only a probe produces a fact.

**Tech Stack:** Python 3.12 + FastAPI (backend), React 19 + TypeScript (frontend), pytest, mypy, oxlint.

**Source spec:** `docs/superpowers/specs/2026-07-31-slice-15-smart-model-picker-design.md`

## Global Constraints

Every task's requirements implicitly include all of these.

- **Interpreter is `.venv-build/Scripts/python.exe`.** Tests: `-m pytest tests -q`. Types: `-m mypy`.
- **Never go below the suite's count when you start.**
- **mypy baseline is `Found 688 errors in 22 files`.** Add nothing to it.
- **No live API calls in tests, ever.** `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1`.
- **`working_models.json` is machine-owned** and replaced wholesale on every sweep; it records what was **measured**. The user's ticks, favourites and notes live in `model_prefs.json`. **A suggestion is an opinion and goes in `model_prefs.json`** — never into the cache. `avg/core/model_cache.py`'s docstring is the rule: "only models a probe PROVED working are ever returned… an empty result has to read as 'build your cache', never as a list of guesses."
- **A measurement outranks an opinion.** An entry that is both probed and suggested reports `probed`.
- **A suggestion never ticks anything.** The user chooses.
- **Nothing below `avg/api/` may print or prompt.**
- **Never use a native form control**; never add a Google Fonts `@import`.
- **One filled terracotta `.btn--primary` primary action per screen.** On the Live List that is `Refresh catalogues`.
- **Reuse `web/src/tokens.css` values.**
- **The frontend has no unit tests by convention.**
- **Match code by content, not by line number.**
- **Commit once per task**, on local `main`. No push, no PR, no GitHub interaction.

**Dependency:** the Live List opens from Settings after **Slice 12 Task 6**. This slice does not move it and does not depend on the move having happened.

---

## Task 1: `avg/providers/recommend.py`

**Files:**
- Create: `avg/providers/recommend.py`
- Create: `tests/providers/test_recommend.py`

**Interfaces:**
- Consumes: `avg.providers.text.router.generate(prompt, phase, *, config) -> TextResult` (`TextResult` has `.text`, `.provider`, `.model`, `.used_backup`), and `router.config_from_settings(...)`.
- Produces, and Tasks 2–3 depend on these exactly:
  - `@dataclass(frozen=True, slots=True) class Suggestion: entry_id: str; kind: str; tier: str | None; reason: str`
  - `suggest_models(catalogue: dict[str, list[str]], *, config: Any) -> list[Suggestion]`
  - `suggest_voices(catalogue: list[str], *, config: Any) -> list[Suggestion]`
  - `TIERS: tuple[str, ...] = ("cheap", "balanced", "high", "ultra")`

**Why:** Measured 2026-07-31 after one refresh: `__text__gemini` 33, `__text__huggingface` 20, `__text__groq` 10, `__image__stablehorde` 155, `__voice__edge` **322**, `__voice__elevenlabs` 26 — 566 entries.

The model is sent the catalogue the app actually holds, so it chooses from real entries rather than recalling names — **that part is grounded**. Only the tier and the reason are its opinion, because `avg/core/costs.py` holds per-**phase** prices (`LEAF_COST`, ten entries keyed by pipeline leaf) and **no per-model price exists anywhere in the codebase**.

**Every returned `entry_id` is validated against the catalogue that was sent.** A model inventing a plausible id is the exact failure this guard exists for.

- [ ] **Step 1: Write the failing tests**

Create `tests/providers/test_recommend.py`:

```python
"""Model recommendations. Never a live call: every test here injects the
text response rather than generating one.
"""

from __future__ import annotations

import pytest

from avg.providers import recommend


def _reply(monkeypatch, text: str) -> None:
    """Replace the router's generate with a canned answer."""

    class _Result:
        def __init__(self, t: str) -> None:
            self.text = t
            self.provider = "groq"
            self.model = "test"
            self.used_backup = False

    monkeypatch.setattr(recommend, "_generate", lambda prompt, config: _Result(text))


def test_a_suggestion_carries_a_tier_and_a_reason(monkeypatch):
    _reply(
        monkeypatch,
        '[{"entry_id": "groq/llama-3.1-8b", "kind": "text", "tier": "cheap",'
        ' "reason": "fast and cheap for drafting"}]',
    )
    out = recommend.suggest_models({"text": ["groq/llama-3.1-8b"], "image": []}, config=None)

    assert len(out) == 1
    assert out[0].entry_id == "groq/llama-3.1-8b"
    assert out[0].tier == "cheap"
    assert "drafting" in out[0].reason


def test_an_entry_that_was_not_in_the_catalogue_is_dropped(monkeypatch):
    """The most important guard here. A model naming a plausible id it was
    never shown must not reach the Live List."""
    _reply(
        monkeypatch,
        '[{"entry_id": "openai/gpt-9-ultra", "kind": "text", "tier": "ultra", "reason": "invented"},'
        ' {"entry_id": "groq/real", "kind": "text", "tier": "cheap", "reason": "real"}]',
    )
    out = recommend.suggest_models({"text": ["groq/real"], "image": []}, config=None)

    assert [s.entry_id for s in out] == ["groq/real"]


def test_a_reply_that_is_not_json_yields_nothing(monkeypatch):
    _reply(monkeypatch, "Sure! Here are my picks: use Llama, it's great.")
    assert recommend.suggest_models({"text": ["groq/real"], "image": []}, config=None) == []


def test_voices_carry_no_tier(monkeypatch):
    """Voices have no cost axis, so a tier there would be a meaningless
    value dressed as information."""
    _reply(
        monkeypatch,
        '[{"entry_id": "edge/en-GB-RyanNeural", "kind": "voice", "tier": "ultra",'
        ' "reason": "warm narration voice"}]',
    )
    out = recommend.suggest_voices(["edge/en-GB-RyanNeural"], config=None)

    assert len(out) == 1
    assert out[0].tier is None
    assert out[0].kind == "voice"


def test_an_unknown_tier_becomes_none_rather_than_being_invented(monkeypatch):
    _reply(
        monkeypatch,
        '[{"entry_id": "groq/real", "kind": "text", "tier": "spectacular", "reason": "x"}]',
    )
    out = recommend.suggest_models({"text": ["groq/real"], "image": []}, config=None)
    assert out[0].tier is None
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/providers/test_recommend.py -q
```

Expected: FAIL — `ModuleNotFoundError: avg.providers.recommend`.

- [ ] **Step 3: Write the module**

Create `avg/providers/recommend.py`:

```python
"""Ask a text model which models and voices are worth shortlisting.

**What is grounded and what is not.** The model is sent the catalogue this
app actually holds, so it chooses from real entries rather than recalling
names - and every id it returns is validated back against that catalogue,
because a model inventing a plausible id is the failure this exists to
catch. The TIER and the REASON are its opinion: `avg.core.costs` holds
per-PHASE prices and there is no per-model price anywhere in this codebase,
so a cost ranking is recollection from training data for a market that
changes without notice.

That is why a suggestion is stored in `model_prefs.json` beside the user's
own ticks and never in `working_models.json`, and why the Live List chips it
`Suggested` rather than `Verified`. `avg.core.model_cache`'s rule is
inherited verbatim: only models a probe PROVED working are ever reported as
working, and an empty result reads as "build your cache", never as a list of
guesses.

Voices carry no tier. They have no cost axis, so a tier there would be a
meaningless value dressed as information.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: Cost tiers, for text and image only. The model's claim, never a measured
#: price - see the module docstring.
TIERS: tuple[str, ...] = ("cheap", "balanced", "high", "ultra")


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One recommendation. `tier` is `None` for voices, and for any tier the
    model returned that is not one of `TIERS` - an unrecognised tier is
    dropped rather than passed through as though it meant something."""

    entry_id: str
    kind: str
    tier: str | None
    reason: str


def _generate(prompt: str, config: Any) -> Any:
    """The one call out. A seam so tests inject a reply instead of spending
    one, and so this module holds no HTTP of its own."""
    from avg.providers.text import router  # noqa: PLC0415

    return router.generate(prompt, "brief", config=config)


def _parse(raw: str, allowed: set[str], *, tiers_allowed: bool) -> list[Suggestion]:
    """Validated suggestions out of a model's reply.

    Anything not parseable yields nothing rather than raising: a malformed
    reply is a suggestion run that produced nothing, which the screen can
    state, and never an exception reaching the route.
    """
    text = raw.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:  # noqa: BLE001 - an unparseable reply is simply no suggestions
        return []
    if not isinstance(parsed, list):
        return []

    out: list[Suggestion] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get("entry_id", "")).strip()
        # The guard that matters: a model may only recommend what it was
        # shown. An invented id would otherwise reach the Live List looking
        # exactly like a real one.
        if entry_id not in allowed:
            continue
        raw_tier = str(item.get("tier", "")).strip().lower()
        tier = raw_tier if (tiers_allowed and raw_tier in TIERS) else None
        out.append(
            Suggestion(
                entry_id=entry_id,
                kind=str(item.get("kind", "")).strip() or "text",
                tier=tier,
                reason=str(item.get("reason", "")).strip(),
            )
        )
    return out


def suggest_models(catalogue: dict[str, list[str]], *, config: Any) -> list[Suggestion]:
    """Text and image models, in one call - they share a cost axis.

    `catalogue` is `{"text": [...], "image": [...]}` of `provider/model` ids.
    """
    text_ids = list(catalogue.get("text", []))
    image_ids = list(catalogue.get("image", []))
    allowed = set(text_ids) | set(image_ids)
    if not allowed:
        return []

    prompt = (
        "You are helping choose AI models for a short-form video generator.\n"
        "Pick at most four TEXT models and at most four IMAGE models from the "
        "lists below, one for each cost tier where you can.\n"
        f"Cost tiers: {', '.join(TIERS)}.\n\n"
        f"TEXT MODELS:\n{chr(10).join(text_ids)}\n\n"
        f"IMAGE MODELS:\n{chr(10).join(image_ids)}\n\n"
        "Reply with ONLY a JSON array, no prose. Each element: "
        '{"entry_id": "<exactly one id from the lists above>", '
        '"kind": "text" or "image", '
        f'"tier": one of {list(TIERS)}, '
        '"reason": "<one short sentence>"}'
    )
    return _parse(_generate(prompt, config).text, allowed, tiers_allowed=True)


def suggest_voices(catalogue: list[str], *, config: Any) -> list[Suggestion]:
    """Voices, in their own call. No cost axis, so no tier is asked for."""
    allowed = set(catalogue)
    if not allowed:
        return []

    prompt = (
        "You are helping choose narration voices for a short-form video generator.\n"
        "Pick at most four voices from the list below - the best for clear, "
        "engaging narration.\n\n"
        f"VOICES:\n{chr(10).join(sorted(allowed))}\n\n"
        "Reply with ONLY a JSON array, no prose. Each element: "
        '{"entry_id": "<exactly one id from the list above>", '
        '"kind": "voice", "reason": "<one short sentence>"}'
    )
    return _parse(_generate(prompt, config).text, allowed, tiers_allowed=False)
```

- [ ] **Step 4: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/providers/test_recommend.py -q
```

Expected: PASS (5 tests).

- [ ] **Step 5: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 5 more than your starting count.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 6: Commit**

```bash
git add avg/providers/recommend.py tests/providers/test_recommend.py
git commit -m "feat: ask a model which models are worth shortlisting, and check its answer"
```

---

## Task 2: Suggestions stored beside the ticks, never in the cache

**Files:**
- Modify: `avg/core/model_prefs.py`
- Test: `tests/core/test_model_prefs.py`

**Interfaces:**
- Consumes from Task 1: `Suggestion`.
- Produces, and Task 3 depends on these exactly:
  - `ModelPrefs.set_suggestions(rows: list[dict[str, Any]]) -> None` — replaces wholesale
  - `ModelPrefs.suggestions() -> dict[str, dict[str, Any]]` — by `entry_id`

**Why:** `working_models.json` is machine-owned and replaced wholesale on every sweep; it records what was **measured**. Folding an opinion into it would break the one guarantee that file makes. `model_prefs.json` already holds the things that cannot be regenerated — ticks, favourites, notes — and a suggestion belongs with them.

A new suggestion run replaces the previous suggestions wholesale and **touches no tick, favourite or note**.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_model_prefs.py`:

```python
def test_suggestions_live_beside_the_ticks_and_replace_wholesale(tmp_path):
    from avg.core.model_prefs import ModelPrefs

    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "groq/a", selected=True, note="mine")

    prefs.set_suggestions([
        {"entry_id": "groq/a", "kind": "text", "tier": "cheap", "reason": "fast"},
        {"entry_id": "groq/b", "kind": "text", "tier": "high", "reason": "better"},
    ])
    assert set(prefs.suggestions()) == {"groq/a", "groq/b"}

    # A second run replaces, never merges - a stale suggestion must be able
    # to disappear, exactly as a stale probe verdict can.
    prefs.set_suggestions([{"entry_id": "groq/c", "kind": "text", "tier": None, "reason": "x"}])
    assert set(prefs.suggestions()) == {"groq/c"}


def test_a_suggestion_run_never_disturbs_what_the_user_wrote(tmp_path):
    """The one thing in this document that cannot be regenerated."""
    from avg.core.model_prefs import ModelPrefs

    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "groq/a", selected=True, favourite=True, note="warm, good for narration")

    prefs.set_suggestions([{"entry_id": "groq/a", "kind": "text", "tier": "cheap", "reason": "fast"}])

    entry = prefs.all("text")["groq/a"]
    assert entry.selected is True
    assert entry.favourite is True
    assert entry.note == "warm, good for narration"


def test_suggestions_are_empty_before_any_run(tmp_path):
    from avg.core.model_prefs import ModelPrefs

    assert ModelPrefs(tmp_path / "model_prefs.json").suggestions() == {}
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_model_prefs.py -q
```

Expected: FAIL — `'ModelPrefs' object has no attribute 'set_suggestions'`.

- [ ] **Step 3: Store them**

`ModelPrefs._load` currently returns only the document's `entries` and filters to `KINDS`. Suggestions are a sibling section, so add two methods that read and write the whole document rather than going through `_load`/`_save`:

```python
    def _load_document(self) -> dict[str, Any]:
        """The whole document, not just `entries`.

        `_load` deliberately returns only the entry sections; suggestions are
        a sibling of those, so reading and writing them needs the document
        itself.
        """
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a broken file means "nothing curated yet"
            return {}
        return data if isinstance(data, dict) else {}

    def suggestions(self) -> dict[str, dict[str, Any]]:
        """What an AI last recommended, by entry id.

        An OPINION, stored here rather than in `working_models.json` because
        that document records what was MEASURED and is replaced wholesale by
        every probe. See `avg.providers.recommend`.
        """
        raw = self._load_document().get("suggested")
        if not isinstance(raw, dict):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    def set_suggestions(self, rows: list[dict[str, Any]]) -> None:
        """Replace every suggestion with `rows`, touching nothing else.

        Wholesale, like a probe sweep: a suggestion that is no longer made
        has to be able to disappear. The user's ticks, favourites and notes
        live in `entries` and are rewritten untouched - they are the one
        thing in this document that cannot be regenerated.
        """
        document = self._load_document()
        document["schema_version"] = SCHEMA_VERSION
        document.setdefault("entries", {})
        document["suggested"] = {
            str(row["entry_id"]): {
                "kind": str(row.get("kind", "")),
                "tier": row.get("tier"),
                "reason": str(row.get("reason", "")),
            }
            for row in rows
            if row.get("entry_id")
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".model_prefs-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2, sort_keys=True)
            os.replace(tmp_name, self.path)
        except BaseException:
            with suppress(OSError):
                os.remove(tmp_name)
            raise
```

Add `Any` to the module's `typing` import if it is not already there.

- [ ] **Step 4: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_model_prefs.py -q
```

Expected: PASS.

- [ ] **Step 5: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 3 more than after Task 1.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 6: Commit**

```bash
git add avg/core/model_prefs.py tests/core/test_model_prefs.py
git commit -m "feat: store AI suggestions beside the user's ticks, not in the probe cache"
```

---

## Task 3: `POST /api/live-list/suggest`, and a fourth provenance

**Files:**
- Modify: `avg/api/routes_settings.py` (the route, and `_entry_rows`)
- Test: `tests/api/test_live_list_routes.py`

**Interfaces:**
- Consumes from Tasks 1–2: `suggest_models`, `suggest_voices`, `Suggestion`, `ModelPrefs.set_suggestions`, `ModelPrefs.suggestions`.
- Produces, and Task 4 depends on these exactly:
  - `POST /api/live-list/suggest` body `{"kind": "models" | "voices"}` → `{"started": true}`, 202
  - `GET /api/live-list` rows gain `source: "suggested"`, plus `tier: string | null` and `reason: string`

**Why:** The Live List already labels every entry by provenance — `probed: "Verified"`, `catalogue: "Listed"`, `manual: "Added by you"`. A suggestion joins that vocabulary rather than sitting outside it.

**A measurement outranks an opinion**: an entry that is both suggested and probed reports `probed`.

`CLAUDE.md` records only Groq as currently generating text, so the feature that recommends models is itself a text call and cannot run before one is proven — it must say so rather than fail obscurely.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_live_list_routes.py`:

```python
def test_a_suggestion_run_stores_rows_and_reports_them(client, monkeypatch):
    from avg.providers.recommend import Suggestion

    monkeypatch.setattr(
        "avg.api.routes_settings.recommend.suggest_models",
        lambda catalogue, *, config: [
            Suggestion(entry_id="groq/a", kind="text", tier="cheap", reason="fast")
        ],
    )
    assert client.post("/api/live-list/suggest", json={"kind": "models"}, headers=AUTH).status_code == 202
    client.app.state.suggest_thread.join(timeout=5)

    rows = client.get("/api/live-list", headers=AUTH).json()["text"]
    row = next(r for r in rows if r["id"] == "groq/a")
    assert row["source"] == "suggested"
    assert row["tier"] == "cheap"
    assert row["reason"] == "fast"


def test_a_measurement_outranks_an_opinion(client, monkeypatch):
    """Suggested and Verified are not exclusive - but once a probe proves a
    model, the chip must say so."""
    from avg.providers.recommend import Suggestion

    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models",
        lambda selection, **k: {"groq": {"a": True}},
    )
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)
    monkeypatch.setattr(
        "avg.api.routes_settings.recommend.suggest_models",
        lambda catalogue, *, config: [
            Suggestion(entry_id="groq/a", kind="text", tier="cheap", reason="fast")
        ],
    )
    client.patch("/api/live-list/text/groq/a", json={"selected": True}, headers=AUTH)
    client.post("/api/live-list/suggest", json={"kind": "models"}, headers=AUTH)
    client.app.state.suggest_thread.join(timeout=5)
    client.post("/api/models/cache/build", headers=AUTH)
    client.app.state.probe_thread.join(timeout=5)

    rows = client.get("/api/live-list", headers=AUTH).json()["text"]
    assert next(r for r in rows if r["id"] == "groq/a")["source"] == "probed"


def test_a_suggestion_run_leaves_the_probe_cache_untouched(client, monkeypatch):
    from avg.providers.recommend import Suggestion

    before = client.app.state.model_cache.path.read_bytes() if client.app.state.model_cache.path.is_file() else b""
    monkeypatch.setattr(
        "avg.api.routes_settings.recommend.suggest_models",
        lambda catalogue, *, config: [Suggestion(entry_id="groq/a", kind="text", tier=None, reason="x")],
    )
    client.post("/api/live-list/suggest", json={"kind": "models"}, headers=AUTH)
    client.app.state.suggest_thread.join(timeout=5)

    after = client.app.state.model_cache.path.read_bytes() if client.app.state.model_cache.path.is_file() else b""
    assert after == before


def test_a_second_suggest_while_one_runs_is_refused(client, monkeypatch):
    import threading

    started = threading.Event()
    release = threading.Event()

    def slow(*_a, **_k):
        started.set()
        release.wait(timeout=5)
        return []

    monkeypatch.setattr("avg.api.routes_settings.recommend.suggest_models", slow)
    try:
        assert client.post("/api/live-list/suggest", json={"kind": "models"}, headers=AUTH).status_code == 202
        assert started.wait(timeout=5)
        assert client.post("/api/live-list/suggest", json={"kind": "models"}, headers=AUTH).status_code == 409
    finally:
        release.set()
        client.app.state.suggest_thread.join(timeout=5)

    # Its own slot, never the render slot.
    assert client.app.state.render_thread is None


def test_an_unknown_kind_is_refused(client):
    res = client.post("/api/live-list/suggest", json={"kind": "everything"}, headers=AUTH)
    assert res.status_code == 400
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_live_list_routes.py -q
```

Expected: FAIL — 404 on the suggest route.

- [ ] **Step 3: Add the route**

In `avg/api/routes_settings.py`, add the import:

```python
from avg.providers import recommend
```

and the route:

```python
class SuggestRequest(BaseModel):
    """`POST /api/live-list/suggest` - which call to spend.

    Two calls, not one: text and image share a cost axis and are chosen
    together; voices have no cost axis at all. The user picks which to run.
    """

    kind: str


def _suggest_running(app_state: Any) -> bool:
    thread = getattr(app_state, "suggest_thread", None)
    return thread is not None and thread.is_alive()


@router.post("/api/live-list/suggest", status_code=status.HTTP_202_ACCEPTED)
async def suggest_live_list(body: SuggestRequest, request: Request) -> dict:
    """Ask a text model which entries are worth shortlisting.

    The suggestions are an OPINION and are stored as one - in
    `model_prefs.json` beside the user's ticks, never in
    `working_models.json`, which records what a probe measured. The Live List
    chips them `Suggested`, and a probe that later proves one outranks it.

    Own slot, 409 on a second start, deliberately not the render slot.
    """
    if body.kind not in ("models", "voices"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown kind {body.kind!r} - expected 'models' or 'voices'",
        )

    app_state = request.app.state
    if _suggest_running(app_state):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a suggestion run is already going",
        )

    prefs, _scope = _prefs_for_scope(app_state)
    values = _channel_values(app_state)
    try:
        router_config = config_from_settings(
            values, fallback_default=str(get_spec("text_model_default").default)  # type: ignore[union-attr]
        )
    except ValueError as exc:
        # No text model configured at all: say so, rather than failing inside
        # the worker where the user cannot see it.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    text_ids = sorted(_known_text(app_state))
    image_ids = sorted(_known_from_catalogue(app_state, "image"))
    voice_ids = sorted(_known_from_catalogue(app_state, "voice"))

    def _run() -> None:
        # Never raises out of the thread: a failed run leaves the previous
        # suggestions in place, which is more useful than none.
        try:
            if body.kind == "models":
                rows = recommend.suggest_models(
                    {"text": text_ids, "image": image_ids}, config=router_config
                )
            else:
                rows = recommend.suggest_voices(voice_ids, config=router_config)
            prefs.set_suggestions(
                [
                    {"entry_id": s.entry_id, "kind": s.kind, "tier": s.tier, "reason": s.reason}
                    for s in rows
                ]
            )
        except Exception:
            pass

    thread = threading.Thread(target=_run, name="live-list-suggest", daemon=True)
    app_state.suggest_thread = thread
    thread.start()
    return {"started": True}
```

In `avg/api/app.py`, beside `app.state.probe_thread`:

```python
    # The suggestion run's own single slot - one text call, and two at once
    # would just overwrite each other.
    app.state.suggest_thread = None
```

- [ ] **Step 4: Report the fourth provenance**

In `_entry_rows`, add the suggestion data. Replace the row construction:

```python
def _entry_rows(prefs: ModelPrefs, kind: str, known: dict[str, str]) -> list[dict]:
    """Merge what the app knows about with what the user has said about it.

    `known` maps entry id -> source (`probed` / `catalogue`). Anything in
    the prefs document but not in `known` is reported as `manual` rather
    than silently dropped.

    A suggestion is a fourth provenance and the WEAKEST: it is an opinion,
    so anything the app has actually measured or listed outranks it. An
    entry that is both suggested and probed reports `probed`, because a
    measurement beats a recommendation.
    """
    saved = prefs.all(kind)
    suggested = prefs.suggestions()
    rows: list[dict] = []
    for entry_id in sorted(set(known) | set(saved) | set(suggested)):
        entry = saved.get(entry_id)
        hint = suggested.get(entry_id)
        if entry_id in known:
            source = known[entry_id]
        elif entry_id in suggested:
            source = "suggested"
        else:
            source = "manual"
        rows.append(
            {
                "id": entry_id,
                "source": source,
                "selected": bool(entry.selected) if entry else False,
                "favourite": bool(entry.favourite) if entry else False,
                "note": entry.note if entry else "",
                "tier": (hint or {}).get("tier"),
                "reason": str((hint or {}).get("reason", "")),
            }
        )
    rows.sort(key=lambda r: (not r["favourite"], r["id"].lower()))
    return rows
```

`_entry_rows` is called for all three kinds, so a suggestion of the wrong kind could leak into another tab's rows. Filter by kind when reading suggestions if your tests show that happening — and add a test for it if so.

- [ ] **Step 5: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_live_list_routes.py -q
```

Expected: PASS.

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 5 more than after Task 2.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/api/routes_settings.py avg/api/app.py tests/api/test_live_list_routes.py
git commit -m "feat: suggest models from the Live List, chipped as the opinion it is"
```

---

## Task 4: The Suggested chip, and saying what it is

**Files:**
- Modify: `web/src/api.ts` (`LiveEntry`, `suggestLiveList`)
- Modify: `web/src/LiveList.tsx`
- Modify: `web/src/tokens.css` (the tier chip)

**Interfaces:**
- Consumes from Task 3: `source: "suggested"`, `tier`, `reason`, and `POST /api/live-list/suggest`.
- Produces: nothing further.

**Why:** Every entry on that screen already declares where its authority comes from. The panel must carry one sentence, once, stating that tiers and reasons come from the model and are not prices the app checked — the same discipline Slice 10 applied to `Reachable`.

**Sorting leads with what is known:** favourites first, then verified, then suggested.

- [ ] **Step 1: Extend the types**

In `web/src/api.ts`, add `"suggested"` to `LiveEntry["source"]`, and add:

```ts
  /** The model's claimed cost tier, or null. An OPINION — the app holds no
   * per-model pricing, so this was never measured. Absent for voices, which
   * have no cost axis. */
  tier: string | null;
  /** One line of the model's reasoning. Also an opinion. */
  reason: string;
```

```ts
/** Ask a text model which entries are worth shortlisting. Costs one text
 * call. Results are recommendations, never measurements. */
export function suggestLiveList(kind: "models" | "voices"): Promise<{ started: boolean }> {
  return apiPost<{ started: boolean }>("/api/live-list/suggest", { kind });
}
```

- [ ] **Step 2: Show it honestly**

In `web/src/LiveList.tsx`:

Add to `SOURCE_LABEL` and `sourceClass`:

```ts
const SOURCE_LABEL: Record<LiveEntry["source"], string> = {
  probed: "Verified",
  catalogue: "Listed",
  manual: "Added by you",
  suggested: "Suggested",
};
```

```ts
function sourceClass(source: LiveEntry["source"]): string {
  if (source === "probed") return "chip chip--ok";
  if (source === "manual") return "chip chip--amber";
  // Deliberately not chip--ok: a suggestion is an opinion, and must not
  // wear the same colour as something a probe proved.
  if (source === "suggested") return "chip chip--accent";
  return "chip";
}
```

Add two plain buttons beside the existing `Refresh catalogues` primary — **neither becomes `.btn--primary`**:

```tsx
        <button type="button" className="btn" onClick={() => void suggest("models")} disabled={busy}>
          Suggest models
        </button>
        <button type="button" className="btn" onClick={() => void suggest("voices")} disabled={busy}>
          Suggest voices
        </button>
```

Show the tier and reason on a row that has them, and add **one** sentence under the existing note paragraph:

```tsx
      <p className="channels__note">
        Suggestions come from an AI reading these catalogues. The cost tiers and reasons are its
        opinion — this app holds no pricing and has not checked them. Only “Verified” means a real
        call succeeded.
      </p>
```

Sort so knowledge leads opinion — extend the existing sort:

```tsx
  const RANK: Record<LiveEntry["source"], number> = {
    probed: 0, catalogue: 1, manual: 2, suggested: 3,
  };
```

and apply it after the favourite ordering the backend already does.

- [ ] **Step 3: Build and lint**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean. `SOURCE_LABEL` is a `Record` over the union, so TypeScript will fail the build if `"suggested"` was missed anywhere — that is the type system doing the work.

- [ ] **Step 4: Confirm no native control**

```bash
grep -rn "<select\|type=\"checkbox\"" web/src/
```

Expected: only Home's sanctioned hidden `.select-dot` checkbox.

- [ ] **Step 5: Report what renders**

State in your report what a row looks like in each provenance, and confirm the panel's one-sentence disclaimer is present and not repeated per row.

- [ ] **Step 6: Commit**

```bash
git add web/src/api.ts web/src/LiveList.tsx web/src/tokens.css
git commit -m "feat: a Suggested chip that never pretends to be a measurement"
```

---

## Final verification

- [ ] `.venv-build/Scripts/python.exe -m pytest tests -q` — expect ~13 new tests.
- [ ] `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- [ ] `npm --prefix web run build && npm --prefix web run lint` — clean.
- [ ] Press Suggest models: up to four text and four image entries appear, each chipped **Suggested** with a tier and a reason.
- [ ] Press Suggest voices: entries appear with reasons and **no** tier.
- [ ] The panel states plainly that tiers are the model's claim, not a measured price.
- [ ] Run Test Providers on a suggested model: its chip becomes **Verified** and its reason survives.
- [ ] `working_models.json` is byte-identical after a suggestion run, and no tick moved.
- [ ] Clear every key so no text model is proven, press Suggest: the app says why it cannot.
- [ ] Take log: one text call per press, nothing generated.

---

## Appendix: spec coverage

| Spec section | Task |
|---|---|
| §3.1 `recommend.py`, two calls, validation, no tier for voices | 1 |
| §3.2 Storage in `model_prefs.json`, wholesale replace | 2 |
| §3.2 The route, single slot, 409 | 3 |
| §3.3 Fourth provenance, measurement outranks opinion, sorting | 3 (backend), 4 (frontend) |
| §3.3 The one-sentence disclaimer | 4 |
| §4 Testing | 1, 2, 3 |
| §5 Verification | Final verification |
