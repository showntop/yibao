# E 上下文唤起设计：划词动作条 + 截图即问（2026-08-08）

> 方向来源：高频场景探索结论——E（划词/截图上下文唤起）为「真本事的上下文唤起」第一刀。
> 差异化：别家唤起是一个框，译宝唤起是「团子跑过来」——光标旁浮层带小团子 + 动作直达，不是给输入框备料。

## 1. 目标与范围

**目标**：选中文字 → ⌘⇧U → 光标旁弹出动作条（解释/翻译/存素材），点一下直接出结果；⌘⇧I → 全屏 overlay 框选区域 → 截图 + 提问 → vision 直答。每天几十次的肌肉记忆级微交互。

**两个 slice（同一计划内顺序交付）**：
1. 划词动作条：⌘⇧U 抓到文字后弹光标旁浮层，动作直达（不再只塞 chip 等打字）。
2. 截图即问：⌘⇧I 框选 → 区域截图暂存 → 宠物窗输入问题 → vision 模型直答。

**不做**（YAGNI，后续单独立项）：热键配置化（现状全硬编码）、宠物窗程序化跳动（固定窗口约定 + 点击穿透热区是雷区，用浮层小团子「假装跑过来」）、富格式剪贴板保真（既有注释自认限制）、截图问答多轮对话。

## 2. 现状盘点（已实装，本次复用）

- ⌘⇧U 划词链路完整：`grab_selected_text()`（剪贴板接力 + CGEvent ⌘C + 修饰键等待，lib.rs:1291-1307）→ `pet-invoke-selection` 事件 → App.vue 存 `selectionCtx` chip。
- 截图管线：`MacScreenshotter`（mss+PIL，host_mac.py:121-153）已有整屏 `capture()` 与窗口裁剪 `capture_window()`（region dict 先例）；`ComputerUseClient` 视觉双 provider 配置（llm.py:335+）；`describe_screen` 是「截图+prompt→文本」现成范式（llm.py:295-309）。
- 素材库：`zimeiti.mat_save`（LLM 摘要打标 → materials 表），api.toml direct 先例 `hot_mat_save`；但 mat_save 自带 `result.panel="zimeiti:materials"`，直调会弹素材库面板——本次需 `quiet` 抑制。
- 光标坐标：Rust 侧 `device_query` 每 40ms 读全局光标（点击穿透轮询，lib.rs:1186+），坐标系已按 scale 换算与 Tauri 逻辑坐标一致。
- 窗口模式：预创建隐藏 + show/hide 复用（home/panel 窗先例）；vite 多页入口（vite.config.ts）。
- 事件桥：webview `emit` → 全窗广播；App.vue `onEvent` 已处理 `action_result`/`final_reply`/`notice` 等 kind。

## 3. 架构

### 3.1 划词动作条

```
⌘⇧U → Rust 抓选中文字（现有线程）
     ├─ emit pet-invoke-selection {text} → 主窗存 selectionCtx（现有；不再强制展开，保持静默）
     └─ 有文字 → invoke-bar 小窗光标旁落位 + show + focus
           InvokeBar.vue（团子小头像 + 解释/翻译/存素材/×）
           ├─ 点按钮 → emit invoke-action {action} → 自隐
           ├─ Esc / blur → 自隐
           └─ 主窗 App.vue onInvokeAction：
                 explain/translate → 展开 + submit(动作话术) 走现有 run（selectionCtx 自动拼入）
                 save → panelAction("zimeiti.invoke_mat_save", {text}) → 直调落库
                        → action_result 回执气泡「已存素材：《title》」+ success 闪现（不弹面板）
```

- **invoke-bar 窗口**：setup 预创建隐藏，transparent/decorations(false)/always_on_top/skip_taskbar，328×56。落位 = 光标右下偏移，纯函数 `clamp_bar_pos` 做屏幕边缘翻转（cargo 单测）。
- **静默优先**：⌘⇧U 不再强制展开宠物窗（改动 `onPetInvokeSelection`：存 chip 不 expand）；用户选动作才展开。存素材不展开——`flashValence("success")` 400ms 闪现 + 气泡待见。
- **quiet 抑制**：`ApiMethod` 加 `quiet: bool = False`；`handle_panel_action` 在 quiet 时跳过 panel 事件（action_result 照发）。api.toml 新增 `invoke_mat_save`（direct + quiet）。

### 3.2 截图即问

```
⌘⇧I → snip 全屏 overlay 窗铺满光标所在显示器 + focus（emit snip-start 复位）
      SnipOverlay.vue：拖拽画矩形（Esc 取消 / 单击或 <8px 取消）
      → finish_snip {rect 逻辑坐标} → Rust 换算物理像素（snip_abs_rect 纯函数，单测）
      → 隐藏 overlay → write_to_brain snip_capture {left,top,width,height}
      → emit snip-captured {w,h} → 主窗展开 + snipCtx chip「区域截图 WxH，想问什么？」
sidecar：snip_capture → MacScreenshotter.capture_region → b64 暂存 snip_ctx（TTL 300s，可多次提问）
主窗 submit：snipCtx 在 → 走 vision_query {question}（不走 run，不占对话历史）
sidecar：vision_query → answer_image_query(_wvision, b64, question) → final_reply 事件 → run_done 复位
```

- **vision_query 独立通道**：复用 run 的事件/run_done 协议（id 0 与 run_input 一致），壳侧状态机零改动。
- **`serve_async` 注入 `vision_client`**：测试注入假 client（`provider=FakeProvider()` 先例）；生产默认 None → 走 `vision_api_key() and computer_use_enabled()` 建 `ComputerUseClient()`。
- **`_peek_snip(stash)`**：新鲜返回 b64 不清空（同一截图可追问），过期丢弃。仿 `_consume_invoke_context` 但非一次性。

### 3.3 错误处理

- 无选中文字（⌘C 静默失败/剪贴板未变）：不弹动作条，退化为旧行为（展开+无 chip）。
- 视觉未配置（`_wvision is None`）：vision_query 回 error 事件「视觉端点未配置」。
- 截图过期/未框选：error 事件「截图已过期或尚未框选，请 ⌘⇧I 重新框选」。
- vision API 失败：`answer_image_query` 返 None → error 事件；全部异常路径静默降级，不炸 sidecar（stderr 日志）。
- 屏幕录制权限缺失：mss 截图黑/空——PermissionsBanner 已有 screen 权限入口，不另做引导。

### 3.4 测试策略

- **sidecar（pytest）**：capture_region（monkeypatch mss）；answer_image_query（假 client）；`_peek_snip`（新鲜/过期/空）；serve_async 端到端 vision_query（注入 vision_client + 预置 snip_ctx）；snip_capture 无 host 静默；panel_action quiet 抑制 panel 事件。
- **Rust（cargo test）**：`clamp_bar_pos` 边缘翻转；`snip_abs_rect` 逻辑→物理换算（含 scale、负原点）。
- **前端**：vue-tsc + vite build（仓内无单测框架，不新设）。
- **真机验收**：⌘⇧U 三动作、⌘⇧I 框选问答、Esc/blur 收起、多显示器落位。

## 4. 界面与文案

- 动作条：团子 28px 头像 + 三按钮「解释 / 翻译 / 存素材」，tokens.css slate/sky 体系，玻璃 L3 材质（浮层）。
- 翻译话术（组装进 run）：「把这段文字翻译成中文（如果它已经是中文，就翻译成英文）」；解释话术：「解释这段文字，讲清要点」。
- SNIP_QA_PROMPT：屏幕问答助手，200 字以内，只依据截图可见内容，看不到就明说。
- 设置页热键清单补两行：⌘⇧U 划词唤起 / ⌘⇧I 截图即问。

## 5. 风险与雷区

- **不动宠物主窗**（固定窗口约定 + 点击穿透热区坐标算死，window.ts:1 / lib.rs:1204 注释）。
- **mat_save 弹面板**：必须 quiet，否则 780×580 面板抢工作焦点，违背静默优先。
- **多显示器**：落位/overlay 都按「光标所在显示器」处理；物理原点可能为负（副屏在主屏左侧），用 i64。
- **capabilities**：新窗（invoke-bar/snip）的 emit/listen/hide/invoke 权限需在 capabilities/*.json 覆盖（实现时验证，按现有窗先例补）。
