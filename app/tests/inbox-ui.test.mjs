import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");

test("pet window consumes the full pending-confirm queue", () => {
  assert.match(appSource, /onPendingConfirms/);
  assert.match(appSource, /sendConfirmBatch/);
  assert.match(appSource, /pendingConfirms\.length\s*>\s*1/);
});

test("pet window retires the old full confirmation dialog", () => {
  assert.doesNotMatch(appSource, /ConfirmDialog/);
  assert.match(appSource, /项待批准，去大窗批量处理/);
});
