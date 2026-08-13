import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/state/**/*.test.ts", "src/lib/**/*.test.ts"],
  },
});
