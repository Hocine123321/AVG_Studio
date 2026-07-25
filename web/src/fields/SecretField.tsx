import { useState } from "react";
import type { FieldProps } from "./types";

/**
 * A provider API key. `ctx.keys[spec.id]` is a boolean - "is a key set" -
 * never the key itself; that boolean is all this component, or any of its
 * state, ever holds about a key that already exists. The only place a real
 * key value exists in this tree is `draft`, and only for as long as it
 * takes to fire the request: it is cleared in `submit()` before the
 * request even resolves, whether it succeeds or fails, so it never lingers
 * in memory and is never sent anywhere except `ctx.onSetKey` (which POSTs
 * straight to `/api/keys/{provider}` - see `Settings.tsx`). Nothing here
 * ever pre-fills from a loaded value, because there is no loaded value to
 * pre-fill from.
 */
export default function SecretField({ spec, ctx }: FieldProps) {
  const provider = spec.id;
  const isSet = Boolean(ctx.keys[provider]);
  const busy = Boolean(ctx.keyBusy[provider]);
  const error = ctx.keyErrors[provider] ?? null;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  function submit() {
    const key = draft.trim();
    setDraft("");
    setEditing(false);
    if (key) ctx.onSetKey(provider, key);
  }

  function cancelEdit() {
    setDraft("");
    setEditing(false);
  }

  return (
    <div className="secret-field">
      <span className={`secret-field__status${isSet ? " secret-field__status--set" : ""}`}>
        {isSet ? "Set" : "Not set"}
      </span>

      {editing ? (
        <>
          <input
            type="password"
            autoComplete="off"
            className="field-input secret-field__input"
            placeholder="Paste new key"
            aria-label={`New ${spec.label}`}
            value={draft}
            disabled={busy}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
              if (e.key === "Escape") cancelEdit();
            }}
          />
          <button type="button" className="btn btn--sm" onClick={submit} disabled={busy || !draft.trim()}>
            {busy ? "Saving…" : "Save key"}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={cancelEdit} disabled={busy}>
            Cancel
          </button>
        </>
      ) : (
        <>
          <button type="button" className="btn btn--sm" onClick={() => setEditing(true)} disabled={busy}>
            {isSet ? "Replace" : "Set key"}
          </button>
          {isSet && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => ctx.onDeleteKey(provider)}
              disabled={busy}
            >
              Remove
            </button>
          )}
        </>
      )}

      {error && <p className="secret-field__error">{error}</p>}
    </div>
  );
}
