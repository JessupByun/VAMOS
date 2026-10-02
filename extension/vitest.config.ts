import { defineConfig } from "vitest/config";

// Unit tests only; the extension build config (CRXJS) is not needed here.
export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
  },
});
