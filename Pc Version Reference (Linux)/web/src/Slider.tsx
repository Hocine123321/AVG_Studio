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
   * sentinels (design spec §2), not a general-purpose label override. */
  zeroLabel?: string;
}

/**
 * The one slider every numeric setting with real bounds uses (design spec
 * §2) - native `<input type="range">`, restyled, not a bespoke control per
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
