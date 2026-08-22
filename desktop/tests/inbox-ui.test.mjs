import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
const homeChatSource = await readFile(new URL("../src/components/HomeChat.vue", import.meta.url), "utf8");
const homeContextSource = await readFile(new URL("../src/components/HomeContextPanel.vue", import.meta.url), "utf8");
const homePluginsSource = await readFile(new URL("../src/components/HomePlugins.vue", import.meta.url), "utf8");
const panelAppSource = await readFile(new URL("../src/components/PanelApp.vue", import.meta.url), "utf8");
const brainSource = await readFile(new URL("../src/lib/brain.ts", import.meta.url), "utf8");

test("pet window consumes the full pending-confirm queue", () => {
  assert.match(appSource, /onPendingConfirms/);
  assert.match(appSource, /sendConfirmBatch/);
  assert.match(appSource, /pendingConfirms\.length\s*>\s*1/);
});

test("pet window can approve or reject every pending confirmation at once", () => {
  assert.match(appSource, /function decideAllPending/);
  assert.match(appSource, />全部批准</);
  assert.match(appSource, />全部拒绝</);
  assert.match(appSource, /pendingConfirms\.value\.map/);
});

test("pet window keeps a short approval guard so repeated clicks cannot hit stop", () => {
  assert.match(appSource, /approvalGuard/);
  assert.match(appSource, /releaseApprovalGuard\(delay = 850\)/);
  assert.match(appSource, /v-if="approvalGuard"/);
  assert.match(appSource, /v-else-if="!pending"[\s\S]*@interrupt="onInterrupt"/);
});

test("pet header uses a two-state plugins and chat control", () => {
  assert.match(appSource, /view === 'chat' \? 'plug' : 'chat'/);
  assert.match(appSource, /view === 'chat' \? 'plugins' : 'chat'/);
  assert.doesNotMatch(appSource, /class="pl-back"/);
});

test("pet window retires the old full confirmation dialog", () => {
  assert.doesNotMatch(appSource, /ConfirmDialog/);
  assert.match(appSource, /项待批准/);
});

test("large-window chat does not duplicate confirmation handling", () => {
  assert.doesNotMatch(homeChatSource, /ConfirmDialog/);
  assert.doesNotMatch(homeChatSource, /sendConfirm\b/);
  assert.doesNotMatch(homeChatSource, /case\s+"confirmation_needed"/);
});

test("session inspector can approve or reject a pending command", () => {
  assert.match(homeContextSource, /sendConfirmBatch/);
  assert.match(homeContextSource, />拒绝</);
  assert.match(homeContextSource, /仅允许本次/);
  assert.match(homeContextSource, /rememberLabelForSkill/);
  assert.match(homeContextSource, /remember:\s*remembered/);
  assert.match(homeContextSource, /approval\.params\?\.command/);
  assert.match(homeContextSource, /approval\.params\?\.cwd/);
});

test("session inspector retains privacy-safe processed items per conversation", () => {
  assert.match(homeContextSource, /yb-session-processed-v1/);
  assert.match(homeContextSource, /processedSessionKey/);
  assert.match(homeContextSource, />已处理</);
  assert.match(homeContextSource, /event\.task\?\.id/);
  assert.match(homeContextSource, /if \(approval\.skill === "watch_command"\) return "后台命令"/);
  assert.doesNotMatch(homeContextSource, /title:\s*approvalCommand/);
});

test("unanswered approvals survive restart as paused, non-executable snapshots", () => {
  assert.match(homeContextSource, /yb-session-pending-v1/);
  assert.match(homeContextSource, />上次未处理/);
  assert.match(homeContextSource, /重启后已暂停，不会自动执行/);
  assert.match(homeContextSource, /重新准备后会放入输入框/);
  assert.match(homeContextSource, /不要直接执行/);
  assert.match(homeContextSource, /removePendingSnapshot/);
  assert.doesNotMatch(homeContextSource, /InterruptedApproval[\s\S]{0,220}params/);
});

test("command approvals remember only the same action label", () => {
  assert.match(brainSource, /本会话允许相同命令/);
  assert.match(brainSource, /skill === "watch_command"/);
  assert.match(brainSource, /本会话不再询问/);
});

test("session inspector only shows preview fixtures behind an explicit demo flag", () => {
  assert.match(homeContextSource, /URLSearchParams\(window\.location\.search\)\.has\("demo"\)/);
  assert.match(homeContextSource, /if \(!previewDemo\) return \[\]/);
});

test("large-window plugin view routes confirmations only to the home inbox", () => {
  assert.doesNotMatch(homePluginsSource, /sendConfirm\b/);
  assert.doesNotMatch(homePluginsSource, /case\s+"confirmation_needed"/);
  assert.doesNotMatch(homePluginsSource, /confirm-bar/);
});

test("floating panel consumes the full panel confirmation queue", () => {
  assert.match(panelAppSource, /onPendingConfirms/);
  assert.match(panelAppSource, /sendConfirmBatch/);
  assert.match(panelAppSource, /pendingConfirms\.length\s*>\s*1/);
  assert.doesNotMatch(panelAppSource, /sendConfirm\b/);
  assert.doesNotMatch(panelAppSource, /case\s+"confirmation_needed"/);
  assert.match(panelAppSource, /项待批准，去大窗批量处理/);
});

test("failed batch confirmation restores the shared queue and the old single API is retired", () => {
  assert.match(brainSource, /_pcRestore\(removed\)/);
  assert.doesNotMatch(brainSource, /export function sendConfirm\(/);
});
