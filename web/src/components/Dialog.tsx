import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode, type RefObject } from "react";

type DialogProps = {
  title: string;
  open: boolean;
  onClose: () => void;
  busy: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
};

function focusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(
    'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
  )).filter((element) => !element.hasAttribute("disabled") && element.tabIndex >= 0);
}

export function Dialog({ title, open, onClose, busy, initialFocusRef, children }: DialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    (initialFocusRef?.current ?? (dialogRef.current ? focusableElements(dialogRef.current)[0] : null) ?? dialogRef.current)?.focus();
    return () => previousFocus?.focus();
  }, [initialFocusRef, open]);

  if (!open) return null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (!busy) onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = focusableElements(dialog);
    if (focusable.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable.at(-1)!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return <div className="modal-backdrop">
    <div ref={dialogRef} className="modal card" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} onKeyDown={onKeyDown}>
      <h2 id={titleId}>{title}</h2>
      {children}
    </div>
  </div>;
}
