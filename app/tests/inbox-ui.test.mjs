import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
const homeChatSource = await readFile(new URL("../src/components/HomeChat.vue", import.meta.url), "utf8");
const homePluginsSource = await readFile(new URL("../src/components/HomePlugins.vue", import.meta.url), "utf8");

test("pet window consumes the full pending-confirm queue", () => {
  assert.match(appSource, /onPendingConfirms/);
  assert.match(appSource, /sendConfirmBatch/);
  assert.match(appSource, /pendingConfirms\.length\s*>\s*1/);
});

test("pet window retires the old full confirmation dialog", () => {
  assert.doesNotMatch(appSource, /ConfirmDialog/);
  assert.match(appSource, /项待批准，去大窗批量处理/);
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
