import { useEffect, useMemo, useRef, useState } from "react";
import { portToGlobal, type SettingSpec } from "./api";
import HealthPanel from "./HealthPanel";
import ModelsPanel from "./ModelsPanel";
import SettingsGroup from "./SettingsGroup";
import { useSettingsData } from "./useSettingsData";

/** Nicer display names for the schema's raw `group` strings. Anything not
 * listed here still renders - just title-cased - so a brand-new group
 * value on the backend needs no matching entry here to show up correctly;
 * this map only ever improves on the fallback, never gates it. */
const GROUP_LABELS: Record<string, string> = {
  app: "App & components",
  video: "Video format",
  captions: "Captions",
  overlay: "Overlay & cards",
  voice: "Voice",
  images: "Images",
  render: "Render",
  creative: "Creative",
  models: "Models & cost",
  keys: "Provider keys",
};

function labelForGroup(group: string): string {
  return GROUP_LABELS[group] ?? group.charAt(0).toUpperCase() + group.slice(1);
}

/** Pulls the JSON `detail` out of `api.ts`'s formatted error message, same
 * trick `App.tsx` uses for render-start failures - so a 400 like "no
 * active channel — activate a channel before saving channel-scoped
 * settings" reaches the user as that sentence, not as the whole
 * "POST /api/... failed: 400 Bad Request - {...}" wrapper. */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

/**
 * The settings screen: renders itself entirely from
 * `GET /api/settings/schema` (35 entries at last count) rather than one
 * hand-written control per setting - the legacy PowerShell app's settings
 * menu was ~4,300 lines for exactly that reason. Add a row to
 * `avg.core.settings_schema.SETTINGS` and it appears here with no change
 * to this file, `SettingsGroup.tsx`, or anything under `fields/` (as long
 * as its `type` is one of the seven the schema already declares).
 *
 * Three things this screen is built around:
 *
 * 1. Search leads. The search box filters every group's fields by label,
 *    help text AND id, and search always wins over the category list -
 *    the category nav is the fallback for browsing, not the main door in.
 * 2. Every non-secret edit joins one `draft` batch and is only sent on
 *    "Save changes" - the one terracotta action on this whole screen -
 *    via a single `POST /api/settings/values` call. That mirrors the
 *    route's own atomicity (it validates the whole batch, including "is a
 *    channel even active", before writing anything), and means a failed
 *    save never leaves the user guessing which of several edits landed.
 * 3. Secret fields are a separate lane entirely: they never enter
 *    `draft`, are saved/cleared immediately via `/api/keys/{provider}`,
 *    and this component never sees or stores a real key value - only
 *    `SecretField`'s own transient input does, for as long as one request
 *    takes (see that file).
 */
export default function Settings() {
  const {
    schema,
    ctx,
    dirtyCount,
    saving,
    saveError,
    handleSave,
    handleDiscard,
    loadError,
    activeChannelId,
    channelLabel,
  } = useSettingsData();
  const { valueById, followingGlobal } = ctx;

  const [porting, setPorting] = useState(false);
  const [portError, setPortError] = useState<string | null>(null);
  const [portResult, setPortResult] = useState<number | null>(null);

  const [search, setSearch] = useState("");
  /** Which group is currently in view. Derived from an IntersectionObserver
   * while the user scrolls, and set outright by a click - see `jumpTo`,
   * which silences the observer for the length of the smooth scroll so the
   * mark does not flicker through every group the animation crosses. */
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const jumping = useRef(false);
  const jumpTimer = useRef<number | null>(null);

  /** Port to global (Important 3 of the Slice 12 review): the route existed
   * with no button in front of it, so a channel switched to "global" landed
   * on an empty defaults document and every value read back as the
   * catalogue default - the feature's first-run experience was "all my
   * settings reset". A confirm sits in front of it because the route
   * overwrites the defaults document wholly, not merges into it. */
  async function handlePortToGlobal() {
    if (!activeChannelId) return;
    const ok = window.confirm(
      "Copy this channel's current settings into the app-wide defaults? " +
        "This replaces the defaults document completely - any channel already " +
        "following it will pick up these values immediately.",
    );
    if (!ok) return;
    setPorting(true);
    setPortError(null);
    setPortResult(null);
    try {
      const res = await portToGlobal();
      setPortResult(res.ported);
    } catch (err) {
      setPortError(extractDetail(err instanceof Error ? err.message : String(err)));
    } finally {
      setPorting(false);
    }
  }

  const groups = useMemo(() => {
    if (!schema) return [];
    const order: string[] = [];
    const byGroup = new Map<string, SettingSpec[]>();
    for (const spec of schema) {
      if (!byGroup.has(spec.group)) {
        byGroup.set(spec.group, []);
        order.push(spec.group);
      }
      byGroup.get(spec.group)!.push(spec);
    }
    return order.map((group) => ({ group, label: labelForGroup(group), specs: byGroup.get(group) ?? [] }));
  }, [schema]);

  const query = search.trim().toLowerCase();
  const searching = query.length > 0;

  /** Search filters fields by label, help and id, and a group with no
   * surviving field disappears from the page AND from the nav. Every group
   * renders otherwise - the nav navigates, it no longer filters. */
  const visibleGroups = useMemo(() => {
    if (!searching) return groups;
    return groups
      .map((g) => ({
        ...g,
        specs: g.specs.filter(
          (s) =>
            s.label.toLowerCase().includes(query) ||
            s.help.toLowerCase().includes(query) ||
            s.id.toLowerCase().includes(query),
        ),
      }))
      .filter((g) => g.specs.length > 0);
  }, [groups, searching, query]);

  // Marks whichever group crosses the middle band of the viewport. The
  // root is the viewport rather than `.app-shell__content`: the scroll
  // container fills the viewport, so the two agree, and reaching for an
  // ancestor by class from here would couple this screen to the shell.
  useEffect(() => {
    const root = contentRef.current;
    if (!root) return;
    const sections = Array.from(root.querySelectorAll<HTMLElement>("[data-settings-group]"));
    if (sections.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // A click already knows where it is going. Ignoring the observer
        // for the length of the smooth scroll is what stops the mark
        // flickering through every group the animation passes over and
        // then landing wherever the last callback happened to fire.
        if (jumping.current) return;
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveGroup(entry.target.getAttribute("data-settings-group"));
          }
        }
      },
      { rootMargin: "-40% 0px -55% 0px", threshold: 0 },
    );
    for (const section of sections) observer.observe(section);
    return () => observer.disconnect();
  }, [visibleGroups]);

  function jumpTo(group: string) {
    contentRef.current
      ?.querySelector(`[data-settings-group="${group}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveGroup(group);
    // Long enough to cover a smooth scroll the whole height of the page.
    // There is no event for "a smooth scroll finished" - `scrollend` is not
    // in this WebView2 - so this is a timer, and it is deliberately generous:
    // the cost of it being too long is that the mark lags the scroll for a
    // moment, and the cost of it being too short is the flicker it exists to
    // prevent.
    if (jumpTimer.current !== null) window.clearTimeout(jumpTimer.current);
    jumping.current = true;
    jumpTimer.current = window.setTimeout(() => {
      jumping.current = false;
    }, 700);
  }

  if (loadError) {
    return (
      <div className="settings">
        <p className="home__empty">Could not load settings: {loadError}</p>
      </div>
    );
  }

  if (!schema) {
    return (
      <div className="settings">
        <p className="home__empty">Loading…</p>
      </div>
    );
  }

  const matchCount = visibleGroups.reduce((n, g) => n + g.specs.length, 0);

  return (
    <div className="settings">
      <header className="settings__header">
        <div>
          <div className="home__kicker">Configure</div>
          <h1 className="home__title">Settings</h1>
        </div>
        <span className="home__spacer" />
        {activeChannelId ? (
          <span className="chip">Channel: {channelLabel ?? activeChannelId}</span>
        ) : (
          <span className="chip chip--bad">No active channel</span>
        )}
      </header>

      {followingGlobal && (
        // One note, shown once, rather than repeating it on every disabled
        // row below (Important 4 of the Slice 12 review). Every
        // `scope="channel"` field except this switch itself renders
        // disabled while it is on - see `SettingsGroup.tsx`.
        <div className="studio__banner studio__banner--muted">
          <p>
            This channel is following the app-wide defaults right now, so every channel-scoped
            setting below is shown disabled. Its own values are unchanged and come back the
            moment you switch &ldquo;This channel&rsquo;s settings&rdquo; back to its own
            document. To change the defaults themselves, use Port to global in the App group.
          </p>
        </div>
      )}

      <div className="settings__layout">
        <nav className="settings__nav" aria-label="Setting categories">
          <input
            className="settings__search"
            type="search"
            placeholder="Search settings…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search settings by name or description"
          />
          <div className="settings__nav-list">
            {visibleGroups.map((g) => (
              <button
                key={g.group}
                type="button"
                aria-current={activeGroup === g.group || undefined}
                className={`settings__nav-item${activeGroup === g.group ? " settings__nav-item--on" : ""}`}
                onClick={() => jumpTo(g.group)}
              >
                {g.label}
              </button>
            ))}
          </div>
        </nav>

        <div className="settings__content" ref={contentRef}>
          {searching && (
            <p className="settings__result-count">
              {matchCount} match{matchCount === 1 ? "" : "es"} for &ldquo;{search.trim()}&rdquo;
            </p>
          )}

          {visibleGroups.length === 0 ? (
            <p className="home__empty">Nothing matches &ldquo;{search.trim()}&rdquo;.</p>
          ) : (
            visibleGroups.map((g) => (
              <div key={g.group} data-settings-group={g.group}>
                {/* Only when the whole group is on screen: while searching,
                    a filtered subset of the model fields would give the
                    overview table nothing coherent to summarise. */}
                {/* Components heads App rather than getting a nav entry of
                    its own: both answer "where are this app's tools and do
                    they work", and App is the group that holds the app's
                    own behaviour rather than a video's.

                    It was keyed to a `paths` group until 2026-07-30, and
                    `avg.core.settings_schema.SETTINGS` had no such group at
                    the time - it declared nine (runtime, creative, video,
                    models, captions, voice, images, render, keys), so this
                    panel had silently never rendered. Slice 12 renamed
                    `runtime` to `app` (a channel-scoped group would be the
                    wrong home for "does this machine have ffmpeg");
                    `creative` is untouched and still its own group.
                    `test_the_app_group_replaces_runtime` is what now asserts
                    `runtime` is gone; this panel follows the group's current
                    name rather than being left keyed to one that no longer
                    exists. */}
                {g.group === "app" && !searching && <HealthPanel />}
                {g.group === "app" && !searching && (
                  <section className="models-panel" aria-label="Port to global">
                    <div className="spend-card">
                      <div>
                        <p className="lbl">Port to global</p>
                        <b>Copy this channel&rsquo;s settings into the app-wide defaults</b>
                      </div>
                      <span className="spend-card__note">
                        Overwrites the app-wide defaults document with this channel&rsquo;s
                        current channel-scope values, whole. Any channel following the app-wide
                        defaults picks these up immediately.
                      </span>
                      <button
                        type="button"
                        className="btn"
                        onClick={handlePortToGlobal}
                        disabled={!activeChannelId || porting}
                      >
                        {porting ? "Porting…" : "Port to global"}
                      </button>
                      {portError && <p className="field-row__blocked">{portError}</p>}
                      {portResult !== null && !portError && (
                        <p className="spend-card__note">Ported {portResult} setting{portResult === 1 ? "" : "s"}.</p>
                      )}
                    </div>
                  </section>
                )}
                {g.group === "models" && !searching && (
                  <ModelsPanel
                    specs={g.specs}
                    valueOf={valueById}
                    lengthPreset={valueById("length_preset") || "60s"}
                  />
                )}
                <SettingsGroup title={g.label} specs={g.specs} ctx={ctx} />
              </div>
            ))
          )}
        </div>
      </div>

      <div className="settings__savebar">
        {saveError && <p className="settings__save-error">{saveError}</p>}
        <span className="settings__dirty-note">
          {dirtyCount > 0 ? `${dirtyCount} unsaved change${dirtyCount === 1 ? "" : "s"}` : "No unsaved changes"}
        </span>
        <button type="button" className="btn" onClick={handleDiscard} disabled={dirtyCount === 0 || saving}>
          Discard
        </button>
        <button type="button" className="btn btn--primary" onClick={handleSave} disabled={dirtyCount === 0 || saving}>
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}
