import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";

it("renders the Chinese project workspace shell", () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>
    </QueryClientProvider>
  );
  expect(screen.getByText("PPT Agent")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "项目" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "模型设置" })).toBeInTheDocument();
});
