import { useEffect, useId, useRef, useState } from "react";

/**
 * The app's own select. Replaces the native `<select>`, whose dropdown list
 * is drawn by Windows itself and cannot be styled: it arrived in the OS grey,
 * in the OS font, with square corners, in the middle of a warm rounded
 * Caprasimo/Figtree interface. Everything else in this app is themed; the one
 * control that ignored the theme read as a bug.
 *
 * Deliberately hand-rolled rather than pulled from a component library - the
 * app has no form-widget dependency and adding one for this would be a
 * framework layer nobody asked for.
 *
 * What it keeps from the native control, because losing these is how custom
 * selects usually go wrong:
 *
 *   - It is a real `<button>` with `aria-haspopup="listbox"`, and the list is
 *     a real `role="listbox"` of `role="option"`s, so a screen reader gets
 *     the same shape it would from a `<select>`.
 *   - Full keyboard control: Up/Down move, Home/End jump, Enter/Space commit,
 *     Escape closes and returns focus to the button, Tab closes.
 *   - Typing a letter jumps to the next option starting with it.
 *   - Closes on outside click and on scroll of any ancestor.
 *
 * The open list is absolutely positioned inside the control, NOT portalled:
 * every use in this app is inside a normally-flowing panel, and a portal
 * would add layer management for no gain here.
 */
export interface SelectOption {
  value: string;
  label?: string;
}

export interface SelectProps {
  value: string;
  options: readonly (string | SelectOption)[];
  onChange: (value: string) => void;
  disabled?: boolean;
  /** Accessible name. Use when there is no visible `<label>` tied to this. */
  ariaLabel?: string;
  id?: string;
}

function normalise(option: string | SelectOption): SelectOption {
  return typeof option === "string" ? { value: option, label: option } : { label: option.value, ...option };
}

export default function Select({ value, options, onChange, disabled, ariaLabel, id }: SelectProps) {
  const items = options.map(normalise);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(() => Math.max(0, items.findIndex((o) => o.value === value)));
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const listId = useId();

  const selected = items.find((o) => o.value === value);

  // Outside click and ancestor scroll both close it. Scroll matters because
  // the list is positioned relative to the control: if the page moves under
  // an open list, the list would otherwise be left pointing at nothing.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onScroll(event: Event) {
      if (rootRef.current && event.target instanceof Node && event.target.contains(rootRef.current)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  useEffect(() => {
    if (open) setActiveIndex(Math.max(0, items.findIndex((o) => o.value === value)));
    // Only when the list opens - re-syncing on every render would fight the
    // arrow keys, which move the active option without committing it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function commit(index: number) {
    const option = items[index];
    if (!option) return;
    onChange(option.value);
    setOpen(false);
    buttonRef.current?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (disabled) return;

    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        setOpen(true);
      }
      return;
    }

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, items.length - 1));
        return;
      case "ArrowUp":
        event.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
        return;
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        return;
      case "End":
        event.preventDefault();
        setActiveIndex(items.length - 1);
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        commit(activeIndex);
        return;
      case "Escape":
        event.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
        return;
      case "Tab":
        setOpen(false);
        return;
      default:
        break;
    }

    // Type-ahead: a single printable character jumps to the next option
    // starting with it, wrapping - the one native behaviour people miss
    // most in a hand-rolled select.
    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      const needle = event.key.toLowerCase();
      const from = activeIndex + 1;
      const order = [...items.slice(from), ...items.slice(0, from)];
      const hit = order.find((o) => (o.label ?? o.value).toLowerCase().startsWith(needle));
      if (hit) setActiveIndex(items.indexOf(hit));
    }
  }

  return (
    <div className={`sel${open ? " sel--open" : ""}`} ref={rootRef}>
      <button
        type="button"
        id={id}
        ref={buttonRef}
        className="sel__button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyDown}
      >
        <span className="sel__value">{selected?.label ?? selected?.value ?? value}</span>
        <span className="sel__caret" aria-hidden="true" />
      </button>

      {open && (
        <ul className="sel__list" id={listId} role="listbox" tabIndex={-1} aria-label={ariaLabel}>
          {items.map((option, index) => (
            <li
              key={option.value}
              role="option"
              aria-selected={option.value === value}
              className={`sel__option${index === activeIndex ? " sel__option--active" : ""}${
                option.value === value ? " sel__option--on" : ""
              }`}
              // mousedown, not click: the document-level mousedown listener
              // above would otherwise close the list before click fired.
              onMouseDown={(e) => {
                e.preventDefault();
                commit(index);
              }}
              onMouseEnter={() => setActiveIndex(index)}
            >
              {option.label ?? option.value}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
