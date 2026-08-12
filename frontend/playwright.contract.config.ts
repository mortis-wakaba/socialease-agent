import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "conversation-stream-contract.spec.ts",
  reporter: [["list"]]
});
