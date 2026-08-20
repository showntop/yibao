// node 内置模块的最小类型垫片:工程无 @types/node 依赖(纯前端面板),
// 仅 style.dark.test.ts 需要 fs 读 style.css 本体(?raw 导入会被 vitest CSS 管线截成空串)。
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
declare module "node:url" {
  export function fileURLToPath(url: string | URL): string;
}
