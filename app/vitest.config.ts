import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: "node",
    include: ["src/state/**/*.test.ts", "src/lib/**/*.test.ts", "src/components/**/*.test.ts"],
  },
});
