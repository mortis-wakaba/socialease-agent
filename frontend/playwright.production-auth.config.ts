import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "production-auth.spec.ts",
  timeout: 30_000,
  expect: {
    timeout: 5_000
  },
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3001",
    trace: "retain-on-failure"
  },
  webServer: {
    command:
      "NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=production NEXT_PUBLIC_SOCIALEASE_TOKEN_STORAGE=localStorage NEXT_PUBLIC_SOCIALEASE_SHOW_TRACE=true npm run dev -- --hostname 127.0.0.1 --port 3001",
    url: "http://127.0.0.1:3001",
    reuseExistingServer:
      process.env.CI !== "true" && process.env.PLAYWRIGHT_REUSE_SERVER !== "false",
    timeout: 120_000
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ]
});
