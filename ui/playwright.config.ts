import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:7799", channel: "chrome", headless: true, trace: "retain-on-failure" },
  webServer: { command: "bash e2e/start.sh", url: "http://127.0.0.1:7799/health", reuseExistingServer: false, timeout: 60_000 },
});
