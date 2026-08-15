/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  clearScreen: false,
  server: { port: 5173, strictPort: true, host: true }, // host: 局域网体验——手机浏览器经 http://<Mac内网IP>:5173 访问
  test: { environment: "jsdom" },
});
