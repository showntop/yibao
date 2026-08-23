# 能力接入统一设计：底座 / 插件 / Skill / MCP

日期: 2026-08-23 | 修订: 按 Tool / Skill / CapabilityRecord 三分层重写 | 前篇: specs/2026-07-16-desktop-agent-design.md（总原则）、specs/2026-07-18-yibao-v2-plugin-architecture.md（插件）、research/2026-07-16-landscape-research.md（调研） | 依据: sidecar/src/yibao_brain/{tools,core_tools,plugins,genpanel,panel,invoker,ipc,llm}.py 现行实现（2026-08-23 目录重构：skills→tools、skills_real→core_tools、skills_composite→composite_tools）

## 范围

1. **能力接入统一模型**：core / plugin / skill / mcp 四种来源并列，统一接入；差异只在「发现与翻译（adapter）」
2. **词汇对齐**：执行单元 `class Skill → class Tool`；说明书叫 Skill（SKILL.md）；台账行叫 `CapabilityRecord`。本期一次改完，不做旧数据兼容
3. **两级台账**：登记单位 = 来源（`CapabilityRecord`）；展开单位 = Tool（仅 plugin / mcp）
4. **适配层**：四种来源各一个 Manager，对登记单位：discover / install / uninstall / update / status
5. **热加载**：reload = adapter 重跑 discover + **同 id 替换** / 消失注销 / 新增注册
6. **安全**：一切 Tool 过 `ToolInvoker`；Skill 本身不过闸门
7. **管理面**：P3；P1 对话接入走现有 `skills.*`
8. **演进** P1a → P3

对应总原则（research/2026-07-16-landscape-research.md:77）：*工具=MCP、技能=SKILL.md、动态解题=code execution*。MCP 是外设协议，内部执行单元仍是 Tool。相对 v2 §12-7「MCP 不算 v2」改判为 P2 外设接入。

## 对象模型（拍板）

三种对象，不要叠层：

```
CapabilityRecord          台账一行（装 / 卸 / 停作用在这里）
        │
        │  source_type 四选一，并列
        ├── core      → Tool[]
        ├── plugin    → Tool[]  +  可选 Skill[]  +  可选面板/数据/记忆
        ├── skill     → Skill（独立 SKILL.md）
        └── mcp       → Tool[]
                              │
                              ▼
                         ToolInvoker   只执行 Tool
```

**`CapabilityRecord`** — 登记单位，管理面上的一行。

```text
CapabilityRecord
  id                 coding | skill:ppt | mcp.github | core.perception
  source_type        core | plugin | skill | mcp
  source             SourceRef {path|url|server, version, installed_at}
  status             active | disabled | error
  privileged         管理面不可卸、不可 disable
  tools[]            它展开出来的 Tool id
  bundled_skills[]   仅 plugin：包内自带的 Skill id
```

状态持久化到 `SourceStore`（id / source_type / source / status / version）。重启后扫描与存储对账（不能只活在内存），规则：扫描有、存储无 → 注册（status 按上次记录或 default_status）；存储有、扫描无 → 注销 / 标 error；两边都有 → 以扫描为准（同 id 替换）。

**`Tool`** — 执行单位。即 `tools.py` 的 `class Tool`（原 skills.py 的 `class Skill`）。有 `run()`、风险、给 LLM 的 schema。只有它进 `ToolInvoker`。

```text
Tool
  id            coding.start | screenshot | notes.keep
  default_risk  L0–L4
  run(params, ctx) → ActionResult
  precheck / safe_result / openai_schema
```

**`Skill`** — 一篇 SKILL.md。不是 Tool，没有 `run()`，不进 `ToolInvoker`。执行方式（2026-08-23 use_skill 重构）：底座 `use_skill` 把 SKILL.md+references 说明书展开进**主上下文**（与 `use_plugin` 对称），agent 在工具循环里读说明、调 `code_exec` 等完成——不再单轮生成。

```text
Skill
  id       skill:ppt（独立安装）或 zimeiti:ppt（插件自带）
  owner    None（独立）或 "zimeiti"（插件 bundle）
  body     SKILL.md 文本
```

**Skill 命名空间（防跨来源撞名）**：独立安装的 Skill id 一律带 `skill:` 前缀；插件 bundle 的按 `<owner>:<name>`。`use_skill` 查时 owner 可省略——命中唯一则直取，多命中选择或报歧义。台账 `CapabilityRecord.id` 四域不交叠：`core.*` / `<plugin id>` / `skill:<name>` / `mcp.<server>`。

plugin 作为**发行包**可可选带 `skills/*.md`。那些 Skill 挂在该插件的 `CapabilityRecord.bundled_skills` 上，不另开 `source_type=skill` 行。卸插件时包内 Skill 一起走。独立从用户目录 / GitHub 装的，才单独占一行。

生效风险：`effective_risk = max(declared, source_floor)`，只许收紧。

## 关键设计决策

### A. 统一模型：出口统一，入口各配 adapter

**现状已经是统一出口**，保留并按词汇改名：

- 注册：`ToolRegistry`（现 `SkillRegistry`，tools.py:162）命名空间强制（`<pid>.<tool>`）
- 暴露：`use_plugin` 路由式展开（现 UsePluginSkill，id 与 LLM 名仍是 `use_plugin`）——默认隐藏，按需展开
- 执行：`ToolInvoker`（propose → L0–L4 / 确认 / 记忆 → execute → `ActionResult`），**只有 Tool** 走这条闸门
- 面板：`ActionResult.panel` → `panel_payload` → `panel` 事件
- 命名：`llm_name` 点号→下划线发给 LLM，回调 `resolve_llm_name` 映射回

```
来源层         底座 core_tools.py     插件 plugins/*       技能库 SKILL.md      MCP servers
                  │                     │                     │                   │
适配层         CoreManager          PluginManager         Skill 线            McpManager
                  │                     │                     │                   │
台账层         CapabilityRecord（登记单位；SourceStore 持久化）
                  │
工具层         ToolRegistry（展开单位 = Tool）
                  │
暴露层         core Tool 常驻；plugin / mcp 经 use_plugin 展开；Skill 经 use_skill 展开说明书（同语义）
                  │
执行层         ToolInvoker → ActionResult + panel 事件 + 审计
```

**一句话**：注册 / 暴露 / 执行 / 面板 / 审计已是统一的；本文补适配层、台账、热加载、管理面，并把误用的 `Skill` 基类改回 Tool。

Skill 线 P1 先做完现有 `plugins/skills` 桥（单入口 + 内部索引），不先抽一等 `SkillManager`。独立 Skill 的登记行仍写入 `CapabilityRecord`。

### B. 词汇与协议改名（本期一次改完，不做旧数据兼容）

领域词：

| 概念 | 领域名 | 代码名 |
|---|---|---|
| 台账行 | Capability | `CapabilityRecord` |
| SKILL.md 说明书 | Skill | `Skill` |
| 可执行动作 | Tool | `class Tool`（现状 `class Skill`） |

进程内（基类）：

| 现状 | 改为 | 说明 |
|---|---|---|
| `class Skill`（原 skills.py:36，现 tools.py） | `class Tool` | 执行单元 |
| `SkillContext` | `ToolContext` | 跟着基类 |
| `SkillRegistry` | `ToolRegistry` | 工具登记；台账另用 `CapabilityRecord` |
| `UsePluginSkill` | `UsePluginTool` | **id 与 LLM 名仍是 `use_plugin`** |
| `EchoSkill` 等子类 | `EchoTool` 等 | 同期机械替换 |
| `register_real_skills` | `register_core_tools` | |
| `register_composite_skills` | `register_composite_tools` | |
| `make_tools(ctx)` | **保持** | 插件约定已经是 Tool，加载器按函数名查找 |

协议与跨边界字段（同期改，无双字段、不迁旧库、不读旧事件）：

| 现状 | 改为 | 改动面 |
|---|---|---|
| `Action.skill_id` | `Action.tool_id` | ipc.py + 所有构造 / 读取 |
| `ToolCall.skill_id` | `ToolCall.tool_id` | llm.py + loop / panel / bridge |
| `ToolCallDelta.skill_id` | `ToolCallDelta.tool_id` | llm.py 流式拼接 |
| 审计表 `actions.skill_id` | `actions.tool_id` | 新列名；旧库直接重建或丢弃 |
| 事件 JSON `action.skill_id` | `action.tool_id` | 桌面 / Rust / 手机 |
| `confirm_meta.skill_id` | `confirm_meta.tool_id` | server / mobile |
| 前端 `a.skill_id`、`procSkip` | `a.tool_id` | proc.ts、确认条、宠物窗、面板 |
| Rust `event_recorder` 读 `skill_id` | 读 `tool_id` | 特判 `use_plugin` 仍比 tool id |

**不改：**

- `use_plugin` 的 Tool id 与 LLM 名（仍只展开插件；系统提示、前端 `procSkip`、Rust 跳过过程行继续认这个字符串）
- `make_tools` 函数名
- `plugins/` 目录名
- LLM 名规则（点号→下划线）与命名空间强制

漏改靠全量测试兜底，全绿才 commit。旧审计行、旧会话事件、旧手机确认队列一律不兼容，按新字段写。

### C. 两级台账

- **登记单位 = `CapabilityRecord`**：core 组、插件、独立 Skill、MCP server。装 / 卸 / 更 / 停作用在这里。
- **展开单位 = Tool**：仅 plugin 与 mcp。LLM 经 `use_plugin` 展开后才看见该来源的 Tool。
- **Skill 不展开成 Tool**。独立 Skill 与插件包内 Skill 都经底座 `use_skill` 展开说明书使用。
- core 的 Tool 常驻，不走展开。
- MCP server 挂 100 个工具不爆台账：先占一行，展开后才进 LLM 上下文。

### D. 适配层

```python
class CapabilityManager(ABC):
    discover() -> list[CapabilityRecord]
    install(source) -> CapabilityRecord
    uninstall(id) / update(id)
    status(id) -> Status
```

职责两层：**来源生命周期**（对登记单位）+ **工具翻译**（Source → Tool[]）。返回登记单位，不返回 `Tool`；MCP 工具翻译成 `Tool` 再挂到该 server 的 `tools[]` 上。P1 Skill 线走 `plugins/skills` 桥，不先实现一等 `SkillManager`。

- `CoreManager`：只读（`register_core_tools` 收编），不可装卸
- `PluginManager`：收编 `load_plugins`（plugins.py:534），补增量注册 / 替换；扫到 `skills/*.md` 写入 `bundled_skills`
- Skill 线（P1 = 底座 `use_skill` + `plugins/skills` 管理）：技能索引共享底座 `tools/skills_index.py`（扫 `$YIBAO_SKILLS_DIR` → `data_dir()/skills`，递归发现含嵌套集合）。**底座 `use_skill(skill)`**（L0 只读）把 SKILL.md+references 全文作为 ActionResult.data.text 回填 messages → 说明书进主上下文，agent 循环执行（与 `use_plugin` 对称）；`plugins/skills` 只做管理：`skills.list`（清单）/`refresh`（热加载）/`install`（git clone 集合容错）/`import`（固化+单形态激活）。每篇独立 Skill 写一行 `CapabilityRecord`
- `McpManager`（P2）：连接 server → 拉工具表 → 逐个译成 `Tool`（`run` 时 JSON-RPC 转发）→ 维护 online / offline / error

固化（`skill_import` 生成 manifest 转正）后置 P3+。

### E. 热加载

- **reload = adapter 重跑 discover + 同 id 替换 + 消失注销 + 新增注册**
- 不是「已注册跳过」。跳过会导致改了代码仍跑旧对象
- `ToolRegistry` 补：`unregister(id)`、`register` 遇已存在则替换、`set_status`
- 触发：`capability.refresh`、目录监听（可选）、MCP 挂载 / 断开
- 失败隔离沿用 plugins.py:556（单插件失败不拖垮）
- **代码插件仍建议重启**（`importlib` 模块缓存）；若要热更代码插件，reload 时需先从 `sys.modules` 清掉 `yibao_plugin_*` 模块再 `_import_file`（plugins.py:659）。热加载优先服务 Skill 目录与 MCP 连接。此条改判 v2 §3.2「热加载不做」

### F. 安全与信任分级

| 来源 | source_floor | 默认策略 |
|---|---|---|
| core | 按声明 | 随仓库，最高信任 |
| plugin | 按 manifest 声明 | 作者发布 |
| skill | L1 只读 | open 生态，prompt 注入面；副作用一律转 `code_exec`（L3） |
| mcp | 声明与 floor 取严 | 第三方远程；挂载时展示工具清单确认 |

- 一切 **Tool** 过 `ToolInvoker`
- `use_skill` 是 Tool，所以「展开哪篇 Skill 说明书」过闸门；Skill 正文（说明书）不进 Invoker
- `skills.install`（拉 GitHub）至少 L3
- 审计带 `source`（谁装、什么来源、什么版本）
- privileged（如 coding）：管理面不可卸、不可 disable

### G. 四种来源：定位与展示

**定位**：core = 本机感知与操作；plugin = 垂直产品包（可选 Tool / Skill / 面板 / 数据 / 记忆）；skill = 说明书；mcp = 外设。

| | core | plugin | skill | mcp |
|---|---|---|---|---|
| 加载 | 随进程常驻 | 启动扫 plugins/（可 refresh） | 热加载扫技能根目录 | 运行时挂载 |
| 执行 | 本地 Python Tool | 声明式 / 本地代码 Tool | `use_skill` 展开说明书 → agent 循环执行 | JSON-RPC 转发 |
| 状态 | 无 | 可选 db / 记忆；可选打包 Skill | 无（读文件） | 连接态 |
| 声明 | 代码 | manifest.toml | SKILL.md | MCP schema |
| 登记 | core 组 | 插件一行 | 独立 Skill 一行 | server 一行 |
| 展开 | 常驻，不走 use_plugin | 各 Tool | **无** | 各 Tool |
| 生命周期 | 不可管 | 目录 / refresh / disable | git pull / refresh | 连接 / 断开 |

展示：plugin / mcp 台账行点开看 Tool 列表；skill 行只显示这篇 Skill，没有 Tool 列表。Skill 无固定主屏位，对话触发。

### H. 重合与依赖

1. 一个 `CapabilityRecord` 只属于一个 `source_type`。plugin 包内 Skill 不是独立来源
2. 底座 → 插件单向注入 ctx；Skill 要副作用走 LLM 编排调 Tool（`agents.code_exec`）；插件间禁止代码级互调
3. 同一份 Skill 既在插件包里又被单独安装：插件自带优先，独立桥条目隐藏
4. privileged 不可卸、不可 disable
5. 插件面板走四型；Skill 产出走 `panel_gen`——同一渲染链路，归属不同

### I. 管理面（P3）

- P1 对话只用 `skills.list` / `run` / `install` / `refresh`（现有插件约定，过 `ToolInvoker`）
- P3 再加 `capability.list/install/uninstall/update/status/refresh` 作管理面统一入口；到时 `skills.install` 作别名或删除
- 管理表每行 = `CapabilityRecord`；行内操作按来源出；点开规则见 §G

### J. LLM 看见什么

| 来源 | 台账（人看） | LLM 默认看见 |
|---|---|---|
| core | 一组内置 | 全部 Tool，常驻 |
| plugin | 一行 | 默认藏；`use_plugin` 展开后出现 `coding.start` 等 |
| mcp | 一行 | 默认藏；P2 经 `use_mcp` 展开（展开对象 = 台账行，与 `use_plugin` 同语义，按 source_type 分入口） |
| skill | 一行 | **经 `use_skill` 展开说明书**，不会出现 `ppt` 这个函数 |

两条运行时路径：

- **跑 Tool**：对话选中或面板点按钮 → `ToolInvoker`（propose / precheck / decide / execute / 审计）→ `ActionResult`
- **跑 Skill**：对话调 `use_skill`（此调用本身走路径 A）→ 展开 Skill 说明书进上下文 → agent 在同一循环里读说明、调 `code_exec` 等完成；中途工具回到路径 A

## 演进路线

- **P0（现状）**：core + plugin，基类误叫 `Skill`，协议字段叫 `skill_id`
- **P1a**：词汇与协议一次改完——`class Skill → Tool`、`skill_id → tool_id`（IPC / LLM ToolCall / 审计 / 事件 / 前端 / Rust / 手机），`make_tools` 与 `use_plugin` 保持。不做旧数据兼容
- **P1b（已落地 2026-08-23）**：`CapabilityRecord`（SourceRecord）+ `SourceStore`（data_dir()/sources.json 持久化 + 重启对账/disabled 保留）+ replace 语义 reload（`unregister` / 替换注册 / `capability_refresh`）
- **P1c（已落地 2026-08-23）**：技能桥 —— 底座 `use_skill`（展开说明书进主上下文，对称 use_plugin）+ `plugins/skills` 管理（list / refresh / install 集合容错 / import 固化 + 单形态激活）+ 技能索引 `tools/skills_index.py` 共享
- **P2（已落地 2026-08-23）**：`McpManager`（sidecar/src/yibao_brain/mcp.py：stdio JSON-RPC client + 工具翻译 `mcp.<server>.<tool>` + 配置持久化）+ `use_mcp` 展开 + `mcp_add/connect/disconnect/list` 底座工具；`openai_tools` 组 id 提取改 rsplit 兼容三段 MCP id
- **P3（已完整落地 2026-08-23）**：台账 `tool_list`（core/plugin/mcp 分类 + 风险 + 展开态 + disabled）+ privileged 标记（coding 已标记）+ **管理面板 UI**（CapabilityLedger.vue，HomePlugins「能力台账」入口）+ **操作闭环 + Manager 收编**（`management.py`：`SourceRecord/SourceStore/SourceManager` ABC + `PluginManager`/`SkillManager`；`ledger.py`：`tool_uninstall/tool_disable/tool_enable/tool_status/tool_update`，底座与特权插件不可卸/不可停，`disabled_sources` 过滤 + SourceStore 持久化跨重启；面板直调同源 + 行内操作按钮）。**单形态激活**（§G.3 已落地）：`skill_import` 固化后桥条目停用
- **目录重构 + 命名规则（2026-08-23）**：工具实现归拢为 `sidecar/src/yibao_brain/tools/` 领域包（core / perception / composite / ledger / mcp / management / skills_index；`tools/__init__.py` re-export 保持 `from yibao_brain.tools import X` 兼容）。命名规则定为 `use_*` = 底座「展开领域能力」前缀（`use_plugin` / `use_skill` / `use_mcp`），插件内工具保持 `<领域>_<动词>`（id 命名空间强制）

## 测试与验收

- **改名闸门**：`Skill → Tool` 且 `skill_id → tool_id` 后全量测试绿（test_plugins / test_sandbox / test_agents / test_coding_plugin / 桌面与 Rust 事件测试）。代码与事件里不再出现 `skill_id` 字段。基线不回落
- **台账**：register / unregister / 同 id 替换；`capability.list` 按来源 / 状态过滤
- **reload**：改插件 Tool 实现后 refresh，新 `run()` 生效（不是跳过旧对象）；新增目录 refresh 后新 Tool 对 LLM 可见
- **技能桥**：扫技能根目录 → 索引，LLM 经 `use_skill` 展开说明书；refresh 增量生效
- **验收**：对 yibao 说「装个 PPT 技能」→ `skills.install` 拉取 → 下一轮「用 PPT 技能做《宇宙的诞生》」走 `use_skill` 展开 + agent 循环执行

## 已拍板决策（记录）

1. 来源并列：core / plugin / skill / mcp；Tool 才是执行粒度
2. 台账两级：登记 `CapabilityRecord`，展开仅 plugin / mcp 的 Tool；Skill 不展开
3. 基类 `Skill → Tool`；领域词 Skill = SKILL.md；台账 = `CapabilityRecord`
4. 本期协议字段一律 `skill_id → tool_id`（Action / ToolCall / 审计 / 事件 / 前端 / Rust / 手机）。无双字段、不兼容旧数据
5. `make_tools` 保持；`use_plugin` 的 Tool id 与 LLM 名保持
6. 技能桥先做完 `plugins/skills`；固化后置
7. plugin 可可选打包 Skill（打包关系，不是下级来源）
8. reload = 同 id 替换；代码插件仍建议重启
9. privileged 不可卸也不可 disable
10. 一切 Tool 过 `ToolInvoker`；`skills.install` 至少 L3
