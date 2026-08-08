# 浏览器扩展闭环设计：存页/划词 → 素材库/选题看板（2026-08-08）

> 路线来源：E（全局唤起）→ 联动 L1 第一优先级。闭环「浏览器划词/存页 → zimeiti 素材库 → 选题看板 → 写稿召回」。
> 后半截（看板、写稿召回：方法论指引 + mat_list + 编辑器素材抽屉）已存在，本次只建浏览器入口与桥。

## 1. 目标与范围

**目标**：在 Chromium 系浏览器里，点扩展按钮或右键，把当前页/选中文字存进译宝——存素材（LLM 摘要打标进 materials）或存为选题（进 topics 看板）。团子给「已存素材：《标题》」气泡回执。

**v1 范围**：MV3 扩展（popup 两按钮 + 右键菜单 + options 页）；sidecar 127.0.0.1 微 HTTP 桥（共享 token 认证）；mat_save 兼容「url+text 同给」；设置页「浏览器扩展」区（token 显示/复制 + 安装指引）。

**不做**（YAGNI）：Safari 壳、页面正文智能提取（Readability）、存素材的 feed 记账、端口设置化（env 即可）、热键/自动剪辑、离线队列。

## 2. 关键事实（探索结论）

- sidecar 无直接 HTTP 依赖（uvicorn/starlette 是传递依赖）→ **原生 asyncio.start_server 零依赖**（用户拍板），避免 uvicorn 信号处理器/打包传递依赖问题。
- **mat_save 参数陷阱**：url/text 互斥——仅传 url 会被裸 urllib 重抓（登录墙/SPA 必挂）。扩展在页面上，正文由扩展 DOM 提取传 text 最可靠；来源 url 要留住 → mat_save 加兼容「url+text 同给：text 为正文、url 仅来源元数据，不重抓」。
- **回执零成本**：桥复用 quiet 直调路径（propose→decide→execute 线程池），发 `action_result`（id `pa_http_<n>`，pa_ 前缀是 App.vue 气泡认领条件）——壳侧一行不改。存为选题照加 `invoke_add_topic`（direct+quiet，zimeiti.add 声明式 db tool 已有）。
- 认证：无现成 token 机制 → 新增 settings 键 `http.token`（空则启动时生成 `secrets.token_hex(16)` 并 `save_settings` 持久化），设置页展示供复制；端口 env `YIBAO_HTTP_PORT`（默认 19527）。`YIBAO_*` env 链与 settings 合并机制现成。
- 事件 emit 不带 surface → App.vue 分流过滤放行（与唤起条回执同行为）。
- 阻塞调用（LLM 摘要）必须 `_offload` 挪线程池（同 handle_panel_action），防看门狗误杀。

## 3. 架构

```
extension/（MV3，零构建纯 JS：manifest + background(sw) + popup + options + shared）
   │  右键「存进译宝素材库」/ popup「存素材」「存为选题」
   │  chrome.scripting 提取 {url, title, text=getSelection()||body.innerText(截 20000)}
   │  POST http://127.0.0.1:PORT/save  {url, title, text, mode: "material"|"topic"}
   │  Header: X-Yibao-Token（options 页粘贴一次，chrome.storage.sync 持久）
   ▼
sidecar httpserver.py（asyncio.start_server 微 HTTP：OPTIONS 预检/GET /health/POST /save，1MB 上限）
   │  校验 token → mode 映射 → get_api 白名单 → propose/decide(L1 auto；CONFIRM/DENY 返 403)
   │  → _offload(invoker.execute) → emit action_result → 回 {ok, title}
   ▼
material: zimeiti.invoke_mat_save（mat_save 兼容 url+text）→ materials 表
topic:    zimeiti.invoke_add_topic（新 quiet api 条目）→ topics 看板
回执: action_result → 团子「已存素材：《标题》」气泡（App.vue 零改动）+ success 闪现（既有 action_result 处理）
```

- **HTTP 微服务**（httpserver.py，~90 行）：只认 Content-Length body（拒绝 chunked），响应 `Connection: close`；OPTIONS 回 204 + `Access-Control-Allow-*`（自定义 token 头必触发预检，扩展 origin 不固定故 `*`——token 已把关）；单 POST 端面，手写解析风险可控。
- **挂载**：serve_async 在 reminder_task 后 `_start_bridge(...)`；`http_enabled: bool | None = None` 参数（None = 跟 use_real 走，测试默认关）；端口占用等失败 stderr + 禁用，不拖垮大脑；shutdown 时 `bridge_server.close()`。
- **设置页**（SettingsView 新 s-group「浏览器扩展」）：token 掩码显示 + 显示/复制按钮（navigator.clipboard，拒绝时退化显示全文）、端口说明（19527，env 可覆盖）、安装指引（chrome://extensions 开发者模式加载 extension/）。

## 4. 错误处理

- token 错误 → 401；空 text → 400；未知 mode → 400；方法不在白名单 → 500；策略 CONFIRM/DENY → 403；工具执行失败 → 500 带 error。扩展端全部显示为 notification/popup 状态行。
- 端口被占 → 桥禁用（stderr），大脑照常。
- 扩展 fetch 失败（大脑没起/端口错）→ options「测试连接」与 popup 状态行给明确提示。

## 5. 测试策略

- **httpserver.py**：ephemeral 端口真 socket 请求——OPTIONS/GET/POST/坏 JSON/无 Content-Length/连接关闭。
- **mat_save 兼容**：fake llm/db ctx；url+text 同给断言不重抓（_fetch_text monkeypatch 抛错）且 url 入库。
- **server 桥**：`_ensure_bridge_token`（生成+持久化/保留）；`_make_bridge_route` 函数级（401/health/material/topic/400/403/500 + action_result pa_http_ 形状 + execute 参数）；serve_async `http_enabled=False` 默认不启监听。
- **api 解析**：真 zimeiti/api.toml + 双 dummy Skill 注册 → `zimeiti.invoke_add_topic` direct+quiet。
- **前端**：vue-tsc/vite build（无单测框架）；扩展人工装。
- **真机验收**：登录墙页（公众号/知乎）验证 text 路径、气泡回执、看板/素材库可见、右键菜单、options 测试连接。

## 6. 风险与雷区

- **pa_ 前缀**：action_result 回执气泡的认领条件（App.vue:515），桥 id 用 `pa_http_<n>` 与壳侧 `pa_<Date.now%2^31>` 不撞号。
- **CONFIRM 风险项**：mat_save/add 是 L1 auto；若策略收紧（用户调过风险）桥返回 403——无确认通道是设计使然（桥操作本就低风险）。
- **navigator.clipboard**：WKWebView 需用户手势（点击满足）；失败退化显示全文。
- **MV3 service worker**：瞬时任务（右键/点击）无长连需求；进度反馈用 notifications。
