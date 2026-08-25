// @vitest-environment happy-dom
// SchemaPanel 渲染快照测试（结构级，非像素级）：喂各插件真实 *.schema.json，
// 断言关键字段/列数/行数/动作按钮数量正确——schema 手滑改坏会被这里抓住。
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SchemaPanel from "./SchemaPanel.vue";

// vitest 在 app/ 下跑，plugins/ 在仓库根（happy-dom 下 import.meta.url 不是 file 协议，走 cwd）
const PLUGINS_DIR = join(process.cwd(), "..", "plugins");

/** 读插件真实 schema 文件（测试直接绑死仓库里的协议产物）。 */
function schemaOf(rel: string): Record<string, any> {
  return JSON.parse(readFileSync(join(PLUGINS_DIR, rel), "utf-8"));
}

function mountPanel(schema: Record<string, any> | null, data: Record<string, unknown> = {}) {
  return mount(SchemaPanel, { props: { panel: "test:main", schema, data } });
}

// ---------- notes：list ----------

describe("notes list.schema.json", () => {
  const schema = schemaOf("notes/panel/list.schema.json");
  const rows = [
    { id: "a1", text: "第一句闪念", tags: "[]", created_at: 1700000000 },
    { id: "b2", text: "第二句闪念", tags: "[]", created_at: 1700000100 },
  ];

  it("每行渲染卡片：标题来自 text，行级一个删除动作", () => {
    const w = mountPanel(schema, { rows });
    const cards = w.findAll(".card");
    expect(cards).toHaveLength(2);
    expect(w.findAll(".card-title").map((c) => c.text())).toEqual(["第一句闪念", "第二句闪念"]);
    const btns = w.findAll(".card-actions button");
    expect(btns).toHaveLength(2);
    expect(btns[0].text()).toBe("删除");
  });

  it("点击删除上抛 notes.delete，params 解析出该行 id", async () => {
    const w = mountPanel(schema, { rows });
    await w.findAll(".card-actions button")[1].trigger("click");
    expect(w.emitted("action")).toEqual([[{ method: "notes.delete", params: { id: "b2" } }]]);
  });

  it("空列表渲染空态文案", () => {
    const w = mountPanel(schema, { rows: [] });
    expect(w.find(".empty-title").exists()).toBe(true);
    expect(w.findAll(".card")).toHaveLength(0);
  });
});

describe("notes widget.schema.json（无动作列表）", () => {
  it("条目无 actions 时不渲染动作按钮区", () => {
    const w = mountPanel(schemaOf("notes/panel/widget.schema.json"), {
      rows: [{ id: "a1", text: "一瞥", created_at: 1700000000 }],
    });
    expect(w.findAll(".card")).toHaveLength(1);
    expect(w.find(".card-actions").exists()).toBe(false);
  });
});

// ---------- forge：board / detail / form / doc ----------

describe("forge board.schema.json", () => {
  const schema = schemaOf("forge/panel/board.schema.json");
  const rows = [
    { id: "r1", title: "桌面宠物", pain: "孤独", status: "灵感" },
    { id: "r2", title: "日程助手", pain: "老忘事", status: "快筛过" },
    { id: "r3", title: "待定的", pain: "x", status: "不存在的列" }, // 未知状态归入第一列
  ];

  it("六列全渲染，卡片按状态分列、计数正确，未知状态不丢", () => {
    const w = mountPanel(schema, { rows });
    const cols = w.findAll(".board-col");
    expect(cols).toHaveLength(6);
    expect(w.findAll(".board-label").map((c) => c.text())).toEqual(
      ["灵感", "快筛过", "挑战中", "已立项", "已搁置", "已否决"],
    );
    expect(w.findAll(".board-count").map((c) => c.text())).toEqual(
      ["2", "1", "0", "0", "0", "0"], // r3 状态不匹配任何列 → 归入第一列
    );
    const firstColCards = cols[0].findAll(".card");
    expect(firstColCards).toHaveLength(2);
    expect(firstColCards[0].find(".card-title").text()).toBe("桌面宠物");
    expect(firstColCards[0].find(".card-sub").text()).toBe("孤独");
  });

  it("卡级动作一个（详情），点击上抛 forge.get 带该行 id", async () => {
    const w = mountPanel(schema, { rows });
    const card = w.findAll(".board-col")[1].find(".card");
    const acts = card.findAll(".card-hover-acts button");
    expect(acts).toHaveLength(1);
    expect(acts[0].text()).toBe("详情");
    await card.trigger("click"); // 卡片点击 = 首个卡级 action
    expect(w.emitted("action")).toEqual([[{ method: "forge.get", params: { id: "r2" } }]]);
  });

  it("未声明 drag/quick_add：卡片不可拖、列底无快捷新增框", () => {
    const w = mountPanel(schema, { rows });
    expect(w.find(".card.draggable").exists()).toBe(false);
    expect(w.find(".quick-add").exists()).toBe(false);
  });
});

describe("forge detail.schema.json", () => {
  const schema = schemaOf("forge/panel/detail.schema.json");
  const row = {
    id: "r1", title: "桌面宠物", pain: "孤独", who: "独立开发者", status: "挑战中",
    triage: "真痛点", verdict_reason: "", created_at: 1700000000, updated_at: 1700000100,
  };

  it("8 个字段行全渲染（标签 + 绑定值），8 个动作按钮、首个主按钮", () => {
    const w = mountPanel(schema, { rows: [row] });
    const fields = w.findAll(".detail-card .row");
    expect(fields).toHaveLength(8);
    expect(fields.map((f) => f.find(".k").text())).toEqual(
      ["标题", "痛点", "谁有", "状态", "快筛结论", "裁决理由", "创建", "更新"],
    );
    expect(fields[0].find(".v").text()).toBe("桌面宠物");
    expect(fields[3].find(".v").text()).toBe("挑战中");
    const btns = w.findAll(".detail-actions button");
    expect(btns).toHaveLength(8);
    expect(btns.map((b) => b.text())).toEqual(
      ["挑战", "竞品扫描", "出 PRD", "出原型", "看挑战文档", "看 PRD", "裁决", "删除"],
    );
    expect(btns[0].classes()).toContain("primary");
    expect(btns[1].classes()).toContain("ghost");
  });

  it("back 声明渲染返回按钮，点击上抛 forge.list", async () => {
    const w = mountPanel(schema, { rows: [row] });
    const back = w.find(".back");
    expect(back.text()).toContain("返回看板");
    await back.trigger("click");
    expect(w.emitted("action")).toEqual([[{ method: "forge.list", params: {} }]]);
  });

  it("动作 params 里的 $data 绑定解析为当前行值", async () => {
    const w = mountPanel(schema, { rows: [row] });
    await w.findAll(".detail-actions button")[4].trigger("click"); // 看挑战文档
    expect(w.emitted("action")).toEqual([
      [{ method: "forge.doc_read", params: { id: "r1", kind: "challenge" } }],
    ]);
  });
});

describe("forge verdict_form.schema.json", () => {
  const schema = schemaOf("forge/panel/verdict_form.schema.json");

  it("两个字段（text + textarea），提交合并表单值与 $data.id", async () => {
    const w = mountPanel(schema, { id: "r1", title: "桌面宠物" });
    const labels = w.findAll(".field .k");
    expect(labels).toHaveLength(2);
    expect(labels[0].text()).toContain("裁决");
    expect(w.find('input[type="text"]').exists()).toBe(true);
    expect(w.find("textarea").exists()).toBe(true);
    await w.find('input[type="text"]').setValue("已立项");
    await w.find("textarea").setValue("痛点真实，差异点清晰");
    await w.find("form").trigger("submit");
    expect(w.emitted("action")).toEqual([
      [{ method: "forge.verdict", params: { id: "r1", verdict: "已立项", reason: "痛点真实，差异点清晰" } }],
    ]);
  });
});

describe("forge doc.schema.json", () => {
  it("渲染标题 + markdown 正文（标题行转成 md-h）", () => {
    const w = mountPanel(schemaOf("forge/panel/doc.schema.json"), {
      id: "r1", title: "桌面宠物 · 挑战文档", text: "# 挑战记录\n第一问回答",
    });
    expect(w.find(".doc-title").text()).toBe("桌面宠物 · 挑战文档");
    expect(w.find(".doc-body .md-h").text()).toBe("挑战记录");
    expect(w.find(".doc-body").text()).toContain("第一问回答");
  });
});

// ---------- zimeiti：board（drag + quick_add） ----------

describe("zimeiti board.schema.json（拖拽 + 快捷新增）", () => {
  const schema = schemaOf("zimeiti/panel/board.schema.json");
  const rows = [
    { id: "t1", title: "选题A", angle: "角度A", status: "候选" },
    { id: "t2", title: "选题B", angle: "角度B", status: "已发布" },
  ];

  it("四列渲染，drag 声明让卡片可拖", () => {
    const w = mountPanel(schema, { rows });
    expect(w.findAll(".board-col")).toHaveLength(4);
    expect(w.findAll(".board-count").map((c) => c.text())).toEqual(["1", "0", "0", "1"]);
    expect(w.findAll(".card.draggable")).toHaveLength(2);
  });

  it("quick_add 限定候选列：只有第一列渲染输入框，回车上抛 zimeiti.add", async () => {
    const w = mountPanel(schema, { rows });
    const inputs = w.findAll(".quick-add");
    expect(inputs).toHaveLength(1);
    expect(inputs[0].attributes("placeholder")).toBe("快速记一条选题…");
    await inputs[0].setValue("新选题");
    await inputs[0].trigger("keyup.enter");
    expect(w.emitted("action")).toEqual([[{ method: "zimeiti.add", params: { title: "新选题" } }]]);
  });

  it("拖拽落列上抛 zimeiti.move：$column 解析为目标列 key", async () => {
    const w = mountPanel(schema, { rows });
    const card = w.findAll(".board-col")[0].find(".card");
    const written: string[] = [];
    // WKWebView 回归守卫：dragstart 必须写 dataTransfer，否则真机拖拽会话不建立
    const dt = { setData: (t: string, v: string) => written.push(`${t}:${v}`), effectAllowed: "", dropEffect: "" };
    await card.trigger("dragstart", { dataTransfer: dt });
    expect(written).toEqual(["text/plain:t1"]);
    expect(dt.effectAllowed).toBe("move");
    await w.findAll(".board-col")[1].trigger("drop"); // 写作中
    expect(w.emitted("action")).toEqual([
      [{ method: "zimeiti.move", params: { id: "t1", status: "写作中" } }],
    ]);
  });

  it("卡片 badge 行：platform + updated_at 相对时间，空 platform 跳过", () => {
    const now = Math.floor(Date.now() / 1000);
    const w = mountPanel(schema, {
      rows: [
        { id: "t3", title: "选题C", angle: "", status: "候选", platform: "小红书", updated_at: now - 120 },
        { id: "t4", title: "选题D", angle: "", status: "写作中", platform: "", updated_at: now - 7200 },
      ],
    });
    const cols = w.findAll(".board-col");
    expect(cols[0].findAll(".card-badge").map((b) => b.text())).toEqual(["小红书", "2 分钟前"]);
    expect(cols[1].findAll(".card-badge").map((b) => b.text())).toEqual(["2 小时前"]);
  });
});

describe("zimeiti hot.schema.json（双动作列表）", () => {
  it("每行两个动作，第二个动作的 url 绑定解析", async () => {
    const schema = schemaOf("zimeiti/panel/hot.schema.json");
    const rows = [{ id: "h1", title: "热榜话题", meta: "微博", url: "https://x.com/1", source_ref: "微博" }];
    const w = mountPanel(schema, { rows });
    const btns = w.findAll(".card-actions button");
    expect(btns.map((b) => b.text())).toEqual(["转选题", "存素材"]);
    await btns[1].trigger("click");
    expect(w.emitted("action")).toEqual([
      [{ method: "zimeiti.hot_mat_save", params: { url: "https://x.com/1" } }],
    ]);
    expect(w.find(".back").text()).toContain("返回看板");
  });
});

// ---------- zimeiti：materials（查看/删除双动作） ----------

describe("zimeiti materials.schema.json", () => {
  it("条目两个动作：查看（mat_get 阅读面板）/ 删除", async () => {
    const schema = schemaOf("zimeiti/panel/materials.schema.json");
    const w = mountPanel(schema, { rows: [{ id: "m1", title: "K3 评测", summary: "三点硬伤" }] });
    const btns = w.findAll(".card-actions button");
    expect(btns.map((b) => b.text())).toEqual(["查看", "删除"]);
    await btns[0].trigger("click");
    expect(w.emitted("action")).toEqual([[{ method: "zimeiti.mat_get", params: { id: "m1" } }]]);
  });
});

// ---------- zimeiti：record 录数据表单（declared-fields 提交护栏） ----------

describe("zimeiti record.schema.json（录数据表单）", () => {
  it("提交合并声明字段 + topic_id 绑定；跨表单残留键不混入", async () => {
    // 先挂 forge 裁决表再切到录数据表（同一组件实例）：旧表单键不得污染提交
    const w = mountPanel(schemaOf("forge/panel/verdict_form.schema.json"), { id: "r1", title: "x" });
    await w.find('input[type="text"]').setValue("已立项");
    await w.setProps({ schema: schemaOf("zimeiti/panel/record.schema.json"), data: { rows: [{ id: "t9" }] } });
    const text = w.find('input[type="text"]');
    const nums = w.findAll('input[type="number"]');
    expect(nums).toHaveLength(5);
    await text.setValue("小红书");
    await nums[0].setValue(100);
    await w.find("form").trigger("submit");
    expect(w.emitted("action")).toEqual([
      [{ method: "zimeiti.stat_add", params: { topic_id: "t9", platform: "小红书", views: 100, likes: null, comments: null, favorites: null, shares: null } }],
    ]);
  });
});

// ---------- agents：tasks board ----------

describe("agents tasks.schema.json", () => {
  it("四列看板，卡片两个动作（详情/停止）", () => {
    const schema = schemaOf("agents/panel/tasks.schema.json");
    const rows = [{ id: "k1", prompt: "修 bug", agent: "codex", status: "running", created_at: 1700000000 }];
    const w = mountPanel(schema, { rows });
    expect(w.findAll(".board-col")).toHaveLength(4);
    expect(w.findAll(".board-count").map((c) => c.text())).toEqual(["1", "0", "0", "0"]);
    const card = w.find(".board-col .card");
    expect(card.find(".card-title").text()).toBe("修 bug");
    expect(card.find(".card-sub").text()).toContain("codex");
    expect(card.findAll(".card-hover-acts button").map((b) => b.text())).toEqual(["详情", "停止"]);
  });
});

// ---------- 未知降级 ----------

describe("未知 schema 降级", () => {
  it("未知 type / null schema → 折叠 JSON，不炸", () => {
    const w1 = mountPanel({ type: "hologram" }, { rows: [] });
    expect(w1.find("details.fallback").exists()).toBe(true);
    expect(w1.find("summary").text()).toContain("hologram");
    const w2 = mountPanel(null, { rows: [] });
    expect(w2.find("details.fallback").exists()).toBe(true);
    expect(w2.find("summary").text()).toContain("schema 缺失");
  });
});
