# Slice 29 — Settings screen: Implementation Plan

Implements `docs/superpowers/specs/2026-08-16-slice-29-settings-design.md`.
Read that first.

Verify: `.venv-build\Scripts\python.exe -m pytest tests -q`,
`.venv-build\Scripts\python.exe -m mypy avg`, `cd web && npm run build
&& npm run lint`. No frontend test runner exists (confirmed Slice 22).

## Files touched

| File | Change |
|---|---|
| `web/src/FieldRow.tsx` | New - extracted from `SettingsGroup.tsx`, unchanged behavior |
| `web/src/SettingsGroup.tsx` | Simplifies to use `FieldRow` |
| `web/src/ModelsFieldGroups.tsx` | New - per-phase grouping for the `models` category |
| `web/src/Settings.tsx` | Swaps `ModelsFieldGroups` in for `SettingsGroup` on that one category |
| `web/src/Slider.tsx` | New - the shared slider control |
| `web/src/tokens.css` | `.slider`, `.models-phase-group` families |
| `web/src/fields/Fields.tsx` | `ChoiceField` gains the "custom" escape hatch; new `PersonalityField` |
| `web/src/fields/index.tsx` | Dispatcher: `Slider` for five ids, `PersonalityField` for `content_shape` |
| `web/src/fields/types.ts` | `SettingsCtx` gains `cpuCount` |
| `web/src/useSettingsData.ts` | Fetches hardware info alongside schema/values |
| `web/src/api.ts` | `getHardwareInfo`, `getPersonalityPresets` |
| `avg/api/routes_settings.py` | `GET /api/system/hardware`, `GET /api/personality-presets` |
| `avg/core/settings_schema.py` | `content_shape` gains `source`; `font_family` becomes `choice`; `content_shape.label` → "Personality" |
| `tests/api/test_routes_settings.py` | +2 tests |

Eight tasks. Order: the `FieldRow` extraction first (Task 2 depends on
it) → Models grouping → the Slider component and its four
statically-bounded fields → Render Threads specifically (needs the new
hardware endpoint, kept separate from the other four sliders since it's
the one with real backend work attached) → the ChoiceField "custom"
extension and Captions Font → Personality (the one needing a genuinely
new backend endpoint and picker) → regression.

---

## Task 1 — Extract `FieldRow` from `SettingsGroup`

No behavior change - a seam `ModelsFieldGroups` (Task 2) needs to reuse
the exact same per-field rendering rather than duplicating it.

**Files:** `web/src/FieldRow.tsx` (new), `web/src/SettingsGroup.tsx`

### Step 1.1 — implement

Create `web/src/FieldRow.tsx` containing exactly the per-field block
currently inside `SettingsGroup`'s `.map()` callback, as its own default
export:

```tsx
import type { SettingSpec } from "./api";
import { Field } from "./fields";
import type { SettingsCtx } from "./fields/types";

/**
 * One setting's row - label, scope/ownership chip, help text, dirty dot,
 * and its `Field`. Extracted out of `SettingsGroup.tsx` (Slice 29): the
 * per-phase grouping in `ModelsFieldGroups.tsx` needs the identical
 * rendering, ownership semantics included, not a second copy of it that
 * could drift. `SettingsGroup` itself is unchanged in what it renders,
 * only in how it's structured internally.
 */
export default function FieldRow({ spec, ctx }: { spec: SettingSpec; ctx: SettingsCtx }) {
  const dirty = ctx.isDirty(spec.id);
  // Secrets never join the draft batch (see SecretField's header
  // comment) so they are never "blocked" by channel state - only
  // the six ordinary channel-scoped types can be.
  const channelBlocked = spec.scope === "channel" && spec.type !== "secret" && !ctx.activeChannelId;
  // Ownership only means something for a channel-scope field while
  // looking at the channel's own (not the global) view - no
  // `scope="channel"` setting is ever `type="secret"` (see
  // `avg.core.settings_schema`), so no extra type guard is needed.
  const ownershipApplies = spec.scope === "channel" && ctx.scope === "channel";
  const owned = !ownershipApplies || ctx.isOwned(spec.id);
  const inherited = ownershipApplies && !owned;

  return (
    <div className={`field-row${dirty ? " field-row--dirty" : ""}`}>
      <div className="field-row__info">
        <div className="field-row__label-line">
          <span className="field-row__label">{spec.label}</span>
          {spec.scope === "channel" && (
            <span className={`field-row__scope${ctx.activeChannelId ? "" : " field-row__scope--none"}`}>
              {ctx.activeChannelId ? `→ ${ctx.channelLabel ?? ctx.activeChannelId}` : "No active channel"}
            </span>
          )}
          {dirty && <span className="field-row__dirty-dot" title="Unsaved change" aria-hidden="true" />}
        </div>
        <p className="field-row__help">{spec.help}</p>
        {channelBlocked && <p className="field-row__blocked">Activate a channel before this can be saved.</p>}
        {inherited && (
          <p className="field-row__blocked field-row__blocked--muted">
            Inherited from the app-wide defaults. Take ownership to set this channel&rsquo;s own value.
          </p>
        )}
      </div>
      <div className="field-row__control">
        <Field spec={spec} ctx={ctx} disabled={channelBlocked || inherited} />
        {inherited && (
          <button type="button" className="btn btn--sm" onClick={() => ctx.onCommit(spec, ctx.getValue(spec))}>
            Take ownership
          </button>
        )}
        {ownershipApplies && owned && (
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => ctx.onReleaseOwnership(spec.id)}
            title="Release this channel's own value and go back to inheriting"
          >
            Release
          </button>
        )}
      </div>
    </div>
  );
}
```

In `SettingsGroup.tsx`, replace the import and the map body. Replace:

```typescript
import type { SettingSpec } from "./api";
import { Field } from "./fields";
import type { SettingsCtx } from "./fields/types";
```

with:

```typescript
import type { SettingSpec } from "./api";
import FieldRow from "./FieldRow";
import type { SettingsCtx } from "./fields/types";
```

Replace the whole `<div className="settings-group__list">...</div>`
block:

```tsx
      <div className="settings-group__list">
        {specs.map((spec) => {
          const dirty = ctx.isDirty(spec.id);
          // Secrets never join the draft batch (see SecretField's header
          // comment) so they are never "blocked" by channel state - only
          // the six ordinary channel-scoped types can be.
          const channelBlocked = spec.scope === "channel" && spec.type !== "secret" && !ctx.activeChannelId;
          // Ownership only means something for a channel-scope field while
          // looking at the channel's own (not the global) view - no
          // `scope="channel"` setting is ever `type="secret"` (see
          // `avg.core.settings_schema`), so no extra type guard is needed.
          const ownershipApplies = spec.scope === "channel" && ctx.scope === "channel";
          const owned = !ownershipApplies || ctx.isOwned(spec.id);
          const inherited = ownershipApplies && !owned;

          return (
            <div key={spec.id} className={`field-row${dirty ? " field-row--dirty" : ""}`}>
              <div className="field-row__info">
                <div className="field-row__label-line">
                  <span className="field-row__label">{spec.label}</span>
                  {spec.scope === "channel" && (
                    <span
                      className={`field-row__scope${ctx.activeChannelId ? "" : " field-row__scope--none"}`}
                    >
                      {ctx.activeChannelId ? `→ ${ctx.channelLabel ?? ctx.activeChannelId}` : "No active channel"}
                    </span>
                  )}
                  {dirty && <span className="field-row__dirty-dot" title="Unsaved change" aria-hidden="true" />}
                </div>
                <p className="field-row__help">{spec.help}</p>
                {channelBlocked && (
                  <p className="field-row__blocked">Activate a channel before this can be saved.</p>
                )}
                {inherited && (
                  <p className="field-row__blocked field-row__blocked--muted">
                    Inherited from the app-wide defaults. Take ownership to set this channel&rsquo;s
                    own value.
                  </p>
                )}
              </div>
              <div className="field-row__control">
                <Field spec={spec} ctx={ctx} disabled={channelBlocked || inherited} />
                {inherited && (
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => ctx.onCommit(spec, ctx.getValue(spec))}
                  >
                    Take ownership
                  </button>
                )}
                {ownershipApplies && owned && (
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    onClick={() => ctx.onReleaseOwnership(spec.id)}
                    title="Release this channel's own value and go back to inheriting"
                  >
                    Release
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
```

with:

```tsx
      <div className="settings-group__list">
        {specs.map((spec) => (
          <FieldRow key={spec.id} spec={spec} ctx={ctx} />
        ))}
      </div>
```

`SettingsCtx` stays imported in `SettingsGroup.tsx` - still used by
`SettingsGroupProps`.

### Step 1.2 — verify

`cd web && npm run build && npm run lint`. **Manual check:** the
Settings screen renders identically to before this task - every field,
every ownership/dirty/blocked state, unchanged. This task is a pure
refactor; anything different here is a mistake in the extraction, not an
intended change.

### Step 1.3 — commit

`refactor: extract FieldRow from SettingsGroup — Slice 29 Task 1`

---

## Task 2 — Models grouping (#10)

**Files:** `web/src/ModelsFieldGroups.tsx` (new), `web/src/Settings.tsx`,
`web/src/tokens.css`

### Step 2.1 — implement

Create `web/src/ModelsFieldGroups.tsx`:

```tsx
import type { SettingSpec } from "./api";
import FieldRow from "./FieldRow";
import type { SettingsCtx } from "./fields/types";

interface PhaseGroup {
  phase: string;
  label: string;
  model?: SettingSpec;
  backup?: SettingSpec;
  onFailure?: SettingSpec;
}

const SUFFIXES: { suffix: string; slot: "model" | "backup" | "onFailure" }[] = [
  { suffix: "_model", slot: "model" },
  { suffix: "_backup", slot: "backup" },
  { suffix: "_on_failure", slot: "onFailure" },
];

const LABEL_SUFFIXES: Record<"model" | "backup" | "onFailure", string> = {
  model: " — model",
  backup: " — backup",
  onFailure: " — if both fail",
};

/** Which phase group (if any) a `models`-group spec belongs to, and which
 * slot within it - derived from the id's own suffix, matching exactly
 * how `avg.core.settings_schema`'s per-phase generator built the id and
 * label in the first place (`f"{phase_key}_model"`, `f"{phase_label} —
 * model"`, etc.) - not a second naming convention invented here.
 * `text_model_default` is excluded explicitly: it ends in `_model` too,
 * but it's the standalone fallback, not one phase's own field. */
function classify(spec: SettingSpec): { phase: string; slot: "model" | "backup" | "onFailure" } | null {
  if (spec.id === "text_model_default") return null;
  for (const { suffix, slot } of SUFFIXES) {
    if (spec.id.endsWith(suffix)) {
      return { phase: spec.id.slice(0, -suffix.length), slot };
    }
  }
  return null;
}

/**
 * Groups the `models` category's flat spec list into one section per
 * phase (Primary + Backup + If-both-fail together, spec's own §1) plus
 * whatever doesn't belong to a phase (`text_model_default`,
 * `live_list_source`) rendered at its natural position in the original
 * order. Reuses `FieldRow` for every individual field - this is a layout
 * change over the generic renderer, not a new field-rendering path.
 */
export default function ModelsFieldGroups({ specs, ctx }: { specs: SettingSpec[]; ctx: SettingsCtx }) {
  const groups = new Map<string, PhaseGroup>();
  for (const spec of specs) {
    const classified = classify(spec);
    if (!classified) continue;
    const { phase, slot } = classified;
    const suffix = LABEL_SUFFIXES[slot];
    const group = groups.get(phase) ?? {
      phase,
      label: spec.label.endsWith(suffix) ? spec.label.slice(0, -suffix.length) : phase,
    };
    group[slot] = spec;
    groups.set(phase, group);
  }

  const rendered = new Set<string>();

  return (
    <div className="settings-group__list">
      {specs.map((spec) => {
        const classified = classify(spec);
        if (!classified) return <FieldRow key={spec.id} spec={spec} ctx={ctx} />;
        const group = groups.get(classified.phase);
        if (!group || rendered.has(group.phase)) return null;
        rendered.add(group.phase);
        return (
          <section key={group.phase} className="models-phase-group">
            <h3 className="models-phase-group__title">{group.label}</h3>
            {group.model && <FieldRow spec={group.model} ctx={ctx} />}
            {group.backup && <FieldRow spec={group.backup} ctx={ctx} />}
            {group.onFailure && <FieldRow spec={group.onFailure} ctx={ctx} />}
          </section>
        );
      })}
    </div>
  );
}
```

In `web/src/Settings.tsx`, replace:

```tsx
                {g.group === "models" && !searching && (
                  <ModelsPanel
                    specs={g.specs}
                    valueOf={valueById}
                    lengthPreset={valueById("length_preset") || "60s"}
                  />
                )}
                <SettingsGroup
                  title={g.label}
                  specs={g.specs}
                  ctx={ctx}
                  active={activeGroup === g.group}
                />
```

with:

```tsx
                {g.group === "models" && !searching && (
                  <ModelsPanel
                    specs={g.specs}
                    valueOf={valueById}
                    lengthPreset={valueById("length_preset") || "60s"}
                  />
                )}
                {g.group === "models" ? (
                  <ModelsFieldGroups specs={g.specs} ctx={ctx} />
                ) : (
                  <SettingsGroup
                    title={g.label}
                    specs={g.specs}
                    ctx={ctx}
                    active={activeGroup === g.group}
                  />
                )}
```

(`ModelsFieldGroups` renders only the field list, not a
`.settings-group__title` heading - `ModelsPanel`'s own card immediately
above already establishes "this is Models," so the plain
`SettingsGroup` title `g.label` would be redundant directly under it.
Confirm this reads right once rendered; add a heading back if the
section looks orphaned without one.)

Add the import. Replace:

```typescript
import ModelsPanel from "./ModelsPanel";
```

with:

```typescript
import ModelsFieldGroups from "./ModelsFieldGroups";
import ModelsPanel from "./ModelsPanel";
```

### Step 2.2 — CSS

In `web/src/tokens.css`, add near `.settings-group__list`/`.field-row`:

```css
.models-phase-group {
  border-bottom: 1px solid var(--line);
}

.models-phase-group:last-child {
  border-bottom: 0;
}

.models-phase-group .field-row:last-child {
  border-bottom: 0;
}

.models-phase-group__title {
  padding: 14px 22px 2px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
```

(Horizontal padding matches `.field-row`'s own `16px 22px` exactly,
confirmed against the file directly - the rows underneath a group title
should line up with it, not sit at a different inset.)

### Step 2.3 — verify

`cd web && npm run build && npm run lint`. **Manual check:** Settings →
Models shows the read-only spend table unchanged, then five bordered
sections below it (Brief, Script · Draft, Script · Segmentation,
Director I, Director II), each with its own Model/Backup/If-both-fail
fields grouped together, plus Default text model and Live List source
still rendering at the top/bottom respectively. Every field's ownership/
dirty/take-ownership behavior still works exactly as before.

### Step 2.4 — commit

`feat: group primary/backup models per phase — Slice 29 Task 2`

---

## Task 3 — The Slider component, four statically-bounded fields

**Files:** `web/src/Slider.tsx` (new), `web/src/fields/Fields.tsx`,
`web/src/fields/index.tsx`, `web/src/tokens.css`

### Step 3.1 — the component

Create `web/src/Slider.tsx`:

```tsx
import type { SettingSpec } from "./api";
import type { SettingsCtx } from "./fields/types";

export interface SliderProps {
  spec: SettingSpec;
  ctx: SettingsCtx;
  disabled?: boolean;
  min: number;
  max: number;
  step: number;
  /** Shown instead of the literal number when the current value equals
   * this - `render_threads`'/`captions_font_size`'s own "0 means auto"
   * sentinels (spec §2), not a general-purpose label override. */
  zeroLabel?: string;
}

/**
 * The one slider every numeric setting with real bounds uses (spec §2) -
 * native `<input type="range">`, restyled, not a bespoke control per
 * field. Commits on every `onChange` (which fires continuously while
 * dragging, not only on release) rather than waiting for a mouse-up -
 * `ctx.onCommit` only ever updates local draft state here, same as every
 * other field in this app; nothing about a slider drag reaches the
 * network until the screen's own explicit Save.
 */
export default function Slider({ spec, ctx, disabled, min, max, step, zeroLabel }: SliderProps) {
  const value = Number(ctx.getValue(spec) ?? min);
  const isInt = spec.type === "int";
  const display = zeroLabel !== undefined && value === 0 ? zeroLabel : isInt ? String(Math.round(value)) : value.toFixed(2);

  return (
    <div className="slider">
      <input
        type="range"
        className="slider__input"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          const raw = Number(e.target.value);
          ctx.onCommit(spec, isInt ? Math.round(raw) : raw);
        }}
        aria-label={spec.label}
        aria-valuetext={display}
      />
      <span className="slider__value mono">{display}</span>
    </div>
  );
}
```

### Step 3.2 — CSS

In `web/src/tokens.css`, add (reusing the same cross-browser
range-thumb-styling technique Slice 24's Quick Panel resize handle
established, not inventing a second one):

```css
.slider {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.slider__input {
  flex: 1;
  appearance: none;
  height: 4px;
  border-radius: 2px;
  background: var(--line-2);
  outline-offset: 4px;
}

.slider__input::-webkit-slider-thumb {
  appearance: none;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  background: var(--accent);
  cursor: pointer;
}

.slider__input::-moz-range-thumb {
  width: 15px;
  height: 15px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  cursor: pointer;
}

.slider__input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.slider__value {
  flex-shrink: 0;
  min-width: 42px;
  text-align: right;
  font-size: 12.5px;
  color: var(--muted);
}
```

### Step 3.3 — wire the four statically-bounded fields into the dispatcher

`fields/index.tsx`'s `Field()` checks `spec.source` before falling
through to the type-keyed `REGISTRY` lookup (confirmed against the real
file - `ModelPicker`'s own wiring is exactly this shape, not an
if-chain). The four sliders here aren't distinguished by `type` (`fps`
is `type="int"` same as several non-slider ints) or by `source` (none of
the four have one) - they need their own id-based check, alongside the
existing `source` check rather than replacing it.

Replace:

```tsx
import type { ComponentType } from "react";
import type { SettingType } from "../api";
import { BoolField, ChoiceField, FloatField, IntField, PathField, TextField } from "./Fields";
import SecretField from "./SecretField";
import ModelPicker from "../ModelPicker";
import type { FieldProps } from "./types";

export type { FieldProps, SettingsCtx } from "./types";

/**
 * One renderer per `SettingSpec.type`, chosen by lookup - not a chain of
 * `if (spec.type === ...)` in one component. This map is the entire
 * contract a new setting has to satisfy: give it a `type` that's already a
 * key here (the schema only has these seven) and it renders with zero
 * changes to `Settings.tsx` or `SettingsGroup.tsx`.
 */
const REGISTRY: Record<SettingType, ComponentType<FieldProps>> = {
  bool: BoolField,
  int: IntField,
  float: FloatField,
  text: TextField,
  choice: ChoiceField,
  path: PathField,
  secret: SecretField,
};

export function Field({ spec, ctx, disabled }: FieldProps) {
  // `source` outranks `type`: a field whose options only exist at runtime
  // cannot be described by the catalogue, so the schema declares where they
  // come from instead. Only `model_cache` exists today; an unknown source
  // falls through to the ordinary renderer rather than rendering nothing.
  // Three sources today, one component: `model_cache` (text models, kept
  // for any channel document written before Slice 8) and the two
  // `live_list:` kinds. The kind is the part after the colon.
  if (spec.source === "model_cache" || spec.source?.startsWith("live_list:")) {
    return <ModelPicker spec={spec} ctx={ctx} disabled={disabled} />;
  }
  const Component = REGISTRY[spec.type];
  if (!Component) {
    // Unreachable for anything the current schema can produce - kept as an
    // honest fallback rather than a crash if a future type ships on the
    // backend before this map is updated.
    return <p className="field-row__help">Unsupported setting type: {spec.type}</p>;
  }
  return <Component spec={spec} ctx={ctx} disabled={disabled} />;
}
```

with:

```tsx
import type { ComponentType } from "react";
import type { SettingType } from "../api";
import { BoolField, ChoiceField, FloatField, IntField, PathField, TextField } from "./Fields";
import SecretField from "./SecretField";
import ModelPicker from "../ModelPicker";
import Slider from "../Slider";
import type { FieldProps } from "./types";

export type { FieldProps, SettingsCtx } from "./types";

/**
 * One renderer per `SettingSpec.type`, chosen by lookup - not a chain of
 * `if (spec.type === ...)` in one component. This map is the entire
 * contract a new setting has to satisfy: give it a `type` that's already a
 * key here (the schema only has these seven) and it renders with zero
 * changes to `Settings.tsx` or `SettingsGroup.tsx`.
 */
const REGISTRY: Record<SettingType, ComponentType<FieldProps>> = {
  bool: BoolField,
  int: IntField,
  float: FloatField,
  text: TextField,
  choice: ChoiceField,
  path: PathField,
  secret: SecretField,
};

/** Every slider except `render_threads`, whose upper bound is the
 * machine's own core count and so can't live in a table keyed only by id
 * - handled as its own check in `Field`, Task 4 (Slice 29). Step sizes
 * are 5% of each range (spec §2's own table); `fps`'s 2.25 and
 * `captions_font_size`'s 4.8 are left un-rounded on purpose - a native
 * `<input type="range">` step lands on the nearest multiple regardless,
 * so a "clean" rounded number buys nothing a real fraction doesn't
 * already give the same UX for. */
const SLIDER_BOUNDS: Record<string, { min: number; max: number; step: number; zeroLabel?: string }> = {
  voice_stability: { min: 0, max: 1, step: 0.05 },
  voice_speed: { min: 0.5, max: 2, step: 0.075 },
  fps: { min: 15, max: 60, step: 2.25 },
  captions_font_size: { min: 0, max: 96, step: 4.8, zeroLabel: "Auto-fit" },
};

export function Field({ spec, ctx, disabled }: FieldProps) {
  // `source` outranks `type`: a field whose options only exist at runtime
  // cannot be described by the catalogue, so the schema declares where they
  // come from instead. Only `model_cache` exists today; an unknown source
  // falls through to the ordinary renderer rather than rendering nothing.
  // Three sources today, one component: `model_cache` (text models, kept
  // for any channel document written before Slice 8) and the two
  // `live_list:` kinds. The kind is the part after the colon.
  if (spec.source === "model_cache" || spec.source?.startsWith("live_list:")) {
    return <ModelPicker spec={spec} ctx={ctx} disabled={disabled} />;
  }
  // Chosen by id, not by type or source (Slice 29) - `fps` is `type="int"`
  // the same as several non-slider ints, and none of these four declare a
  // `source`. `render_threads` is a fifth slider, checked separately
  // (Task 4) since its upper bound depends on `ctx.cpuCount`.
  if (spec.id in SLIDER_BOUNDS) {
    return <Slider spec={spec} ctx={ctx} disabled={disabled} {...SLIDER_BOUNDS[spec.id]} />;
  }
  const Component = REGISTRY[spec.type];
  if (!Component) {
    // Unreachable for anything the current schema can produce - kept as an
    // honest fallback rather than a crash if a future type ships on the
    // backend before this map is updated.
    return <p className="field-row__help">Unsupported setting type: {spec.type}</p>;
  }
  return <Component spec={spec} ctx={ctx} disabled={disabled} />;
}
```

### Step 3.4 — verify

`cd web && npm run build && npm run lint`. **Manual check:** Voice
Stability, Voice Speed, Frame Rate, and Font Size each render as a
slider with the right range; dragging updates the displayed value live;
Font Size shows "Auto-fit" at its lowest position; arrow keys move the
slider when it has focus.

### Step 3.5 — commit

`feat: Slider control — voice stability, speed, frame rate, font size — Slice 29 Task 3`

---

## Task 4 — Render Threads: the hardware-aware slider

**Files:** `avg/api/routes_settings.py`, `web/src/api.ts`,
`web/src/fields/types.ts`, `web/src/useSettingsData.ts`,
`web/src/fields/index.tsx`, `tests/api/test_routes_settings.py`

### Step 4.1 — failing test

Append to `tests/api/test_routes_settings.py`:

```python
def test_hardware_endpoint_reports_a_positive_cpu_count(client):
    r = client.get("/api/system/hardware", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["cpu_count"] >= 1
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/api/test_routes_settings.py -q`
— fails, route doesn't exist yet.

### Step 4.2 — backend route

`os` is already imported in `avg/api/routes_settings.py` (added by
Slice 23's Logs-button work) - no new import needed. Add the route,
directly after `get_settings_schema`:

```python
@router.get("/api/system/hardware")
async def get_hardware_info() -> dict:
    """The one machine fact `render_threads`' slider needs client-side -
    its own upper bound (spec §2). Not folded into the settings schema
    response: core count is a fact about this machine, not a description
    of the setting, and the schema is otherwise the same for every
    install.
    """
    return {"cpu_count": os.cpu_count() or 4}
```

(`or 4` matches every other `os.cpu_count()` call site in this codebase's
own fallback - `avg.core.models.RenderRequest`, the vendored render
engine - not a new number picked here.)

### Step 4.3 — verify backend

```
.venv-build\Scripts\python.exe -m pytest tests/api/test_routes_settings.py -q
.venv-build\Scripts\python.exe -m mypy avg
```

### Step 4.4 — frontend: fetch and thread `cpuCount`

In `web/src/api.ts`, add near `getSettingsSchema`:

```typescript
export interface HardwareInfo {
  cpu_count: number;
}

export function getHardwareInfo(): Promise<HardwareInfo> {
  return apiGet<HardwareInfo>("/api/system/hardware");
}
```

In `web/src/fields/types.ts`, add to `SettingsCtx`:

```typescript
  /** The machine's own core count (`GET /api/system/hardware`) -
   * `render_threads`' slider needs it for its own upper bound (Slice 29);
   * `null` until that one fetch resolves. Every other field ignores it. */
  cpuCount: number | null;
```

In `web/src/useSettingsData.ts`, add the fetch to the existing batch.
Replace:

```typescript
  useEffect(() => {
    Promise.all([getSettingsSchema(), getSettingsValues(), getChannels(), getKeys()])
      .then(([schemaRes, valuesRes, channelsRes, keysRes]) => {
        setSchema(schemaRes.settings);
        setAppValues(valuesRes.app);
        setChannelValues(valuesRes.channel);
        setActiveChannelId(valuesRes.active_channel_id);
        setChannels(channelsRes.channels);
        setKeys(keysRes);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);
```

with:

```typescript
  useEffect(() => {
    Promise.all([getSettingsSchema(), getSettingsValues(), getChannels(), getKeys(), getHardwareInfo()])
      .then(([schemaRes, valuesRes, channelsRes, keysRes, hardwareRes]) => {
        setSchema(schemaRes.settings);
        setAppValues(valuesRes.app);
        setChannelValues(valuesRes.channel);
        setActiveChannelId(valuesRes.active_channel_id);
        setChannels(channelsRes.channels);
        setKeys(keysRes);
        setCpuCount(hardwareRes.cpu_count);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);
```

Add the state (near the other `useState` calls at the top of the hook)
and the import. Replace:

```typescript
  const [loadError, setLoadError] = useState<string | null>(null);
```

with:

```typescript
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cpuCount, setCpuCount] = useState<number | null>(null);
```

Replace the `./api` import block. Replace:

```typescript
import {
  deleteProviderKey,
  getChannels,
  getKeys,
  getSettingsSchema,
  getSettingsValues,
  releaseSettingValue,
  saveSettingsValues,
  setProviderKey,
  type ChannelDoc,
  type SettingSpec,
} from "./api";
```

with:

```typescript
import {
  deleteProviderKey,
  getChannels,
  getHardwareInfo,
  getKeys,
  getSettingsSchema,
  getSettingsValues,
  releaseSettingValue,
  saveSettingsValues,
  setProviderKey,
  type ChannelDoc,
  type SettingSpec,
} from "./api";
```

Add `cpuCount` to the returned `ctx`. Replace:

```typescript
  const ctx: SettingsCtx = {
    getValue,
    valueById,
    isDirty,
    onCommit,
    activeChannelId,
    channelLabel,
    scope,
    isOwned,
    onReleaseOwnership: releaseOwnership,
    keys,
    keyBusy,
    keyErrors,
    onSetKey: handleSetKey,
    onDeleteKey: handleDeleteKey,
  };
```

with:

```typescript
  const ctx: SettingsCtx = {
    getValue,
    valueById,
    isDirty,
    onCommit,
    activeChannelId,
    channelLabel,
    scope,
    isOwned,
    onReleaseOwnership: releaseOwnership,
    keys,
    keyBusy,
    keyErrors,
    onSetKey: handleSetKey,
    onDeleteKey: handleDeleteKey,
    cpuCount,
  };
```

One more construction site exists - `web/src/NewTakePanel.tsx` builds
its own `SettingsCtx` for editing per-take overrides. Replace:

```typescript
    keys: {},
    keyBusy: {},
    keyErrors: {},
    onSetKey: () => {},
    onDeleteKey: () => {},
  };
```

with:

```typescript
    keys: {},
    keyBusy: {},
    keyErrors: {},
    onSetKey: () => {},
    onDeleteKey: () => {},
    // Per-take overrides never include render_threads in practice, but
    // the interface requires it regardless - same "present only to
    // satisfy SettingsCtx's shape" reasoning this constructor already
    // uses for keys/keyBusy/keyErrors above.
    cpuCount: null,
  };
```

(Confirmed via search - `NewTakePanel.tsx` and `useSettingsData.ts` are
the only two places a `SettingsCtx` object literal is built anywhere in
`web/src`.)

### Step 4.5 — wire `render_threads` into the dispatcher

Continues directly from Task 3's edit to this same function - add one
more check for this one id, since its max depends on `ctx.cpuCount` and
so can't live in `SLIDER_BOUNDS`. Replace:

```tsx
  // Chosen by id, not by type or source (Slice 29) - `fps` is `type="int"`
  // the same as several non-slider ints, and none of these four declare a
  // `source`. `render_threads` is a fifth slider, checked separately
  // (Task 4) since its upper bound depends on `ctx.cpuCount`.
  if (spec.id in SLIDER_BOUNDS) {
    return <Slider spec={spec} ctx={ctx} disabled={disabled} {...SLIDER_BOUNDS[spec.id]} />;
  }
```

with:

```tsx
  // Chosen by id, not by type or source (Slice 29) - `fps` is `type="int"`
  // the same as several non-slider ints, and none of these four declare a
  // `source`.
  if (spec.id in SLIDER_BOUNDS) {
    return <Slider spec={spec} ctx={ctx} disabled={disabled} {...SLIDER_BOUNDS[spec.id]} />;
  }
  // A sixth id, kept out of SLIDER_BOUNDS: its upper bound is this
  // machine's own core count, not a fixed constant. `?? 8` only covers
  // the brief window before `GET /api/system/hardware` resolves - the
  // slider becomes accurate the moment that fetch lands, same render, no
  // remount.
  if (spec.id === "render_threads") {
    const max = ctx.cpuCount ?? 8;
    return (
      <Slider
        spec={spec}
        ctx={ctx}
        disabled={disabled}
        min={0}
        max={max}
        step={Math.max(1, Math.round(max / 20))}
        zeroLabel="Auto"
      />
    );
  }
```

### Step 4.6 — verify

```
cd web && npm run build && npm run lint
```

**Manual check:** Render Threads' slider max matches this machine's real
core count (compare against Task Manager or `nproc`); position 0 shows
"Auto".

### Step 4.7 — commit

`feat: Render Threads slider, hardware-aware bounds — Slice 29 Task 4`

---

## Task 5 — Captions Font: `ChoiceField`'s "custom" escape hatch

**Files:** `avg/core/settings_schema.py`, `web/src/fields/Fields.tsx`

### Step 5.1 — backend

In `avg/core/settings_schema.py`, replace:

```python
    SettingSpec(
        id="font_family",
        group="captions",
        label="Font",
        help=(
            "The GLOBAL font (family name or a .ttf/.otf path) shared by captions AND "
            "takeover cards - one setting, one look. Empty uses the system default."
        ),
        type="text",
        default="",
        scope="channel",
        invalidates="screen_text",
    ),
```

with:

```python
    SettingSpec(
        id="font_family",
        group="captions",
        label="Font",
        help=(
            "The GLOBAL font shared by captions AND takeover cards - one setting, one "
            "look. \"Custom\" accepts any family name or a .ttf/.otf path, for anything "
            "not in this shortlist."
        ),
        type="choice",
        default="",
        scope="channel",
        choices=["", "Arial", "Segoe UI", "Calibri", "Georgia", "Verdana", "Impact", "Times New Roman", "custom"],
        invalidates="screen_text",
    ),
```

(`""` stays the first choice and the default, unchanged from today -
`ChoiceField` already renders an empty choice as "Not set", which reads
correctly here as "system default," so no extra handling needed for that
part.)

### Step 5.2 — frontend: the custom escape hatch, generic

In `web/src/fields/Fields.tsx`, no new import is needed - `DeferredInput`
is already imported at this file's top (confirmed:
`import { DeferredInput } from "./DeferredInput";`). Replace `ChoiceField`
in full:

```tsx
export function ChoiceField({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  // A bare "" in `choices` (e.g. `image_provider_backup`'s "leave unset to
  // never fall back") is `Select`'s only empty-string option in the whole
  // catalogue. Left as a raw string it normalises to an unlabelled blank
  // row and a blank closed trigger - "no backup" reads as a broken control,
  // not a deliberate choice. `ModelPicker.tsx` already labels its own ""
  // entry "Not set"; give every choice field the same treatment here rather
  // than changing the schema.
  const options = (spec.choices ?? []).map((choice) => (choice === "" ? { value: "", label: "Not set" } : choice));
  return (
    <Select
      value={value}
      options={options}
      disabled={disabled}
      ariaLabel={spec.label}
      onChange={(next) => ctx.onCommit(spec, next)}
    />
  );
}
```

with:

```tsx
export function ChoiceField({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  // A bare "" in `choices` (e.g. `image_provider_backup`'s "leave unset to
  // never fall back") is `Select`'s only empty-string option in the whole
  // catalogue. Left as a raw string it normalises to an unlabelled blank
  // row and a blank closed trigger - "no backup" reads as a broken control,
  // not a deliberate choice. `ModelPicker.tsx` already labels its own ""
  // entry "Not set"; give every choice field the same treatment here rather
  // than changing the schema.
  const options = (spec.choices ?? []).map((choice) => (choice === "" ? { value: "", label: "Not set" } : choice));

  // A literal "custom" choice (Slice 29 - `visual_tone` already had this
  // entry sitting in its own `choices` with no way to act on it;
  // `font_family` gets one too) is an escape hatch, not a real value: the
  // Select shows "custom" whenever the actual stored value isn't one of
  // the OTHER choices, and a follow-up text field carries what the value
  // really is. One fix serves both fields rather than a font-specific
  // special case.
  const choices = spec.choices ?? [];
  const hasCustomEscape = choices.includes("custom");
  const isCustom = hasCustomEscape && !choices.filter((c) => c !== "custom").includes(value);

  return (
    <div className={hasCustomEscape ? "field-choice-with-custom" : undefined}>
      <Select
        value={isCustom ? "custom" : value}
        options={options}
        disabled={disabled}
        ariaLabel={spec.label}
        onChange={(next) => ctx.onCommit(spec, next === "custom" ? "" : next)}
      />
      {isCustom && (
        <DeferredInput
          value={value === "custom" ? "" : value}
          disabled={disabled}
          placeholder="Enter a custom value"
          onCommit={(raw) => ctx.onCommit(spec, raw)}
        />
      )}
    </div>
  );
}
```

Add minimal CSS for the wrapper - a small gap between the Select and the
revealed text field:

```css
.field-choice-with-custom {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
```

### Step 5.3 — verify

`cd web && npm run build && npm run lint`. **Manual check:** Captions
Font shows the curated list; picking "Custom" reveals a text field;
typing a font name or path there and saving persists it; reopening
Settings shows "Custom" selected with that same typed value still in the
text field. `visual_tone`'s own pre-existing "custom" choice now works
the same way, where before it had no way to specify what "custom" meant.

### Step 5.4 — commit

`feat: Captions Font picker with a custom escape hatch — Slice 29 Task 5`

---

## Task 6 — Personality: picker + label rename (#11, #12)

**Files:** `avg/core/settings_schema.py`, `avg/api/routes_settings.py`,
`web/src/api.ts`, `web/src/fields/Fields.tsx`, `web/src/fields/index.tsx`,
`tests/api/test_routes_settings.py`

### Step 6.1 — failing test

Append to `tests/api/test_routes_settings.py`:

```python
def test_personality_presets_endpoint_lists_the_bundled_presets(client):
    r = client.get("/api/personality-presets", headers=AUTH)
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert isinstance(presets, list)
    assert len(presets) > 0
```

Run: fails, route doesn't exist yet.

### Step 6.2 — backend: label rename + `source`, new endpoint

In `avg/core/settings_schema.py`, replace:

```python
    SettingSpec(
        id="content_shape",
        group="creative",
        label="Content shape",
        help="The narrative/content template this channel follows.",
        type="text",
        default="documentary",
        scope="channel",
        invalidates="brief",
    ),
```

with:

```python
    SettingSpec(
        id="content_shape",
        group="creative",
        label="Personality",
        help="The narrative/content template this channel follows.",
        type="text",
        default="documentary",
        scope="channel",
        invalidates="brief",
        # Deliberately still `type="text"`, not `"choice"` - presets are
        # enumerated by avg.pipeline.personality.load_presets(), which
        # this module cannot import (avg/core/ sits below avg/pipeline/ in
        # this app's layering; avg.config.legacy_import importing
        # avg.core.models/takes is the established example of the allowed
        # direction, the reverse is not). Same shape as `voice_id`'s own
        # `source="live_list:voice"`: the field keeps its declared type,
        # `GET /api/personality-presets` (avg.api.routes_settings, the top
        # layer, free to import both) is where the dynamic list actually
        # gets computed.
        source="personality_presets",
    ),
```

(`id`, the field itself, `avg.pipeline.personality`'s own module name,
and every internal identifier stay exactly as they are - display-label-
only, matching the resolution already recorded back in Slice 20's
investigation.)

Add the new route in `avg/api/routes_settings.py`, directly after
`get_hardware_info` (Task 4):

```python
@router.get("/api/personality-presets")
async def get_personality_presets() -> dict:
    """Every bundled personality preset's name - what `content_shape`'s
    picker offers (Slice 29). Imported locally, matching this router's own
    existing pattern for `render_substate`/`script_substate`: this route
    file is the top layer, free to reach into `avg.pipeline`, but keeping
    the import at the call site rather than module-level keeps that
    visible for a router this thin.
    """
    from avg.pipeline.personality import load_presets  # noqa: PLC0415

    return {"presets": sorted(load_presets())}
```

### Step 6.3 — frontend: the picker

In `web/src/api.ts`, add near `getHardwareInfo`:

```typescript
export function getPersonalityPresets(): Promise<{ presets: string[] }> {
  return apiGet<{ presets: string[] }>("/api/personality-presets");
}
```

In `web/src/fields/Fields.tsx`, add (after `ChoiceField`):

```tsx
/**
 * `content_shape`'s picker - `source="personality_presets"`, fetched
 * once per mount rather than pre-loaded into the schema response (the
 * list is dynamic, per-install; the schema response is otherwise
 * identical everywhere). Falls back to a plain `TextField` if the fetch
 * fails or the bundle is somehow empty - never locks the user out of a
 * setting they could always edit before, the same principle `source`'s
 * own docstring in `avg.core.settings_schema` already states for
 * `"model_cache"`.
 */
export function PersonalityField({ spec, ctx, disabled }: FieldProps) {
  const [presets, setPresets] = useState<string[] | null>(null);

  useEffect(() => {
    getPersonalityPresets()
      .then((res) => setPresets(res.presets))
      .catch(() => setPresets([]));
  }, []);

  if (presets === null || presets.length === 0) {
    return <TextField spec={spec} ctx={ctx} disabled={disabled} />;
  }

  const value = String(ctx.getValue(spec) ?? "");
  // A stale value (a preset removed since it was picked) still needs to
  // show as something, not silently jump to whatever's first in the
  // list - same reasoning ModelPicker.tsx already applies to a stale
  // model id.
  const options = presets.includes(value) || value === "" ? presets : [...presets, value];

  return (
    <Select
      value={value}
      options={options}
      disabled={disabled}
      ariaLabel={spec.label}
      onChange={(next) => ctx.onCommit(spec, next)}
    />
  );
}
```

Add `useEffect`/`useState` to this file's imports - confirmed not
already present (`Fields.tsx`'s current top imports are `FieldProps`
from `./types`, `DeferredInput`, and `Select` only, no React hooks used
anywhere in this file yet). Replace:

```typescript
import type { FieldProps } from "./types";
import { DeferredInput } from "./DeferredInput";
import Select from "../Select";
```

with:

```typescript
import { useEffect, useState } from "react";
import type { FieldProps } from "./types";
import { DeferredInput } from "./DeferredInput";
import Select from "../Select";
import { getPersonalityPresets } from "../api";
```

In `web/src/fields/index.tsx`, add one more check to `Field` (continuing
from Tasks 3/4's edits to the same function), for `content_shape`'s
`source` - alongside the existing `model_cache`/`live_list:` check, not
inside it (a different source value, a different component). Replace:

```tsx
  if (spec.source === "model_cache" || spec.source?.startsWith("live_list:")) {
    return <ModelPicker spec={spec} ctx={ctx} disabled={disabled} />;
  }
```

with:

```tsx
  if (spec.source === "model_cache" || spec.source?.startsWith("live_list:")) {
    return <ModelPicker spec={spec} ctx={ctx} disabled={disabled} />;
  }
  if (spec.source === "personality_presets") {
    return <PersonalityField spec={spec} ctx={ctx} disabled={disabled} />;
  }
```

and add the import. Replace:

```tsx
import { BoolField, ChoiceField, FloatField, IntField, PathField, TextField } from "./Fields";
```

with:

```tsx
import { BoolField, ChoiceField, FloatField, IntField, PathField, PersonalityField, TextField } from "./Fields";
```

### Step 6.4 — verify

```
.venv-build\Scripts\python.exe -m pytest tests/api/test_routes_settings.py -q
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
cd web && npm run build && npm run lint
```

**Manual check:** the field labeled "Content shape" now reads
"Personality" everywhere it appears; it renders as a picker listing the
bundled presets (documentary, news_report, zack_d_films, and whatever
else `personality.txt` actually bundles - confirm the real list once this
runs, not the three names recalled from earlier investigation); picking
one and saving persists correctly; the underlying `content_shape` value
saved is still the raw preset name, unchanged in shape from before this
slice.

### Step 6.5 — commit

`feat: Personality picker, sourced from the bundled presets — Slice 29 Task 6`

---

## Task 7 — Full regression, update progress ledger

### Step 7.1

```
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
cd web && npm run build && npm run lint
```

### Step 7.2

Update `.superpowers/sdd/progress.md`: Slice 29 complete - all three
items noted, plus the three discrepancies found against the original
backlog wording (Voice Model and Style already satisfied before this
slice; Font Size added to the slider set despite not being in the
original four).

### Step 7.3 — commit

`docs: Slice 29 complete — Settings screen (Models grouping, Slider, pickers, Personality)`

---

## Deferred / explicitly out of scope

- `voice_model_id` - stays free text, by existing design (§0 of the
  spec).
- Real OS font enumeration - the curated list is deliberately smaller
  in scope than that.
- Any change to `ModelPicker.tsx` itself.
