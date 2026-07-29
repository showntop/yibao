import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
const homeChatSource = await readFile(new URL("../src/components/HomeChat.vue", import.meta.url), "utf8");
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

test("pet window retires the old full confirmation dialog", () => {
  assert.doesNotMatch(appSource, /ConfirmDialog/);
  assert.match(appSource, /项待批准/);
});

test("large-window chat routes confirmations only to the home inbox", () => {
  assert.doesNotMatch(homeChatSource, /ConfirmDialog/);
  assert.doesNotMatch(homeChatSource, /sendConfirm\b/);
  assert.doesNotMatch(homeChatSource, /case\s+"confirmation_needed"/);
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
