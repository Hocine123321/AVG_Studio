import { useCallback, useEffect, useRef, useState } from "react";
import { getSettingsValues, saveSettingsValues } from "./api";

export type Theme = "night" | "day";

const KEY = "avg.theme";

function isTheme(value: unknown): value is Theme {
  return value === "night" || value === "day";
}

/**
 * Applies a `motion_scale` setting value to the document: the numeric
 * `--motion-scale` custom property every duration in `tokens.css` multiplies
 * by, and a `data-motion` attribute for the handful of rules that cannot be
 * expressed as a duration at all - `motion_scale: "none"` mapping to `0`
 * turns a division like `1.3s / max(var(--motion-scale), 0.01)` into a
 * 130 second animation rather than no animation, which for a fill that
 * starts translated outside its own clipped track means the user who asked
 * for the least motion sees nothing move into place at all. `data-motion`
 * lets a rule opt out of the arithmetic entirely and just freeze in its
 * resting position.
 *
 * Shared by `useTheme` (the boot-time application, covering every screen
 * from launch) and `Settings.tsx` (the live-preview write while the user is
 * on that screen) so the mapping is defined once.
 */
export function applyMotionScale(rawScale: unknown): void {
  const scale = String(rawScale ?? "full");
  const numeric = scale === "none" ? "0" : scale === "reduced" ? "0.5" : "1";
  document.documentElement.style.setProperty("--motion-scale", numeric);
  if (scale === "none") {
    document.documentElement.setAttribute("data-motion", "none");
  } else {
    document.documentElement.removeAttribute("data-motion");
  }
}

function initial(): Theme {
  const stored = localStorage.getItem(KEY);
  if (isTheme(stored)) return stored;
  // Night is the default, and deliberately not `prefers-color-scheme`: this
  // app's ground is a judgement about exposure, not about the user's OS
  // preference. Someone on a light desktop still wants a dark surround
  // around a 9:16 frame. They can switch; we just don't guess for them.
  return "night";
}

/**
 * Owns the app's theme. Writes `data-theme` onto `<html>` (which is what
 * `tokens.css`'s `[data-theme="day"]` block keys off).
 *
 * Night and day are both generated from the same Organic OKLCH ramps, so
 * this is a token swap, not an inversion — a sage "done" node carries the
 * same visual weight in either.
 *
 * ── Where the value lives ──────────────────────────────────
 *
 * The `theme` setting in `avg.core.settings_schema` is canonical: this hook
 * reads it on mount and writes back on every change, so the Settings screen
 * and the rail's toggle are two controls over one value rather than two
 * unrelated ones. Until Slice 6C they *were* unrelated — this hook only
 * ever touched `localStorage` and never read the settings document, while
 * the catalogue offered nine theme ids, none of which this file implements.
 * Both halves of that wiring were missing.
 *
 * `localStorage` is kept, but only as a first-paint cache: the settings
 * fetch is a round trip, and without a local value the app would paint
 * night and then flip to day. A value written by an older build (or a
 * settings document still holding one of the nine dead ids) is not a valid
 * theme and is ignored, leaving the default.
 */
export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void; toggle: () => void } {
  const [theme, setThemeState] = useState<Theme>(initial);
  // Suppresses the write-back for the one state change that came FROM the
  // settings document, so loading never turns straight into a save.
  const hydrating = useRef(true);

  useEffect(() => {
    getSettingsValues()
      .then((values) => {
        const stored = values.app.theme;
        if (isTheme(stored)) setThemeState(stored);
        // `motion_scale` rides the same boot-time fetch as theme, for the
        // same reason: Settings' own effect (see Settings.tsx) only applies
        // it once the user opens that screen, which left every other
        // screen — Home included — running at full motion regardless of
        // what the user chose, from launch until they happened to visit
        // preferences. Settings' effect stays too, as the live-preview
        // writer while the user is on that screen; this is what covers
        // every screen before that ever happens.
        applyMotionScale(values.app.motion_scale);
      })
      .catch(() => {
        /* Offline, or before onboarding has written a settings file: the
           local value stands. */
      })
      .finally(() => {
        hydrating.current = false;
      });
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(KEY, theme);
    if (hydrating.current) return;
    // Fire and forget: a failed save costs the preference at next launch,
    // never the theme the user just picked in this session.
    saveSettingsValues({ theme }).catch(() => {});
  }, [theme]);

  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  const toggle = useCallback(() => setThemeState((t) => (t === "night" ? "day" : "night")), []);

  return { theme, setTheme, toggle };
}
