# Slice 10 — UI truth and startup: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the screens honest and quick to use — one filled action per screen, a navigable Settings page, a provider test that says exactly what it proved, dependencies the app can install where installing is legitimate, and a window that appears at once.

**Architecture:** Twelve independent tasks against the existing layering. Eight are frontend-only (`web/src/`). Three add backend surface: reachability storage in `avg/core/model_cache.py`, a ticked-only sweep in `avg/providers/probe.py`, a reshaped `POST /api/models/cache/build`, and a new `POST /api/health/install`. One is `avg/shell.py` alone. Nothing here changes the settings catalogue's role, the phase registry, or any file a phase writes.

**Tech Stack:** Python 3.12 + FastAPI + pydantic (backend), React 19 + TypeScript + Vite (frontend), pytest, mypy, oxlint.

**Source spec:** `docs/superpowers/specs/2026-07-30-slice-10-ui-truth-and-startup-design.md`

## Global Constraints

Every task's requirements implicitly include all of these.

- **Interpreter is `.venv-build/Scripts/python.exe`.** System `python` lacks the dependencies. Tests: `.venv-build/Scripts/python.exe -m pytest tests -q`. Types: `.venv-build/Scripts/python.exe -m mypy`.
- **Baseline measured 2026-07-30: `746 passed`.** No task may end with fewer than 746. mypy must stay clean over its 687 files.
- **No live API calls in tests, ever.** `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1` session-wide. Never call a real provider; never record a fixture in this slice.
- **Nothing below `avg/api/` may print or prompt.** Lower layers return values or emit typed events.
- **Never use a native form control.** No `<select>`, no `<input type="checkbox">`, no `<input type="radio">`. Use `web/src/Select.tsx` for choices and the `.switch` pattern for booleans. `<details>`/`<summary>` is permitted and must be styled.
- **One filled terracotta `.btn--primary` per screen.** Terracotta as identity (links, focus rings, chips) is unrestricted; the filled call-to-action is what is scarce.
- **Reuse `web/src/tokens.css` values.** Radii, spacing and colour come from existing tokens or the nearest existing control doing the same job — never a fresh guess.
- **Never add a Google Fonts `@import`.** Fonts are self-hosted in `web/public/fonts`.
- **Tests must not write to the real `%APPDATA%/AVG Studio`.** Pass `create_app`'s path overrides (`settings_path`, `channels_dir`, `secrets_path`, `errors_path`, `model_cache_path`, `model_prefs_path`, `thumb_cache_dir`).
- **`avg/media/vendor/` is protected.** Never edit.
- **Frontend has no unit tests by convention.** A frontend task is verified by looking: `preview_start` the dev URL, then `computer{action:"screenshot"}`. Computed styles are not a substitute.
- **Dev loop for looking:** `cd web && npm run build`, then `.venv-build/Scripts/python.exe tools/_dev_serve.py`, then `http://127.0.0.1:8756/?token=devtoken`. Kill anything on 8756 first.
- **Commit once per task**, on `main`, no push.

## Two spec gaps resolved in this plan

Both were found while reading the code against the spec. Each is implemented as described here.

1. **Text-model discovery.** Spec §3.3 makes the text probe test *ticked models only*. But today the only way a text model ever reaches the Live List to be ticked is the full probe (`_known_text` in `avg/api/routes_settings.py:341` reads the probe cache and nothing else). Ticked-only probing alone would make the text tab permanently empty on a fresh install. **Resolution (Task 7):** `POST /api/live-list/refresh` also lists advertised *text* models per provider and stores them as `__text__<provider>` catalogue entries. Listing text models is a plain catalogue GET and costs nothing — the same reason images and voices are listed there. Discovery becomes "Listed", proving becomes "Verified".
2. **Install output.** Spec §3.4 says the install "streams the output into the card". Streaming would need a new event type and a WebSocket path for a one-shot action. **Resolution (Task 9):** the route runs pip on a worker thread and returns the combined output in its response; the card shows the output when it lands. The user sees exactly what pip said — just at the end rather than line by line.

---

## Task 1: The flash and the status pill

Two pieces of global chrome, both in `App.tsx`/`StatusBar.tsx`/`tokens.css`. Grouped because they are the same screen furniture and are verified in the same look.

**Files:**
- Modify: `web/src/App.tsx:328`
- Modify: `web/src/StatusBar.tsx:45-55`
- Modify: `web/src/tokens.css:424-426` (the `:has` selector), `:435-444` (`.welcome`), `:2790-2792` (`.screen-enter`), `:2804-2818` (reduced motion), `:2966-3006` (the pill's dot)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the CSS class `.app-shell__screen` (the new animated wrapper) and `.statuspill__ring`. No exported TypeScript symbols change.

**Why:** `.screen-enter` animates `transform: translateY(6px)` and sits on `.app-shell__content`, which is itself the scroll container (`overflow-y: auto`, `tokens.css:411`). A transformed box contributes its shifted position to its ancestor's scrollable overflow, so for 0.18s the document is 6px taller than the viewport and a second scrollbar appears beside the real one. Measured live on 2026-07-30: document `scrollHeight` 726 against `clientHeight` 720.

- [ ] **Step 1: Move the animation off the scroll container**

In `web/src/App.tsx`, replace lines 324-330:

```tsx
      <div className="app-shell__main">
        {/* `key` on the screen name, not the content: remounting on a route
            change is what lets the enter transition actually run each time
            rather than only on first paint.

            The animated element is a wrapper INSIDE the scroll container,
            never the container itself. `.screen-enter` animates a
            transform, and a transformed box contributes its shifted
            position to its ancestor's scrollable overflow - with the class
            on `.app-shell__content` the document was 6px taller than the
            viewport for the animation's 0.18s and grew a second scrollbar
            next to the real one. */}
        <div className="app-shell__content">
          <div className="app-shell__screen screen-enter" key={screen.name}>
            {content}
          </div>
        </div>
        <Transport events={events} projects={projects ?? []} onCancel={cancelRender} onOpen={openProject} />
        <StatusBar connected={events.connected} />
      </div>
```

- [ ] **Step 2: Give the wrapper the height the screens expect**

`.home` and `.settings` use `min-height: 100%` and `.welcome` uses `height: 100%`; all three previously resolved against `.app-shell__content`. In `web/src/tokens.css`, insert after the `.app-shell__content` rule (after line 420):

```css
/* The screen's animated wrapper (App.tsx). It exists so `.screen-enter`'s
   transform is not on the scroll container - see that class.

   `height`, NOT `min-height`, and this is the whole subtlety: every screen
   root (`.home`, `.settings`, `.studio`, `.channels`, `.create`) sizes
   itself with `min-height: 100%`, and a percentage resolves against its
   parent's HEIGHT. A wrapper with only `min-height` has an auto height, so
   all five collapse to their content. `.app-shell__content` is a flex item
   with `flex: 1` in a fixed-height column, so its height is definite and
   `100%` here is real.

   A screen taller than the scrollport simply overflows this box and
   `.app-shell__content` scrolls it, which is what it is for. Deliberately
   NOT a flex column: flex items shrink by default, so a tall screen would
   be squashed to fit instead of overflowing. */
.app-shell__screen {
  height: 100%;
}
```

Leave the `.app-shell__content:has(> .welcome)` rule and `.welcome`'s own `height: 100%` exactly as they are. `App.tsx` returns `<Welcome />` early, in place of the whole shell (`App.tsx:223-230`), so Welcome never renders inside this wrapper and nothing about it changes here.

- [ ] **Step 3: Look at it — no second scrollbar**

Run:

```bash
cd web && npm run build
```

Start the dev server and open `http://127.0.0.1:8756/?token=devtoken`. Change screens five times (Home → Settings → Channels → App health → Home). Expected: exactly one scrollbar at every moment, and Welcome (if `onboarded: false`) still fills the window.

- [ ] **Step 4: The pill's ring**

In `web/src/StatusBar.tsx`, replace lines 40-55 with:

```tsx
  const lead = leadActivity(activities);

  const kind = !connected ? "error" : lead !== null ? "busy" : "ready";
  const label = kind === "error" ? "Reconnecting" : lead !== null ? lead.label : "Ready!";
  // The ring is for a JOB - a render or a pipeline, registered `high` from
  // the event socket (see activity.ts). An ordinary request keeps the dot:
  // a spinner for a 40ms GET would make the pill cry wolf.
  const running = lead !== null && lead.priority === "high";

  return (
    <div className={`statuspill statuspill--${kind}`} role="status" aria-live="polite">
      {running ? (
        <span className="statuspill__ring" aria-hidden="true" />
      ) : (
        <span className="statuspill__dot" aria-hidden="true" />
      )}
      {/* Keyed on the mark so the label replays its pop each time the pill
          changes between resting and working - "Ready!" slides in as the
          ring spins up and pops back out when the job ends. */}
      <span className="statuspill__label" key={running ? "running" : "resting"}>
        {label}
        {lead !== null && "…"}
      </span>
      {lead !== null && <span className="statuspill__clock mono">{elapsedClock(lead.startedAt, now)}</span>}
      {activities.length > 1 && <span className="statuspill__more">+{activities.length - 1}</span>}
    </div>
  );
}
```

- [ ] **Step 5: The ring's CSS**

In `web/src/tokens.css`, insert after the `.statuspill__dot` rules (after line 2977, before `@keyframes avg-pulse`):

```css
/* An indeterminate ring while a job runs: one arc rotating, the shape a
   user already reads as "working". Same 7px optical weight as the dot it
   replaces, grown to 13px so the arc is legible. */
.statuspill__ring {
  width: 13px;
  height: 13px;
  flex-shrink: 0;
  border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--pill) 26%, transparent);
  border-top-color: var(--pill);
  animation: avg-spin 0.9s linear infinite;
}

@keyframes avg-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes pill-pop {
  from {
    opacity: 0;
    transform: translateX(-4px) scale(0.96);
  }
}
```

Add the label animation to the existing `.statuspill__label` rule (line 2991):

```css
.statuspill__label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  animation: pill-pop 0.28s ease-out;
}
```

Extend the reduced-motion block at line 3002 — a static ring, matching how `tokens.css` already disables `rise-in`:

```css
@media (prefers-reduced-motion: reduce) {
  .statuspill__dot,
  .statuspill__ring,
  .statuspill__label {
    animation: none;
  }
}
```

- [ ] **Step 6: Look at it — the ring spins**

Rebuild (`cd web && npm run build`), reload, and press render on any project. Expected: the pill's dot becomes a rotating ring, the label reads `Working…` with a live clock, and it pops back to `Ready!` when the job ends. Turn on reduced motion in Windows (Settings → Accessibility → Visual effects → Animation effects off), reload: the ring is present and static.

- [ ] **Step 7: Lint and commit**

Run:

```bash
cd web && npm run lint
```

Expected: clean.

```bash
git add web/src/App.tsx web/src/StatusBar.tsx web/src/tokens.css
git commit -m "fix: move the screen transition off the scroll container, ring the status pill"
```

---

## Task 2: Home — one filled action, honest cards

**Files:**
- Modify: `web/src/Home.tsx:10-29` (props), `:106-108` (Hero's action), `:190-220` (header)
- Modify: `web/src/ProjectCard.tsx:1-4` (imports), `:89-152` (the whole render)
- Modify: `web/src/App.tsx` (pass `onCreate` to `Home`)
- Modify: `web/src/tokens.css:846-865` (`.project-card__thumb`), `:874-883` (`.project-card__badges`)

**Interfaces:**
- Consumes: `.app-shell__screen` from Task 1 (Home renders inside it).
- Produces: `HomeProps` gains `onCreate: () => void`. `ProjectCardProps` is unchanged.

**Why:** Counted in the DOM on 2026-07-30, Home carried thirteen `.btn--primary` elements — one per card plus the hero's. `CLAUDE.md` records four previous tasks that had to fix over-use of the filled action. And a 16:9 project is currently cropped into the same `aspect-ratio: 9/16` box as a 9:16 one, so the card misrepresents the video it stands for.

- [ ] **Step 1: Every card action becomes plain**

In `web/src/ProjectCard.tsx`, replace lines 124-150 (the `project-card__actions` block):

```tsx
      {/* No `.btn--primary` anywhere on a card. Home spends its one filled
          terracotta action on "New video"; thirteen filled buttons in a
          grid taught the user nothing about which one to press. The card's
          state is already carried by its chip - sage for done, amber for
          running, red for failed - which is colour the state earned and
          which no longer competes with a button for the same meaning. */}
      <div className="project-card__actions" onClick={(e) => e.stopPropagation()}>
        {state === "done" && (
          <>
            <button type="button" className="btn" onClick={open}>
              Play
            </button>
            <button type="button" className="btn" onClick={render}>
              Run again
            </button>
          </>
        )}
        {state === "failed" && (
          <>
            <button type="button" className="btn" onClick={render}>
              Resume
            </button>
            <button type="button" className="btn" onClick={open}>
              Open
            </button>
          </>
        )}
        {(state === "running" || state === "incomplete") && (
          <button type="button" className="btn" onClick={open}>
            {state === "running" ? "Open studio" : "Open"}
          </button>
        )}
      </div>
```

- [ ] **Step 2: The thumbnail takes the project's own aspect ratio, and says so when empty**

In `web/src/ProjectCard.tsx`, replace lines 89-110 (the thumb and badges):

```tsx
  // The project's own ratio, as a CSS `aspect-ratio` value. `aspect_ratio`
  // arrives from `GET /api/projects` as "9:16" or "16:9"; anything else
  // falls back to portrait rather than producing an invalid declaration.
  const aspect = /^\d+:\d+$/.test(project.aspect_ratio)
    ? project.aspect_ratio.replace(":", " / ")
    : "9 / 16";

  return (
    <article className="project-card" onClick={open}>
      <div
        className={`project-card__thumb${thumbBroken ? " project-card__thumb--empty" : ""}`}
        style={{ aspectRatio: aspect }}
      >
        {thumbBroken ? (
          // `GET .../thumbnail` 404s honestly for a project that never
          // generated an image. A grey frame icon reads as a broken image;
          // a sentence says which of the two it actually is.
          <p className="project-card__noimg">No images yet</p>
        ) : (
          <img
            className="washed"
            src={thumbnailUrl(project.id)}
            alt=""
            loading="lazy"
            onError={() => setThumbBroken(true)}
          />
        )}
      </div>

      <div className="project-card__scrim" />

      {/* The status chip owns the left, the aspect chip the right. A long
          "Failed · render" and the aspect chip used to share a corner. */}
      <div className="project-card__badges">
        <span className={statusPillClass}>{statusLabel}</span>
        <span className="pill pill--ar">{project.aspect_ratio}</span>
      </div>
```

Remove the now-unused `IconFrame` import — line 4 becomes nothing; delete it. Verify no other use of `IconFrame` remains in this file.

- [ ] **Step 3: The thumb and badge CSS**

In `web/src/tokens.css`, replace the `.project-card__thumb` rule (lines 846-852):

```css
.project-card__thumb {
  position: relative;
  /* The default only; `ProjectCard.tsx` overrides it inline with the
     project's own ratio. Rows are deliberately no longer a uniform height:
     cropping a widescreen frame into a portrait card misrepresents the
     video the card stands for. */
  aspect-ratio: 9 / 16;
  background: var(--surface-2);
  overflow: hidden;
}
```

(The `max-height: 246px` line is deleted — it is what forced every ratio into the same box.)

Add after the `.project-card__thumb--empty` rule (after line 865):

```css
.project-card__noimg {
  margin: 0;
  padding: 0 18px;
  text-align: center;
  font-size: 13px;
  color: var(--dim);
}
```

Replace the `.project-card__badges` rule (lines 874-883):

```css
.project-card__badges {
  position: absolute;
  top: 11px;
  left: 11px;
  right: 11px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 6px;
  z-index: 2;
}
```

- [ ] **Step 4: Home gets the one filled action**

In `web/src/Home.tsx`, add to `HomeProps` (after line 26, `onGoSettings`):

```tsx
  /** Home's single filled terracotta action. Every card action is plain
   * `.btn`; this is the one thing on the screen the eye should land on. */
  onCreate: () => void;
```

Add `onCreate` to the destructured parameter list (line 141 area, beside `onGoSettings`).

Replace the header block, lines 195-220:

```tsx
      <header className="home__header">
        <div style={{ minWidth: 0 }}>
          <div className="home__kicker">Library</div>
          <h1 className="home__title">Your videos</h1>
        </div>
        <span className="home__spacer" />
        {(archivedCount > 0 || showArchived) && (
          <button
            type="button"
            className={`btn${showArchived ? " btn--on" : ""}`}
            onClick={() => {
              setShowArchived((on) => !on);
              setSelected(new Set());
            }}
          >
            {showArchived ? "Back to library" : `Archived (${archivedCount})`}
          </button>
        )}
        <input
          className="home__search"
          placeholder="Search topics"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search projects by title"
        />
        <button type="button" className="btn btn--primary" onClick={onCreate}>
          New video
        </button>
      </header>
```

Change the Hero's "Open studio" button (line 106) from `className="btn btn--primary"` to `className="btn"`.

- [ ] **Step 5: Wire it in App.tsx**

In `web/src/App.tsx`, find where `<Home ... />` is constructed and add `onCreate={() => setScreen({ name: "create" })}` alongside the existing `onGoSettings` / `onGoHealth` / `onGoChannels` props.

- [ ] **Step 6: Look at it — count the filled buttons**

Rebuild and reload Home at 1280×820 and at 900×700, in both themes, on a library holding both a 9:16 and a 16:9 project. Expected:

- Exactly one filled terracotta button on the screen (`New video`), including while a render's hero is showing.
- The 16:9 project's still is widescreen, not cropped into portrait.
- A project with no images reads "No images yet", not a grey icon.
- The status chip is top-left, the aspect chip top-right, never overlapping.

Portrait cards are now taller than before (the `max-height` cap is gone). If they read as too tall at 900×700, raise `--cardmin` rather than restoring the cap — the cap is what produced the crop.

- [ ] **Step 7: Lint and commit**

```bash
cd web && npm run lint
```

```bash
git add web/src/Home.tsx web/src/ProjectCard.tsx web/src/App.tsx web/src/tokens.css
git commit -m "feat: one filled action on Home, aspect-aware cards, an honest empty thumbnail"
```

---

## Task 3: Settings — a nav that marks where you are

**Files:**
- Modify: `web/src/Settings.tsx:91-92` (state), `:225-241` (`visibleGroups`), `:276-341` (nav and content)
- Modify: `web/src/tokens.css:1608-1668` (the nav), plus a `scroll-margin-top` on `.settings-group`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing exported. The nav's entries stay derived from the distinct `group` values in `GET /api/settings/schema`; `GROUP_LABELS` stays display polish over whatever the schema reports.

**Why:** Measured 2026-07-30: `scrollHeight` 6584 in a 720px viewport — nine viewports, ten groups, 56 settings. The nav exists but *filters* to one group at a time, which the spec explicitly rejected, and it does not narrow while a search is active.

- [ ] **Step 1: Replace the category filter with a scroll-spy**

In `web/src/Settings.tsx`, add `useRef` to the React import on line 1:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
```

Replace lines 91-92:

```tsx
  const [search, setSearch] = useState("");
  /** Which group is currently in view. Derived from an IntersectionObserver,
   * never from a click: clicking scrolls, and the observer then reports
   * where the scroll landed - so the mark can never disagree with the page. */
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
```

- [ ] **Step 2: Simplify `visibleGroups` — search is now the only filter**

Replace lines 222-241:

```tsx
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
```

- [ ] **Step 3: Observe which group is on screen**

Insert after the `visibleGroups` memo:

```tsx
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
  }
```

- [ ] **Step 4: Render the nav from `visibleGroups` and anchor the sections**

Replace lines 276-341 (the `settings__layout` block):

```tsx
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
                {/* Components sits with Paths rather than getting a nav
                    entry of its own: both answer "where are this app's
                    tools and do they work", and Paths owns the Python
                    executable setting. */}
                {g.group === "paths" && !searching && <HealthPanel />}
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
```

Note the `All settings` nav item is gone: every group now renders at once, so it had nothing left to mean.

- [ ] **Step 5: Nav and anchor CSS**

In `web/src/tokens.css`, delete the now-dead rule at lines 1645-1647:

```css
.settings__nav-list[aria-disabled="true"] {
  opacity: 0.5;
}
```

Add after `.settings__nav-item--on` (after line 1668):

```css
/* A smooth jump must not land the group's heading under the floating
   status pill, whose band `.app-shell__content` already reserves. */
.settings-group {
  scroll-margin-top: 12px;
}
```

Confirm `.settings__nav`'s existing `position: sticky; top: 0` (lines 1614-1615) is untouched — it is what keeps the nav beside the scrolling content.

- [ ] **Step 6: Look at it — the mark follows the scroll**

Rebuild, reload, open Settings. Expected:

- All ten groups render in one scroll; the nav stays put while the right side scrolls.
- Scrolling moves the marked nav item to whichever group is in view.
- Clicking a nav item scrolls to that group and marks it.
- Typing `voice` narrows both the page and the nav; clearing it restores all ten.
- No native control anywhere.

Run:

```bash
grep -rn "<select\|type=\"checkbox\"" web/src/
```

Expected: only `web/src/Home.tsx`'s `.select-dot` checkbox, which is the sanctioned `position: absolute; opacity: 0` pattern.

- [ ] **Step 7: Lint and commit**

```bash
cd web && npm run lint
```

```bash
git add web/src/Settings.tsx web/src/tokens.css
git commit -m "feat: settings nav marks the group in view and narrows with the search"
```

---

## Task 4: Live List — a search field

**Files:**
- Modify: `web/src/LiveList.tsx:56-60` (state), `:139-146` (`visible`), `:196-215` (controls)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing exported.

**Why:** Edge advertises 322 voices. Building a shortlist out of them is the entire point of the screen, and today it is done by scrolling.

- [ ] **Step 1: The query state**

In `web/src/LiveList.tsx`, add after line 55 (`const [adding, setAdding] = useState("")`):

```tsx
  // Edge alone advertises 322 voices. A shortlist is what this screen is
  // for, and scrolling to build one is not a way to build one.
  const [query, setQuery] = useState("");
```

- [ ] **Step 2: Filter the rows**

Replace lines 139-146:

```tsx
  const needle = query.trim().toLowerCase();
  const visible = rows.filter((row) => {
    if (onlySelected && !row.selected) return false;
    if (needle) {
      const info = meta[row.id];
      // The id carries the provider ("edge/en-GB-RyanNeural"), so a query
      // for a provider, a model, a note, a language or a gender all land
      // in this one haystack.
      const haystack = [row.id, row.note, info?.locale ?? "", info?.gender ?? ""]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    if (kind !== "voice") return true;
    const info = meta[row.id];
    if (locale !== "all" && info?.locale !== locale) return false;
    if (gender !== "all" && (info?.gender ?? "").toLowerCase() !== gender) return false;
    return true;
  });
```

- [ ] **Step 3: The field itself**

In the `livelist__controls` block, insert as the first child (before the existing `livelist__filter` switch at line 202):

```tsx
        <input
          className="settings__search"
          type="search"
          style={{ maxWidth: 260 }}
          placeholder="Search this list…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search entries by id, provider, note, language or gender"
        />
```

`.settings__search` is reused rather than re-styled: it is the same gesture on the same kind of list, and a second search field with its own radius would be the quiet version of an unthemed control.

- [ ] **Step 4: The empty state must not lie**

Replace the empty-state paragraph (lines 263-271):

```tsx
      {data !== null && visible.length === 0 && (
        <p className="home__empty">
          {rows.length === 0
            ? kind === "text"
              ? "No text models listed yet — press Refresh catalogues."
              : "Nothing listed yet — press Refresh catalogues."
            : "Nothing matches these filters."}
        </p>
      )}
```

- [ ] **Step 5: Look at it**

Rebuild, reload, open App health → Open Live List → Voices. Expected: typing `en-GB` narrows 322 rows to a handful; clearing restores them; the Ticked-only switch and the Language/Gender selects still work alongside it.

- [ ] **Step 6: Lint and commit**

```bash
cd web && npm run lint
```

```bash
git add web/src/LiveList.tsx
git commit -m "feat: search the Live List"
```

---

## Task 5: The Script panel — a scene table, narration behind a disclosure

**Files:**
- Modify: `web/src/artifacts/bodies.tsx:111-145` (`ScriptBody`)
- Modify: `web/src/tokens.css` (append an `.artifact-disclosure` block near `.artifact-table`, after line 3400)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing exported. `ScriptBody`'s signature is unchanged.

**Why:** The scene texts already contain every word of the narration. Printing the whole script twice at full height is what makes this one panel feel unlike its neighbours — Director I and II both use `.artifact-table`.

- [ ] **Step 1: Rewrite `ScriptBody`**

In `web/src/artifacts/bodies.tsx`, replace lines 111-145:

```tsx
export function ScriptBody({ projectId, takeId, files }: BodyProps) {
  const narration = useArtifactText(projectId, takeId, "narration", "narration" in files);
  const scenes = useArtifactJson<Scene[]>(projectId, takeId, "scenes", "scenes" in files);

  if (!("narration" in files) && !("scenes" in files)) return <Missing what="script" />;

  return (
    <div className="artifact-stack">
      {Array.isArray(scenes) && scenes.length > 0 && (
        <>
          <p className="lbl">{scenes.length} scenes</p>
          {/* The same `.artifact-table` Director I and II use. The scene
              texts already contain every word of the narration, so this is
              the script - the prose below is the same words again, which is
              why it is folded away rather than printed at full height. */}
          <table className="artifact-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Text</th>
                <th>Label</th>
                <th>Image prompt</th>
              </tr>
            </thead>
            <tbody>
              {scenes.map((scene, i) => (
                <tr key={scene.index ?? i}>
                  <td className="mono">{scene.index ?? i + 1}</td>
                  <td>{scene.text}</td>
                  <td>{scene.label ? <span className="chip">{scene.label}</span> : "—"}</td>
                  <td>{scene.image_prompt || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {narration && (
        <details className="artifact-disclosure">
          <summary>Narration</summary>
          {/* Prose, because this is what the voice phase reads aloud. */}
          <pre className="artifact-prose">{narration}</pre>
        </details>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Style the disclosure**

In `web/src/tokens.css`, append after the `.artifact-table td` rule (after line 3400 or wherever that block ends):

```css
/* A `<details>`, styled - never left at browser defaults. The marker is
   the OS triangle only in shape; everything around it is the app's. */
.artifact-disclosure {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
}

.artifact-disclosure > summary {
  cursor: pointer;
  padding: 11px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
  list-style: none;
}

.artifact-disclosure > summary::-webkit-details-marker {
  display: none;
}

.artifact-disclosure > summary::before {
  content: "▸";
  display: inline-block;
  width: 14px;
  color: var(--accent);
  transition: transform 0.15s ease-out;
}

.artifact-disclosure[open] > summary::before {
  transform: rotate(90deg);
}

.artifact-disclosure > summary:hover {
  color: var(--text);
}

.artifact-disclosure > summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.artifact-disclosure > .artifact-prose {
  margin: 0;
  padding: 0 14px 14px;
}

@media (prefers-reduced-motion: reduce) {
  .artifact-disclosure > summary::before {
    transition: none;
  }
}
```

- [ ] **Step 3: Look at it**

Rebuild, reload, open a project in Studio with a completed Script phase, and open the Script artifact panel. Expected: a scene table matching Director II's, and a collapsed `Narration` disclosure below it that opens to the prose. The panel is no longer taller than its neighbours.

- [ ] **Step 4: Lint and commit**

```bash
cd web && npm run lint
```

```bash
git add web/src/artifacts/bodies.tsx web/src/tokens.css
git commit -m "feat: the Script panel becomes a scene table with narration behind a disclosure"
```

---

## Task 6: Reachability storage and a ticked-only probe

Backend, no HTTP. Pure storage plus two provider-layer functions.

**Files:**
- Modify: `avg/core/model_cache.py:83-129` (add `verdicts`, `reach`, extend `save`)
- Modify: `avg/providers/probe.py` (add `probe_models`, `check_reachable`)
- Test: `tests/core/test_model_cache.py` (existing — add cases)
- Test: `tests/providers/test_probe.py` (existing — add cases)

**Interfaces:**
- Consumes: `avg.providers.catalogues.list_image_models(provider, *, transport=None) -> list[str]`, `avg.providers.catalogues.list_voices(provider, *, transport=None) -> list[dict[str, str]]`, `avg.providers.catalogues.image_providers() -> list[str]`, `avg.config.secrets.get_key(provider) -> str | None`.
- Produces, and Task 7 depends on exactly these signatures:
  - `ModelCache.verdicts(provider: str) -> dict[str, bool]`
  - `ModelCache.reach() -> dict[str, bool]` — keys are `f"{kind}/{provider}"`
  - `ModelCache.save(results: dict[str, dict[str, bool]], meta: dict[str, dict[str, str]] | None = None, reachable: dict[str, bool] | None = None) -> None`
  - `avg.providers.probe.probe_models(selection: dict[str, list[str]], *, transport: Transport | None = None) -> dict[str, dict[str, bool]]`
  - `avg.providers.probe.check_reachable(kind: str, provider: str, *, transport: Transport | None = None) -> bool | None`

- [ ] **Step 1: Write the failing storage tests**

Append to `tests/core/test_model_cache.py`:

```python
def test_verdicts_reports_failures_not_only_successes(tmp_path):
    """`working()` answers "what can I pick"; `verdicts()` answers "what did
    the sweep prove", and a proven failure is a real answer the UI has to be
    able to state as `failed` rather than as `untested`."""
    cache = ModelCache(tmp_path / "working_models.json")
    cache.save({"groq": {"good": True, "dead": False}})

    assert cache.working("groq") == ["good"]
    assert cache.verdicts("groq") == {"good": True, "dead": False}
    assert cache.verdicts("never-swept") == {}


def test_reachability_is_stored_per_provider_and_survives_a_text_only_save(tmp_path):
    """Reachability is a fact about a PROVIDER, not a model, so it lives
    beside the per-model verdicts rather than pretending to be one. Passing
    `reachable=None` keeps it, exactly as `meta=None` keeps the meta - a
    text sweep knows nothing about image providers and must not wipe them."""
    cache = ModelCache(tmp_path / "working_models.json")
    cache.save({}, reachable={"image/pollinations": True, "voice/edge": False})
    assert cache.reach() == {"image/pollinations": True, "voice/edge": False}

    cache.save({"groq": {"good": True}})
    assert cache.reach() == {"image/pollinations": True, "voice/edge": False}

    cache.save({}, reachable={})
    assert cache.reach() == {}


def test_a_provider_with_no_stored_reachability_is_absent_not_false(tmp_path):
    """Untested is the ABSENCE of a verdict, never a stored one: a provider
    nobody has swept has not been proved unreachable."""
    cache = ModelCache(tmp_path / "working_models.json")
    cache.save({}, reachable={"image/pollinations": True})
    assert "image/stablehorde" not in cache.reach()
```

If `tests/core/test_model_cache.py` does not import `ModelCache`, add `from avg.core.model_cache import ModelCache` at the top.

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_model_cache.py -q
```

Expected: FAIL — `AttributeError: 'ModelCache' object has no attribute 'verdicts'`.

- [ ] **Step 3: Implement the storage**

In `avg/core/model_cache.py`, insert after `working()` (after line 66):

```python
    def verdicts(self, provider: str) -> dict[str, bool]:
        """Every verdict this provider's last sweep recorded, `False` included.

        `working()` answers "what can I pick today" and therefore drops the
        failures. This answers "what did the sweep prove", which is what a
        screen reporting `working` / `failed` / `untested` needs: an absent
        entry is untested, and a `False` entry is a real, earned failure.
        """
        models = self._load().get("models", {})
        if not isinstance(models, dict):
            return {}
        entry = models.get(provider)
        if not isinstance(entry, dict):
            return {}
        return {str(model): bool(ok) for model, ok in entry.items()}
```

Insert after `meta()` (after line 99):

```python
    def reach(self) -> dict[str, bool]:
        """Whether each provider answered its own catalogue, by `kind/provider`.

        Reachability is a fact about a PROVIDER, not about a model - an
        image provider that answers its catalogue has proved the key works
        and the endpoint is up, and has proved nothing at all about any one
        model. Storing it per model would be a claim the check never made.

        A provider absent from this map is UNTESTED, which is a different
        statement from `False`. Nothing here ever writes an absent provider
        as `False`.
        """
        raw = self._load().get("reachable")
        if not isinstance(raw, dict):
            return {}
        return {str(key): bool(value) for key, value in raw.items()}
```

Replace `save()` (lines 101-129):

```python
    def save(
        self,
        results: dict[str, dict[str, bool]],
        meta: dict[str, dict[str, str]] | None = None,
        reachable: dict[str, bool] | None = None,
    ) -> None:
        """Replace the document with one sweep's results.

        A whole-document replace, not a merge: a model that stopped
        working has to be able to disappear, and a merge would keep a
        stale `true` alive forever.

        `meta` and `reachable` are the exceptions, and only in one
        direction: passing `None` keeps whatever was stored, so a text
        probe - which knows nothing about voices or image endpoints - does
        not wipe the language and gender a catalogue refresh put there, nor
        the reachability an earlier provider test proved. Passing an
        explicit `{}` still clears them.
        """
        kept_meta = self.meta() if meta is None else meta
        kept_reach = self.reach() if reachable is None else reachable
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "models": {p: dict(sorted(m.items())) for p, m in results.items()},
                    "meta": dict(sorted(kept_meta.items())),
                    "reachable": dict(sorted(kept_reach.items())),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
```

- [ ] **Step 4: Run the storage tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_model_cache.py -q
```

Expected: PASS.

- [ ] **Step 5: Write the failing probe tests**

Append to `tests/providers/test_probe.py`:

```python
def test_probe_models_touches_only_the_models_it_is_given(monkeypatch):
    """A sweep that tests everything a provider advertises is what the Live
    List exists to replace. Only ticked models are probed, and `list_models`
    is never called at all - the caller already knows which ones."""
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: "k")
    monkeypatch.setattr(
        "avg.providers.probe.list_models",
        lambda *a, **k: pytest.fail("probe_models must not list a catalogue"),
    )

    asked: list[tuple[str, str]] = []

    def fake_answers(provider, model, _transport):
        asked.append((provider, model))
        return model != "dead"

    monkeypatch.setattr("avg.providers.probe._model_answers", fake_answers)

    result = probe.probe_models({"groq": ["good", "dead"]})

    assert asked == [("groq", "good"), ("groq", "dead")]
    assert result == {"groq": {"good": True, "dead": False}}


def test_probe_models_never_probes_an_unkeyed_provider(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: None)
    monkeypatch.setattr(
        "avg.providers.probe._model_answers",
        lambda *a, **k: pytest.fail("no key means no call at all"),
    )
    assert probe.probe_models({"groq": ["good"]}) == {"groq": {}}


def test_reachability_is_a_catalogue_fetch_never_a_generation(monkeypatch):
    """An image probe is a paid image and an ElevenLabs probe is paid
    characters. Reachability is the provider's own free catalogue GET."""
    monkeypatch.setattr(
        "avg.providers.catalogues.list_image_models", lambda p, **k: ["flux"] if p == "pollinations" else []
    )
    monkeypatch.setattr(
        "avg.providers.probe._model_answers",
        lambda *a, **k: pytest.fail("reachability must never generate"),
    )

    assert probe.check_reachable("image", "pollinations") is True
    assert probe.check_reachable("image", "stablehorde") is False


def test_a_provider_with_no_catalogue_endpoint_is_untested_not_unreachable(monkeypatch):
    """OpenAI and Gemini publish no image catalogue. Reporting them
    `unreachable` would be a verdict nothing earned."""
    assert probe.check_reachable("image", "openai") is None
    assert probe.check_reachable("image", "gemini") is None


def test_an_unkeyed_elevenlabs_is_untested_not_unreachable(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: None)
    assert probe.check_reachable("voice", "elevenlabs") is None


def test_voice_reachability_lists_and_does_not_synthesise(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: "k")
    monkeypatch.setattr(
        "avg.providers.catalogues.list_voices",
        lambda p, **k: [{"voice_id": "v", "name": "V", "locale": "en-GB", "gender": "Female"}],
    )
    assert probe.check_reachable("voice", "edge") is True
```

Ensure the file's imports include `import pytest` and `from avg.providers import probe`.

- [ ] **Step 6: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/providers/test_probe.py -q
```

Expected: FAIL — `AttributeError: module 'avg.providers.probe' has no attribute 'probe_models'`.

- [ ] **Step 7: Implement the probe functions**

In `avg/providers/probe.py`, add to the imports at the top:

```python
from avg.providers import catalogues
```

Append after `probe_all` (after line 153):

```python
def probe_models(
    selection: dict[str, list[str]], *, transport: Transport | None = None
) -> dict[str, dict[str, bool]]:
    """Probe exactly the models named, provider by provider. `{provider: {model: worked}}`.

    The difference from `probe_all` is the whole point of the Live List: a
    sweep that tests everything a provider advertises spends a call per
    listed model on models the user will never choose. Here the caller
    passes the shortlist and nothing else is touched - no catalogue is even
    fetched, because the caller already knows the ids.

    Never raises, for the same reason `probe_all` does not: this runs as a
    sweep and one dead provider must not cost the rest. An unkeyed or
    unknown provider comes back as an empty dict, which reads as "nothing
    proved" rather than "everything broken".
    """
    owned = transport is None
    transport = transport if transport is not None else _probe_transport()
    try:
        results: dict[str, dict[str, bool]] = {}
        for provider, models in selection.items():
            if provider not in _BACKENDS or not get_key(provider):
                # No key means no call at all - never a probe that fails for
                # the wrong reason and marks every model broken.
                results[provider] = {}
                continue
            results[provider] = {
                model: _model_answers(provider, model, transport) for model in models
            }
        return results
    finally:
        if owned:
            transport.close()


def check_reachable(
    kind: str, provider: str, *, transport: Transport | None = None
) -> bool | None:
    """Did this provider answer its own catalogue? `None` means untested.

    Three values, and the third is the one that matters:

      - `True`  - the provider answered with this key. THIS MODEL is still
                  unproven; reachability is a fact about a provider.
      - `False` - it has a catalogue endpoint and that endpoint did not answer.
      - `None`  - there was nothing to ask. OpenAI and Gemini publish no
                  image catalogue at all, and an unkeyed ElevenLabs has
                  nothing to say - reporting either `unreachable` would be a
                  verdict nothing earned.

    **Nothing here costs money.** Probing an image model means paying for an
    image and probing an ElevenLabs voice costs characters, which is exactly
    why Slice 8 made images and voices listed rather than probed. A
    catalogue GET is free on every provider that has one.
    """
    if kind == "image":
        if provider not in catalogues.image_providers():
            return None
        return bool(catalogues.list_image_models(provider, transport=transport))
    if kind == "voice":
        if provider == "elevenlabs" and not get_key("elevenlabs"):
            return None
        return bool(catalogues.list_voices(provider, transport=transport))
    return None
```

- [ ] **Step 8: Run the probe tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/providers/test_probe.py -q
```

Expected: PASS.

- [ ] **Step 9: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 755 passed (746 baseline + 9 new).

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Success: no issues found`.

- [ ] **Step 10: Commit**

```bash
git add avg/core/model_cache.py avg/providers/probe.py tests/core/test_model_cache.py tests/providers/test_probe.py
git commit -m "feat: store provider reachability and probe only the ticked models"
```

---

## Task 7: Test Providers — the route, and text discovery

**Files:**
- Modify: `avg/api/routes_settings.py:60` (import), `:208-264` (both model-cache routes), `:341-349` (`_known_text`), `:400-434` (`_list_everything`)
- Modify: `tests/api/test_model_cache_routes.py` (the three tests that monkeypatch `probe_all`)
- Test: `tests/api/test_model_cache_routes.py` (add cases)

**Interfaces:**
- Consumes from Task 6: `ModelCache.verdicts`, `ModelCache.reach`, `ModelCache.save(..., reachable=...)`, `probe.probe_models`, `probe.check_reachable`.
- Produces, and Task 8 depends on exactly this response shape for `GET /api/models/cache`:

```json
{
  "updated": "<iso8601>|null",
  "providers": {"groq": ["good"]},
  "text": {"groq": {"good": true, "dead": false}},
  "reachable": {"image/pollinations": true, "voice/edge": true},
  "building": false
}
```

**Why:** `POST /api/models/cache/build` today probes every model of every keyed provider. Spec §3.3 makes it Test Providers: a real completion for ticked text models, a free catalogue fetch for image and voice providers that have at least one ticked entry, and nothing that costs money ever probed. Text discovery moves to `POST /api/live-list/refresh` — see "Two spec gaps resolved in this plan".

- [ ] **Step 1: Write the failing route tests**

Append to `tests/api/test_model_cache_routes.py`:

```python
def _tick(client, kind: str, entry_id: str) -> None:
    assert client.patch(
        f"/api/live-list/{kind}/{entry_id}", json={"selected": True}, headers=AUTH
    ).status_code == 200


def test_a_sweep_probes_the_ticked_text_models_and_nothing_else(client, monkeypatch):
    """A sweep that tests everything a provider advertises is what the Live
    List exists to replace."""
    asked: dict[str, list[str]] = {}

    def fake_probe_models(selection, **_k):
        asked.update(selection)
        return {p: {m: m != "dead" for m in models} for p, models in selection.items()}

    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", fake_probe_models)
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)

    _tick(client, "text", "groq/good")
    _tick(client, "text", "groq/dead")

    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    assert asked == {"groq": ["dead", "good"]}
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["text"] == {"groq": {"good": True, "dead": False}}


def test_image_and_voice_providers_are_only_reached_never_generated(client, monkeypatch):
    """An image probe is a paid image; an ElevenLabs probe is paid
    characters. Only the free catalogue fetch may run."""
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models",
        lambda selection, **k: {p: {} for p in selection},
    )
    reached: list[tuple[str, str]] = []

    def fake_reachable(kind, provider, **_k):
        reached.append((kind, provider))
        return True

    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", fake_reachable)

    _tick(client, "image", "pollinations/flux")
    _tick(client, "voice", "edge/en-GB-RyanNeural")

    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    assert sorted(reached) == [("image", "pollinations"), ("voice", "edge")]
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["reachable"] == {"image/pollinations": True, "voice/edge": True}


def test_an_untouched_provider_reports_untested_not_unreachable(client, monkeypatch):
    """Untested is the ABSENCE of a stored verdict. A provider nobody
    shortlisted has not been proved unreachable."""
    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", lambda s, **k: {})
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.check_reachable",
        lambda *a, **k: pytest.fail("a provider with no ticked entry is not worth a call"),
    )

    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["reachable"] == {}
    assert body["text"] == {}


def test_a_sweep_keeps_the_catalogue_entries_the_live_list_shows(client, monkeypatch):
    """The Live List's image, voice and text rows come from the catalogue
    entries in this same document. A sweep that replaced the whole document
    would empty the screen the ticks came from."""
    monkeypatch.setattr(
        "avg.api.routes_settings.catalogues.image_providers", lambda: ["pollinations"]
    )
    monkeypatch.setattr(
        "avg.api.routes_settings.catalogues.list_image_models", lambda p, **k: ["flux"]
    )
    monkeypatch.setattr("avg.api.routes_settings.catalogues.voice_providers", lambda: [])
    monkeypatch.setattr("avg.api.routes_settings.probe.known_providers", lambda: [])
    assert client.post("/api/live-list/refresh", headers=AUTH).status_code == 200
    assert [r["id"] for r in client.get("/api/live-list", headers=AUTH).json()["image"]] == [
        "pollinations/flux"
    ]

    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", lambda s, **k: {})
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)
    client.post("/api/models/cache/build", headers=AUTH)
    client.app.state.probe_thread.join(timeout=5)

    assert [r["id"] for r in client.get("/api/live-list", headers=AUTH).json()["image"]] == [
        "pollinations/flux"
    ]


def test_refresh_lists_advertised_text_models_so_they_can_be_ticked(client, monkeypatch):
    """Listing a text catalogue is a plain GET and costs nothing - the same
    reason images and voices are listed here. Without it a fresh install has
    no text model to tick, and a ticked-only probe would have nothing to
    prove."""
    monkeypatch.setattr("avg.api.routes_settings.probe.known_providers", lambda: ["groq"])
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.list_models", lambda p, t: ["llama-3.1-8b"]
    )
    monkeypatch.setattr("avg.api.routes_settings.catalogues.image_providers", lambda: [])
    monkeypatch.setattr("avg.api.routes_settings.catalogues.voice_providers", lambda: [])

    assert client.post("/api/live-list/refresh", headers=AUTH).status_code == 200

    rows = client.get("/api/live-list", headers=AUTH).json()["text"]
    assert [(r["id"], r["source"]) for r in rows] == [("groq/llama-3.1-8b", "catalogue")]
```

- [ ] **Step 2: Update the three existing tests that monkeypatch `probe_all`**

`probe_all` is no longer what the route calls. In `tests/api/test_model_cache_routes.py`:

Replace `test_a_build_stores_only_what_worked` (lines 53-63):

```python
def test_a_build_stores_only_what_worked(client, monkeypatch):
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models",
        lambda *a, **k: {"groq": {"good": True, "dead": False}, "gemini": {}},
    )
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)
    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["providers"] == {"groq": ["good"], "gemini": []}
    assert body["updated"] is not None
```

In `test_build_is_refused_while_one_is_running`, change the monkeypatch target on line 77 to `"avg.api.routes_settings.probe.probe_models"` and give `slow_probe` the matching signature:

```python
    def slow_probe(*_a, **_k):
        started.set()
        release.wait(timeout=5)
        return {}

    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", slow_probe)
```

In `test_a_failed_sweep_leaves_the_previous_cache_alone`, change both monkeypatch targets (lines 90 and 98) from `"avg.api.routes_settings.probe_all"` to `"avg.api.routes_settings.probe.probe_models"`, and add `monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)` after the first one.

Add `import pytest` to the file if the new tests' `pytest.fail` needs it (it is already imported at line 13).

- [ ] **Step 3: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_model_cache_routes.py -q
```

Expected: FAIL — `AttributeError: <module 'avg.api.routes_settings'> does not have the attribute 'probe'`.

- [ ] **Step 4: Swap the import for the module**

In `avg/api/routes_settings.py`, replace line 60:

```python
from avg.providers import probe
```

(`probe_all` is no longer referenced from this module. Importing the module rather than the function is what lets a test replace one function without replacing the module.)

- [ ] **Step 5: Reshape the two model-cache routes**

Replace lines 208-264 of `avg/api/routes_settings.py`:

```python
@router.get("/api/models/cache")
async def get_model_cache(request: Request) -> dict:
    """What the last provider test proved, in the three states it can prove.

    - `providers` - models a real completion succeeded on, per provider.
      Proven only; a provider probed with nothing working appears with an
      empty list, which is a different statement from being absent.
    - `text` - the full per-model verdicts including the failures, so the
      UI can say `failed` rather than fall back to `untested`.
    - `reachable` - per `kind/provider`, whether the provider answered its
      own catalogue. THIS MODEL is unproven either way; reachability is a
      fact about a provider.

    An entry absent from `text` or `reachable` is UNTESTED. `updated: null`
    means no test has ever run - the UI says so and never invents a list.
    """
    cache = request.app.state.model_cache
    updated = cache.updated_at()
    return {
        "updated": updated.isoformat() if updated is not None else None,
        "providers": {p: cache.working(p) for p in cache.providers()},
        "text": {
            p: cache.verdicts(p) for p in cache.providers() if not p.startswith("__")
        },
        "reachable": cache.reach(),
        "building": _probe_running(request.app.state),
    }


def _by_provider(entry_ids: list[str]) -> dict[str, list[str]]:
    """`["groq/a", "groq/b"]` -> `{"groq": ["a", "b"]}`. Ids without a
    provider prefix are dropped rather than guessed at."""
    out: dict[str, list[str]] = {}
    for entry_id in entry_ids:
        provider, _, model = entry_id.partition("/")
        if not provider or not model:
            continue
        out.setdefault(provider, []).append(model)
    return {provider: sorted(models) for provider, models in out.items()}


def _probe_running(app_state: Any) -> bool:
    thread = getattr(app_state, "probe_thread", None)
    return thread is not None and thread.is_alive()


@router.post("/api/models/cache/build", status_code=status.HTTP_202_ACCEPTED)
async def build_model_cache(request: Request) -> dict:
    """Test Providers: prove the ticked text models, reach the rest.

    Three verdicts, and only one of them costs anything:

      | Kind  | What runs                        | Verdict                   |
      |-------|----------------------------------|---------------------------|
      | Text  | A real minimal completion per    | `working` / `failed`      |
      |       | TICKED model                     |                           |
      | Image | The provider's catalogue fetch   | `reachable`/`unreachable` |
      | Voice | The provider's voice list        | `reachable`/`unreachable` |

    **Nothing that costs money is probed.** An image probe is a paid image
    and an ElevenLabs probe is paid characters, which is why Slice 8 made
    images and voices listed rather than probed; reachability is a
    catalogue GET, which both serve for free.

    Only ticked text models, and only providers with at least one ticked
    entry: a sweep over everything a provider advertises is precisely what
    the Live List exists to replace, and a provider nobody shortlisted is
    not worth a call.

    Runs on a background thread with a single slot and a 409 on a second
    start, matching how renders are guarded. Deliberately NOT sharing the
    render slot - testing providers touches no project and no media engine.
    """
    app_state = request.app.state
    if _probe_running(app_state):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a provider test is already running",
        )

    cache = app_state.model_cache
    prefs, _scope = _prefs_for_scope(app_state)

    def _sweep() -> None:
        # Never raises out of the thread: a failed sweep leaves the
        # previous verdicts in place, which is more useful than none.
        try:
            results = probe.probe_models(_by_provider(prefs.selected_ids("text")))

            reachable: dict[str, bool] = {}
            for kind in ("image", "voice"):
                for provider in sorted(_by_provider(prefs.selected_ids(kind))):
                    verdict = probe.check_reachable(kind, provider)
                    # `None` is "there was nothing to ask" - an unkeyed
                    # ElevenLabs, or a provider that publishes no catalogue
                    # at all. Storing it would turn untested into a verdict.
                    if verdict is not None:
                        reachable[f"{kind}/{provider}"] = verdict

            # The `__`-namespaced catalogue entries are what the Live List
            # lists. `save` replaces the whole document, so they are carried
            # forward explicitly - dropping them would empty the very screen
            # the ticks came from.
            merged: dict[str, dict[str, bool]] = {
                provider: {model: True for model in cache.working(provider)}
                for provider in cache.providers()
                if provider.startswith("__")
            }
            merged.update(results)
            cache.save(merged, reachable=reachable)
        except Exception:
            pass

    thread = threading.Thread(target=_sweep, name="provider-test", daemon=True)
    app_state.probe_thread = thread
    thread.start()
    return {"started": True}
```

- [ ] **Step 6: Merge catalogue text models into the Live List's text rows**

Replace `_known_text` (lines 341-349):

```python
def _known_text(app_state: Any) -> dict[str, str]:
    """Text models the pickers could offer, as `provider/model`.

    Two sources, and the probed one wins: a model the catalogue merely
    lists is `catalogue` ("Listed"), and a model a real completion
    succeeded on is `probed` ("Verified"). Listing has to be here at all
    because Test Providers only proves the models the user TICKED - and on
    a fresh install nothing has been ticked yet, so without the advertised
    list the text tab would have nothing to tick.
    """
    cache = app_state.model_cache
    known = _known_from_catalogue(app_state, "text")
    known.update(
        {
            f"{provider}/{model}": "probed"
            for provider in cache.providers()
            if not provider.startswith("__")
            for model in cache.working(provider)
        }
    )
    return known
```

- [ ] **Step 7: List text catalogues on refresh**

In `_list_everything` (inside `refresh_live_list`), insert after the `results` seed block and before the image loop (after line 418):

```python
        # Text models are LISTED here and PROVED by Test Providers. Listing
        # is a plain catalogue GET on every text provider - it costs
        # nothing, exactly like the image and voice lists below - and it is
        # what puts a model in front of the user to tick in the first place.
        for provider in probe.known_providers():
            try:
                models = probe.list_models(provider, None)
            except Exception:
                models = []
            if models:
                results[f"__text__{provider}"] = {model: True for model in models}
```

- [ ] **Step 8: Run the route tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_model_cache_routes.py tests/api/test_live_list_routes.py -q
```

Expected: PASS. If a live-list test breaks on the new `__text__` entries, adjust that test's expectation — the new rows are correct behaviour, not a regression.

- [ ] **Step 9: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 761 passed.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Success: no issues found`.

- [ ] **Step 10: Commit**

```bash
git add avg/api/routes_settings.py tests/api/test_model_cache_routes.py tests/api/test_live_list_routes.py
git commit -m "feat: Test Providers proves ticked text models and reaches the rest"
```

---

## Task 8: The Test Providers card

**Files:**
- Modify: `web/src/api.ts:451-470` (`ModelCacheResponse`)
- Modify: `web/src/ModelCacheCard.tsx` (whole file)

**Interfaces:**
- Consumes from Task 7: `GET /api/models/cache` returning `updated`, `providers`, `text`, `reachable`, `building`.
- Produces: nothing new exported. `AppHealth.tsx`'s `<ModelCacheCard />` usage is unchanged.

**Why:** The button says "Build model cache" and the card reports only successes. Three states now reach the UI and each must be stated in words, never implied.

- [ ] **Step 1: Extend the response type**

In `web/src/api.ts`, replace the `ModelCacheResponse` interface (lines 451-460):

```ts
/** `GET /api/models/cache` — what the last provider test proved.
 *
 * Three states, and the third is the absence of the other two:
 *
 *   - `text[provider][model] === true`  — a real completion succeeded.
 *   - `text[provider][model] === false` — it ran and failed.
 *   - absent                            — never tested.
 *
 * `reachable` is keyed `kind/provider` and says the provider answered its
 * own catalogue with this key. THIS MODEL is unproven either way —
 * reachability is a fact about a provider, not a model. `providers` keeps
 * its old meaning (proven models only) because the pickers read it.
 * `updated: null` means no test has ever run. See
 * `avg/core/model_cache.py`. */
export interface ModelCacheResponse {
  updated: string | null;
  providers: Record<string, string[]>;
  text: Record<string, Record<string, boolean>>;
  reachable: Record<string, boolean>;
  building: boolean;
}
```

- [ ] **Step 2: Rewrite the card**

Replace the whole of `web/src/ModelCacheCard.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { buildModelCache, getModelCache, type ModelCacheResponse } from "./api";

/**
 * Test Providers, on App Health.
 *
 * What it runs, and what each result is allowed to claim:
 *
 *   - **Working** — a real minimal completion succeeded on this text model.
 *     Text only; it is the one verdict that costs anything, and only ticked
 *     models are ever tested.
 *   - **Reachable** — the provider answered its own catalogue with this key.
 *     THIS MODEL is unproven. Image and voice providers get this and nothing
 *     more: an image probe is a paid image and an ElevenLabs probe is paid
 *     characters, which is why Slice 8 made them listed rather than probed.
 *   - **Untested** — never checked. The absence of a verdict, not a verdict.
 *
 * The button is plain, not terracotta: App Health already spends its one
 * filled action on "Re-run everything".
 */
const POLL_MS = 3000;

interface Row {
  key: string;
  label: string;
  verdict: "working" | "failed" | "reachable" | "unreachable";
  detail: string;
}

function chipFor(verdict: Row["verdict"]): string {
  if (verdict === "working" || verdict === "reachable") return "chip chip--ok";
  return "chip chip--bad";
}

const VERDICT_LABEL: Record<Row["verdict"], string> = {
  working: "Working",
  failed: "Failed",
  reachable: "Reachable",
  unreachable: "Unreachable",
};

export default function ModelCacheCard() {
  const [cache, setCache] = useState<ModelCacheResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      setCache(await getModelCache());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Polled while a test runs, and only then: it takes tens of seconds on a
  // real account and has no event of its own to listen for.
  useEffect(() => {
    if (!cache?.building) return;
    timer.current = window.setTimeout(() => void load(), POLL_MS);
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [cache, load]);

  async function run() {
    setError(null);
    try {
      await buildModelCache();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const rows: Row[] = [];
  for (const [provider, models] of Object.entries(cache?.text ?? {})) {
    for (const [model, ok] of Object.entries(models)) {
      rows.push({
        key: `text:${provider}/${model}`,
        label: `${provider}/${model}`,
        verdict: ok ? "working" : "failed",
        detail: ok ? "a real completion succeeded" : "the completion did not answer",
      });
    }
  }
  for (const [key, ok] of Object.entries(cache?.reachable ?? {})) {
    rows.push({
      key: `reach:${key}`,
      label: key,
      verdict: ok ? "reachable" : "unreachable",
      // Stated, never implied: this is the whole reason the two words are
      // different. Nothing was generated and nothing was synthesised.
      detail: ok
        ? "the provider answered its catalogue — this entry itself is unproven"
        : "the provider's catalogue did not answer",
    });
  }
  const building = Boolean(cache?.building);

  return (
    <section className="errorlog" aria-label="Test providers">
      <header className="errorlog__head">
        <div>
          <h2 className="health-stage__title">Test providers</h2>
          <p className="health-stage__detail">
            {cache?.updated
              ? `Last tested ${new Date(cache.updated).toLocaleString()}.`
              : "Untested. Nothing here has been checked yet."}{" "}
            Ticked text models get a real completion; image and voice providers get their own
            catalogue only — nothing that costs money is ever generated.
          </p>
        </div>
        <span className="home__spacer" />
        <button type="button" className="btn" onClick={() => void run()} disabled={building}>
          {building ? "Testing…" : cache?.updated ? "Test again" : "Test providers"}
        </button>
      </header>

      {error && <p className="settings__save-error">Could not test the providers: {error}</p>}

      {rows.length === 0 && !building && (
        <p className="home__empty">
          Untested. Tick the models and voices you want on the Live List, then press Test
          providers.
        </p>
      )}

      <ul className="errorlog__list">
        {rows.map((row) => (
          <li key={row.key} className="errorlog__row">
            <span className={chipFor(row.verdict)}>{VERDICT_LABEL[row.verdict]}</span>
            <span className="errorlog__summary mono" style={{ cursor: "default" }}>
              {row.label}
            </span>
            <span className="errorlog__meta">{row.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 3: Look at it**

Rebuild and reload. Open App health with nothing ticked. Expected: the card reads "Untested" and names the Live List. Then tick a Groq text model and a voice on the Live List, come back, press **Test providers**. Expected: text rows come back `Working` or `Failed`, the voice provider row comes back `Reachable`, and providers nobody ticked do not appear at all.

Confirm against the take log that nothing generated an image and no ElevenLabs characters were spent.

- [ ] **Step 4: Lint and commit**

```bash
cd web && npm run lint
```

```bash
git add web/src/api.ts web/src/ModelCacheCard.tsx
git commit -m "feat: Test Providers says what it proved — working, reachable, untested"
```

---

## Task 9: Dependency install — the route

**Files:**
- Modify: `avg/core/health.py:52-66` (`Component`), `:114-134` (`_check_package`)
- Modify: `avg/api/routes_settings.py:135-152` (`GET /api/health`), and add `POST /api/health/install`
- Test: `tests/api/test_settings_routes.py` (add cases) — or `tests/core/test_health.py` for the `package` field

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces, and Task 10 depends on exactly this:
  - `GET /api/health` gains `"can_install": bool` at the top level and `"package": str` on every component.
  - `POST /api/health/install` with body `{"package": "<distribution name>"}` returns `{"ok": bool, "output": str}`; 400 for an unknown package, 400 for a frozen build.
  - `avg.core.health.installable_packages() -> set[str]`

**Why:** Spec §3.4. In a frozen build a missing required dependency is a packaging bug in the build, not something a user should paper over at runtime — that is the `pydub` lesson from Slice 9, where the real fix was to bundle it and correct `health.py`'s claim that it was optional. No private package directory is ever put on `sys.path`.

The allowlist is not optional: this route shells a package name, so the name must come from `_PACKAGES` and nowhere else.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_install_route.py`:

```python
"""Tests for `POST /api/health/install`.

Nothing here runs pip. The route's whole risk is that it shells a package
name, so what is asserted is what it REFUSES and what exact argv it builds.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.core import health

TOKEN = "t0k"
AUTH = {"X-AVG-Token": TOKEN}


@pytest.fixture
def paths(tmp_path):
    return {
        "settings_path": tmp_path / "settings.json",
        "channels_dir": tmp_path / "channels",
        "secrets_path": tmp_path / "secrets.dat",
        "output_root": tmp_path / "output",
        "thumb_cache_dir": tmp_path / "thumbs",
        "errors_path": tmp_path / "errors.json",
        "model_cache_path": tmp_path / "working_models.json",
        "model_prefs_path": tmp_path / "model_prefs.json",
    }


@pytest.fixture
def client(paths):
    return TestClient(create_app(token=TOKEN, **paths))


class _Result:
    returncode = 0
    stdout = "Successfully installed pydub-0.25.1\n"
    stderr = ""


def test_install_requires_a_token(client):
    assert client.post("/api/health/install", json={"package": "pydub"}).status_code == 401


def test_it_shells_pip_for_exactly_one_named_package(client, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        seen.append(list(argv))
        return _Result()

    monkeypatch.setattr("avg.api.routes_settings.subprocess.run", fake_run)

    res = client.post("/api/health/install", json={"package": "pydub"}, headers=AUTH)

    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert seen == [[sys.executable, "-m", "pip", "install", "pydub"]]


def test_it_refuses_a_package_that_is_not_one_of_ours(client, monkeypatch):
    """The route shells a name. The name comes from `_PACKAGES` or it does
    not run at all - there is no path from a request body to an arbitrary
    install."""
    monkeypatch.setattr(
        "avg.api.routes_settings.subprocess.run",
        lambda *a, **k: pytest.fail("an unknown package must never reach pip"),
    )
    res = client.post(
        "/api/health/install", json={"package": "pydub; rm -rf /"}, headers=AUTH
    )
    assert res.status_code == 400
    assert "not one of this app" in res.json()["detail"]


def test_it_refuses_itself_in_a_frozen_build(client, monkeypatch):
    """A required dependency missing from a frozen build is a packaging bug
    in the build, not something a user should paper over at runtime."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "avg.api.routes_settings.subprocess.run",
        lambda *a, **k: pytest.fail("a packaged build must not install at runtime"),
    )
    res = client.post("/api/health/install", json={"package": "pydub"}, headers=AUTH)
    assert res.status_code == 400
    assert "packaged build" in res.json()["detail"]


def test_health_says_whether_installing_is_offered_at_all(client, monkeypatch):
    body = client.get("/api/health", headers=AUTH).json()
    assert body["can_install"] is True
    # Every row names the distribution it would install, or names none -
    # FFmpeg is not a pip package and its row has to explain rather than act.
    by_name = {c["name"]: c for c in body["components"]}
    assert by_name["pydub"]["package"] == "pydub"
    assert by_name["Pillow"]["package"] == "Pillow"
    assert by_name["FFmpeg"]["package"] == ""

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert client.get("/api/health", headers=AUTH).json()["can_install"] is False


def test_the_allowlist_is_the_package_table_itself():
    """Derived, never restated: a package added to `_PACKAGES` is installable
    with no second edit, and nothing else ever is."""
    assert health.installable_packages() == {
        dist for _label, _module, dist, _required in health._PACKAGES
    }
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_install_route.py -q
```

Expected: FAIL — 404 on the route, and `AttributeError` on `health.installable_packages`.

- [ ] **Step 3: Add `package` to `Component` and the allowlist to `health`**

In `avg/core/health.py`, add a field to the `Component` dataclass (after `required`, line 65):

```python
    #: The distribution name this row could be installed under, or `""`
    #: when there is nothing to install. FFmpeg is a binary, not a pip
    #: package - its row explains rather than acting, which is the honest
    #: answer and not a missing feature.
    package: str = ""
```

In `_check_package`, pass `package=distribution` on all three returns:

```python
def _check_package(label: str, module: str, distribution: str, required: bool) -> Component:
    # Importing to check costs whatever that import costs - which for
    # edge-tts is the ~2s that Slice 7 took OFF startup. That is fine and
    # must stay fine: a health check runs on demand, never at import, so
    # this does not put the cost back. Do not "optimise" this into a
    # metadata-only check; a package that installs but cannot import is
    # exactly the failure worth catching.
    try:
        __import__(module)
    except Exception as exc:  # noqa: BLE001 - any import failure is the answer
        return Component(
            name=label,
            ok=False,
            detail=f"cannot import: {exc}",
            required=required,
            package=distribution,
        )
    try:
        version = metadata.version(distribution)
    except Exception:  # noqa: BLE001
        # Imports fine but has no installed metadata - true in some frozen
        # builds. Present and usable is the thing being reported, so this
        # is healthy with an unknown version, not a failure.
        return Component(
            name=label,
            ok=True,
            detail="installed (version unavailable)",
            required=required,
            package=distribution,
        )
    return Component(name=label, ok=True, version=version, required=required, package=distribution)
```

Add after the `_PACKAGES` tuple (after line 111):

```python
def installable_packages() -> set[str]:
    """The distribution names an install action may name, and nothing else.

    Derived from `_PACKAGES` rather than restated: a package added there is
    installable with no second edit, and a name that is not in that table
    can never reach a shell. `avg/api/routes_settings.py` validates every
    install request against this set before it builds an argv - the request
    body is data, not a command line.
    """
    return {distribution for _label, _module, distribution, _required in _PACKAGES}
```

- [ ] **Step 4: Add the route and extend `GET /api/health`**

In `avg/api/routes_settings.py`, add to the imports:

```python
import subprocess
import sys
```

Replace `get_health` (lines 135-152):

```python
@router.get("/api/health")
async def get_health(refresh: bool = False) -> dict:
    """Component states for the settings screen's Components table.

    `refresh=true` is the Recheck button and skips the cache, paying for the
    `ffmpeg -version` subprocess again. Never raises for an unhealthy
    component: a failure is an ordinary row with `ok: false` and a reason,
    which is what lets this screen report a broken FFmpeg instead of 500ing
    about it.

    `can_install` is false in a frozen build. A required dependency missing
    THERE is a packaging bug in the build, not something a user should paper
    over at runtime - so the row explains instead of offering an action. See
    `POST /api/health/install`.
    """
    components = health.check_all(refresh=refresh)
    return {
        "components": [
            {
                "name": c.name,
                "ok": c.ok,
                "version": c.version,
                "detail": c.detail,
                "required": c.required,
                "package": c.package,
            }
            for c in components
        ],
        "ok": all(c.ok for c in components),
        "can_install": not getattr(sys, "frozen", False),
    }


class InstallRequest(BaseModel):
    """`POST /api/health/install` - one package, by distribution name."""

    package: str


@router.post("/api/health/install")
async def install_package(body: InstallRequest, request: Request) -> dict:
    """Install one missing dependency with pip, in a dev checkout only.

    Two refusals, both deliberate:

    - **Not a package this app knows.** The request body names something
      that gets shelled, so it is validated against
      `health.installable_packages()` - derived from `_PACKAGES` - before
      any argv exists. There is no path from a request to an arbitrary
      install, and the name is passed as one argv element, never through a
      shell.
    - **A frozen build.** A required dependency missing from a packaged app
      is a packaging bug in the build. That is the `pydub` lesson from Slice
      9, where the real fix was to bundle it and correct this module's claim
      that it was optional - and it is why no private package directory is
      ever put on `sys.path`. An app running a mix of bundled and downloaded
      code is the hardest kind of "works on my machine" to debug, and it
      needs the network at exactly the moment something is already wrong.

    Runs on a worker thread (pip is tens of seconds) and returns pip's own
    combined output, so the user reads what pip said rather than a verdict
    this route invented.
    """
    if getattr(sys, "frozen", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "this is a packaged build - a missing dependency here is a bug in the "
                "build, not something to install at runtime. Reinstall AVG Studio."
            ),
        )

    package = body.package.strip()
    if package not in health.installable_packages():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{package!r} is not one of this app's own dependencies",
        )

    def _run() -> tuple[int, str]:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True,
            text=True,
            timeout=600,
            # No console window flashes on Windows when the app is frozen
            # and windowed; harmless everywhere else.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode, f"{result.stdout or ''}{result.stderr or ''}"

    try:
        code, output = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001 - the reason is the answer
        return {"ok": False, "output": f"could not run pip: {exc}"}

    if code == 0:
        # The five-minute component cache would otherwise keep reporting the
        # package as missing for the rest of it.
        health.clear_cache()
    return {"ok": code == 0, "output": output}
```

- [ ] **Step 5: Run the install tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_install_route.py -q
```

Expected: PASS (7 tests).

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 768 passed. If an existing health test asserts the exact key set of a component dict, extend it with `package` rather than dropping the field.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Success: no issues found`.

- [ ] **Step 7: Commit**

```bash
git add avg/core/health.py avg/api/routes_settings.py tests/api/test_install_route.py
git commit -m "feat: install a missing dependency from a dev checkout, explain in a frozen build"
```

---

## Task 10: Dependency install — the Components table

**Files:**
- Modify: `web/src/api.ts:263-284` (`HealthComponent`, `HealthResponse`, add `installPackage`)
- Modify: `web/src/HealthPanel.tsx` (add the action column)

**Interfaces:**
- Consumes from Task 9: `HealthComponent.package`, `HealthResponse.can_install`, `POST /api/health/install`.
- Produces: `installPackage(pkg: string): Promise<{ ok: boolean; output: string }>` in `api.ts`.

- [ ] **Step 1: Extend the types and add the call**

In `web/src/api.ts`, replace the `HealthComponent` and `HealthResponse` interfaces (lines 263-276):

```ts
export interface HealthComponent {
  name: string;
  ok: boolean;
  version: string;
  detail: string;
  /** Whether the app can work without it. An optional component that is
   * missing is worth knowing about, not worth reading as a broken app. */
  required?: boolean;
  /** The distribution name an Install action would name, or `""` when
   * there is nothing to install — FFmpeg is a binary, not a pip package. */
  package?: string;
}

export interface HealthResponse {
  components: HealthComponent[];
  ok: boolean;
  /** False in a frozen build. A required dependency missing there is a
   * packaging bug in the build, so the row explains instead of acting. */
  can_install?: boolean;
}
```

Add after `getHealth` (after line 284):

```ts
/** `POST /api/health/install`. One dependency, by distribution name; the
 * backend refuses anything that is not one of its own packages and refuses
 * outright in a frozen build. Returns pip's own combined output, so the
 * card shows what pip said rather than a verdict this app invented. */
export function installPackage(pkg: string): Promise<{ ok: boolean; output: string }> {
  return apiPost<{ ok: boolean; output: string }>("/api/health/install", { package: pkg });
}
```

If `apiPost`'s signature differs from `(path, body)`, match whatever the neighbouring calls in this file already do.

- [ ] **Step 2: The action column**

In `web/src/HealthPanel.tsx`, replace the import line 2:

```tsx
import { getHealth, installPackage, type HealthResponse } from "./api";
```

Add state after line 21 (`const [error, setError] = useState<string | null>(null);`):

```tsx
  const [installing, setInstalling] = useState<string | null>(null);
  const [output, setOutput] = useState<string | null>(null);
```

Add the handler after the `load` callback:

```tsx
  async function install(pkg: string) {
    setInstalling(pkg);
    setOutput(null);
    try {
      const res = await installPackage(pkg);
      setOutput(res.output.trim() || (res.ok ? "installed" : "pip said nothing"));
      // Re-check rather than assume: a pip that reported success and a
      // package that actually imports are two different claims.
      await load(true);
    } catch (err) {
      setOutput(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(null);
    }
  }
```

Add a fourth column. Replace the `<thead>` block (lines 62-68):

```tsx
            <thead>
              <tr>
                <th>Component</th>
                <th>State</th>
                <th>Version</th>
                <th>Fix</th>
              </tr>
            </thead>
```

Replace the row body (lines 70-87):

```tsx
              {health.components.map((component) => (
                <tr key={component.name}>
                  <td>{component.name}</td>
                  <td>
                    <span className={component.ok ? "chip chip--ok" : "chip chip--bad"}>
                      {component.ok ? "Present" : "Missing"}
                    </span>
                  </td>
                  {/* No Detail column here, per the 3rd redesign: this is
                      the at-a-glance summary and the full reason for a
                      failure lives on the App Health screen. The detail is
                      still reachable as the row's tooltip. */}
                  <td className="mono" title={component.detail}>
                    {component.version || "—"}
                  </td>
                  <td>
                    {component.ok ? (
                      "—"
                    ) : !component.package ? (
                      // FFmpeg. Not a pip package, so there is nothing
                      // honest to offer here.
                      <span className="field-row__help">Not a pip package</span>
                    ) : health.can_install === false ? (
                      // A packaged build. A missing required dependency
                      // there is a bug in the build, not something to paper
                      // over at runtime.
                      <span className="field-row__help">Bundled — reinstall AVG Studio</span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--sm"
                        disabled={installing !== null}
                        onClick={() => void install(component.package!)}
                      >
                        {installing === component.package ? "Installing…" : "Install"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
```

Add pip's output below the table, after the closing `</div>` of `models-scroll`:

```tsx
      {output && <pre className="artifact-prose">{output}</pre>}
```

- [ ] **Step 3: Look at it — in the dev checkout**

Rebuild, reload, open Settings → Paths & components. With every dependency present, expect every Fix cell to read `—` except FFmpeg's, which reads `Not a pip package` only if FFmpeg is missing.

To exercise the real path, uninstall one degradable package and reload:

```bash
.venv-build/Scripts/python.exe -m pip uninstall -y edge-tts
```

Expected: the `edge-tts` row turns red with an **Install** button; pressing it shows pip's output and the row turns green after the re-check. Reinstall if the button did not:

```bash
.venv-build/Scripts/python.exe -m pip install edge-tts
```

- [ ] **Step 4: Lint and commit**

```bash
cd web && npm run lint
```

```bash
git add web/src/api.ts web/src/HealthPanel.tsx
git commit -m "feat: install a missing dependency from the Components table"
```

---

## Task 11: Startup — window first

**Files:**
- Modify: `avg/shell.py` (module docstring, imports, `main`, add `splash_html`)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `avg.shell.splash_html() -> str` — a pure function taking no arguments, reading no file, and importing nothing.

**Why:** Measured 2026-07-30 with `python -X importtime`: `create_app` takes **42 ms**, while importing `avg.shell` takes **~1.6 s** cold — `fastapi` 0.42 s, `uvicorn` 0.27 s, `avg.api.app` 0.72 s cumulative. Startup is entirely import-bound, so the fix is to stop waiting on the imports before showing anything, not to micro-optimise the import graph.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_shell.py`:

```python
"""What the shell must be true of before anything is imported.

The timing itself is measured by hand and recorded in
`.superpowers/sdd/progress.md`; a wall-clock assertion in a suite is a
flake waiting to happen. What is asserted here is the SEAM that makes the
window able to open first.
"""

import sys

from avg.shell import make_token, pick_port, splash_html


def test_port_is_free_and_ephemeral():
    p = pick_port()
    assert 1024 < p < 65536


def test_token_is_unguessable_and_unique():
    a, b = make_token(), make_token()
    assert a != b and len(a) >= 32


def test_the_splash_needs_no_server_no_app_and_no_settings():
    """The property that lets the window open first: this page is built
    from nothing. No port, no token, no `create_app`, no settings file - so
    it can be handed to pywebview before a single heavy import has run."""
    html = splash_html()
    assert "AVG Studio" in html
    # Nothing to fetch: a splash that needed the server running would defeat
    # its own purpose.
    assert "http://" not in html
    assert "https://" not in html


def test_the_shell_does_not_import_the_server_at_module_scope():
    """`fastapi` is 0.42s, `uvicorn` 0.27s and `avg.api.app` 0.72s
    cumulative. Importing any of them at module scope puts all of it back in
    front of the window."""
    import avg.shell as shell

    assert not hasattr(shell, "uvicorn")
    assert not hasattr(shell, "create_app")
    assert not hasattr(shell, "load_settings")
    assert "avg.shell" in sys.modules
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/test_shell.py -q
```

Expected: FAIL — `ImportError: cannot import name 'splash_html' from 'avg.shell'`.

- [ ] **Step 3: Make the heavy imports lazy**

In `avg/shell.py`, replace the import block (lines 30-46):

```python
from __future__ import annotations

import secrets
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import webview

HOST = "127.0.0.1"
```

`uvicorn`, `avg.api.app.create_app`, `avg.config.settings` and `avg.media.environment` all move to function scope. Only `webview` stays at module level — it is what has to exist before the window can open, and a test asserts the others do not.

In `resolve_output_root`, move the settings import inside the function. Replace its body's first lines (after the docstring):

```python
    # Imported here, not at module scope: `avg.config.settings` pulls in
    # pydantic, and this module's whole point is that nothing heavy runs
    # before the window is on screen. See `main`.
    from avg.config.settings import LEGACY_OUTPUT_ROOT, load_settings  # noqa: PLC0415

    override = os.environ.get("AVG_OUTPUT_ROOT", "").strip()
```

- [ ] **Step 4: Add `splash_html`**

Insert after `make_token` (after line 141):

```python
#: The splash's canvas colour, kept in step with `--canvas` in
#: `web/src/tokens.css`. Duplicated on purpose and duplicated nowhere else:
#: this page must render before anything is imported, so it cannot read a
#: stylesheet the server has not started serving yet.
SPLASH_CANVAS = "#1c1a16"
SPLASH_TEXT = "#f2ebdd"


def splash_html() -> str:
    """The page the window shows while the server is still being built.

    Inline HTML, not a file, and a pure function of nothing: it must render
    before `fastapi`, `uvicorn` and `avg.api.app` are imported (~1.6s cold,
    measured 2026-07-30), and a page that needed the server running to load
    would defeat its own purpose. That is also why there is no URL of any
    kind in it - a test asserts that.

    It carries the app's canvas colour, its name, and nothing that moves
    except a single slow pulse, so a slow machine does not show a spinner
    that outlives its welcome.
    """
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AVG Studio</title>
<style>
  html, body {{ height: 100%; margin: 0; }}
  body {{
    background: {SPLASH_CANVAS};
    color: {SPLASH_TEXT};
    display: grid;
    place-items: center;
    font-family: "Segoe UI", system-ui, sans-serif;
  }}
  .name {{ font-size: 30px; letter-spacing: 0.02em; animation: pulse 2.2s ease-in-out infinite; }}
  .sub {{ margin-top: 10px; font-size: 13px; opacity: 0.55; text-align: center; }}
  @keyframes pulse {{ 50% {{ opacity: 0.55; }} }}
  @media (prefers-reduced-motion: reduce) {{ .name {{ animation: none; }} }}
</style></head>
<body><div><div class="name">AVG Studio</div><div class="sub">Starting up…</div></div></body>
</html>"""
```

- [ ] **Step 5: Open the window first, then build the server**

Replace `main` (lines 183-231):

```python
def main() -> None:
    """Open the window, then build the server behind it.

    The order is the whole change. Importing `fastapi`, `uvicorn` and
    `avg.api.app` costs ~1.6s cold (measured 2026-07-30 with
    `python -X importtime`; `create_app` itself is 42ms), and all of it used
    to happen before pywebview was asked for a window - so the app showed
    nothing at all for that time. Now the window opens immediately on
    `splash_html()`, and `_boot` does the imports, the build and the server
    start on pywebview's own worker thread before pointing the window at the
    real URL.

    The imports still happen. They are what the server needs. They just
    happen behind a window that already looks like the app rather than
    behind nothing.
    """
    window = webview.create_window(
        "AVG Studio",
        html=splash_html(),
        js_api=_Api(),
        width=1280,
        height=820,
    )

    # Set by `_boot` so the teardown below can stop what it started. A list
    # rather than a `nonlocal` because `_boot` runs on another thread and
    # this is the simplest handoff that needs no lock: one writer, one
    # reader, and the reader only runs after `webview.start()` returns.
    started: list = []

    def _boot() -> None:
        # Frozen builds ship their own ffmpeg/ffprobe (vendor/ffmpeg) because
        # a clean target machine has neither on PATH. This must run before
        # anything shells out - including a render child process, which
        # inherits this modified PATH from the environment at the time it is
        # spawned. No-op in development. See avg.media.environment.
        #
        # Same reasoning for window flashes: this process warms thumbnails by
        # shelling out to ffprobe/ffmpeg, and a windowed build has no console
        # for them to inherit, so each call would pop one.
        from avg.media.environment import prepend_to_path, suppress_console_windows  # noqa: PLC0415

        prepend_to_path()
        suppress_console_windows()

        # The expensive imports, deliberately here rather than at module
        # scope - this is the 1.6s the window no longer waits behind.
        import uvicorn  # noqa: PLC0415

        from avg.api.app import create_app  # noqa: PLC0415

        port = pick_port()
        token = make_token()
        app = create_app(token=token, output_root=resolve_output_root())

        server = uvicorn.Server(
            uvicorn.Config(app, host=HOST, port=port, log_level="warning")
        )
        server_thread = threading.Thread(
            target=server.run, daemon=True, name="avg-api-server"
        )
        server_thread.start()
        started.append((server, server_thread))

        try:
            _wait_until_ready(f"http://{HOST}:{port}/api/settings/schema", token)
        except RuntimeError as exc:
            server.should_exit = True
            # Nothing below `avg/api/` may print, but this is the shell and
            # the window is the only surface there is: a failure here means
            # the app never started, and the splash must say so rather than
            # sit on "Starting up…" forever.
            window.load_html(
                splash_html().replace("Starting up…", f"Could not start: {exc}")
            )
            return

        window.load_url(f"http://{HOST}:{port}/?token={token}")

    webview.start(_boot, private_mode=False)

    for server, server_thread in started:
        server.should_exit = True
        server_thread.join(timeout=5)
```

Note `webview.start(_boot, ...)` runs `_boot` on pywebview's worker thread once the GUI loop is up — that is the pywebview idiom for "do work after the window exists". `private_mode=False` matches the previous default behaviour of a normal WebView2 profile; if the current pywebview version's `start()` does not accept it, drop the argument rather than changing anything else.

- [ ] **Step 6: Run the shell tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/test_shell.py -q
```

Expected: PASS (4 tests).

- [ ] **Step 7: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 770 passed.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Success: no issues found`.

- [ ] **Step 8: Measure time to window, by hand, before and after**

The number that improves is time to window. Measure the *after* by launching the real shell and timing until the splash paints:

```bash
.venv-build/Scripts/python.exe -c "import time; t=time.perf_counter(); import avg.shell; print('import avg.shell', round(time.perf_counter()-t, 3), 's')"
```

Expected: well under the ~1.6 s recorded on 2026-07-30, because `fastapi`/`uvicorn`/`avg.api.app` are no longer on that path. Then run the app itself and confirm by eye that the window appears with "AVG Studio / Starting up…" and swaps to the real UI a second or so later:

```bash
.venv-build/Scripts/python.exe -m avg.shell
```

Record both numbers in `.superpowers/sdd/progress.md` in Task 12.

- [ ] **Step 9: Commit**

```bash
git add avg/shell.py tests/test_shell.py
git commit -m "perf: open the window on a splash, then build the server behind it"
```

---

## Task 12: Full verification and the ledger

**Files:**
- Create: `.superpowers/sdd/progress.md` (gitignored by `.superpowers/sdd/.gitignore`; it is a local working file and is not committed)
- Modify: `docs/APP_CODING_GUIDE.md` §3 (the route table)

**Interfaces:**
- Consumes: every preceding task.
- Produces: nothing code-facing.

**Why:** `CLAUDE.md` calls `.superpowers/sdd/progress.md` plus `git log` the thing that makes resuming possible after a session dies mid-slice — and the file does not currently exist on disk. Spec §5 also requires the time-to-window measurement to be recorded there.

- [ ] **Step 1: Full suite, types, build, lint**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 770 passed, never fewer than 746.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Success: no issues found`.

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

- [ ] **Step 2: Walk the spec's verification list**

Against the dev server at `http://127.0.0.1:8756/?token=devtoken`, confirm each:

- Home at 1280×820 and 900×700, both themes, on a library holding both 9:16 and 16:9 projects: exactly one filled terracotta button on the screen, widescreen thumbnails not cropped into portrait, and a project with no images reading as a sentence rather than a grey icon.
- Settings: the nav marks the group in view while scrolling, clicking a group jumps to it, and a query filters both page and nav.
- No native control anywhere — `grep -rn "<select\|type=\"checkbox\"" web/src/` finds only Home's sanctioned hidden `.select-dot` checkbox.
- Change screens five times: no second scrollbar appears at any point.
- Press render: the pill's ring spins and `Ready!` pops back when the job ends. With reduced motion on, the ring is static.
- Live List: a query narrows Edge's 322 voices to a handful.
- Test Providers: text rows come back `Working`/`Failed`, image and voice rows `Reachable`, an untouched provider absent (untested); nothing generated an image and no ElevenLabs characters were spent — checked against the take log, which records every call.
- The dependency card offers Install in the dev checkout, installs one package, and turns that row green.
- The Script panel is a scene table with narration behind a disclosure.

- [ ] **Step 3: Update the coding guide's route table**

In `docs/APP_CODING_GUIDE.md` §3, add one row after the `/api/health` row:

```
| POST | `/api/health/install` | installs one of this app's own dependencies; refuses in a frozen build | `core.health` |
```

And change the `POST /api/models/cache/build` row's description from `202, or 409 while one runs` to:

```
| POST | `/api/models/cache/build` | Test Providers: 202, or 409 while one runs. Ticked text models get a completion; image/voice providers only a catalogue fetch | `providers.probe` |
```

- [ ] **Step 4: Write the ledger**

Create `.superpowers/sdd/progress.md`:

```markdown
# AVG Studio — running ledger

Recreated 2026-07-30: the file `CLAUDE.md` names as the thing that makes a
resumed session possible was missing from disk. `.superpowers/sdd/.gitignore`
ignores everything in this folder, so it is local-only and cannot be
recovered from git — it has to be kept by hand from here.

## Slices 1–9

Done before this file was recreated. See `git log` and
`docs/superpowers/specs/` for each slice's design. Baseline at the start of
Slice 10, measured 2026-07-30: **746 tests passing**.

## Slice 10 — UI truth and startup

Spec: `docs/superpowers/specs/2026-07-30-slice-10-ui-truth-and-startup-design.md`
Plan: `docs/superpowers/plans/2026-07-30-slice-10-ui-truth-and-startup.md`

### Measurements

| What | Before | After |
|---|---|---|
| `import avg.shell` (cold) | ~1.6 s | _fill in from Task 11 Step 8_ |
| Time to window (by eye) | ~1.6 s of nothing | _fill in_ |
| `create_app` | 42 ms | unchanged |
| Home `.btn--primary` count | 13 | 1 |
| Settings `scrollHeight` @720px | 6584 | unchanged; now navigable |
| Test count | 746 | _fill in_ |

### Decisions taken during execution

- **Text discovery moved to `POST /api/live-list/refresh`.** Spec §3.3 makes
  the text probe ticked-only, but the Live List's text rows came from the
  probe cache alone — so a fresh install would have had nothing to tick.
  Listing a text catalogue is a free GET, exactly like the image and voice
  lists already there.
- **Install output is returned, not streamed.** Streaming would need a new
  event type and a WebSocket path for a one-shot action; the card shows
  pip's own combined output when it lands.
- **`max-height` came off `.project-card__thumb`.** It is what forced every
  aspect ratio into the same portrait box, which is the defect.

### Still open

- Toasts and the device-frame player (Slice 6C leftovers).
- The card-background timing defect (`avg/media/overlays.py`), left open by
  Slice 9 — render correctness, belongs with the pipeline.
- The codebase-simplification refactor; input is `docs/APP_CODING_GUIDE.md`.
- `web/src/phases.ts` still restates `REGISTRY`'s eight keys by hand.
```

Fill in every `_fill in_` with the number actually measured. A placeholder left in this file is the failure mode the file exists to prevent.

- [ ] **Step 5: Build the packaged app and check it there**

```bash
"Build Installer.bat" -SkipInstaller
```

Then run `AVG APP/AVG Studio.exe` and confirm: the window appears at once on the splash, Home shows one filled button, and the Components table's Fix column reads `Bundled — reinstall AVG Studio` rather than offering Install.

- [ ] **Step 6: Commit**

`.superpowers/sdd/progress.md` is gitignored and is not staged.

```bash
git add docs/APP_CODING_GUIDE.md
git commit -m "docs: record the Test Providers and install routes in the coding guide"
```

---

## Appendix: spec coverage

| Spec section | Task |
|---|---|
| §3.1 Home: one action, honest cards | 2 |
| §3.2 Settings: navigable and searchable | 3 |
| §3.3 Test Providers: three verdicts | 6, 7, 8 |
| §3.4 Dependency auto-install | 9, 10 |
| §3.5 Startup: window first | 11 |
| §3.6 Status pill | 1 |
| §3.6 Live List search | 4 |
| §3.6 Script panel | 5 |
| §3.6 The flash | 1 |
| §4 Testing | 6, 7, 9, 11 |
| §5 Verification | 12 |
