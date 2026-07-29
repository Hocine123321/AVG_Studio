import { IconChannels, IconFilm, IconGrid, IconMoon, IconPlus, IconPulse, IconSliders, IconSun } from "./Icons";
import type { Theme } from "./theme";

export type RailSection = "home" | "studio" | "create" | "channels" | "settings" | "health";

export interface RailProps {
  /** Which section is showing right now. The amber marker names "where you
   * are" — in Organic Night the accent carries identity as well as
   * liveness, so this no longer competes with the render indicator (that
   * one pulses; this one doesn't). */
  active: RailSection;
  onHome: () => void;
  onCreate: () => void;
  onChannels: () => void;
  onSettings: () => void;
  onHealth: () => void;
  /** Owned by `App.tsx` (not this component) so the same choice also
   * applies to the welcome screens, which render before Rail ever mounts. */
  theme: Theme;
  onToggleTheme: () => void;
}

/**
 * The icon rail — present on every screen (mounted once in `App.tsx`).
 *
 * Two changes from the previous build:
 *   - Real Lucide icons at stroke-width 2.75, replacing the Unicode glyphs
 *     (`▤ ＋ ◇ ⚙`), which rendered at whatever weight the font felt like.
 *   - A theme switch above Settings. It is a real control, not a tweak:
 *     Night is the default, Day is a full citizen, and the choice persists.
 *
 * The create wizard (Slice 4, Task 6) is a real screen now, reachable
 * here — the "+" button is no longer disabled. Settings (Slice 2 Task 4)
 * is likewise a real screen, reachable here.
 */
export default function Rail({
  active,
  onHome,
  onCreate,
  onChannels,
  onSettings,
  onHealth,
  theme,
  onToggleTheme,
}: RailProps) {
  const night = theme === "night";

  return (
    <nav className="rail" aria-label="Sections">
      <div className="rail__logo" aria-hidden="true">
        A
      </div>

      <button
        type="button"
        className={`rail__item${active === "home" ? " rail__item--on" : ""}`}
        onClick={onHome}
        title="Your videos"
        aria-label="Your videos"
        aria-current={active === "home" ? "page" : undefined}
      >
        <IconGrid />
      </button>

      <button
        type="button"
        className={`rail__item${active === "create" ? " rail__item--on" : ""}`}
        onClick={onCreate}
        title="New video"
        aria-label="New video"
        aria-current={active === "create" ? "page" : undefined}
      >
        <IconPlus />
      </button>

      <span className={`rail__item${active === "studio" ? " rail__item--on" : ""}`} aria-hidden="true">
        <IconFilm />
      </span>

      <span className="rail__spacer" />

      <button
        type="button"
        className={`rail__item${active === "channels" ? " rail__item--on" : ""}`}
        onClick={onChannels}
        title="Channels"
        aria-label="Channels"
        aria-current={active === "channels" ? "page" : undefined}
      >
        <IconChannels />
      </button>

      <button
        type="button"
        className={`rail__item${active === "health" ? " rail__item--on" : ""}`}
        onClick={onHealth}
        title="App health"
        aria-label="App health"
        aria-current={active === "health" ? "page" : undefined}
      >
        <IconPulse />
      </button>

      {/* A compact quick-toggle, not a full 48px rail item: switching theme
          is a preference flip, not navigation, and giving it the same weight
          as Library/Settings overstated it. Half height, same width, so it
          still lines up with the column. */}
      <button
        type="button"
        className="rail__item rail__item--mini"
        onClick={onToggleTheme}
        title={night ? "Switch to day" : "Switch to night"}
        aria-label={night ? "Switch to day" : "Switch to night"}
        aria-pressed={!night}
      >
        {/* A filled accent disc, per the 3rd redesign: it reads as a
            switch rather than as another place to navigate to. */}
        <span className="rail__toggle-disc">{night ? <IconMoon size={11} /> : <IconSun size={11} />}</span>
      </button>

      <button
        type="button"
        className={`rail__item rail__settings${active === "settings" ? " rail__item--on" : ""}`}
        onClick={onSettings}
        title="Settings"
        aria-label="Settings"
        aria-current={active === "settings" ? "page" : undefined}
      >
        <IconSliders />
      </button>
    </nav>
  );
}
