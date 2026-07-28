# 译宝感知体系设计（2026-07-27）

> 定位：感知是「伙伴」的认知基础。用户决策：**感知求全，安全/隐私作为独立轨道并行设计**——两条轨互为接口（数据生命周期 + 权限闸门），不互相裁剪。
> 业界前车：微软 Recall 默认开启 + 明文 SQLite → TotalRecall PoC「两行代码偷走一切」→ 被迫回炉重写（opt-in + TPM + 三层过滤）。本设计把它的教训直接写成结构。

## 0. 结论速览（TL;DR）

1. **双轨**：感知能力轨（求全：六类源、事件驱动）‖ 信任架构轨（求稳：授权/透明/控制/删除/加密）。接口只有两个——数据生命周期表（§5）与分级闸门（§6.1）。
2. **采集用 screenpipe 模式**：事件驱动（应用切换/窗口变化/输入停顿触发）+ 心跳兜底，**a11y 树优先、截图/OCR 兜底**——比定时截屏省一个数量级存储，且天然适合 agent 消费。
3. **原始数据默认不出本机，出站必须逐能力单独授权**——已实现的 A/C 活动加载与未来 B 源视觉概括各有独立默认关闭闸门；只发送完成当前请求所需的有界数据。
4. **Observations 不是记忆**：原始观察短留存自动过期；提炼出的模式经既有记忆路径进 mem0（守 spec 禁双写）。
5. **信任三底线**（Recall 反弹后的行业基线）：默认全关 opt-in、感知状态常驻可见、三层过滤（黑名单→隐私窗→敏感分类器）。

## 1. 认知循环（感知只是第一环）

```
感知 → 沉淀 → 联想/反刍 → 主动行为
（看）  （记）   （懂）      （伙伴）
```

没有消费方的感知是监控，有消费方的感知才是伙伴。每接一个感知源，必须同时回答：它喂给哪个消费场景（§7）。

## 2. 感知源全景（六类，敏感度 S0–S3）

| # | 源 | 采集 | 频率 | 原始数据 | 敏感度 | 成本 |
|---|---|---|---|---|---|---|
| A | 应用与窗口 | NSWorkspace/AX 轮询 | 5s，变化才记 | app 名 + 窗口标题 | S1 | ≈0 |
| B | 屏幕内容 | 事件驱动截图 + a11y 树 | 事件触发 + 5min 心跳 | a11y 树文本 / 截图 | S3 | 高（截图+LLM） |
| C | 输入活动 | CGEventTap 计数（不读内容） | 状态切换才记 | 活跃/空闲、节奏统计 | S1 | ≈0 |
| D | 系统环境 | IOKit/系统 API | 随观察事件附带 | 时间/电量/网络/勿扰/耳机 | S0 | ≈0 |
| E | 数字足迹 | 剪贴板变化监听 / FSEvents | 变化触发 | 剪贴板文本 / 文件路径 | S2–S3 | 低 |
| F | **禁区** | — | — | 麦克风常开、摄像头、按键内容、secure input | — | 永远不做 |

- **B 的 a11y 树优先**：结构化文本（窗口里有什么控件/文字），比截图小千倍、可直接检索；树拿不到（自绘 UI）才截图走视觉概括——与 computer-use 的「结构化接口优先，像素兜底」同一哲学。
- **E 分级处理**：剪贴板 S2（默认关，开则只留文本不留图/文件）；文件变更限目录白名单（如 桌面/文稿，S2）；浏览器历史不碰（各浏览器数据文件加密且涉第三方 TOS，投入产出比差）。
- **F 是永久红线**：按键内容 = 键盘记录器，麦克风常开 = 窃听器——做了就不是伙伴是木马。涉他录音若未来要做，必须 Limitless 式 Consent Mode。

## 3. 架构

```
┌─ sensors（sidecar 线程，每源一个）──────────────────┐
│ A 应用窗口   B 屏幕(事件驱动)   C 活动   D 环境   E 足迹 │
└──────────────┬───────────────────────────────────────┘
               ▼  Observation{ts, source, kind, payload, sensitivity}
     ObservationBus（内存队列，背压丢弃 oldest）
               ▼
     ObsStore（SQLite，observations.db，与 feed 同目录）
               ▼  留存分层自动清理（§5）
     Distiller（LLM 批处理：空闲/手动触发）
               ├─→ 模式记忆 → mem0（既有记忆路径，禁双写）
               └─→ 重要事件 → Feed（「它注意到的事」也有账本）
               ▼
     消费方：对话上下文注入 / 晨间反刍 / 主动建议(旋钮闸门) / 时间线
```

- **背压**：队列满丢弃最旧（感知是增强面，不许拖垮主链路——与 feed「写失败只 print」同款纪律）。
- **Distiller 批处理**：提炼是 LLM 调用，集中在空闲时段做，不在感知路径上实时调（成本与延迟都扛不住）。
- **事件驱动 B 源**：监听 A 源切换/窗口标题变化/输入停顿（C 源信号）触发截图或 a11y 树抓取；5 分钟心跳兜底防「一直看同一页什么都没记」。

## 4. 数据位置与出站点

- **原始数据默认只在本机**：observations.db + 未来截图文件（数据目录 `perceptions/`，随清数据功能一起可清）。
- **已实现的 A/C 按需出站点**：只有用户开启独立的 `perception.model_access` 后，LLM 调用 `load_user_activity` 时，所选时间窗口内的应用名、窗口标题和活动状态才发送给当前模型服务。单次最多 24 小时、最多返回最近 120 段；不发送截图或按键内容。完整结果只活在当前模型轮次，审计、壳事件和会话历史只保存安全摘要。
- **未来 B 源出站点**：截图 → 视觉模型概括，另设默认关闭闸门：
  1. B 源开关独立于总开关，开启时明示「截图内容将发送给模型服务商做概括」；
  2. 发送前过敏感过滤（§6.3 三层过滤过了才发）；
  3. 后路：本地视觉模型成熟后切换，B 源出站点可归零。
- D/E 源仍只做本地处理；若未来新增模型消费，必须另开授权设计，不能复用 A/C 授权偷渡。

## 5. 数据生命周期表（双轨接口 ①）

| 数据 | 产生 | 存储 | 过期 | 提炼去向 | 用户可删粒度 |
|---|---|---|---|---|---|
| a11y 树文本 | B 事件 | obs 表（payload 加密） | 7 天 | 模式记忆 | 单条/按 app/按时段/全部 |
| 截图原图 | B 事件 | 文件系统 | 24 小时 | 概括后即删（只留概括文本 7 天） | 同上 |
| app/窗口流 | A 变化 | obs 表（payload 加密） | 30 天 | 使用模式（时段×app） | 同上 |
| 活跃/空闲 | C 切换 | obs 表（payload 加密） | 30 天 | 作息模式 | 同上 |
| 剪贴板文本 | E 变化（开关） | obs 表（payload 加密） | 24 小时 | 不提炼进记忆 | 同上 |
| 环境快照 | D 附带 | obs 表 | 7 天 | 不提炼 | 同上 |
| 模式/结论 | Distiller | mem0 | 长期 | — | 记忆管理页（已有） |

清理 daemon：sidecar 启动时 + 每小时跑一次过期清理；截图目录与 obs 表一致性检查（孤儿文件即删）。

## 6. 信任架构（独立轨道）

### 6.1 授权模型（双轨接口 ②）

- 设置页「感知」组：总开关 + A/B/C/E 各自独立开关，**全部默认关**；开启 B/E 弹一次明示对话（数据敏感性 + 出站点说明）。
- macOS 联动：B 依赖屏幕录制权限、A/C 依赖辅助功能——开关状态与系统权限状态联动展示（沿用 PermissionsBanner 检测）。
- macOS 15+ 录屏权限周期性重新弹窗：**当特性不当 bug**——弹窗时译宝感知页同步显示「系统正在帮你确认」，把平台的提醒转化为信任资产。

### 6.2 透明

- **感知日志页**：设置页内或独立页——按时间列出它注意到的一切（来源徽章 + 内容 + 敏感度），单条删除/按 app 删/清空。
- **感知状态灯**：团子新增「观察中」微态（天线灯常亮一点青白，不动画不抢眼）；感知暂停时立刻熄灭。Recall 基线：托盘图标区分 录制中/暂停/过滤中——我们比它多一张脸。
- **出站点明示**：感知页明确列出 A/C 模型读取会发送的字段；未来 B 源上线时另列截图概括，不用笼统的「感知已开启」代替出站授权。

### 6.3 控制与过滤（三层，Recall 同款）

1. **应用黑名单**：默认内置（1Password、钥匙串访问、银行/支付类、系统偏好设置的密码页），用户可增删；命中即整段不采。
2. **隐私窗自动排除**：浏览器隐私窗口不采（窗口标题/AX 属性启发式 + 浏览器清单）。
3. **敏感内容兜底**：secure input 激活（密码框聚焦）时 B 源立即弃帧；文本过敏感分类器（密码/卡号/证件号正则 + LLM 复核）命中即删。
- **一键暂停**：对团子说「别看了」/ 设置页拨停 / 定时隐身时段（如 23:00–8:00 默认建议开启）。
- **尊重防御方**：对声明了禁止捕获的窗口（Signal 模式，macOS 屏幕共享排除标志）截到黑屏 = 正确行为，不重试不绕过。

### 6.4 安全（at-rest）

- v1：**字段级加密从第一版启用**。`payload` 使用 Fernet（AES-128-CBC + HMAC-SHA256）加密，密钥存 macOS Keychain；测试通过注入 key provider，不接触真实 Keychain。数据库文件权限 0600 + 数据目录权限 0700。时间、来源、类型、敏感度保留明文用于留存清理与分页，但窗口标题、应用名、空闲时长等实际内容不以明文落盘。
- v2 加密增强：评估 SQLCipher 全库加密（隐藏时间/来源等元数据）与查看感知日志时可选 Touch ID；迁移必须原地重加密并保留可回滚备份。
- 访问限流：感知日志/删除接口防暴力枚举（Recall anti-hammering 的对标）。

## 7. 消费场景（价值闭环）

| 场景 | 吃什么 | 落地期 |
|---|---|---|
| 对话上下文加载（「我刚才在干嘛」） | LLM 按需选择的 A/C 时间窗口 | v1.1 已实现 |
| 屏幕内容上下文（「那个报错是什么」） | B 源 a11y/视觉概括 | v3 |
| 晨间反刍（问候真实化） | 昨日模式 + 今日待办 + 观察 | v3 |
| 时间线回顾（主屏新页） | 全量观察流 | v3 |
| 主动建议（「你查这个报错三次了，要不要我看看」） | 模式 + 闸门 | v4 |
| 使用统计（屏幕时间报告） | A/C 源聚合 | v4 |

纪律：感知驱动的主动行为全部过自主权旋钮（settings 已有 `proactive_voice`，扩展 `proactive_level`）；进收件箱不弹窗（反模式：弹窗轰炸）。

## 8. 成本模型

- A/C/D/E：CPU/存储 ≈ 0（文本行级，MB/月量级）。
- B（最重）：事件驱动 ≈ 20–60 事件/天 ×（截图 ~2MB + 概括 ~1.5K token）→ 存储 ~100MB/天（24h 滚动）+ token ~10 万/天。a11y 树优先可把截图事件压到 1/5。
- Distiller：每日 1–2 次批处理，~5 万 token。
- 结论：B 源是唯一有真实成本的源，开关独立 + 事件驱动 + 树优先三件套把它压到可接受。

## 9. 路线图

- **v1 骨架**：ObsStore + A（应用窗口）+ C（活动）+ 设置「感知」组（开关默认关）+ 感知日志页 + 过期清理。无 LLM 成本，零出站点。实施细化见 §10。
- **v2 眼睛**：B 源（事件驱动 a11y 树优先 + 截图概括）+ 三层过滤 + 出站点闸门 + 黑名单 + 团子「观察中」状态灯。
- **v3 反刍**：B 源内容上下文 + 晨间问候接入 + 主屏时间线 + Distiller 模式提炼进 mem0（A/C 按需对话加载已提前在 v1.1 落地）。
- **v4 伙伴**：主动建议（旋钮闸门）+ 使用统计 + 加密增强（Keychain/Touch ID）。

## 10. v1 实施细化（技术方案已核实依赖）

### 11.1 采集技术选型（sidecar 依赖已含 pyobjc Quartz/Cocoa/ApplicationServices）

- **A 源（应用与窗口）**：`NSWorkspace.sharedWorkspace().frontmostApplication()` 取前台 app；窗口标题优先 AX（`AXUIElementCopyAttributeValue` 前台窗口 title，复用既有辅助功能权限），取不到退化 `CGWindowListCopyWindowInfo`（免权限，z-order 第一窗）。**5s 轮询，(app,title) 变化才写**——每秒产物是零，纯文本行。
- **C 源（活动强度）**：`CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateCombinedSessionState, kCGAnyInputEventType)` 轮询空闲秒数；≥60s 判空闲、<60s 判活跃，**只记状态切换**。**明确不用 CGEventTap**——event tap 是键盘记录器的相邻物（要单独权限、伦理越界），空闲轮询拿到同样的 活跃/空闲 结论且零风险。
- **D 源**：v1 不做（观察事件自带时间戳，环境上下文够 v3 用）。

### 11.2 存储（ObsStore，`perception.py`，feed.py 同款模式）

```sql
CREATE TABLE observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  source TEXT NOT NULL,      -- 'app' | 'activity'（v2: 'screen' | 'clipboard'）
  kind TEXT NOT NULL,        -- app: 'frontmost'；activity: 'active' | 'idle'
  payload TEXT NOT NULL DEFAULT '{}',  -- JSON：{app, title} / {idle_seconds}
  sensitivity TEXT NOT NULL  -- 'S0'..'S3'
);
CREATE INDEX idx_obs_ts ON observations(ts);
CREATE INDEX idx_obs_source ON observations(source);
```

- 库文件 `observations.db`（数据目录，与 feed.db 同目录）；`payload` 入库前 Fernet 加密、读取时解密，密钥由 macOS Keychain service `com.yibao.perception` 提供；写失败只 print 不抛（增强面纪律同 feed）。无法取得密钥时感知保持关闭并向 stderr 明示，**禁止退化成明文**。
- **v1 无 ObservationBus**：sensors 直写 `ObsStore.append`（量极小；Bus 背压抽象留给 B 源——别过早实现，但 `append` 即接口留好了）。
- 留存：A/C 30 天；清理 = 启动时 + 每小时 purge（复用 `_reminder_loop` 后台线程模式）。
- 文件权限：observations.db 0600、数据目录 0700；密钥只进 Keychain，不写 settings/.env/数据库（§6.4 v1 基线）。

### 11.3 协议（server.py，feed/widgets 同款转发模式）

- `perception_list {limit?, before_id?}` → `{items: 倒序, sources: 出现过的源清单}`；分页用 before_id（id < before_id）。
- `perception_delete {per_id}` → `perception_deleted {id, ok, error?}`——**观察 id 走 `per_id` 键**（信封 id 被请求序号占用，mem_delete 同款坑）。
- `perception_clear {}` → `perception_cleared {count}`。
- Rust：转发 `brain-perception`/`brain-perception-deleted`/`brain-perception-cleared` + 三命令；前端 `getPerceptionOnce`（once+3s 竞速，feed 同款）。

### 11.4 settings 键（并入 settings.json 已知键，即时生效）

`perception.master`（总开关）/ `perception.app` / `perception.activity` / `perception.model_access`（允许当前模型按需读取 A/C 记录）——**全部默认 false**；sensors 与活动加载工具都读取同一份运行期 settings，`settings_set` 即时生效免重启。模型访问与采集总开关独立：暂停采集后，已保留历史仍可在授权开启时查询。B/E 键预留（v2 才进 UI 与默认表）。

### 11.5 前端（SettingsView 两组，记忆管理同款样式）

- 「感知」组：总开关 + 应用窗口/活动强度两个采集子开关 + 独立的模型读取开关 + 状态行（运行中·N 条观察 / 已暂停）；总开关关 = 采集子开关置灰，但模型读取开关不置灰，因为保留历史仍可查询。
- 「感知日志」组：来源徽章（应用/活动）+ 内容一行（「Xcode — yibao — App.vue」「活跃 → 空闲 12 分钟」）+ 相对时间 + 单条删除 + 底部「清空感知记录」；空态给「感知未开启」或「还没有观察」人话。
- 团子「观察中」状态灯：v1 不做——A/C 源不「看内容」，指示灯的必要性随 v2（B 源截图）一起来；v1 的透明由日志页 + 状态行承担。

### 11.6 测试清单（test_perception.py）

ObsStore：append/查询倒序+分页/delete/clear/留存 purge（构造过期行）/坏 JSON payload 容忍；协议：三方法往返 + 默认关时 sensors 不写（mock 轮询源）+ 变化才写（同 app 连续轮询只写一条）+ settings 拨开后即时开始写。

### 11.7 任务分解（照单执行顺序）

1. `perception.py`：ObsStore + purge
2. sensors：A 轮询（NSWorkspace+AX/CGWindowList）+ C 空闲轮询，settings 闸门
3. server.py：启动清理线程 + 三协议方法 + sensors 挂载
4. config.py：三个 settings 键
5. Rust 转发 + 前端 brain.ts + SettingsView 两组
6. 测试 + spec 实装记录

### 11.8 实装记录（2026-07-28）

- 已实现：`perception.py` 的 Fernet 字段加密 store、macOS Keychain 密钥、0600/0700 权限、分页/删除/清空/分源留存；Keychain 访问 5 秒超时并 fail closed，禁止明文降级。
- 已实现：A 源前台应用/窗口（AX 优先、CGWindow 退化）与 C 源活跃/空闲（60 秒阈值、不装 event tap），5 秒轮询、只记变化、总开关/子开关每轮即时读取。
- 已实现：sidecar 生命周期与每小时清理、三类 JSONL IPC、Rust/Tauri 透明转发、TypeScript 超时接口、设置页三开关与可见/可删/可清空日志。
- 已实现消费闭环：`load_user_activity` 是默认可见的 L0 底座工具；LLM 自行判断是否加载并选择带时区的时间窗口，单次最多 24 小时。store 读取窗口前 A/C 状态种子，合并成时间线；最多返回真正最近的 120 段，高密度窗口会保留最近观察并置 `truncated=true`。
- 已实现独立出站授权：`perception.model_access` 默认 false、设置页明确披露应用名/窗口标题/活动状态会发送给当前模型；不开启时在解密前拦截。采集暂停不影响读取尚未删除的历史。
- 已实现敏感结果边界：当前模型轮次拿完整结构化活动；audit、`action_result`、panel 与 history 只拿安全摘要。敏感工具的摘要钩子异常时 fail closed 为 `redacted=true`；使用非空活动回答后发 notice「已参考最近活动」。
- 自动验证：sidecar 全量 `512 passed`；`vue-tsc --noEmit`、Vite production build、`cargo check`、`cargo test` 全部 exit 0。
- 真机验收（2026-07-28 核心通过）：真实模型加载成功（11 条观察、12 个时间段）；真实 `audit.db` 只含时间窗与计数、无活动详情；真实会话历史无应用名/窗口标题、最终回答已替换为敏感占位。
- 待真机验收（剩余）：`perception.model_access` 关闭时的拦截与引导文案；notice「已参考最近活动」的实际出现；采集暂停后历史仍可查；首次创建/重启读取 Keychain、AX 标题与 CGWindow 退化、设置页视觉与实际采样。

## 11. 风险与反模式

- ❌ 默认开启（Recall 2024-05 死因：opt-out 变 opt-in 才活下来）
- ❌ 明文存储原始观察（Beaumont/TotalRecall：「两行代码偷走一切」）
- ❌ 先感知后找场景（监控心态；每个源必须挂消费方）
- ❌ 感知幻觉当事实：视觉概括会错——呈现消费时标注「它以为」，可纠正（纠正本身是最好的记忆）
- ❌ 授权疲劳对抗用户（macOS 周弹窗时引导用户关掉感知而不是教用户忽略弹窗）
- ❌ 黑洞感：感知开着但日志页看不到东西（透明失效 = 信任破产）

## 附录：业界参考（2026-07-27 调研）

- **Microsoft Recall**：快照（几秒+变化触发）→ 本地 OCR+向量化；2024-05 默认开 → ICO 问询 + TotalRecall PoC → 回炉：opt-in、Hello ESS 在场证明、TPM 封装密钥、VBS Enclave、三层过滤（敏感分类/应用站点名单/隐私窗）、按时段/应用/站点删除、托盘状态图标；2025-04 随 24H2 推送（仍标 preview）。Signal 用 DRM 标志让 Recall 截全黑（防御方声明须尊重）。
- **Rewind.ai**：ScreenCaptureKit ~2s/帧 + 本地 Vision OCR + 本地 ASR，H.264 压缩（宣称 3750×），只存本地；Ask 功能上云（GPT-4）；2024 转 Limitless（Consent Mode 声纹+口头同意；加密云 Confidential Cloud）；2025-12 被 Meta 收购，Rewind 停服。
- **screenpipe**（开源）：事件驱动采集（OS 事件触发同时间戳抓截图+a11y 树，OCR 兜底）+ SQLite FTS5 + localhost API/MCP 喂 agent——本设计采集层的主要参照。
- **macOS TCC**：15+ 录屏权限每周/每重启重新弹窗；菜单栏紫色录屏指示器为平台强制。
