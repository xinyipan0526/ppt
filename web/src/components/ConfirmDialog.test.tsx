import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

it("requires the acknowledgement before confirming a risky action", () => {
  const onConfirm = vi.fn();
  render(<ConfirmDialog
    open
    title="确认重复计费风险"
    confirmLabel="授权重试"
    acknowledgement="我已在 Provider 控制台核对结果"
    busy={false}
    onClose={vi.fn()}
    onConfirm={onConfirm}
  ><p>原请求可能已经计费。</p></ConfirmDialog>);

  const confirm = screen.getByRole("button", { name: "授权重试" });
  expect(confirm).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "我已在 Provider 控制台核对结果" }));
  fireEvent.click(confirm);
  expect(onConfirm).toHaveBeenCalledOnce();
});
