// 插件面板共享构建:node build.mjs <plugin_id>
// 把 plugins/<pid>/panel(index.html 入口的多文件工程)构建到 plugins/<pid>/panel/dist。
// vue 外置:产物里保留 `from "vue"` 裸导入,运行时由宿主 importmap 指到共享 runtime
// (yibao-plugin://<pid>/__yibao__/vue.esm-browser.js,见 plugin_proto.rs)——插件 bundle 不打 Vue。
import { build } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath } from "node:url";
import path from "node:path";

const pid = process.argv[2];
if (!pid || !/^[a-z0-9_-]+$/.test(pid)) {
  console.error("用法: node build.mjs <plugin_id>");
  process.exit(1);
}
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../plugins", pid, "panel");

await build({
  root,
  base: "./", // 相对资源路径:yibao-plugin://<pid>/panel/dist/index.html 下直取
  plugins: [vue()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: { external: ["vue"] },
  },
});
console.log(`[panel-build] ${pid} → plugins/${pid}/panel/dist`);
