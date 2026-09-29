import { useEffect, useMemo, useState } from "react";
import {
  activateChannel,
  createChannel,
  deleteChannel,
  getChannels,
  updateChannel,
  type ChannelDoc,
} from "./api";
import { IconPlus } from "./Icons";

/** Same trick `Settings.tsx` and `App.tsx` use to surface the server's own
 * refusal sentence instead of the raw "POST ... failed: 4xx - {...}" wrapper. */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

interface EditDraft {
  id: string;
  label: string;
  note: string;
  busy: boolean;
  error: string | null;
}

/**
 * The channels screen: create, rename, delete and switch the active
 * channel — the scope that owns every `scope="channel"` creative setting
 * (see `Settings.tsx`, which reads the very same `active_channel_id` this
 * screen writes via `POST /api/channels/{id}/activate`).
 *
 * Three things this screen is built around:
 *
 * 1. Deletion is final and says so. `avg.config.channels` deliberately
 *    removed the legacy app's "re-seed missing profiles on launch"
 *    mechanism, so a delete here is not recoverable by relaunching — the
 *    inline confirmation step exists because of that, not as decoration.
 * 2. No channel active is an ordinary, supported state, not an error —
 *    the banner below says so in the same words `Settings.tsx` uses for
 *    its "No active channel" chip, and nothing on this screen is disabled
 *    because of it.
 * 3. "Set active" is a plain `.btn`, never `.btn--primary` — the one
 *    terracotta action on this screen is "New channel". An active channel
 *    reads as settled via the sage "Active" chip, not via the accent.
 */
export default function Channels() {
  const [channels, setChannels] = useState<ChannelDoc[] | null>(null);
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [createLabel, setCreateLabel] = useState("");
  const [createNote, setCreateNote] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [edit, setEdit] = useState<EditDraft | null>(null);

  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleteBusyId, setDeleteBusyId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [activateBusyId, setActivateBusyId] = useState<string | null>(null);
  const [activateError, setActivateError] = useState<string | null>(null);

  function load() {
    getChannels()
      .then((res) => {
        setChannels(res.channels);
        setActiveChannelId(res.active_channel_id);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }

  useEffect(load, []);

  const sorted = useMemo(
    () => (channels ? [...channels].sort((a, b) => a.label.localeCompare(b.label)) : []),
    [channels],
  );

  function openCreate() {
    setCreating(true);
    setCreateLabel("");
    setCreateNote("");
    setCreateError(null);
  }

  async function handleCreate() {
    const label = createLabel.trim();
    if (!label) return;
    setCreateBusy(true);
    setCreateError(null);
    try {
      await createChannel(label, createNote.trim());
      setCreating(false);
      load();
    } catch (err) {
      setCreateError(extractDetail(err instanceof Error ? err.message : String(err)));
    } finally {
      setCreateBusy(false);
    }
  }

  function startEdit(channel: ChannelDoc) {
    setConfirmDeleteId(null);
    setEdit({ id: channel.id, label: channel.label, note: channel.note, busy: false, error: null });
  }

  async function handleSaveEdit() {
    if (!edit) return;
    const label = edit.label.trim();
    if (!label) return;
    setEdit({ ...edit, busy: true, error: null });
    try {
      await updateChannel(edit.id, { label, note: edit.note });
      setEdit(null);
      load();
    } catch (err) {
      setEdit((prev) =>
        prev ? { ...prev, busy: false, error: extractDetail(err instanceof Error ? err.message : String(err)) } : prev,
      );
    }
  }

  async function handleDelete(id: string) {
    setDeleteBusyId(id);
    setDeleteError(null);
    try {
      await deleteChannel(id);
      setConfirmDeleteId(null);
      load();
    } catch (err) {
      setDeleteError(extractDetail(err instanceof Error ? err.message : String(err)));
    } finally {
      setDeleteBusyId(null);
    }
  }

  async function handleActivate(id: string) {
    setActivateBusyId(id);
    setActivateError(null);
    try {
      await activateChannel(id);
      load();
    } catch (err) {
      setActivateError(extractDetail(err instanceof Error ? err.message : String(err)));
    } finally {
      setActivateBusyId(null);
    }
  }

  if (loadError) {
    return (
      <div className="channels">
        <p className="home__empty">Could not load channels: {loadError}</p>
      </div>
    );
  }

  if (!channels) {
    return (
      <div className="channels">
        <p className="home__empty">Loading…</p>
      </div>
    );
  }

  return (
    <div className="channels">
      <header className="settings__header">
        <div>
          <div className="home__kicker">Manage</div>
          <h1 className="home__title">Channels</h1>
        </div>
        <span className="home__spacer" />
        <button type="button" className="btn btn--primary" onClick={openCreate} disabled={creating}>
          <IconPlus size={16} />
          New channel
        </button>
      </header>

      <p className="channels__note">
        {activeChannelId === null
          ? "No channel is active. The app still works — app-level settings apply as usual — but channel-scoped creative settings are unavailable until you activate one below."
          : "Channel-scoped creative settings on the Settings screen always apply to whichever channel is active here."}
      </p>

      {activateError && <p className="channels__error">{activateError}</p>}
      {deleteError && <p className="channels__error">{deleteError}</p>}

      <div className="channel-grid">
        {creating && (
          <div className="channel-card channel-card--form">
            <div className="channel-form">
              <input
                className="field-input"
                placeholder="Channel name"
                aria-label="New channel name"
                value={createLabel}
                autoFocus
                onChange={(e) => setCreateLabel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreate();
                  if (e.key === "Escape") setCreating(false);
                }}
              />
              <input
                className="field-input"
                placeholder="Note (optional)"
                aria-label="New channel note"
                value={createNote}
                onChange={(e) => setCreateNote(e.target.value)}
              />
              {createError && <p className="channels__error">{createError}</p>}
              <div className="channel-card__actions">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setCreating(false)}
                  disabled={createBusy}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  onClick={handleCreate}
                  disabled={createBusy || !createLabel.trim()}
                >
                  {createBusy ? "Creating…" : "Create"}
                </button>
              </div>
            </div>
          </div>
        )}

        {sorted.map((channel) => {
          const isActive = channel.id === activeChannelId;
          const pinned = Object.keys(channel.settings).length;

          if (edit?.id === channel.id) {
            return (
              <div key={channel.id} className="channel-card channel-card--form">
                <div className="channel-form">
                  <input
                    className="field-input"
                    aria-label="Channel name"
                    value={edit.label}
                    autoFocus
                    onChange={(e) => setEdit({ ...edit, label: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveEdit();
                      if (e.key === "Escape") setEdit(null);
                    }}
                  />
                  <input
                    className="field-input"
                    placeholder="Note (optional)"
                    aria-label="Channel note"
                    value={edit.note}
                    onChange={(e) => setEdit({ ...edit, note: e.target.value })}
                  />
                  {edit.error && <p className="channels__error">{edit.error}</p>}
                  <div className="channel-card__actions">
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => setEdit(null)}
                      disabled={edit.busy}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="btn btn--primary btn--sm"
                      onClick={handleSaveEdit}
                      disabled={edit.busy || !edit.label.trim()}
                    >
                      {edit.busy ? "Saving…" : "Save"}
                    </button>
                  </div>
                </div>
              </div>
            );
          }

          return (
            <div key={channel.id} className={`channel-card${isActive ? " channel-card--active" : ""}`}>
              <div className="channel-card__head">
                <h3 className="channel-card__label" dir="auto" title={channel.label}>
                  {channel.label}
                </h3>
                {isActive && <span className="chip chip--ok">Active</span>}
              </div>

              <p className="channel-card__note">{channel.note || "No note."}</p>

              <p className="channel-card__meta">
                {pinned} setting{pinned === 1 ? "" : "s"} pinned
              </p>

              {confirmDeleteId === channel.id ? (
                <div className="channel-card__confirm">
                  <p>Delete this channel? Its pinned settings go with it and it will not come back.</p>
                  <div className="channel-card__actions">
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => setConfirmDeleteId(null)}
                      disabled={deleteBusyId === channel.id}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="btn btn--danger btn--sm"
                      onClick={() => handleDelete(channel.id)}
                      disabled={deleteBusyId === channel.id}
                    >
                      {deleteBusyId === channel.id ? "Deleting…" : "Confirm delete"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="channel-card__actions">
                  {!isActive && (
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => handleActivate(channel.id)}
                      disabled={activateBusyId === channel.id}
                    >
                      {activateBusyId === channel.id ? "Activating…" : "Set active"}
                    </button>
                  )}
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => startEdit(channel)}>
                    Rename
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      setDeleteError(null);
                      setConfirmDeleteId(channel.id);
                    }}
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {sorted.length === 0 && !creating && <p className="home__empty">No channels yet.</p>}
      </div>
    </div>
  );
}
