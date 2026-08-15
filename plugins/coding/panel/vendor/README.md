# vendor 第三方库（chat.html 渲染基座）

CSP `script-src 'unsafe-inline'` 下所有 JS 必须内联：chat.html 通过
`<!--inject:vendor/xxx.min.js-->` 占位注释由 sidecar（`plugins.py:_inline_vendor`）
在加载面板时替换为本目录文件内容。升级时重新 `npm pack` 取同路径文件覆盖，
并同步更新下表版本。

| 文件 | 库 | 版本 | 来源（npm 包 → 包内路径） | 许可证 | 用途 |
| --- | --- | --- | --- | --- | --- |
| marked.min.js | marked | 18.0.9 | `marked@18.0.9` → `lib/marked.umd.js`（官方已压缩 UMD，许可证头保留） | MIT | AI 气泡 markdown（GFM）解析 |
| dompurify.min.js | DOMPurify | 3.4.13 | `dompurify@3.4.13` → `dist/purify.min.js` | Apache-2.0 / MPL-2.0 双许可 | markdown 渲染后 HTML 消毒 |
| highlight.min.js | highlight.js | 11.12.0 | `@highlightjs/cdn-assets@11.12.0` → `highlight.min.js`（`highlight.js` 包内无压缩浏览器构建，CDN 包为官方等价物，common 语言集 36 种） | BSD-3-Clause | 代码块语法高亮 |

各文件在浏览器全局分别挂载 `marked` / `DOMPurify` / `hljs`（已用 node vm 裸
sandbox 验证）。注意：DOMPurify 在无 DOM 环境（裸 sandbox）下 `isSupported=false`
且不挂 `sanitize`，真实 webview 中有 DOM 才可用——属预期行为。
