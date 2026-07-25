import { useCallback, useEffect, useState } from "react";

export type Theme = "night" | "day";

const KEY = "avg.theme";

function initial(): Theme {
  const stored = localStorage.getItem(KEY);
  if (stored === "night" || stored === "day") return stored;
  // Night is the default, and deliberately not `prefers-color-scheme`: this
  // app's ground is a judgement about exposure, not about the user's OS
  // preference. Someone on a light desktop still wants a dark surround
  // around a 9:16 frame. They can switch; we just don't guess for them.
  return "night";
}

/**
 * Owns the app's theme. Writes `data-theme` onto `<html>` (which is what
 * `tokens.css`'s `[data-theme="day"]` block keys off) and remembers the
 * choice across launches.
 *
 * Night and day are both generated from the same Organic OKLCH ramps, so
 * this is a token swap, not an inversion — a sage "done" node carries the
 * same visual weight in either.
 */
export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void; toggle: () => void } {
  const [theme, setThemeState] = useState<Theme>(initial);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(KEY, theme);
  }, [theme]);

  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  const toggle = useCallback(() => setThemeState((t) => (t === "night" ? "day" : "night")), []);

  return { theme, setTheme, toggle };
}
