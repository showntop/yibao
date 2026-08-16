# 译宝手机伴生端（mobile）

局域网内的手机浏览器伴侣面：语音翻译会话、多会话历史、远程审批。Vite + Vue 3，dev 端口 **5173**（strictPort），`vite-plugin-mkcert` 出 **HTTPS**（安全上下文 + PWA 加主屏的前提）。

要求 **Node ≥ 22.19**（依赖链下限；`node -v` 自查，低了 pnpm install 会报 engine 警告或起不来）。

```bash
pnpm install
pnpm dev     # 首跑会生成/复用本地 CA（见下节），随后监听 https://<Mac内网IP>:5173
pnpm test    # vitest（jsdom）
pnpm build
```

## HTTPS 与 mkcert 根证书（每台手机一次）

`pnpm dev` 首跑时 vite-plugin-mkcert 用 mkcert 为 `localhost` + 本机所有 IP（含内网 IP）现签服务证书，根证书与服务证书都落在 `~/.vite-plugin-mkcert/`（`rootCA.pem` 可发给手机装）。手机不装根证书也能打开，但有红锁警告、且非安全上下文（clipboard 等受限）；装一次后无警告且可加主屏。

macOS 上首跑/换网络导致 IP 变化时会触发重签，插件执行 `mkcert -install` 经 **sudo** 把根证书写进系统信任库——**在真终端里跑，会要一次开机密码**（无 TTY 的环境会启动失败）。IP 不变则复用既有证书，零密码零网络。本机 mkcert 二进制来自 Homebrew（`brew install mkcert`）；插件自带的 GitHub/coding 下载源在本网络不可达。

1. 把根证书传到手机：`~/.vite-plugin-mkcert/rootCA.pem` 经 AirDrop / 微信文件 / 自建 http 链接发过去。
2. **iOS**：点开 `.pem` → 允许下载描述文件 → 设置 → 通用 → VPN与设备管理 → 安装「mkcert」描述文件 → 再到 设置 → 通用 → 关于本机 → **证书信任设置** → 勾选 mkcert 根证书（不勾等于没装）。
3. **Android**：设置 → 安全 → 加密与凭据 → 安装证书 → **CA 证书**（各厂商路径略有差异，搜「安装证书」即可）→ 选中 rootCA.pem；Chrome 里证书警告可点「继续访问」。

> 该根证书只给本机开发用；换 Mac、删过 `~/.vite-plugin-mkcert/` 后重新生成时，手机需要重装新证书。

## 加到主屏（PWA）

manifest（`public/manifest.webmanifest`）+ 图标已就位，HTTPS 下浏览器才认：

- **iOS Safari**：分享 → 添加到主屏幕 → 之后从主屏图标打开即为全屏独立窗口（无地址栏）。
- **Android Chrome**：菜单 → 添加到主屏幕 / 安装应用。

## 局域网配对（连到桌面译宝）

前提：手机与 Mac 同一 Wi-Fi，桌面译宝已运行（大脑在线）。

1. Mac 上打开译宝 → **设置 → 手机伴生端 → 配对二维码**（无内网 IP 时二维码隐藏，检查 Wi-Fi）。
2. 手机扫码 → 打开 `https://<Mac内网IP>:5173/#/pairing`，host（桌面 sidecar 地址，仍走 http）与 token 已预填 → 确认即配对。
3. 配对后即可收推送、翻译、审批；token 在设置页可热重置，重置后旧手机自动失效，需重扫。

常见问题：

- 打不开页面：Mac 防火墙放行 5173；确认手机用的是 `https://`（dev 面已是 TLS，手敲 `http://` 打不开）。
- 配对页报「无法连接大脑」：sidecar（`host` 参数指向的 `http://<内网IP>:<端口>`）未起或端口变了，回设置页重扫。
