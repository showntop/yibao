/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import mkcert from "vite-plugin-mkcert";

// vitest 下不挂 mkcert：单测不碰 TLS/钥匙串（插件首跑会执行 mkcert -install 装根证书）。
// 经 globalThis 探测，避免为此引 @types/node（M1 新增依赖仅限 marked/dompurify/mkcert 三件）。
const isVitest = !!(globalThis as { process?: { env?: { VITEST?: string } } }).process?.env?.VITEST;

export default defineConfig({
  plugins: [vue(), ...(isVitest ? [] : [mkcert()])], // mkcert: 本地 CA 签发证书，dev 面出 https——安全上下文（clipboard/randomUUID）+ PWA 加主屏前提
  clearScreen: false,
  // host: 局域网体验——手机浏览器经 https://<Mac内网IP>:5173 访问（证书含内网 IP，手机装一次 rootCA 即无红锁）
  // proxy: sidecar(aiohttp) 无 TLS，https 页面页内直连 http://…:19527 撞 WebKit mixed content
  // 拦截（"Load failed"）——浏览器态 apiBase 走同源，经此代理到本机 sidecar（SSE 流式可透传）
  server: { port: 5173, strictPort: true, host: true, proxy: { "/v1": "http://127.0.0.1:19527" } },
  test: { environment: "jsdom" },
});
