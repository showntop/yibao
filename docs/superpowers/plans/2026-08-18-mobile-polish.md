# 移动端打磨批（三轮评审遗留 Minor 清账）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一次清掉 B/M2/M3 评审台账 deferred 的 7 条 mobile 侧 Minor——审批页提示语义三修、深链分流直测、Feed 卸载竞态守卫、进行中计数去重、Android 分享文档补键。单席执行单席评审。

**Architecture:** 纯 mobile/ 改动（一处文档），零服务端、零新依赖；每项独立可测，TDD 先红后绿。

**Spec:** `docs/superpowers/specs/2026-08-14-mobile-companion-design.md` §5/§15（打磨不扩面）

## Global Constraints

- 只动 `mobile/`；不碰 sidecar/、app/（跨会话 WIP 区）
- vitest fake 注入；先红后绿；中文注释；一项一测不为凑数写空断言
- 72 用例基线全绿不许回归；`pnpm build` 绿

---

### Task 1: 审批页提示语义三修 + 深链分流直测 + Feed 两修 + 文档补键

**Files:**
- Modify: `mobile/src/views/Approvals.vue`、`mobile/src/state/approvals.ts` + `approvals.test.ts`
- Modify: `mobile/src/deeplink.ts` + `deeplink.test.ts`
- Modify: `mobile/src/views/Feed.vue` + `mobile/src/state/feed.test.ts`
- Modify: `mobile/docs/share-setup.md`

**七项（每项：先测后码）：**

1. **goneNote 清除**：Approvals 中 decide 返回 "ok" 时清空 goneNote（新成功操作不留陈旧「已在桌面处理」提示）。
2. **错误语义区分**：decide 的网络/异常路径不再复用 "gone" 文案——error ref 写「审批发送失败（网络）」且**不弹 goneNote**；只有 404 才是「该审批已在桌面处理」。approvals.ts 的 decide 仍返 "gone"（兼容既有调用）？**改三态返回 `"ok" | "gone" | "fail"`**，UI 按态分流（这是接口变更：Approvals.vue 是唯一消费方，同步改）。
3. **decide in-flight 防抖**：Approvals.vue 加 deciding ref（id 级锁），进行中该卡两按钮 disabled，防双击重复 POST。
4. **handleDeepUrl 直测**：deeplink.test 补三条——`yibao://pair?host=..&token=..` 走 pair 分支（save+push /chat）、`yibao://approvals` 走 approvals 分支（push /approvals，不 save）、`yibao://chat` 无效不动。若 handleDeepUrl 现为实现于 App.vue 内联，把它提取到 deeplink.ts 导出（App.vue 改调用，行为不变）。
5. **Feed 卸载竞态守卫**：Feed.vue onMounted 的 `await loadConn()` 之后构造 useFeed 前查 disposed 标志（onUnmounted 置位），已卸载则 return——消掉 interval 漏停。
6. **进行中计数去重**：Feed.vue 进行中区块显示时，statline 隐藏「进行中 N」段（`v-if` 或模板条件三元），区块隐藏时 statline 完整显示。
7. **文档补键**：share-setup.md Android body 模板补 `"url": ""`（与 iOS 表对齐；服务端本就容忍，纯文档一致性）。

- [ ] Step 1: 各项失败测试先行（1/2/3 在 approvals.test 或组件挂载测试按现有基建择最小面；4 在 deeplink.test；5/6 属组件行为——5 若无 Feed 组件测试基建，以 state 层「构造前守卫」的等价单测+实现注明；7 文档无测试）。
- [ ] Step 2: 实现七项 → `cd mobile && pnpm test && pnpm build` 全绿（基线 72 + 新增）。
- [ ] Step 3: 提交 `fix(mobile): 三轮评审遗留七项清账——审批提示语义/防抖/深链直测/Feed 竞态与去重/文档补键`。

---

## 验收

- [ ] mobile pnpm test/build 绿（+≥5 用例，无回归）
- [ ] 浏览器快查：审批页一次成功操作后无陈旧 goneNote；断网点批准提示「发送失败」非「已处理」；Feed 有进行中时计数只显示一处
