import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

// plugins: [vue] —— SFC 编译(App.mount.test.ts 等组件挂载用例);纯逻辑用例不受影响
export default defineConfig({
  plugins: [vue()],
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
