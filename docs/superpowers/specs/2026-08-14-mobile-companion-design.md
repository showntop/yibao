# 译宝手机伴生端（Mobile Companion）设计

- 日期：2026-08-14
- 状态：待用户评审
- 决策记录：拓扑=伴生前端（大脑仍在 Mac）；通道=国内 VPS + frp + Caddy（哑管道）；平台=iOS+安卓双端；栈=Capacitor + Vue；v1 功能=流式对话 + 推送/远程审批 + 分享送素材

## 1. 背景

译宝是本地常驻 AI 桌面 agent（Tauri+Vue 桌宠壳 + Python sidecar 大脑）。现有两个前端：桌宠壳（stdio IPC）、浏览器扩展（本地 HTTP 桥）。移动端是第三个前端：**手机经外网连回 Mac 大脑**，复用其全部能力（记忆/自动化/技能/风险闸门），不复制任何大脑逻辑。

用户已确认的核心场景：人在外面时与大脑对话（流式+可打断）、Mac 上风险闸门待批准时推送到手机远程审批、把手机上看到的内容一键存进译宝素材库。

## 2. 目标 / 非目标

**目标（v1）**
1. 文字对话：流式回复 + 打断，与桌宠 surface 互不抢占
2. 推送 + 远程审批：待批准（L3+）/主动提醒推送到手机；手机上批/拒，桌面收件箱同源生效
3. 分享送素材：系统分享 → 直接 POST 素材库（零原生 App 代码）
4. 外网可达：4G/异地均能连回

**非目标（v1 明确不做）**
- Feed/收件箱三区浏览、记忆库浏览（后续版本）
- 语音对话
- 手机端独立运行/离线大脑（拓扑已定为伴生）
- 自建中继（会话路由/消息队列/离线补偿服务端）——只留扩展口，见 §8
- 多设备并发 UX 打磨（技术上多 SSE 连接天然支持）

## 3. 总体架构

```
┌─ iPhone / 安卓 ─────────────┐        ┌─ 国内 VPS（哑管道，零译宝代码）─┐
│ 译宝 App (Capacitor+Vue)     │        │ Caddy :443 (TLS)               │
│  对话/审批 ← HTTPS POST ───────────▶ │   └─ frps :7000 (token+tls)     │
│  流式/事件 ← SSE ←────────────────── │        ║ frp 隧道              │
│  ← 推送 ← 极光(APNs/厂商通道)        └────────╫───────────────────────┘
└─────────────────────────────          │ frpc 出站连接（Mac 无公网入站）
                           ┌─ Mac ─────▼──────────────────────────┐
                           │ sidecar aiohttp 127.0.0.1:19527      │
                           │  ├ /save   扩展桥（现有，迁移）       │
                           │  ├ /v1/*   移动 API（新增）           │
                           │  └ push.py → 极光 REST（出站 HTTPS）  │
                           └──────────────────────────────────────┘
```

数据面（请求/流式）走 VPS 管道；推送面独立——sidecar 直调极光 REST（Mac 出站），不经 VPS。VPS 宕只影响对话，不影响推送。

## 4. 服务端设计（sidecar）

### 4.1 HTTP 面：aiohttp 替换手写 httpserver.py

- 新 `http_api.py`（替代 `mobile_api.py` 命名，因它同时承载扩展桥）：`web.Application` + `AppRunner`/`TCPSite` 程序化嵌入 `serve_async` 现有 asyncio loop——零信号处理器/事件循环冲突（原 docstring 记录的 uvicorn 顾虑，aiohttp 无此问题）
- 扩展桥 `/save`、`/health` 从 `httpserver.py` 迁入，**删除 httpserver.py**；`_start_bridge` 改为 `_start_http_api`，端口/失败降级语义不变
- sidecar 依赖 +`aiohttp`（push.py 的 REST 调用也用它，不引 requests）
- SSE 用 `web.StreamResponse` 分块写 + flush；心跳注释行 `: ping` 每 30s（Caddy 流式 flush 行为在 P2 验证）

### 4.2 移动 API（前缀 /v1/，独立 token）

| 端点 | 方法 | 请求 → 响应 |
|---|---|---|
| `/v1/health` | GET | `{ok, service:"yibao", version}` |
| `/v1/state` | GET | `{ok, running:{surface}\|null, pending:[{id,skill_id,summary,risk,created_at}]}` |
| `/v1/chat` | POST | `{text, conversation_id?}` → `{ok, run_id, conversation_id}` |
| `/v1/interrupt` | POST | `{}` → `{ok}`（只打断 mobile surface 的 run） |
| `/v1/events` | GET | SSE 流（见 4.3），token 走 query 参数 |
| `/v1/confirm` | POST | `{id, approved, remember}` → `{ok}`；未知/过期 id → 404 |
| `/v1/save` | POST | 同扩展桥 `/save`（共享同一处理函数） |
| `/v1/push/register` | POST | `{registration_id, platform}` → `{ok}` |

- 对话执行复用 `_stream_agent(surface="mobile", conversation_id=...)`：surface 隔离抢占已存在，手机不抢桌宠、桌宠不抢手机
- chat 是异步发起：立即返回 run_id，回复经 SSE 流回

### 4.3 SSE 协议与事件分接头

- `serve_async` 加事件分接头（EventTap）：包装 write_msg，stdio 事件照发 Tauri 壳，同时复制给订阅中的 SSE 队列（每连接一个 asyncio.Queue，发布即投递，慢消费者丢弃+断开）
- 每事件单调 `seq`；环形缓冲（deque maxlen=256）存最近编码帧，重连带 `Last-Event-ID` 从断点补发——这是留向 C 的核心原语
- SSE 帧格式：

```
id: 42
event: chunk
data: {"run_id":"...","text":"部分回复"}
```

事件 kind：`chunk`（final_reply 片段）、`run_done`（含 interrupted 标记）、`confirmation_needed`（复用现有 `confirmation_needed` 事件，不新增 kind）、`proactive`（reminder 等已过闸门的主动事件）
- token 经 query 参数（EventSource 无法设 header）；TLS 下可接受，风险记录于 §10

### 4.4 审批闭环（双端一个真相）

1. `batch_confirmer` 注册 future 时同步登记元数据：`confirm_meta[cid] = {skill_id, summary, risk, created_at}`（新增，现有只存 future）
2. 无早到答案 → 经 EventTap 发 SSE `confirm_request` + 调 push.py 推手机（fire-and-forget，失败仅 stderr）
3. 手机 `POST /v1/confirm` → resolve 同一个 future（与桌面 `confirm_batch` IPC 同路径）；批/拒结果双端同步生效
4. future 兑现或被抢占取消时清理 confirm_meta

### 4.5 推送发送器（push.py）

- 极光 JPush REST（v3 push），aiohttp client 出站调用；无 registration_id 注册时静默跳过
- 设备登记存 settings `push.devices`：`[{registration_id, platform, added_at}]`
- 触发点挂在 ProactiveDispatcher 的 gated 通道上（复用 quiet_hours/闸门）：confirm_request、reminder 类事件多一路推手机
- 推送带深链：审批 → `yibao://approvals`，提醒 → `yibao://chat`

### 4.6 安全

- 移动端独立 token：settings `http.mobile_token`（生成/持久化方式同 `http.token`），与扩展 token 隔离，可单独重置
- 认证失败限速：内存计数，5 次失败锁 60s；常量时间比较
- TLS 由 VPS Caddy 终结（Let's Encrypt 自动续期）；sidecar 仍只监听 127.0.0.1
- frp 自身 auth token + tls；frps 端口 7000 仅接受 frpc 连接
- token 重置即旧 token 立即失效

## 5. 客户端设计（mobile/，Capacitor + Vue）

- 新顶层工程 `mobile/`：Vite + Vue3 + TS + Capacitor；路由/状态沿用桌面 app 的模式；iOS 出 TestFlight/自签安装，安卓出 APK 侧载
- 依赖：`@capacitor/app`（深链/生命周期）、`@capacitor/preferences`（存 host/token）、极光官方 Capacitor 插件（P4 初验证质量，是已知风险点）；SSE 用浏览器原生 EventSource（自动重连+Last-Event-ID 内建）
- 页面：
  - **配对**：首次进入填服务器地址+token；主路径是扫桌面设置页二维码 → 系统相机识别 `yibao://pair?host=…&token=…` → 深链打开 App 自动填充 → 测试连接（/v1/health）
  - **对话**：消息列表 + 流式气泡（chunk 拼接）+ 输入栏 + 打断按钮；conversation_id 续会话
  - **审批**：待批列表（/v1/state）+ 批/拒 + remember 勾选；推送深链直达
  - **设置**：连接状态、token 重置入口（提示去桌面操作）、推送开关、手动粘贴存素材兜底
- SSE 生命周期：前台连接、退后台断开（移动 OS 也会杀 socket）；重连由 EventSource 自动+seq 补发
- 连接状态条区分三种失败：无网络 / 服务器不可达（VPS 宕）/ Mac 不在线（Caddy 502）

## 6. 基建（VPS + frp + Caddy）

1. 国内轻量 VPS（阿里/腾讯，Debian）+ 域名一条 A 记录
2. Caddy：`yibao.example.com { reverse_proxy 127.0.0.1:19527 }`（frps 转发监听在 VPS 本机的 19527），自动 TLS；流式 flush 行为验证
3. frps（VPS）+ frpc（Mac，brew services/launchd 常驻）：tcp 代理 remote_port 19527 → Mac `127.0.0.1:19527`；frp auth token + tls
4. 防火墙只开 80/443/7000
5. 验收：手机 4G 关 WiFi，浏览器/curl 走域名打 `/v1/health` 通

## 7. 分享送素材（零原生代码）

- iOS：系统「快捷指令」建一条「发给译宝」（接收 share sheet 的文本/URL → POST `https://域名/v1/save`，token 内嵌）；提供导入用快捷指令模板文档
- Android：开源应用 HTTP Shortcuts（活跃维护）同款配置模板
- App 内手动粘贴保存兜底
- 不做 iOS Share Extension / 安卓 intent 胶水（v1 风险最大的原生开发直接消解；日后需要再补）

## 8. 留向 C（自建中继）的扩展口——仅此三处，不过度

1. API 版本前缀 `/v1/`
2. SSE 事件 `seq` + `Last-Event-ID` 断点续传（环形缓冲即未来离线补偿的原语）
3. 客户端 base URL 可配置（换中继只改地址）

**不做**：服务端会话存储、消息队列、多设备分发逻辑。

## 9. 错误处理与边界

- **Mac 休眠/关机**：大脑不可用，App 显示「Mac 离线」。v1 接受；缓解=系统设置防休眠/caffeinate；唤醒策略列后续项
- **VPS 宕/frp 断**：推送仍可达（Mac 直发），点开推送后 App 连不上并明确提示；对话暂不可用
- **SSE 断线**：EventSource 自动重连 + seq 补发（256 事件内）；超出缓冲则全量拉 /v1/state 重建
- **推送失败**：静默降级，仅 stderr，不拖垮主链路（与 proactive dispatcher 隔离原则一致）
- **sidecar 重启**：SSE 断→自动重连→/v1/state 恢复 UI
- **token 泄露**：桌面设置页重置，旧值立即失效
- **限速**：认证失败 5 次/分钟锁 60s

## 10. 已知风险与接受项

- SSE token 走 query 参数会进 VPS/Caddy 访问日志——TLS+个人服务器+可重置，接受；如在意可后续换 ticket 机制
- 极光 Capacitor 插件质量未验证——P4 第一件事验证，退路=极光 REST 通知 + App 内轮询兜底（体验降级）
- iOS 真推送必须 Apple 开发者账号（$99/年）；免费自签 7 天过期且无推送
- 国内 DERP 不可用与本项目无关（未用 Tailscale）

## 11. 测试策略

- **sidecar（pytest，沿用现有 fake 注入风格 + aiohttp test_utils）**：
  - token 认证：对/错/限速锁定；mobile 与扩展 token 隔离
  - /v1/chat：fake _stream_agent 注入，run_id 返回、surface=mobile、conversation_id 透传
  - SSE：事件序 seq 单调、chunk/run_done 帧、Last-Event-ID 断点补发、心跳
  - confirm：注册→SSE 事件→HTTP 兑现→future resolve→meta 清理；过期 id 404；抢占时取消路径
  - /v1/save 与扩展桥 /save 共享函数回归（现有桥测试移植）
  - push.register 存储、无设备时跳过
  - /v1/state 快照正确性
- **mobile（vitest，沿用桌面 app 测试栈）**：store/composable 单测（SSE 拼接、状态机、深链参数解析），组件轻测
- **真机验收清单（P5）**：外网 4G 下——配对/流式对话/打断/审批推送+深链+批拒桌面同步/快捷指令分享落库/断网恢复

## 12. 前置条件与成本

| 项 | 说明 |
|---|---|
| 国内 VPS | ~¥60-100/年（阿里/腾讯轻量） |
| 域名 | ~¥20/年（TLS 必需） |
| Apple 开发者 | $99/年（iOS 安装+真推送硬前置） |
| 极光 | 个人免费档 |
| 安卓 | APK 侧载，无成本 |

## 13. 分阶段交付

- **P1 服务端**：aiohttp 迁移（含桥）+ /v1 API + SSE + 审批 meta + 测试——curl 全可验
- **P2 通道**：~~VPS/Caddy/frp 搭建，手机浏览器外网验证 SSE~~ **已封存（2026-08-15）**：工作 Mac 受公司管控禁装一切内网穿透工具。VPS 侧成果保留（域名/证书/openresty 反代已通），待个人电脑接手时换 frp 复用
- **P3 App 骨架**：Capacitor 工程 + 配对 + 对话流式（开发期用桌面浏览器直连 `http://127.0.0.1:19527` 验证；真机联调随联网方案回来后补）
- **P4 推送审批**：极光接入 + 深链 + 审批页（外网真机）
- **P5 收尾**：分享模板 + 桌面设置页（token 区块+二维码）+ 全量真机验收

## 14. 开放问题

- 桌面设置页需新增 `http.public_url` 配置项（二维码内容用它；不填提示仅局域网调试）
- conversation 的历史消息拉取（App 重开后回显旧对话）——v1 靠 seq 补发+state，是否加历史端点待 P3 实做时定
- 多设备同时 SSE 在环（手机+平板）行为符合预期（广播），未做设备定向——记录，不实现
- token 热重载失效：auth 中间件持有启动时的 token 快照，P5 重置 UI 落地前须改为读 settings 或重置即重启
- iOS WKWebView CORS 大概率要补响应头（P3 计划风险项）
- 跨 surface 排队窗口内手机 interrupt 连环杀（P3 客户端落地前收紧为 cancel 属主判）
- `_register_push` 落盘缺测试（P4 消费 push.devices 前补）
