import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const brainSource = await readFile(new URL("../src/lib/brain.ts", import.meta.url), "utf8");
const bannerSource = await readFile(new URL("../src/components/PermissionsBanner.vue", import.meta.url), "utf8");
const settingsSource = await readFile(new URL("../src/components/SettingsView.vue", import.meta.url), "utf8");
const appSource = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
const chatSource = await readFile(new URL("../src/components/HomeChat.vue", import.meta.url), "utf8");

test("input monitoring is part of the typed permissions contract", () => {
  assert.match(brainSource, /input:\s*boolean/);
  assert.match(brainSource, /"ax"\s*\|\s*"screen"\s*\|\s*"input"/);
});

test("permission surfaces explain and link input monitoring", () => {
  assert.match(bannerSource, /Privacy_ListenEvent/);
  assert.match(bannerSource, /输入监控/);
  assert.match(settingsSource, /Privacy_ListenEvent/);
  assert.match(settingsSource, /输入监控/);
});

test("pet and chat surfaces treat missing input monitoring as actionable", () => {
  assert.match(appSource, /!perms\.value\.input/);
  assert.match(chatSource, /!perms\.value\.input/);
});
