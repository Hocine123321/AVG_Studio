import { useCallback, useEffect, useRef, useState } from "react";
import { getSettingsValues, saveSettingsValues } from "./api";

export type Theme = "night" | "day";

const KEY = "avg.theme";

function isTheme(value: unknown): value is Theme {
  return value === "night" || value === "day";
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
