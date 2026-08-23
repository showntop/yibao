# 跨端协议契约（desktop / mobile / extension ↔ sidecar 大脑）

> 日期：2026-08-22
> 目的：让同一大脑的三类前端（Tauri 桌面 app、Capacitor mobile、Chrome extension）共享**同一事件与数据形状**的单一事实源，消除手写双份类型的漂移（现状：`RunMetrics`/`FeedItem`/`PendingConfirm`/`MemItem` 在 desktop 与 mobile 各定义一份，snake/camel 混用）。

## 1. 传输通道

| 端 | 通道 | 协议 |
|---|---|---|
| desktop（Tauri） | Rust IPC `invoke`（壳→脑）+ 事件 `brain-event`（脑→壳） | sidecar stdio JSON 行；Rust 桥转发 |
| mobile（Capacitor） | HTTP `POST /v1/*`（请求）+ SSE `GET /v1/events`（事件流） | `X-Yibao-Token` / `?token=`；`last_event_id` 断点续传 |
| extension（Chrome） | HTTP `POST /v1/*`（`X-Yibao-Token`） | 与 mobile 同侧 |

## 2. 事件 kind（三端一致，命名建议统一）

| kind | 含义 | desktop（brain-event） | mobile/extension（SSE） |
|---|---|---|---|
| `thought` | 推理过程（不展示） | ✅ | — |
| `action_proposed` | 提议工具调用 | ✅ | — |
| `confirmation_needed` | 待批确认（攒批） | ✅ | ✅ |
| `action_result` | 工具结果（含 `proc` 状态） | ✅ | — |
| `final_reply_chunk` | 流式正文 | ✅ | ✅ |
| `final_reply` | 终态回复（可带 `metrics`） | ✅ | ✅ |
| `interrupted` | 被打断 | ✅ | ✅ |
| `error` | 错误 | ✅ | ✅ |
| `listening` / `listening_done` | 语音状态 | ✅ | — |
| `speaking` / `speaking_done` | 播报状态 | ✅ | ✅ |
| `reminder` | 主动提醒（`type`/`day`/`task` 语义载荷） | ✅ | ✅ |
| `notice` | 排队提示 | ✅ | ✅ |
| `panel` / `panel_data` | 面板事件 | ✅ | — |
| `run_done` | 一轮 run 收口 | — | ✅ |
| `thinking` | 思考占位 | — | ✅ |

> 差异原因：desktop 经 Rust 桥收到完整 brain-event；mobile/extension 只订阅 SSE 白名单 kinds（`KNOWN_KINDS`）。**建议**：SSE 侧补齐 `panel`/`action_proposed`，让 mobile 也能跟进桌面进行中的工具过程（现状 mobile 只显示最终回复）。

## 3. 数据形状与命名映射

### 3.1 命名规范映射表（sidecar snake_case ↔ 前端 camelCase）

sidecar/Rust 落盘与 IPC 用 snake_case；desktop 前端协议层（`protocol/brain-types.ts`）保持 snake_case 透传（`conversationId` 除外——事件信封已用 camel）；mobile 端建议以 snake_case 直收（与 HTTP 载荷对齐）。

| 概念 | sidecar/Rust | desktop 前端 | 说明 |
|---|---|---|---|
| 会话 id | `conversation_id` | `conversationId` | brain-event 信封字段（后端 emit 时即 camel） |
| 任务 id | `task_id` | `task_id` | brain-event 顶层 |
| 退出码 | `exit_code` | `exit_code` | brain-event 顶层 |
| 面板引用 | `panel` | `panel` | PanelPayload |
| 感知来源 | `source` | `source` | PerceptionItem |
| 敏感级 | `sensitivity` | `sensitivity` | PerceptionItem |
| Base URL | `base_url` | `base_url`（SetupConfig） | 前端组件展示层转 camel（HomeChat 曾误映射，已修） |
| 运行统计 | `prompt_tokens`/`cost`/`elapsed_ms` | 同（snake） | RunMetrics |
| 记忆命名空间 | `ns` | `ns` | MemItem |
| 待批表面 | `surface` | `surface` | PendingConfirm |

### 3.2 需要合并的类型（现状双份，收敛到单一事实源）

| 类型 | desktop 定义处 | mobile 定义处 | 差异 |
|---|---|---|---|
| `FeedItem` | `protocol/brain-types.ts` | `mobile/src/state/feed.ts` | mobile 的 `status: string`（desktop `"none"\|"follow"\|"ignore"`）|（desktop `"none"\|"follow"\|"ignore"`）|（desktop `"none"\|"follow"\|"ignore"`） |
| `FeedStats` | 同上 | 同上 | mobile 字段全可选（防御旧版） |
| `RunningTask` | 同上 | 同上 | mobile 无 `kind` 联合收窄 |
| `PendingConfirm` | 同上 | `mobile/src/state/approvals.ts` | desktop 有 `tool_id/label/desc`，mobile 有 `tool_id/summary`（2026-08-23 已对齐 `tool_id`，漂移消除） |
| `MemItem` | 同上 | `mobile/src/state/memories.ts` | mobile `created_at` 必选字符串 |
| `RunMetrics` | 同上 | —（mobile 无） | — |

> **漂移已消除（2026-08-23）**：`PendingConfirm` 字段随协议改名 `skill_id → tool_id`（capability-unified-design spec §B）统一为 `tool_id`，desktop 本地字段 `skill` 同步对齐；sidecar 落盘字段为 `Action.tool_id`。

### 3.2.1 双端类型维护决策（2026-08-22，R-29 复核落定）

**决策：desktop 与 mobile 双端类型各自维护，以本文档 3.2 节对照表为契约基准，不做代码级共享。**

理由：
1. 两端是独立构建链（desktop：Tauri/Vite；mobile：Capacitor/Vite），代码级共享需引入 monorepo workspace 或 npm 私有包，构建复杂度收益比不划算；
2. 双端载荷本就不同构（mobile 直收 HTTP/SSE snake_case，desktop 经 Rust 桥混 camelCase——见 3.1 映射表），强行共享类型反而要引入映射层；
3. 漂移风险靠对照表 + 双端各自测试锁定，副作用可接受。

**维护纪律**：sidecar 事件/载荷形状变更时，必须同步更新本文档 3.1/3.2 节，并检查双端对应类型；PR 中不同步更新契约文档的协议变更不予合入。

**desktop 内部单源化（已完成，R-29）**：`RunMetrics` 唯一定义于 `desktop/src/protocol/brain-types.ts`（state/types.ts re-export 兼容旧路径）；`AvatarState`（7 态共享集）唯一定义于 protocol/brain-types.ts，Home.vue/HomeFrame.vue/HomePlugins.vue/PanelApp.vue/usePetState（PetAvatarState 扩展）/useHomeChatSession（HomeAvatarState 别名）全部引用单源。

### 3.3 HTTP 端点（mobile/extension 侧，请求形状）

| 端点 | 用途 | 请求体 |
|---|---|---|
| `GET /v1/health` | 连接测试 | — |
| `GET /v1/feed?limit=60` | 动态流 | — |
| `GET /v1/conversations` | 会话列表 | — |
| `GET /v1/history?conversation_id=` | 单桶历史 | — |
| `GET /v1/state` | 待批 + 状态 | — |
| `POST /v1/confirm` | 裁决 `{id, approved, remember}`；404=已处理 | — |
| `POST /v1/chat` | 发消息 `{text, conversation_id}` → `{run_id}` | — |
| `POST /v1/interrupt` | 打断 `{conversation_id}` | — |
| `GET /v1/memories` | 记忆库 | — |
| `GET /v1/reminders` | 提醒列表 | — |
| `POST /v1/reminders/cancel` | 取消 `{id}` | — |
| `POST /v1/save` | 素材入库（extension） | — |

## 4. 落地建议（后续迭代）

1. **第一步（低风险）**：以 `protocol/brain-types.ts` 为基准，把 mobile 的 `feed.ts`/`approvals.ts`/`memories.ts` 类型改为 `import type`（跨包引用或复制 + 标注来源），双端字段统一 `tool_id`。
2. **第二步**：建 `shared/protocol/`（仓库根）放 TypeScript 类型源，desktop/mobile 双端 import；sidecar 侧经 `docs/` 此契约文档对齐字段名。
3. **第三步**：SSE 事件 kind 补齐（mobile 订阅 `panel` 等），让移动端与桌面能力对齐。
