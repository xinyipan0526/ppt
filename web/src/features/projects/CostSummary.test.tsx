import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { CostSummary } from "./CostSummary";

it("shows each currency independently with settled, reserved, unknown, and available amounts", () => {
  render(<CostSummary costs={{ project_id: "p1", totals: [], currencies: [
    { currency: "CNY", hard_limit: 5000, settled: 1020, active_reserved: 480, held_unknown: 130, available: 3370 },
    { currency: "USD", hard_limit: 2000, settled: 250, active_reserved: 50, held_unknown: 25, available: 1675 },
  ] }} />);

  expect(screen.getByText("CNY")).toBeInTheDocument();
  expect(screen.getByText("¥10.20")).toBeInTheDocument();
  expect(screen.getByText("¥1.30")).toBeInTheDocument();
  expect(screen.getByText("USD")).toBeInTheDocument();
  expect(screen.getByText("US$16.75")).toBeInTheDocument();
  expect(screen.queryByText(/换算/)).not.toBeInTheDocument();
});

it("renders an explicit empty state instead of inventing a budget", () => {
  render(<CostSummary costs={{ project_id: "p1", totals: [], currencies: [] }} />);
  expect(screen.getByText("尚未设置项目预算")).toBeInTheDocument();
});
