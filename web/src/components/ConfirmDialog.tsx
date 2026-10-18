import { useEffect, useState, type ReactNode } from "react";
import { Dialog } from "./Dialog";

type ConfirmDialogProps = { open: boolean; title: string; confirmLabel: string; acknowledgement?: string; danger?: boolean; busy: boolean; onClose: () => void; onConfirm: () => void; children: ReactNode };

export function ConfirmDialog({ open, title, confirmLabel, acknowledgement, danger = false, busy, onClose, onConfirm, children }: ConfirmDialogProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  useEffect(() => { if (!open) setAcknowledged(false); }, [open]);
  return <Dialog title={title} open={open} onClose={onClose} busy={busy}>
    <div className="dialog-body">{children}</div>
    {acknowledgement && <label className="choice"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} /> {acknowledgement}</label>}
    <div className="action-row"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="button" className={`button ${danger ? "danger" : "primary"}`} disabled={busy || Boolean(acknowledgement && !acknowledged)} onClick={onConfirm}>{confirmLabel}</button></div>
  </Dialog>;
}
