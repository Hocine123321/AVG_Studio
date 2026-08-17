/**
 * Lucide icons, inlined at the system's stroke-width of 2.75 for the
 * rounder, heavier look the Organic guide asks for. Inlined rather than
 * imported from `lucide-react` so the app keeps its zero-runtime-dependency
 * front end — swap to the package if you'd rather, the names match.
 *
 * Every icon paints on `currentColor` and takes its size from `size`.
 */

interface IconProps {
  size?: number;
}

const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  viewBox: "0 0 24 24",
};

export function IconGrid({ size = 21 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <rect x="3" y="3" width="7" height="7" rx="2" />
      <rect x="14" y="3" width="7" height="7" rx="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" />
    </svg>
  );
}

export function IconPlus({ size = 21 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function IconFilm({ size = 21 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="4" />
      <path d="M8 4v16M16 4v16M3 12h18" />
    </svg>
  );
}

export function IconSliders({ size = 21 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M4 7h16M4 17h16" />
      <circle cx="9" cy="7" r="2.4" fill="currentColor" stroke="none" />
      <circle cx="16" cy="17" r="2.4" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconMoon({ size = 19 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
    </svg>
  );
}

export function IconSun({ size = 19 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.5 1.5M17.3 17.3l1.5 1.5M18.8 5.2l-1.5 1.5M6.7 17.3l-1.5 1.5" />
    </svg>
  );
}

export function IconArrowLeft({ size = 17 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M19 12H5M11 6l-6 6 6 6" />
    </svg>
  );
}

export function IconCheck({ size = 13 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} strokeWidth={3.4} aria-hidden="true">
      <path d="m5 12.5 5 5L19 6.5" />
    </svg>
  );
}

export function IconAlert({ size = 13 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} strokeWidth={3} aria-hidden="true">
      <path d="M12 6v8" />
      <circle cx="12" cy="18.4" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconPlay({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M7 4.5 20 12 7 19.5Z" />
    </svg>
  );
}

export function IconStop({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="5.5" y="5.5" width="13" height="13" rx="3" />
    </svg>
  );
}

export function IconPause({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="6" y="5" width="4" height="14" rx="1.2" />
      <rect x="14" y="5" width="4" height="14" rx="1.2" />
    </svg>
  );
}

export function IconVolume({ size = 17 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M4 9v6h4l5 4V5L8 9H4Z" strokeLinejoin="round" />
      <path d="M16.5 9a4.5 4.5 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11" />
    </svg>
  );
}

export function IconMute({ size = 17 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M4 9v6h4l5 4V5L8 9H4Z" strokeLinejoin="round" />
      <path d="M16 9l5 6M21 9l-5 6" />
    </svg>
  );
}

/** Broadcast/RSS glyph — stands in for "channels" (the app's per-channel
 * creative-settings scope, not a literal radio feature). */
export function IconChannels({ size = 21 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M4 11a9 9 0 0 1 9 9" />
      <path d="M4 4a16 16 0 0 1 16 16" />
      <circle cx="5" cy="19" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconFrame({ size = 26 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} strokeWidth={2} aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="4" />
      <path d="m5 16 4.2-4.2a2 2 0 0 1 2.8 0L16 15.8" />
      <circle cx="15.2" cy="9.4" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** App health: a heartbeat trace. Reads as "is this thing alive", which is
 * exactly what that screen answers. */
export function IconPulse({ size = 21 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M3 12h4l2.5-6 4 12 2.5-6h5" />
    </svg>
  );
}

/** The status pill's detail-panel toggle (Slice 17). Points down at rest;
 * the caller rotates it 180° open via CSS (`transform: rotate(180deg)`),
 * same as every other directional control in this file that has only one
 * glyph and lets its user rotate it, rather than shipping an "up" twin. */
export function IconChevron({ size = 13 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} strokeWidth={3} aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

/** Dismiss / acknowledge - the sticky queue's own close glyph (Slice 17).
 * Same stroke weight as `IconCheck`/`IconAlert`, the two icons it shares
 * the pill's small-icon slot with. */
export function IconX({ size = 13 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} strokeWidth={3} aria-hidden="true">
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}
