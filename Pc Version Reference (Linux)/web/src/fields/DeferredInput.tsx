import { useEffect, useState } from "react";

/**
 * A text-shaped input (text/path/int/float all use this) that keeps its own
 * keystroke-by-keystroke draft and only calls `onCommit` on blur or Enter -
 * committing on every keystroke would mark the field dirty and re-render
 * the whole settings screen per character, and would fight a user who is
 * still typing a number. Re-syncs its local draft whenever `value` changes
 * from outside (e.g. after Save, or Discard resets the draft).
 *
 * Not itself one of the per-type field renderers registered in
 * `fields/index.tsx` - it is the shared input primitive that `TextField`,
 * `PathField`, `IntField` and `FloatField` each wrap with their own
 * parsing.
 */
export function DeferredInput({
  value,
  onCommit,
  disabled,
  className = "",
  inputMode,
  placeholder,
}: {
  value: string;
  onCommit: (raw: string) => void;
  disabled?: boolean;
  className?: string;
  inputMode?: "text" | "numeric" | "decimal";
  placeholder?: string;
}) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  function commit() {
    if (draft !== value) onCommit(draft);
  }

  return (
    <input
      className={`field-input ${className}`.trim()}
      value={draft}
      disabled={disabled}
      inputMode={inputMode}
      placeholder={placeholder}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setDraft(value);
      }}
    />
  );
}
