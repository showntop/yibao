# 感知 v2（B 源屏幕内容 + 首个消费闭环）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地感知 v2 眼睛：事件驱动 B 源采集（a11y 树文本优先/截图概括兜底）+ 三层过滤 + 出站闸门 + 透明（日志/状态灯）+ `load_screen_content` 消费闭环。Spec：`docs/superpowers/specs/2026-08-02-perception-v2-screen-design.md`。

**Architecture:** `perception.py` 加树文本序列化器 + `PerceptionSensors` screen 段（复用 A 源变化信号 + 心跳 + 三层过滤 + 预算闸）；server 装配 vision sampler（概括+敏感正则）；消费工具照 `LoadUserActivitySkill` 模式；前端设置开关（行内两段确认）+ 日志徽章 + Avatar observing 叠加点。

**Tech Stack:** Python（sidecar）+ pytest、Vue3+TS、Rust（仅转发已有 IPC，无新命令——确认走前端行内态）。

## Global Constraints

- sidecar 测试：`cd sidecar && uv run --extra dev pytest -q`（必须带 `--extra dev`）；前端 `cd desktop && npm run build`；Rust `cd desktop/src-tauri && cargo check`。
- 提交信息：中文 conventional commit，每任务一提交。
- 感知纪律：payload 一律 Fernet 加密（禁明文降级）；写失败只 print 不抛；secure input 当帧弃；a11y 树文本永不出站。
- `query_window` 默认源集合保持 `('app','activity')` 不变（load_user_activity 语义不动），新参数 `sources` 按需传。
- vision 概括是真实 GLM 调用：单测全用注入 fake，真机验收才走真调用。

---

### Task 1: B 源采集核心（perception.py：序列化器 + sensors screen 段 + 过滤 + 预算）

**Files:**
- Modify: `sidecar/src/yibao_brain/perception.py`（serialize_tree_text 新增；query_window 加 sources；PerceptionSensors 加 screen 段）
- Test: `sidecar/tests/test_perception.py`

**Interfaces:**
- Produces:
  - `serialize_tree_text(tree: dict, max_chars: int = 4096) -> str`（DFS 缩进行 `role: 标题或值`，行预算 300/子节点 50/缩进 ≤8 层/单值 ≤80 字；空→""）
  - `PerceptionSensors.__init__` 新增注入：`screen_sampler=None`（() → (tree_dict|None, screenshot_path|None, app, bundle_id, title)）、`vision_summarizer=None`（path → str|None）、`secure_input_checker=None`（() → bool）
  - sensors tick 的 screen 段契约：开关 `perception.screen`；触发 = `(app,title) 变化` 或 `距上次 screen 事件 ≥300s`；过滤 = L1 黑名单（内置 + `perception.blacklist` 列表）→ L2 隐私窗启发式 → L3 secure input；内容 = 有树存 kind='tree'（S3），无树且 vision_summarizer 可用 → 概括（过 `_sensitive_text`）存 kind='vision'（S3，payload 含 path）；预算 = screen 事件 ≤120/日、vision ≤30/日（内存计数，跨日重置）
  - `_sensitive_text(text) -> bool`（15-19 连续数字 / 18 位身份证 / password|密码[:=] 赋值模式）
  - `query_window(start_ts, end_ts, limit=2000, sources=None)`（None → 旧默认 ('app','activity')）

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_perception.py` 追加：

```python
# ---------- B 源：树文本序列化 ----------
def test_serialize_tree_text_compact_and_budget():
    from yibao_brain.perception import serialize_tree_text

    tree = {"role": "AXWindow", "title": "主窗", "children": [
        {"role": "AXButton", "title": "保存", "children": []},
        {"role": "AXTextArea", "value": "你好世界", "children": []},
        {"role": "AXGroup", "children": [
            {"role": "AXStaticText", "value": "深层文字", "children": []},
        ]},
    ]}
    text = serialize_tree_text(tree)
    assert "AXWindow: 主窗" in text and "AXButton: 保存" in text
    assert "AXTextArea: 你好世界" in text and "AXStaticText: 深层文字" in text
    assert serialize_tree_text({"role": "AXWindow", "children": []}) == ""
    big = {"role": "AXWindow", "title": "x" * 200, "children": []}
    assert len(serialize_tree_text(big, max_chars=50)) <= 51  # 截断+省略号


# ---------- B 源：sensors screen 段 ----------
def _screen_sensor(store, settings, *, screen_sampler, vision_summarizer=None,
                   secure_input_checker=None, clock=None):
    from yibao_brain.perception import PerceptionSensors

    s = PerceptionSensors(store, settings, app_sampler=lambda: None, idle_sampler=lambda: None,
                          screen_sampler=screen_sampler, vision_summarizer=vision_summarizer,
                          secure_input_checker=secure_input_checker, clock=clock or time.time)
    return s


def test_screen_event_on_change_stores_tree(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _open_store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    tree = {"role": "AXWindow", "title": "Safari — 天气", "children": []}
    samples = iter([("tree", tree, None, "Safari", "com.apple.Safari", "天气")])
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: next(samples))
    s.tick()
    rows = [r for r in store.list() if r["source"] == "screen"]
    assert len(rows) == 1 and rows[0]["kind"] == "tree" and rows[0]["sensitivity"] == "S3"
    assert "Safari — 天气" in rows[0]["payload"]["text"]
    store.close()


def test_screen_skips_unchanged_and_fires_heartbeat(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _open_store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    tree = {"role": "AXWindow", "title": "同页", "children": []}
    now = [1000.0]
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("tree", tree, None, "App", "com.x", "同页"),
                       clock=lambda: now[0])
    s.tick()                      # t=1000：首次记一条
    s.tick(); s.tick()            # 无变化不记
    assert len([r for r in store.list() if r["source"] == "screen"]) == 1
    now[0] += 301                 # t=1301：超心跳间隔 → 再记
    s.tick()
    assert len([r for r in store.list() if r["source"] == "screen"]) == 2
    store.close()


def test_screen_blacklist_and_privacy_window_and_secure_input(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _open_store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    tree = {"role": "AXWindow", "title": "x", "children": []}
    # L1 黑名单（内置 1Password）
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("tree", tree, None, "1Password", "com.1password.1password", "x"))
    s.tick()
    # L2 隐私窗（Chrome 无痕）
    s2 = _screen_sensor(store, settings,
                        screen_sampler=lambda: ("tree", tree, None, "Chrome", "com.google.Chrome", "新标签页 - 无痕浏览"))
    s2.tick()
    # L3 secure input
    s3 = _screen_sensor(store, settings,
                        screen_sampler=lambda: ("tree", tree, None, "App", "com.x", "y"),
                        secure_input_checker=lambda: True)
    s3.tick()
    assert [r for r in store.list() if r["source"] == "screen"] == []
    store.close()


def test_screen_tree_missing_uses_vision_with_sensitive_filter(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _open_store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    # 树为空 → vision 兜底；概括含卡号 → 敏感丢弃
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("empty", None, "/tmp/shot.png", "Canvas", "com.x", "画板"),
                       vision_summarizer=lambda path: "卡号 6222 0000 0000 0000 可见")
    s.tick()
    assert [r for r in store.list() if r["source"] == "screen"] == []
    # 概括正常 → 存 vision 条目（payload 含 path）
    s2 = _screen_sensor(store, settings,
                        screen_sampler=lambda: ("empty", None, "/tmp/shot.png", "Canvas", "com.y", "画板"),
                        vision_summarizer=lambda path: "Excalidraw 画板，有一个矩形")
    s2.tick()
    rows = [r for r in store.list() if r["source"] == "screen"]
    assert len(rows) == 1 and rows[0]["kind"] == "vision" and rows[0]["payload"]["path"] == "/tmp/shot.png"
    store.close()


def test_screen_daily_budget(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _open_store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    tree = {"role": "AXWindow", "title": "x", "children": []}
    n = [0]
    def sampler():
        n[0] += 1
        return ("tree", tree, None, "App", "com.x", f"第{n[0]}页")
    s = _screen_sensor(store, settings, screen_sampler=sampler)
    for _ in range(125):
        s.tick()
    assert len([r for r in store.list() if r["source"] == "screen"]) == 120  # 预算闸
    store.close()


def test_query_window_sources_param(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _open_store(tmp_path)
    store.append("app", "frontmost", {"app": "A"}, "S1", ts=100.0)
    store.append("screen", "tree", {"text": "T"}, "S3", ts=101.0)
    default = store.query_window(0, 200)
    assert all(r["source"] != "screen" for r in default)          # 旧语义不含 screen
    with_screen = store.query_window(0, 200, sources=("screen",))
    assert len(with_screen) == 1 and with_screen[0]["source"] == "screen"
    store.close()
```

（`_open_store(tmp_path)` 为该文件已有 helper——用临时 key 开 PerceptionStore；若无则用 `PerceptionStore(str(tmp_path/"obs.db"), key=os.urandom(32)` 形式补一个。`PerceptionSensors`/`time` import 按文件头部现有情况补。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -q -k "screen or serialize or sources"`
Expected: FAIL（serialize_tree_text / sources 参数 / 新注入不存在）

- [ ] **Step 3: 实现**

`perception.py` 改动：

1. 文件顶部常量与函数：

```python
SCREEN_HEARTBEAT_SECONDS = 300.0
SCREEN_DAILY_EVENT_CAP = 120
SCREEN_DAILY_VISION_CAP = 30
_BUILTIN_BLACKLIST = frozenset({
    "com.1password.1password", "com.apple.keychainaccess",
})
_PRIVATE_TITLE_MARKERS = ("无痕", "隐私浏览", "incognito", "inprivate", "private browsing")
_BROWSER_BUNDLES = frozenset({
    "com.google.Chrome", "com.apple.Safari", "com.microsoft.edgemac",
    "company.thebrowser.Browser",
})
_SENSITIVE_RES = (
    re.compile(r"\b\d{15,19}\b"),                     # 卡号
    re.compile(r"\b\d{17}[\dXx]\b"),                  # 身份证
    re.compile(r"(?i)(password|passwd|密码)[:：=]\s*\S+"),
)


def _sensitive_text(text: str) -> bool:
    return any(p.search(text or "") for p in _SENSITIVE_RES)


def serialize_tree_text(tree: dict, max_chars: int = 4096) -> str:
    """a11y 树 → 紧凑文本（B 源存储用）：DFS 缩进行 role: 标题或值。
    预算：300 行 / 每父 50 子 / 缩进 ≤8 层 / 单值 ≤80 字 / 总 ≤max_chars。空→""。"""
    lines: list[str] = []

    def walk(node: dict, depth: int) -> None:
        if len(lines) >= 300 or not isinstance(node, dict):
            return
        role = str(node.get("role") or "")
        label = str(node.get("title") or node.get("value") or "").strip()
        if role and label:
            lines.append("  " * min(depth, 8) + f"{role}: {label[:80]}")
        for child in (node.get("children") or [])[:50]:
            walk(child, depth + 1)

    walk(tree, 0)
    text = "\n".join(lines)
    return text[:max_chars] + ("…" if len(text) > max_chars else "") if text else ""
```

2. `query_window` 签名加 `sources=None`，SQL 源集合 `sources or ("app", "activity")`。
3. `PerceptionSensors.__init__` 加 `screen_sampler=None, vision_summarizer=None, secure_input_checker=None, clock=time.time`；新增状态 `_last_screen_key=None, _last_screen_ts=0.0, _screen_day="", _screen_events=0, _screen_visions=0`。tick 末尾加 screen 段：

```python
        # ---- B 源（屏幕内容，S3）：变化/心跳触发，三层过滤，树优先截图兜底 ----
        if not self.settings.get("perception.master") or not self.settings.get("perception.screen"):
            self._last_screen_key = None
            return
        self._roll_screen_day(now)
        if self._screen_events >= SCREEN_DAILY_EVENT_CAP:
            return
        sample = self.screen_sampler() if self.screen_sampler else None
        if not sample:
            return
        status, tree, shot, app, bundle_id, title = sample
        key = (app, title)
        if key == self._last_screen_key and now - self._last_screen_ts < SCREEN_HEARTBEAT_SECONDS:
            return
        if self._is_screen_filtered(bundle_id, title):
            return
        if self.secure_input_checker and self.secure_input_checker():
            return
        if status == "tree" and tree:
            text = serialize_tree_text(tree)
            if not text:
                return
            self.store.append("screen", "tree", {"app": app, "title": title, "text": text}, "S3", ts=now)
            self._screen_events += 1
            self._last_screen_key, self._last_screen_ts = key, now
        elif shot and self.vision_summarizer and self._screen_visions < SCREEN_DAILY_VISION_CAP:
            summary = self.vision_summarizer(shot)
            if summary and not _sensitive_text(summary):
                self.store.append("screen", "vision",
                                  {"app": app, "title": title, "text": summary, "path": shot}, "S3", ts=now)
                self._screen_events += 1
                self._screen_visions += 1
                self._last_screen_key, self._last_screen_ts = key, now

    def _roll_screen_day(self, now: float) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if day != self._screen_day:
            self._screen_day, self._screen_events, self._screen_visions = day, 0, 0

    def _is_screen_filtered(self, bundle_id: str, title: str) -> bool:
        blocked = set(_BUILTIN_BLACKLIST) | set(self.settings.get("perception.blacklist") or [])
        if bundle_id in blocked:
            return True
        if bundle_id in _BROWSER_BUNDLES:
            t = (title or "").lower()
            return any(m in t for m in _PRIVATE_TITLE_MARKERS)
        return False
```

（`_screen_events/_screen_visions/_screen_day` 的时钟用注入的 `clock`，app/activity 段不改；tick 里 now 的获取改用 self.clock()——若现有 tick 已用 time.time() 则 screen 段用 `self.clock()` 而旧段不动。）

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -q && uv run --extra dev pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/perception.py sidecar/tests/test_perception.py
git commit -m "feat(perception): B 源采集核心——树文本优先/截图兜底 + 三层过滤 + 日预算"
```

---

### Task 2: vision 概括 + server 装配 + config 键

**Files:**
- Modify: `sidecar/src/yibao_brain/llm.py`（summarize_screen）
- Modify: `sidecar/src/yibao_brain/server.py`（screen_sampler/vision_summarizer/secure_input_checker 装配 + store 共享给 sensors）
- Modify: `sidecar/src/yibao_brain/config.py`（perception.screen / perception.blacklist 默认键）
- Test: `sidecar/tests/test_llm.py`、`sidecar/tests/test_server.py`

**Interfaces:**
- Produces:
  - `llm.summarize_screen(client, b64) -> str | None`（prompt：概括前台应用显示内容 ≤80 字）
  - config 默认键：`"perception.screen": False`、`"perception.blacklist": []`
  - server 的 sensors 注入：screen_sampler（frontmost_tree + capture_window 兜底/secure input 查 Quartz）

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_llm.py` 追加（fake client 断言 prompt 语义与返回）：

```python
def test_summarize_screen_prompt_and_result():
    captured = {}

    class FakeResp:
        choices = [type("C", (), {"message": type("M", (), {"content": "VS Code 编辑 App.vue，左侧文件树"})()})()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured.update(kw)
                    return FakeResp()

    from yibao_brain.llm import ComputerUseClient, summarize_screen

    c = ComputerUseClient(api_key="x", model="m", base_url="https://x", client_factory=FakeClient)
    out = summarize_screen(c, "data:image/png;base64,x")
    assert out == "VS Code 编辑 App.vue，左侧文件树"
    assert "概括" in captured["messages"][0]["content"]
```

`sidecar/tests/test_server.py` 追加（装配冒烟：real 模式下 sensors 有 screen 注入——走 build_loop 不易真机，改测 config 默认键与 server 侧 sampler 函数）：

```python
def test_config_perception_screen_defaults():
    from yibao_brain.config import load_settings

    s = load_settings()
    assert s.get("perception.screen") is False
    assert isinstance(s.get("perception.blacklist"), list)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run --extra dev pytest tests/test_llm.py::test_summarize_screen_prompt_and_result tests/test_server.py::test_config_perception_screen_defaults -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`llm.py`（describe_screen 之后）：

```python
SCREEN_SUMMARY_PROMPT = (
    "用一句话（80 字以内）概括这张屏幕截图里前台应用正在显示的内容："
    "应用名 + 内容主题 + 可见的关键文字。只输出这句话。"
)


def summarize_screen(client, b64: str) -> str | None:
    """B 源截图兜底概括。client 为 ComputerUseClient；失败返 None。"""
    try:
        resp = _vision_create_with_retry(lambda: client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": SCREEN_SUMMARY_PROMPT},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": b64}}]},
            ],
        ))
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return text[:120] or None
    except Exception as e:
        print(f"[yibao] B 源截图概括失败（已跳过）：{e}", file=sys.stderr)
        return None
```

`config.py` 默认 settings dict（:253-256 区域）加 `"perception.screen": False,` 和 `"perception.blacklist": [],`。

`server.py`（perception 装配段，:768 附近）：

```python
    # B 源采样器：a11y 树优先；树空 → 截图留 path 待概括；secure input 检测
    def _screen_sampler():
        if agent.host is None:
            return None
        try:
            from .mac.a11y_mac import MacA11yReader

            reader = MacA11yReader()
            app, bundle_id, title = reader.frontmost_app_info()  # 按实际 API 对齐
            tree = reader.frontmost_tree()
            if tree:
                return ("tree", tree, None, app, bundle_id, title)
            shot = agent.host.screenshotter.capture()
            return ("empty", None, shot, app, bundle_id, title)
        except Exception:
            return None

    def _vision_summarizer(path: str):
        if _wvision is None:
            return None
        try:
            import base64

            with open(path, "rb") as f:
                b64 = "data:image/png;base64," + base64.b64encode(f.read()).decode()
            from .llm import summarize_screen

            return summarize_screen(_wvision, b64)
        except Exception:
            return None

    def _secure_input_checker() -> bool:
        try:
            import Quartz

            return bool(Quartz.IsSecureEventInputEnabled())
        except Exception:
            return False

    perception_sensors = PerceptionSensors(
        pstore, settings,
        screen_sampler=_screen_sampler if agent.host is not None else None,
        vision_summarizer=_vision_summarizer,
        secure_input_checker=_secure_input_checker,
    )
```

（`MacA11yReader` 的前台 app 信息 API 按 `a11y_mac.py` 实际方法对齐——A 源 app_sampler 已有同款取法，照抄其 app/bundle/title 获取方式。）

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd sidecar && uv run --extra dev pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/llm.py sidecar/src/yibao_brain/server.py sidecar/src/yibao_brain/config.py sidecar/tests/
git commit -m "feat(perception): B 源 vision 概括与装配——采样器/敏感过滤/secure input/config 键"
```

---

### Task 3: load_screen_content 消费工具

**Files:**
- Modify: `sidecar/src/yibao_brain/perception.py`（LoadScreenContentSkill）
- Modify: `sidecar/src/yibao_brain/server.py`（注册）
- Test: `sidecar/tests/test_perception.py`

**Interfaces:**
- Consumes: Task 1 的 `query_window(..., sources=("screen",))`；`LoadUserActivitySkill` 的 precheck/safe_result/notice 模式。
- Produces: `LoadScreenContentSkill(store, settings)`：id `load_screen_content`，L0；参数 `minutes`（默认 30，≤1440）、`limit`（默认 10，≤20）；precheck=`perception.model_access` 未开则拦截文案同 LoadUserActivitySkill 风格；返回 `{"minutes", "items": [{ts,app,kind,text}], "count", "truncated"}`；safe_result 只留计数；notice「已参考屏幕内容」。

- [ ] **Step 1: 写失败测试**

```python
def test_load_screen_content_gate_and_window(tmp_path):
    from yibao_brain.perception import LoadScreenContentSkill

    store = _open_store(tmp_path)
    store.append("screen", "tree", {"app": "Safari", "title": "天气", "text": "AXWindow: 天气页"}, "S3")
    store.append("screen", "vision", {"app": "Canvas", "text": "画板一个矩形", "path": "/x.png"}, "S3")
    store.append("app", "frontmost", {"app": "Safari"}, "S1")
    # 未开 model_access → 拦截
    skill = LoadScreenContentSkill(store, {"perception.model_access": False})
    r = skill.run({}, _skill_ctx())
    assert not r.success and "未开启" in (r.error or "")
    # 开启 → 返回 screen 条目（不含 app 源），按时间倒序
    skill2 = LoadScreenContentSkill(store, {"perception.model_access": True})
    r2 = skill2.run({}, _skill_ctx())
    assert r2.success and r2.data["count"] == 2
    assert all(it["kind"] in ("tree", "vision") for it in r2.data["items"])
    assert r2.data["items"][0]["app"] == "Canvas"
    assert r2.safe_result(r2.data)["count"] == 2 and "items" not in r2.safe_result(r2.data)
    assert skill2.post_reply_notice({}) == "已参考屏幕内容"
    store.close()
```

（`_skill_ctx()` 按该文件现有构造对齐，无则 `from yibao_brain.ipc import SkillContext; SkillContext()`。）

- [ ] **Step 2: 跑测试确认失败 → Step 3: 实现（LoadUserActivitySkill 同构，分钟换算时间窗，sources=("screen",)）→ Step 4: 全量绿 → Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/perception.py sidecar/src/yibao_brain/server.py sidecar/tests/test_perception.py
git commit -m "feat(perception): load_screen_content 消费工具——屏幕内容进对话上下文"
```

---

### Task 4: 前端（开关+行内确认 + 日志徽章 + Avatar 观察中）

**Files:**
- Modify: `desktop/src/components/SettingsView.vue`（screen 开关 + 行内两段确认 + 日志 source/text 分支）
- Modify: `desktop/src/components/Avatar.vue`（observing prop + 青白点）
- Modify: `desktop/src/App.vue`、`desktop/src/Home.vue`（宿主传 observing：getSettingsOnce + brain-settings 监听）
- Modify: `desktop/src/lib/brain.ts`（SettingsValues 加 perception.screen）

**Interfaces:**
- Consumes: `perception.screen` settings 键；`brain-settings` 事件。
- Produces: 无新 IPC（settings_set 已有）；Avatar `observing?: boolean` prop（叠加指示，不占 state 通道）。

- [ ] **Step 1: SettingsView**

照 `perceptionConfirming` 行内两段模式：开 `perception.screen` 时先弹行内说明「屏幕内容将被持续观察；界面结构文本只存本机，无法读取结构时的截图会发送给智谱 GLM 做概括」+ 确认/取消，确认才 `setPerceptionSetting("perception.screen", true)`；关闭直接生效。模板开关插 :753 后，`:disabled="!perceptionMaster"`。日志：`perceptionSource` 加 `'screen' → "屏幕"`；`perceptionText` 加 screen 分支（`payload.text` 截 60 字；kind==='vision' 前缀「概括」）。brain.ts `SettingsValues` 加 `"perception.screen"?: boolean;`。

- [ ] **Step 2: Avatar observing 叠加点**

`Avatar.vue` props 加 `observing?: boolean`；`.dot-grp`（:310-317）后加：

```html
<circle v-if="observing" cx="86" cy="20" r="3" fill="#cfe8f5" opacity="0.9" />
```

App.vue / Home.vue：refs 加 `observing = ref(false)`；onMounted `getSettingsOnce().then(s => { observing.value = !!(s?.["perception.master"] && (s?.["perception.app"] || s?.["perception.activity"] || s?.["perception.screen"])); })`；挂 `brain-settings` 监听同逻辑刷新（brain.ts 已有 settings 事件监听设施则复用）。`<Avatar ... :observing="observing" />`。

- [ ] **Step 3: 验证 + Commit**

`cd desktop && npm run build` 通过。

```bash
git add desktop/src/components/SettingsView.vue desktop/src/components/Avatar.vue desktop/src/App.vue desktop/src/Home.vue desktop/src/lib/brain.ts
git commit -m "feat(perception): B 源前端——screen 开关行内确认 + 日志徽章 + 团子观察中点"
```

---

### Task 5: 真机验收（手动清单）+ spec 实装记录

- [ ] **Step 1: 验收流程**（逐项记录结果）
  1. 设置 → 隐私 → 开「屏幕内容」：弹行内明示，确认后开启；团子天线旁出现青白点
  2. 依次切：Safari（网页）、系统设置、VS Code、Excalidraw（自绘）各停留 >5s
  3. 感知日志页：出现「屏幕」条目——Safari/系统设置/VS Code 应为 tree（界面结构文本），Excalidraw 应为 vision（概括）
  4. 打开 1Password 停留 >5s：日志无新条目（黑名单）
  5. 问译宝「我刚才看的页面讲了什么」：回答引用 tree/概括内容，并出现 notice「已参考屏幕内容」
  6. 设置关掉 `perception.model_access` 再问：被拦截并引导去设置
  7. 关「屏幕内容」：青白点灭；此后无新条目
- [ ] **Step 2: 实装记录回写**

在 `docs/superpowers/specs/2026-08-02-perception-v2-screen-design.md` 末尾追加「实装记录（2026-08-02）」：落地清单 + 验收结果 + 偏差。

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-02-perception-v2-screen-design.md
git commit -m "docs(perception): v2 B 源实装记录与真机验收"
```

---

## Self-Review 记录

- **Spec 覆盖**：采集（T1/T2）、三层过滤（T1）、出站闸门（T2 config + T4 确认）、透明（T4）、消费（T3）、工程/验证（T1-5）→ 全覆盖。
- **占位符扫描**：`_open_store`/`_skill_ctx`/`MacA11yReader.frontmost_app_info` 三处注明按现有 helper 对齐——这是有意的适配点（实现者读文件即得），非 TBD；其余代码完整。
- **类型一致性**：screen_sampler 六元组 `(status, tree, shot, app, bundle_id, title)` 在 sensors/测试/server 三处一致；kind='tree'/'vision' 与 payload 键（text/path）在采集/日志/消费三处一致；notice 文案「已参考屏幕内容」在测试与实现一致。
- **风险**：sensors tick 的 `now` 时钟源（现 time.time vs 注入 clock）在 Step 3 已注明只 screen 段用注入 clock，不动旧段——避免 A/C 既有测试被破坏。
