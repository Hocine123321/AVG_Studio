# Slice 15 — Smart model picker

**Goal:** turn "here are 33 Gemini models, 20 HuggingFace models and 322 Edge
voices, good luck" into a short list with a reason attached to each — without
the app ever stating as fact something it did not measure.

**Date:** 2026-07-31. Every count and source reference below was read from
the running app on that date.

Fifth slice of the 2026-07-31 backlog. It depends on Slice 12 (the Live List
moves into Settings) and reads the catalogue Slice 10 already populates.

---

## 1. What the evidence shows

### 1.1 The scale is real

Measured from `working_models.json` after one catalogue refresh on
2026-07-31:

| Catalogue | Entries |
|---|---|
| `__text__gemini` | 33 |
| `__text__huggingface` | 20 |
| `__text__groq` | 10 |
| `__image__stablehorde` | 155 |
| `__voice__edge` | **322** |
| `__voice__elevenlabs` | 26 |

566 entries. The Live List's tick-and-note curation exists precisely because
that is unusable raw, and Slice 10 added a search field for the same reason.
Neither tells the user *which* to pick.

### 1.2 The app holds no per-model pricing

`avg/core/costs.py` is a maintained table of **per-phase** estimates —
`LEAF_COST` has ten entries keyed by pipeline leaf (`brief`, `draft`,
`visuals`…), each a flat USD figure. Its docstring is explicit that the split
exists because phases "run different models at genuinely different prices",
but the table records the phase's price, not any model's.

There is no per-model price anywhere in the codebase.

So a cost ranking produced by an AI is the model's **recollection** of
pricing, from training data, for a market that changes without notice. It is
not a measurement, and the app has nothing to check it against.

### 1.3 The codebase's oldest rule is about exactly this

`avg/core/model_cache.py`, module docstring:

> **The rule inherited verbatim, because the legacy app learned it the hard
> way:** only models a probe PROVED working are ever returned… The old app's
> built-in model list went stale and it became impossible to tell what had
> actually been verified - so an empty result has to read as "build your
> cache", never as a list of guesses.

And the Live List already has a provenance vocabulary for this —
`web/src/LiveList.tsx:38`:

```ts
probed: "Verified", catalogue: "Listed", manual: "Added by you"
```

Every entry on that screen already declares where its authority comes from. A
suggestion has to join that vocabulary rather than sit outside it.

### 1.4 Picking models needs a working model

`CLAUDE.md` records that only Groq currently generates text, with a handful
of working models. The feature that recommends models is itself an AI call,
so it cannot run before at least one text model is proven — a dependency
worth stating rather than discovering at runtime.

---

## 2. Decisions

1. **Suggestions are a fourth provenance, `Suggested`**, carrying the cost
   tier and a reason as the model's claim — never as measured fact. Chosen
   over dropping cost tiers, and over building a maintained price table.
2. **Two calls, not one**: text and images together, voices separately, as
   requested — the reasoning differs and the voice call has no cost axis.
3. **A suggestion never ticks anything.** The user still chooses.

---

## 3. Architecture

One new backend module, one route, and an addition to a screen that already
exists. Nothing changes the phase registry or what a phase writes.

### 3.1 `avg/providers/recommend.py`

A new module in the provider layer, beside `probe.py` and `catalogues.py`,
with one public function per call:

```python
def suggest_models(catalogue: dict[str, list[str]], *, transport=None)
        -> list[Suggestion]      # text + image, one call
def suggest_voices(catalogue: list[VoiceEntry], *, transport=None)
        -> list[Suggestion]      # voices, one call
```

`Suggestion` is a frozen dataclass: `entry_id` (`provider/model`), `kind`,
`tier`, `reason`. `tier` is one of `cheap`, `balanced`, `high`, `ultra` for
text and image, and is **`None` for voices** — voices carry no cost axis, so
the voice call asks only for the best few and the field is absent rather than
filled with a meaningless value.

The prompt is sent the catalogue the app actually holds, so the model is
choosing from real entries rather than recalling model names — that part *is*
grounded. Only the tier and the reason are its opinion.

**Every returned `entry_id` is validated against the catalogue that was sent**
and anything unrecognised is dropped. A model that invents a plausible model
id is the exact failure this guard exists for, and it is cheap to enforce.

The call goes through the configured default text model via the existing
router, so it obeys the same routing, fallback and error taxonomy as every
other text call. When no text model is proven (§1.4), the feature says so and
points at Test Providers rather than failing obscurely.

### 3.2 `POST /api/live-list/suggest`

One route, taking `{"kind": "models"}` or `{"kind": "voices"}` so the user
chooses which call to spend, as requested.

It runs on a worker thread with its own single slot and a 409 on a second
start — the same shape as `probe_thread`, and deliberately not the render
slot.

Suggestions are stored in `model_prefs.json` beside the user's ticks, as a
`suggested` section: `{entry_id: {tier, reason, at}}`. **Not** in
`working_models.json`, which is machine-owned, replaced wholesale on every
sweep, and reserved for what was *measured* — mixing an opinion into it would
break the one guarantee that file makes.

A new suggestion run replaces the previous suggestions wholesale, and touches
no tick, favourite or note.

### 3.3 How it reads on screen

The Live List gains `suggested` as a fourth `source`, rendered with its own
chip beside the three that exist:

| Chip | Means |
|---|---|
| Verified | a real completion succeeded (Test Providers) |
| Listed | the provider advertises it |
| Added by you | typed by hand |
| **Suggested** | **an AI's opinion — nothing measured it** |

A suggested row shows its tier and its one-line reason. The panel carries one
sentence, once, stating that tiers and reasons come from the model and are
not prices the app checked — the same discipline Slice 10 applied to
`Reachable`, where the distinction between "the provider answered" and "this
model works" is spelled out rather than implied.

**Suggested and Verified are not exclusive.** A model can be suggested and
then proven; when Test Providers verifies it, it reads Verified, because a
measurement outranks an opinion. The reason text stays visible, since it is
still the most useful thing on the row.

Sorting puts favourites first as it does today, then verified, then
suggested — so the screen leads with what is known before what is guessed.

---

## 4. Testing

No test may make a live provider call: `tests/conftest.py` forces
`AVG_REPLAY_FIXTURES=1`.

- **Validation**: a response naming a model that was not in the catalogue is
  dropped, and a response that is not valid JSON yields no suggestions rather
  than raising. This is the §3.1 guard and the most important test here.
- **Voices carry no tier**: `suggest_voices` returns `tier is None` for every
  entry, and the voice prompt does not ask for one.
- **Storage separation**: a suggestion run writes only to `model_prefs.json`,
  leaves `working_models.json` byte-identical, and preserves every existing
  tick, favourite and note.
- **Provenance**: an entry that is both suggested and probed reports
  `probed`, so a measurement always outranks an opinion.
- **No text model proven** produces a stated refusal, not an exception.
- **The single slot**: a second suggest while one runs gets a 409, and the
  render slot is untouched.

Target: **769 tests still passing**, mypy no worse than its `Found 688
errors in 22 files` baseline.

---

## 5. Verification

- `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 769.
- `npm --prefix web run build && npm --prefix web run lint` — clean.
- Press Suggest models: four text and four image entries appear, each with a
  tier and a reason, each chipped **Suggested**.
- Press Suggest voices: entries appear with reasons and **no** tier.
- Confirm the panel states plainly that tiers are the model's claim and not a
  measured price.
- Run Test Providers on a suggested model: its chip becomes **Verified** and
  its reason survives.
- Confirm `working_models.json` is unchanged by a suggestion run, and that no
  tick was moved.
- Clear all keys so no text model is proven, press Suggest: the app says why
  it cannot and points at Test Providers.
- Check the take log: one text call per press, and nothing generated.

---

## 6. Out of scope

- A maintained per-model price table. §1.2 is why tiers are labelled as
  opinion instead; building real pricing is its own slice with its own
  maintenance argument.
- Auto-ticking suggestions, or auto-applying them to settings. The user
  chooses; that is the whole point of the Live List.
- Suggesting anything the catalogue does not contain — §3.1 drops it.
- Parallel pipelines (Slice 16).
