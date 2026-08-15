# P2 基建：外网通道（VPS + frp + Caddy）采购清单与执行手册

- 日期：2026-08-15
- 前置：P1 已合并 main（`50358cc`），sidecar 监听 `127.0.0.1:19527`
- 目标：手机在外网（4G/异地）经 `https://<域名>` 直达 Mac 大脑；SSE 流式可用
- 性质：纯基建操作，VPS 上**零译宝代码**（哑管道）

## 1. 采购清单

| 项 | 推荐 | 价格参考 | 备注 |
|---|---|---|---|
| VPS | 腾讯云轻量 香港 2核2G（或阿里云轻量香港同档） | ~¥250-300/年 | **香港免备案**，大陆延迟 +30-50ms 可接受；大陆节点便宜一半但绑域名开 443 要 ICP 备案（1-2 周），不推荐为它等 |
| 域名 | 任意注册商，`.top`/`.xyz` 便宜 | ~¥20/年（.com ~¥60） | TLS 必需；DNS 解析加一条 A 记录即可 |
| （后续 P3/P4）Apple 开发者 | Developer Program | $99/年 | iOS 安装+真推送硬前置，**P2 不需要** |

不需要：备案（香港节点）、任何推送服务（P4 才碰）。

## 2. 执行步骤

### 2.1 DNS

注册商控制台加 A 记录：`yibao.<你的域名>` → VPS 公网 IP，TTL 默认。

### 2.2 VPS（Ubuntu 22/24，SSH 登录）

```bash
# 0) 基础
apt update && apt upgrade -y
apt install -y curl ufw

# 1) 防火墙：只开 ssh/http/https（frp 的 7000 是 frpc 连入用，见下；19527 绝不对公网开）
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 7000/tcp && ufw --force enable

# 2) frps（服务端）
curl -LO https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_linux_amd64.tar.gz
tar xzf frp_0.61.1_linux_amd64.tar.gz && cd frp_0.61.1_linux_amd64
install -m755 frps /usr/local/bin/frps
mkdir -p /etc/frp
cat > /etc/frp/frps.toml <<'EOF'
bindPort = 7000
auth.token = "<openssl rand -hex 16 生成，替换>"
EOF
# systemd 常驻
cat > /etc/systemd/system/frps.service <<'EOF'
[Unit]
Description=frps
After=network.target
[Service]
ExecStart=/usr/local/bin/frps -c /etc/frp/frps.toml
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl enable --now frps

# 3) Caddy（自动 TLS）
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
cat > /etc/caddy/Caddyfile <<'EOF'
yibao.<你的域名> {
    reverse_proxy 127.0.0.1:19527
}
EOF
systemctl reload caddy
```

注意：
- frp 的 tcp 代理会在 VPS 全接口监听 19527，但 ufw 未放行该端口 → 外部不可达，仅本机 Caddy 能 `127.0.0.1:19527` 访问。**验证这点（见 4）**
- 版本号以 frp GitHub Releases 当时最新为准

### 2.3 Mac（frpc 客户端）

```bash
brew install frp
mkdir -p /usr/local/etc/frp /opt/frpc-log 2>/dev/null
cat > /opt/homebrew/etc/frp/frpc.toml <<'EOF'
serverAddr = "<VPS 公网 IP>"
serverPort = 7000
auth.token = "<与 frps 相同>"
transport.tls.enable = true

[[proxies]]
name = "yibao-http"
type = "tcp"
localIP = "127.0.0.1"
localPort = 19527
remotePort = 19527
EOF
```

常驻（launchd，比 brew services 可控——brew 的 frp service 默认跑 frps）：

```bash
cat > ~/Library/LaunchAgents/com.yibao.frpc.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.yibao.frpc</string>
  <key>ProgramArguments</key><array>
    <string>/opt/homebrew/bin/frpc</string>
    <string>-c</string><string>/opt/homebrew/etc/frp/frpc.toml</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>/tmp/frpc.err.log</string>
</dict></plist>
EOF
launchctl load ~/Library/LaunchAgents/com.yibao.frpc.plist
```

（Apple Silicon 的 brew 前缀是 `/opt/homebrew`；Intel 是 `/usr/local`，按机器改两处。）

### 2.4 验证（P2 完成定义）

1. **VPS 本机**：`curl -s -H "X-Yibao-Token: <mtok>" http://127.0.0.1:19527/v1/health` → ok（frpc↔frps 隧道通 + Mac sidecar 在跑）
2. **Mac 本机走域名**：`curl -s -H "X-Yibao-Token: <mtok>" https://yibao.<域名>/v1/health` → ok 且证书有效（`-v` 看 Let's Encrypt）
3. **手机 4G（关 WiFi）**：Safari 打开 `https://yibao.<域名>/v1/events?token=<mtok>`——桌面发条消息（或 Mac 上 curl POST /v1/chat），手机页面应看到 `id:`/`event:` 帧持续追加 = **外网 SSE 流式通**
4. **安全面**：手机 4G 下 `https://yibao.<域名>:19527/` 连不通（frp 端口未暴露）；无 token 打 `/v1/health` → 401
5. **SSE 经 Caddy 不缓冲**：验证 3 里帧是实时逐条出现而非攒一坨——若攒坨，Caddyfile 的 reverse_proxy 加 `flush_interval -1`

mtok 获取：`python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/Library/Application Support/yibao/settings.json')))['http.mobile_token'])"`

## 3. 已知边界（spec §9）

- Mac 合盖休眠 → 大脑离线，隧道空转（App 侧表现为 502/连不上）。v1 接受；缓解=系统设置防休眠或 `caffeinate`
- VPS 宕只影响数据面，不影响推送面（P4 的推送是 Mac 直发极光）

## 4. 完成后

P3（Capacitor 工程骨架 + 配对 + 对话）开工前注意 spec §14 两条 P3 风险：iOS WKWebView 的 CORS 大概率要补头（坏 JSON 400→500 的旧桥语义回退也一并看）；跨 surface 排队窗口内手机 interrupt 连环杀的收紧。
