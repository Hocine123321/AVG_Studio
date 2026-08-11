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

/** The category tab strip's own grouping of the schema's raw `group`
 * values (Slice 19 §1.1) - deliberately coarser than `GROUP_LABELS`
 * above, which only renames a group; this decides which tab it lives
 * under. A schema group missing from every list here is a real bug (a
 * setting the UI would never show), which is exactly what the runtime
 * assertion in `groups` below exists to catch loudly rather than let the
 * setting quietly vanish from the screen. */
const CATEGORIES: { label: string; groups: string[] }[] = [
  { label: "Creative", groups: ["creative"] },
  { label: "Models", groups: ["models", "keys"] },
  { label: "Images", groups: ["images"] },
  { label: "Voice", groups: ["voice"] },
  { label: "Captions", groups: ["captions", "overlay"] },
  { label: "Render", groups: ["render", "video"] },
  { label: "Paths", groups: ["app"] },
];

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
 * as its `type` is one of the seven the schema already declares, and its
 * `group` has a home in `CATEGORIES` above).
 *
 * Four things this screen is built around:
 *
 * 1. Search leads. The search box filters every group's fields by label,
 *    help text AND id, and search always wins over the category tabs -
 *    while searching, matches show across every category regardless of
 *    which tab is selected; the tabs are the browsing fallback, not the
 *    main door in.
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
 * 4. The Global/Channel toggle (§3) decides which document channel-scope
 *    fields read/write - the active channel's own, or the app-wide
 *    defaults directly. Ownership (§2.4, `SettingsGroup.tsx`) only means
 *    anything in the former: a field the channel has no opinion on renders
 *    disabled with a "Take ownership" affordance rather than editable.
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
    scope,
    setScope,
  } = useSettingsData();
  const { valueById } = ctx;

  const [porting, setPorting] = useState(false);
  const [portError, setPortError] = useState<string | null>(null);
  const [portResult, setPortResult] = useState<number | null>(null);

  const [search, setSearch] = useState("");
  /** Which category tab is selected. Set outright by a click
   * (`jumpToCategory`) or, while searching, corrected to wherever the
   * first match actually lives (§1.2 "search always wins over the
   * category list") - see the effect below. */
  const [activeCategory, setActiveCategory] = useState<string>(CATEGORIES[0].label);
  /** Which group is nearest the middle of the viewport, within the active
   * category's own (possibly multi-group) content - purely a scrollspy
   * detail for `.settings-group__title--active` below; single-group
   * categories simply never have anything else to disambiguate, so this
   * stays `null` there (§1.2's "the mechanism is simply inert"). */
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
    const result = order.map((group) => ({
      group,
      label: labelForGroup(group),
      specs: byGroup.get(group) ?? [],
    }));
    // Slice 19 §1.1's own completeness test, run as a loud runtime
    // assertion rather than a frontend unit test (CLAUDE.md's "no
    // frontend unit tests" house style) - "settings that the UI never
    // shows have been deleted before for looking unused," so a schema
    // group with no tab to live under must crash the first render that
    // sees it, not fail silently.
    for (const g of result) {
      if (!CATEGORIES.some((c) => c.groups.includes(g.group))) {
        throw new Error(`Settings group "${g.group}" has no category home in CATEGORIES (Settings.tsx)`);
      }
    }
    return result;
  }, [schema]);

  const query = search.trim().toLowerCase();
  const searching = query.length > 0;

  /** Search filters fields by label, help and id, across EVERY group
   * regardless of the active category tab (§1.2) - the tabs narrow when
   * not searching (see `categoryGroups` below); a search match must never
   * be hidden by whichever tab happens to be selected. */
  const searchResults = useMemo(() => {
    if (!searching) return [];
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

  /** Not searching: only the active category's own groups, whole - this is
   * what makes clicking a tab actually show that category rather than just
   * scroll to an anchor within one long continuous page (§1.2's own
   * description of Task 10.3). */
  const categoryGroups = useMemo(() => {
    const cat = CATEGORIES.find((c) => c.label === activeCategory);
    if (!cat) return groups;
    return groups.filter((g) => cat.groups.includes(g.group));
  }, [groups, activeCategory]);

  const visibleGroups = searching ? searchResults : categoryGroups;

  // A search match outside the active category corrects the tab
  // highlight to wherever the match actually lives, rather than leaving
  // the strip pointing at a tab whose own content is not what's on
  // screen right now.
  useEffect(() => {
    if (!searching || searchResults.length === 0) return;
    const activeCat = CATEGORIES.find((c) => c.label === activeCategory);
    const stillMatches = activeCat && searchResults.some((g) => activeCat.groups.includes(g.group));
    if (stillMatches) return;
    const home = CATEGORIES.find((c) => c.groups.includes(searchResults[0].group));
    if (home) setActiveCategory(home.label);
  }, [searching, searchResults, activeCategory]);

  // Marks whichever group crosses the middle band of the viewport, within
  // whatever is currently rendered (the active category's groups, or every
  // search match) - narrowed automatically since `visibleGroups` already
  // reflects the active category (§1.2 Task 10.4). The root is the
  // viewport rather than `.app-shell__content`: the scroll container fills
  // the viewport, so the two agree, and reaching for an ancestor by class
  // from here would couple this screen to the shell.
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
  }, [visibleGroups, activeCategory]);

  function jumpToCategory(label: string) {
    setActiveCategory(label);
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
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
        <div className="settings__scope-toggle" role="group" aria-label="Editing scope">
          <span className={scope === "channel" ? "settings__scope-label--on" : "settings__scope-label"}>
            Channel
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={scope === "global"}
            aria-label="Editing scope"
            className={`switch${scope === "global" ? " switch--on" : ""}`}
            onClick={() => setScope((s) => (s === "channel" ? "global" : "channel"))}
            disabled={!activeChannelId}
            title={activeChannelId ? undefined : "Activate a channel to switch to it, or to edit its defaults"}
          >
            <span className="switch__thumb" aria-hidden="true" />
          </button>
          <span className={scope === "global" ? "settings__scope-label--on" : "settings__scope-label"}>
            Global
          </span>
        </div>
        {activeChannelId ? (
          <span className="chip">Channel: {channelLabel ?? activeChannelId}</span>
        ) : (
          <span className="chip chip--bad">No active channel</span>
        )}
      </header>

      {scope === "global" && (
        // One note, shown once, rather than repeating it on every row
        // below - the Global/Channel counterpart of the old `followingGlobal`
        // banner this replaced. Nothing here is disabled for inheritance
        // reasons while this is on (§3: there is nothing above the
        // defaults document to inherit from) - this is purely informational.
        <div className="studio__banner studio__banner--muted">
          <p>
            Editing the app-wide defaults directly. Every channel-scoped setting below reads and
            saves to that shared document, not to {channelLabel ?? "the active channel"}&rsquo;s own -
            switch back to &ldquo;Channel&rdquo; to edit its values instead.
          </p>
        </div>
      )}

      <div className="settings__layout">
        <div className="settings__top">
          <input
            className="settings__search"
            type="search"
            placeholder="Search settings…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search settings by name or description"
          />
          <nav className="tabstrip" aria-label="Setting categories">
            {CATEGORIES.map((c) => (
              <button
                key={c.label}
                type="button"
                aria-current={activeCategory === c.label || undefined}
                className={`tabchip${activeCategory === c.label ? " on" : ""}`}
                onClick={() => jumpToCategory(c.label)}
              >
                {c.label}
              </button>
            ))}
          </nav>
        </div>

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
                <SettingsGroup
                  title={g.label}
                  specs={g.specs}
                  ctx={ctx}
                  active={activeGroup === g.group}
                />
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
