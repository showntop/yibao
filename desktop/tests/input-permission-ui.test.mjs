import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const brainTypesSource = await readFile(new URL("../src/protocol/brain-types.ts", import.meta.url), "utf8");
const brainClientSource = await readFile(new URL("../src/services/brainClient.ts", import.meta.url), "utf8");
const bannerSource = await readFile(new URL("../src/components/pet/PermissionsBanner.vue", import.meta.url), "utf8");
const settingsSource = await readFile(new URL("../src/views/settings/PrivacySection.vue", import.meta.url), "utf8");
const petSource = await readFile(new URL("../src/windows/pet/PetWindow.vue", import.meta.url), "utf8");
const chatSource = await readFile(new URL("../src/views/chat/HomeChat.vue", import.meta.url), "utf8");

test("input monitoring is part of the typed permissions contract", () => {
  assert.match(brainTypesSource, /input:\s*boolean/);
  assert.match(brainClientSource, /"ax"\s*\|\s*"screen"\s*\|\s*"input"/);
});

test("permission surfaces explain and link input monitoring", () => {
  assert.match(bannerSource, /Privacy_ListenEvent/);
  assert.match(bannerSource, /输入监控/);
  assert.match(settingsSource, /Privacy_ListenEvent/);
  assert.match(settingsSource, /输入监控/);
});

test("pet and chat surfaces treat missing input monitoring as actionable", () => {
  assert.match(petSource, /!perms\.value\.input/);
  assert.match(chatSource, /!perms\.value\.input/);
});
