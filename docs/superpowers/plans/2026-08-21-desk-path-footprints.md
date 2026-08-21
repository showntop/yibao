# 工位足迹 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 主对话里每次摊开工位只落一只脚印；收起不另起一行；脚印用居中淡字而不是虚线协作卡。

**Architecture:** 判定集中在 `home-desk-presence`（误触 / 开行 / 关行）。`HomeChat` 只在打开时 `append`，关只清内存。`work-thread` 把历史关行从纸上拿掉。纸面/线索把 `panelLink` 开行画成 `.path-print`。

**Tech Stack:** Vue 3 + TypeScript；app 仓 vitest。

## Global Constraints

- 一次到访一行：`摊开 闪念盘` / `用了 工具箱` / `已请 改登录`。不写 `收起` / `已走`。
- 再来一次是新脚印；收起后马上摊同一面且中间没有用户话/一轮工作，不追加。
- 脚印不是协作卡：无虚线框、无「展开 ›」。
- 不改 sidecar、小窗、Glance、工人脑顶栏「译宝请来」。
- 不合成「今天走了」摘要。不提交 git（除非用户明确要求）。

---

### Task 1: 误触判定 + 关行识别

**Files:**
- Modify: `app/src/lib/home-desk-presence.ts`
- Test: `app/src/lib/home-desk-presence.test.ts`

**Interfaces:**
- Produces: `isDeskPathOpenLine(text: string): boolean`；`isDeskPathCloseLine(text: string): boolean`；`isDeskPathBounce(last: DeskWork | null | undefined, next: DeskWork, since: readonly PathTalkBubble[]): boolean`；`shouldStampDeskPath(current: DeskWork | null, last: DeskWork | null, next: DeskWork, since: readonly PathTalkBubble[]): boolean`
- `PathTalkBubble`: `{ role: string; text: string; panelLink?: boolean; proc?: unknown; icon?: string }`

- [x] **Step 1: Write the failing tests**

```ts
describe("desk path bounce", () => {
  const notes = { plugin: "notes", title: "闪念盘" };
  const box = { plugin: "toolbox", title: "工具箱" };
  it("does not stamp a second print for the same surface with no talk in between", () => {
    expect(isDeskPathBounce(notes, notes, [])).toBe(true);
    expect(shouldStampDeskPath(null, notes, notes, [])).toBe(false);
  });
  it("stamps again after a user turn or a work piece", () => {
    expect(shouldStampDeskPath(null, notes, notes, [{ role: "user", text: "记一条" }])).toBe(true);
    expect(shouldStampDeskPath(null, notes, notes, [{ role: "ai", text: "记下了" }])).toBe(true);
  });
  it("stamps when the object changed", () => {
    expect(shouldStampDeskPath(null, notes, box, [])).toBe(true);
  });
  it("does not stamp while the same workstation is still open", () => {
    expect(shouldStampDeskPath(notes, notes, notes, [])).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `cd app && npx vitest run src/lib/home-desk-presence.test.ts`

- [ ] **Step 3: Implement bounce / open / close line helpers**

- [ ] **Step 4: Tests pass**

---

### Task 2: 纸上藏关行

**Files:**
- Modify: `app/src/lib/work-thread.ts`
- Test: `app/src/lib/work-thread.test.ts`

**Interfaces:**
- Consumes: `isDeskPathCloseLine` from `home-desk-presence`
- Produces: `groupThread` / `groupPages` skip `收起 …` / `已走 …`（含无 `·` 的短句）；开行仍进 misc

- [ ] **Step 1: Failing test** — `groupPages` of reminder + `已请 改登录` + `已走 改登录` + `收起 闪念盘` 只留下 reminder 和 `已请`

- [ ] **Step 2: Hide close stamps in `groupThread` (same skip as orphan)**

- [ ] **Step 3: Tests pass**

---

### Task 3: 只在打开时 stamp + 脚印脸

**Files:**
- Modify: `app/src/components/HomeChat.vue`
- Modify: `app/src/lib/home-chat-session.ts`（`livePathLine`）
- Modify: `app/src/components/HomeChatPaper.vue`
- Modify: `app/src/components/HomeChatThread.vue`
- Modify: `app/src/components/home-chat-faces.css`
- Test: `app/src/lib/home-desk-work.test.ts`

**Interfaces:**
- Consumes: `shouldStampDeskPath`, `deskPathOpen`, `isDeskPathOpenLine`
- Produces: `stampDeskClose` / `deskPathClose(` 调用从 HomeChat 消失；脚印 `.path-print`；`livePathLine` 给当前工位那只略深

- [ ] **Step 1: Source test** — HomeChat 含 `shouldStampDeskPath`、`path-print`；不含 `stampDeskClose`；paper/thread 含 `path-print`

- [ ] **Step 2: Implement watch/stamp/render/CSS**

- [ ] **Step 3: `npx vitest run src/lib/home-desk-presence.test.ts src/lib/work-thread.test.ts src/lib/home-desk-work.test.ts`**
