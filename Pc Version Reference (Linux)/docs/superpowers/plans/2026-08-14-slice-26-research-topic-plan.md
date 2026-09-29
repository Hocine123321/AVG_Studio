# Slice 26 — Create Video → Research Topic: Implementation Plan

Implements `docs/superpowers/specs/2026-08-14-slice-26-research-topic-design.md`.
Read that first - the backend is already complete; this plan only
touches `Create.tsx`.

Frontend only. Verify: `cd web && npm run build && npm run lint`. No
frontend test runner exists (confirmed Slice 22).

## Files touched

| File | Change |
|---|---|
| `web/src/Create.tsx` | `overrides` widens to `Record<string, unknown>`; a fourth chip, a switch, for `topic_research` |

One file, one task.

---

## Task 1 — The toggle

**Files:** `web/src/Create.tsx`

### Step 1.1 — implement

Widen the overrides state type. Replace:

```typescript
  const [topic, setTopic] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
```

with:

```typescript
  const [topic, setTopic] = useState("");
  // `unknown`, not `string`: the three existing chips are all
  // `type="choice"` and correctly send strings, but `topic_research` is
  // `type="bool"` and `avg.core.settings_schema.validate_value` requires
  // an actual JSON boolean for that type (no string coercion, unlike its
  // permissive int/float handling) - see the design spec §1. Matches
  // `createProject`'s own `Record<string, unknown>` parameter already -
  // no API change, just no longer narrowing it away here.
  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
```

Add the research-value resolver, directly after `isOverridden`. Replace:

```typescript
  function isOverridden(id: string): boolean {
    return Object.prototype.hasOwnProperty.call(overrides, id) && overrides[id] !== String(channelValues[id] ?? "");
  }
```

with:

```typescript
  function isOverridden(id: string): boolean {
    return Object.prototype.hasOwnProperty.call(overrides, id) && overrides[id] !== String(channelValues[id] ?? "");
  }

  const RESEARCH_ID = "topic_research";

  /** Same overrides-then-channel-then-schema-default chain `valueFor`
   * uses for the Select chips, kept as its own function rather than a
   * generalization of `valueFor` - that one is typed to always return a
   * `string` (it feeds `Select`'s `value` prop), this one has to stay a
   * real boolean the whole way through (see Step 1.1's note on
   * `validate_value`). */
  function researchOn(): boolean {
    if (Object.prototype.hasOwnProperty.call(overrides, RESEARCH_ID)) return Boolean(overrides[RESEARCH_ID]);
    const fromChannel = channelValues[RESEARCH_ID];
    if (fromChannel !== undefined) return Boolean(fromChannel);
    return Boolean(specById.get(RESEARCH_ID)?.default ?? false);
  }
```

Add the chip. Replace:

```tsx
      <div className="create__chips">
        <span className="chip">Channel: {channelLabel ?? activeChannelId}</span>
        {CHIP_IDS.map((id) => {
          const spec = specById.get(id);
          if (!spec || !spec.choices) return null;
          return (
            <label key={id} className={`create-chip${isOverridden(id) ? " create-chip--dirty" : ""}`}>
              <span className="create-chip__label">{CHIP_LABELS[id] ?? spec.label}</span>
              <Select
                value={valueFor(id)}
                options={spec.choices}
                ariaLabel={`${CHIP_LABELS[id] ?? spec.label} for this video`}
                onChange={(next) => setOverrides((o) => ({ ...o, [id]: next }))}
              />
            </label>
          );
        })}
      </div>
```

with:

```tsx
      <div className="create__chips">
        <span className="chip">Channel: {channelLabel ?? activeChannelId}</span>
        {CHIP_IDS.map((id) => {
          const spec = specById.get(id);
          if (!spec || !spec.choices) return null;
          return (
            <label key={id} className={`create-chip${isOverridden(id) ? " create-chip--dirty" : ""}`}>
              <span className="create-chip__label">{CHIP_LABELS[id] ?? spec.label}</span>
              <Select
                value={valueFor(id)}
                options={spec.choices}
                ariaLabel={`${CHIP_LABELS[id] ?? spec.label} for this video`}
                onChange={(next) => setOverrides((o) => ({ ...o, [id]: next }))}
              />
            </label>
          );
        })}
        {specById.has(RESEARCH_ID) && (
          <label className="create-chip">
            <span className="create-chip__label">Research topic</span>
            <button
              type="button"
              role="switch"
              aria-checked={researchOn()}
              aria-label="Research the topic before writing"
              className={`switch${researchOn() ? " switch--on" : ""}`}
              onClick={() => setOverrides((o) => ({ ...o, [RESEARCH_ID]: !researchOn() }))}
            >
              <span className="switch__thumb" aria-hidden="true" />
            </button>
          </label>
        )}
      </div>
```

(`specById.has(RESEARCH_ID)` guards against a schema that somehow doesn't
carry this id - matches how the three Select chips already guard with
`if (!spec || !spec.choices) return null`, never assuming the catalogue
shape.)

### Step 1.2 — verify

`cd web && npm run build && npm run lint`. **Manual check:** toggle
Research on, start a video, confirm `<project>/script/topic_research.txt`
gets written (or, if the AI call fails, that the brief still completes
and nothing crashes - the existing degrade-on-failure path, just
confirming the toggle actually reaches `ctx.channel_values.get(
"topic_research")`). Toggle off, confirm the file is NOT written for a
fresh project.

### Step 1.3 — commit

`feat: Research Topic toggle on Create Video — Slice 26`

---

## Task 2 — Update progress ledger

### Step 2.1

Update `.superpowers/sdd/progress.md`: Slice 26 complete - noting the
backend was already fully built (BriefPhase's research call, the
`topic_research` SettingSpec, ScriptPhase's `extra_context` consumption),
this slice was a Create.tsx-only addition.

### Step 2.2 — commit

`docs: Slice 26 complete — Create Video Research Topic toggle`
