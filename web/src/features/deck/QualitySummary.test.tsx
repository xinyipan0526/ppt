import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { QualitySummary } from "./QualitySummary";

it("distinguishes passed, warning, failed, and pending quality states", () => {
  const { rerender } = render(<QualitySummary quality={null} />);
  expect(screen.getByText("质检尚未完成")).toBeInTheDocument();

  rerender(<QualitySummary quality={{ passed: true, issues: [] }} />);
  expect(screen.getByText("质检通过")).toBeInTheDocument();

  rerender(<QualitySummary quality={{ passed: true, issues: [{ code: "low_contrast", severity: "warning", summary: "对比度偏低" }] }} />);
  expect(screen.getByText("质检有警告")).toBeInTheDocument();
  expect(screen.getByText("对比度偏低")).toBeInTheDocument();

  rerender(<QualitySummary quality={{ passed: false, issues: [{ code: "layout_overflow", severity: "error", summary: "正文溢出" }] }} />);
  expect(screen.getByText("质检未通过")).toBeInTheDocument();
  expect(screen.getByText("正文溢出")).toBeInTheDocument();
});
