import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";
import { expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";

function DialogHarness({ busy = false, onClose = vi.fn(), withInitialFocus = true }: { busy?: boolean; onClose?: () => void; withInitialFocus?: boolean }) {
  const [open, setOpen] = useState(false);
  const initialFocusRef = useRef<HTMLInputElement>(null);
  const close = () => {
    onClose();
    setOpen(false);
  };
  return <>
    <button onClick={() => setOpen(true)}>打开对话框</button>
    <Dialog title="测试对话框" open={open} onClose={close} busy={busy} {...(withInitialFocus ? { initialFocusRef } : {})}>
      <input ref={initialFocusRef} aria-label="名称" />
      <button>中间操作</button>
      <button>最后操作</button>
    </Dialog>
  </>;
}

it("labels the dialog, traps tab focus, and restores focus after Escape closes it", async () => {
  render(<DialogHarness />);
  const opener = screen.getByRole("button", { name: "打开对话框" });
  opener.focus();
  fireEvent.click(opener);

  const dialog = screen.getByRole("dialog", { name: "测试对话框" });
  const heading = screen.getByRole("heading", { name: "测试对话框" });
  expect(dialog).toHaveAttribute("aria-labelledby", heading.id);
  await waitFor(() => expect(screen.getByRole("textbox", { name: "名称" })).toHaveFocus());

  const first = screen.getByRole("textbox", { name: "名称" });
  const last = screen.getByRole("button", { name: "最后操作" });
  last.focus();
  fireEvent.keyDown(dialog, { key: "Tab" });
  expect(first).toHaveFocus();
  fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
  expect(last).toHaveFocus();

  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(opener).toHaveFocus();
});

it("does not close a busy dialog with Escape", async () => {
  const onClose = vi.fn();
  render(<DialogHarness busy onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: "打开对话框" }));
  const dialog = await screen.findByRole("dialog");

  fireEvent.keyDown(dialog, { key: "Escape" });

  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("focuses and traps from the first control when no initial focus ref is supplied", async () => {
  render(<DialogHarness withInitialFocus={false} />);
  fireEvent.click(screen.getByRole("button", { name: "打开对话框" }));

  const dialog = await screen.findByRole("dialog");
  const first = screen.getByRole("textbox", { name: "名称" });
  const last = screen.getByRole("button", { name: "最后操作" });
  expect(first).toHaveFocus();

  fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
  expect(last).toHaveFocus();
});
