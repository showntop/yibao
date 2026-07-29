import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const brainSource = await readFile(new URL("../src/lib/brain.ts", import.meta.url), "utf8");
const homeSource = await readFile(new URL("../src/components/HomeFeed.vue", import.meta.url), "utf8");

test("feed response carries normalized running tasks", () => {
  assert.match(brainSource, /export interface RunningTask/);
  assert.match(brainSource, /running_tasks:\s*RunningTask\[\]/);
  assert.match(brainSource, /running_tasks:\s*\[\]/);
});
