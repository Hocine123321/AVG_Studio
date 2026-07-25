import { IconFilm, IconGrid, IconMoon, IconPlus, IconSliders, IconSun } from "./Icons";
import { useTheme } from "./theme";

export type RailSection = "home" | "studio";

export interface RailProps {
  /** Which section is showing right now. The amber marker names "where you
   * are" — in Organic Night the accent carries identity as well as
   * liveness, so this no longer competes with the render indicator (that
   * one pulses; this one doesn't). */
  active: RailSection;
  onHome: () => void;
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
 * The create wizard and settings screen are still out of scope for Slice 1,
 * so their icons render but stay disabled rather than pretending to open
 * something that doesn't exist yet.
 */
export default function Rail({ active, onHome }: RailProps) {
  const { theme, toggle } = useTheme();
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

      <button type="button" className="rail__item" disabled title="New video — not available in this build">
        <IconPlus />
      </button>

      <span className={`rail__item${active === "studio" ? " rail__item--on" : ""}`} aria-hidden="true">
        <IconFilm />
      </span>

      <span className="rail__spacer" />

      <button
        type="button"
        className="rail__item"
        onClick={toggle}
        title={night ? "Switch to day" : "Switch to night"}
        aria-label={night ? "Switch to day" : "Switch to night"}
        aria-pressed={!night}
      >
        {night ? <IconMoon /> : <IconSun />}
      </button>

      <button
        type="button"
        className="rail__item rail__settings"
        disabled
        title="Settings — not available in this build"
      >
        <IconSliders />
      </button>
    </nav>
  );
}
