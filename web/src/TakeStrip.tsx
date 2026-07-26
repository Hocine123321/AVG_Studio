import { useEffect, useState } from "react";
import type { Take } from "./api";
import { IconPlus } from "./Icons";

type ChipStatus = "pending" | "running" | "done" | "failed";

/** Mirrors `ProjectCard.tsx`'s own `resolveState` (phases + master_output),
 * scoped to one take instead of the whole project - the same "failed wins
 * over running, running wins over done" ordering, since a dead re-run can
 * leave a stale 'running' record behind exactly like a dead project-level
 * render can. `forcedRunning` covers the brief window right after a take is
 * created, before its own pipeline has recorded a single phase transition
 * yet (see `Studio.tsx`'s `runningTakeId`). */
function resolveStatus(take: Take, forcedRunning: boolean): ChipStatus {
  if (forcedRunning) return "running";
  const phases = Object.values(take.phases);
  if (phases.some((p) => p.status === "failed")) return "failed";
  if (phases.some((p) => p.status === "running")) return "running";
  if (take.master_output) return "done";
  if (phases.length > 0 && phases.every((p) => p.status === "done")) return "done";
  return "pending";
}

function dotColor(status: ChipStatus): string {
  if (status === "running") return "var(--accent)";
  if (status === "done") return "var(--sage)";
  if (status === "failed") return "var(--bad)";
  return "var(--dim)";
}

function totalDurationMs(take: Take): number {
  return Object.values(take.phases).reduce((sum, p) => sum + (p.duration_ms ?? 0), 0);
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "0:00";
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function elapsedSince(iso: string): number {
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : Math.max(0, Date.now() - t);
}

/** Same trick every other screen in this app uses to surface the server's
 * own refusal sentence (e.g. "cannot delete the last remaining take")
 * instead of the raw "DELETE ... failed: 400 Bad Request - {...}" wrapper. */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

export interface TakeStripProps {
  takes: Take[];
  /** True only until the first `GET .../takes` response lands - not a
   * general spinner condition (see `Studio.tsx`). */
  loading: boolean;
  currentTakeId: number | null;
  selectedTakeId: number | null;
  /** Locally tracked in `Studio.tsx` from the moment `NewTakePanel` reports
   * a take was created until a terminal event for this project arrives -
   * bridges the gap before the take's own `phases` dict has any 'running'
   * entry of its own yet. */
  runningTakeId: number | null;
  onSelect: (id: number) => void;
  onDelete: (id: number) => Promise<void>;
  onRename: (id: number, label: string) => Promise<void>;
  onNewTake: () => void;
}

/**
 * The take strip - takes across the top, under the project header. The
 * second of the two perpendicular axes this screen is built on (phases run
 * down `Studio.tsx`'s own rail).
 *
 * Selecting a chip NEVER calls `POST .../takes/{id}/select` - that pins a
 * take as CURRENT, a distinct and deliberate action (see `avg.core.takes`'
 * own module docstring: "merely viewing a take in the UI never touches this
 * field"). This component only ever changes what the rest of Studio is
 * looking at; `onSelect` is purely local state in the parent.
 *
 * Colour discipline: selection is an elevated surface with a stronger
 * border, never the accent - terracotta is reserved for the take that is
 * actually running (the status dot), so the running take stays findable
 * even while browsing a different one.
 */
export default function TakeStrip({
  takes,
  loading,
  currentTakeId,
  selectedTakeId,
  runningTakeId,
  onSelect,
  onDelete,
  onRename,
  onNewTake,
}: TakeStripProps) {
  // Forces a re-render once a second while anything is running, so the
  // "running · 1:20" elapsed readout actually ticks - the same trick
  // `Home.tsx`'s `useRenderClock` uses for the hero card.
  const [, tick] = useState(0);
  useEffect(() => {
    if (runningTakeId === null) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [runningTakeId]);

  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deleteBusyId, setDeleteBusyId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  function startRename(take: Take) {
    setConfirmDeleteId(null);
    setRenamingId(take.id);
    setRenameValue(take.label);
  }

  async function commitRename(id: number) {
    const label = renameValue.trim();
    if (!label) {
      setRenamingId(null);
      return;
    }
    setRenameBusy(true);
    try {
      await onRename(id, label);
      setRenamingId(null);
    } catch {
      // Left open on failure - the user can see their edit is still there
      // and try again, rather than it silently reverting.
    } finally {
      setRenameBusy(false);
    }
  }

  async function confirmDelete(id: number) {
    setDeleteBusyId(id);
    setDeleteError(null);
    try {
      await onDelete(id);
      setConfirmDeleteId(null);
    } catch (err) {
      // The API refuses to delete the last remaining take - surfaced here
      // rather than hidden, per the brief.
      setDeleteError(extractDetail(err instanceof Error ? err.message : String(err)));
    } finally {
      setDeleteBusyId(null);
    }
  }

  return (
    <div className="take-strip">
      <div className="take-strip__row">
        {loading ? (
          <span className="take-strip__loading">Loading takes…</span>
        ) : (
          takes.map((take) => {
            const status = resolveStatus(take, take.id === runningTakeId);
            const selected = take.id === selectedTakeId;
            const isCurrent = take.id === currentTakeId;

            if (confirmDeleteId === take.id) {
              return (
                <div key={take.id} className="take-chip take-chip--confirm">
                  <p>Delete this take? It cannot be undone.</p>
                  <div className="take-chip__confirm-actions">
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => setConfirmDeleteId(null)}
                      disabled={deleteBusyId === take.id}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="btn btn--danger btn--sm"
                      onClick={() => confirmDelete(take.id)}
                      disabled={deleteBusyId === take.id}
                    >
                      {deleteBusyId === take.id ? "Deleting…" : "Confirm delete"}
                    </button>
                  </div>
                </div>
              );
            }

            return (
              <div
                key={take.id}
                className={`take-chip${selected ? " take-chip--selected" : ""}`}
              >
                {renamingId === take.id ? (
                  <input
                    className="take-chip__rename"
                    value={renameValue}
                    autoFocus
                    disabled={renameBusy}
                    aria-label={`Rename ${take.label}`}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={() => commitRename(take.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename(take.id);
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                  />
                ) : (
                  <button
                    type="button"
                    className="take-chip__main"
                    onClick={() => onSelect(take.id)}
                    onDoubleClick={() => startRename(take)}
                    title="Double-click to rename"
                  >
                    <span className="take-chip__label">
                      {take.label}
                      {isCurrent && <span className="take-chip__current">current</span>}
                    </span>
                    <span className="take-chip__meta mono">
                      <span
                        className={`dot${status === "running" ? " pulse" : ""}`}
                        style={{ background: dotColor(status) }}
                        aria-hidden="true"
                      />
                      {status === "running"
                        ? `running · ${formatDuration(elapsedSince(take.created))}`
                        : formatDuration(totalDurationMs(take))}
                    </span>
                  </button>
                )}
                <button
                  type="button"
                  className="take-chip__delete"
                  onClick={() => {
                    setDeleteError(null);
                    setConfirmDeleteId(take.id);
                  }}
                  aria-label={`Delete ${take.label}`}
                  title="Delete this take"
                >
                  ×
                </button>
              </div>
            );
          })
        )}

        <button type="button" className="btn btn--sm take-strip__new" onClick={onNewTake}>
          <IconPlus size={14} />
          New take
        </button>
      </div>
      {deleteError && <p className="take-strip__error">{deleteError}</p>}
    </div>
  );
}
