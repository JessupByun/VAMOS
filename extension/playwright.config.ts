import { tmpdir } from "node:os";
import { join } from "node:path";

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  // Test output stays out of the repo; Playwright empties it at the start of each run.
  outputDir: join(tmpdir(), "vamos-playwright"),
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
});
