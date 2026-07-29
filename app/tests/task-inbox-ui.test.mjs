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

test("home inbox renders running pending and completed zones", () => {
  assert.match(homeSource, /const runningTasks = ref<RunningTask\[\]>/);
  assert.match(homeSource, />进行中/);
  assert.match(homeSource, />待批准/);
  assert.match(homeSource, />已完成/);
  assert.match(homeSource, /panelAction\("agents\.task_list"/);
});

test("completed tasks leave the generic activity stream", () => {
  assert.match(homeSource, /completedTasks\s*=\s*computed[\s\S]*kind === "task"[\s\S]*slice\(0, 5\)/);
  assert.match(homeSource, /activityItems\s*=\s*computed[\s\S]*kind !== "task"/);
  assert.match(homeSource, /v-for="it in activityItems"/);
  assert.match(homeSource, /v-for="it in completedTasks"/);
});

test("home refreshes task inbox when agent tasks start or finish", () => {
  assert.match(homeSource, /e\.kind === "reminder"/);
  assert.match(homeSource, /e\.kind === "action_result"[\s\S]*startsWith\("agents\."\)/);
  assert.match(homeSource, /fetchFeed\(\)/);
});
