import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const home = readFileSync(resolve(import.meta.dirname, "../Home.vue"), "utf8");
const chat = readFileSync(resolve(import.meta.dirname, "../components/HomeChat.vue"), "utf8");
const paper = readFileSync(resolve(import.meta.dirname, "../components/HomeChatPaper.vue"), "utf8");
const thread = readFileSync(resolve(import.meta.dirname, "../components/HomeChatThread.vue"), "utf8");
const faces = readFileSync(resolve(import.meta.dirname, "../components/home-chat-faces.css"), "utf8");
const plugins = readFileSync(resolve(import.meta.dirname, "../components/HomePlugins.vue"), "utf8");
const glance = readFileSync(resolve(import.meta.dirname, "../components/HomePluginGlance.vue"), "utf8");

describe("plugin host stays on the desk", () => {
  it("does not hide the home desk when a workstation opens", () => {
    expect(home).toMatch(/deskWork \? capability : null/);
    expect(home).not.toMatch(/tab === ['"]home['"] && !sceneActive/);
    expect(home).toMatch(/plugin-host\.on-desk/);
    expect(home).not.toMatch(/plugin-host\.on-desk \{[\s\S]*?width: 0;/);
    expect(home).toMatch(/--yb-work-w/);
    expect(plugins).not.toMatch(/Teleport to="#yb-desk-work-body"/);
  });

  it("names the switch as 委派 on the paper, not a plugin room", () => {
    expect(chat).toMatch(/HomeDeskWork/);
    expect(chat).toMatch(/deskPathOpen/);
    expect(chat).toMatch(/shouldStampDeskPath/);
    expect(chat).not.toMatch(/stampDeskClose/);
    expect(chat).toMatch(/HomeHostAsk/);
    expect(plugins).toMatch(/takeDeskOrigin/);
    expect(plugins).toMatch(/v-if="!props\.scene" class="bench"/);
    expect(glance).toMatch(/setDeskOrigin/);
    expect(glance).toMatch(/摊开/);
    expect(glance).toMatch(/isDeskLivePlugin/);
    expect(glance).toMatch(/正在用/);
    expect(glance).toMatch(/liveKind/);
    expect(chat).toMatch(/deskKind\(/);
    expect(chat).not.toMatch(/:receipt=/);
    expect(chat).toMatch(/live-panel/);
    expect(chat).not.toMatch(/正在和「/);
    expect(chat).not.toMatch(/协作结束/);
    expect(paper).toMatch(/path-print/);
    expect(thread).toMatch(/path-print/);
    expect(faces).toMatch(/\.path-print/);
  });
});
