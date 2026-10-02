import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 60_000 },
  fullyParallel: false,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: "http://localhost:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "powershell.exe -NoProfile -Command \"$env:PYTHONPATH='../src'; & '../.venv/Scripts/python.exe' -m ppt_agent.cli --workspace '../.cache/playwright-workspace' --port 8765 serve --no-open\"",
    url: "http://localhost:8765/api/v1/health",
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
