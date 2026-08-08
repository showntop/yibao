# E 上下文唤起 实施计划（划词动作条 + 截图即问）

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** ①⌘⇧U 划词 → 光标旁动作条（解释/翻译/存素材）动作直达；②⌘⇧I 框选区域 → 截图 + 提问 → vision 直答。

**Architecture:** 动作条/截图层 = 两个新的预创建隐藏 Tauri 窗（invoke-bar 328×56 光标旁落位 / snip 全屏 overlay），webview 经 `emit` 广播与主窗协作；解释/翻译走现有 run 管线（selectionCtx 自动拼入）；存素材走 panel_action 直调 `zimeiti.mat_save`（新增 `quiet` 抑制弹面板）；截图问答走 sidecar 新消息 `snip_capture`/`vision_query`（区域截图 b64 暂存 + `answer_image_query` 直答，不占 run/对话历史）。

**Tech Stack:** Rust + Tauri v2（global-shortcut/device_query/mss 坐标）、Vue3/TS（两个新窗入口）、Python sidecar（mss/OpenAI SDK）、pytest。

**关联 spec：** `docs/superpowers/specs/2026-08-08-context-invoke-design.md`

## Global Constraints

- **不动宠物主窗**（window.ts「恒 360×520」约定 + lib.rs:1204 点击穿透热区按固定布局算死）。
- **静默优先**：⌘⇧U 不再强制展开宠物窗；存素材不弹面板（quiet）、不展开，只 success 闪现 + 气泡。
- **sidecar pytest 全绿（基线 821）；vue-tsc/vite build exit 0；cargo check + cargo test exit 0**。
- **commit**：每任务一 commit，中文 scope（`feat(invoke): …`），仅 stage 本任务文件，不动 `.gitignore`，提交在 `feat/context-invoke` 分支。
- 所有经 `_vision_create_with_retry` 的视觉调用失败一律静默降级（stderr 日志 + error 事件），不炸 sidecar。
- 坐标系：device_query 光标坐标 = macOS 逻辑点（与点击穿透轮询 lib.rs:1200 同系）；mss 截图用物理像素，`snip_abs_rect` 负责换算。

## File Structure

**新建：**
- `app/invoke.html`、`app/src/invoke.ts`、`app/src/components/InvokeBar.vue`（动作条窗）
- `app/snip.html`、`app/src/snip.ts`、`app/src/components/SnipOverlay.vue`（截图 overlay 窗）

**改：**
- `sidecar/src/yibao_brain/host.py`（Screenshotter Protocol 加 capture_region）
- `sidecar/src/yibao_brain/mac/host_mac.py`（MacScreenshotter.capture_region）
- `sidecar/src/yibao_brain/llm.py`（SNIP_QA_PROMPT + answer_image_query）
- `sidecar/src/yibao_brain/server.py`（snip_ctx/_peek_snip + snip_capture/vision_query 分支 + serve_async vision_client 注入）
- `sidecar/src/yibao_brain/plugins.py`（ApiMethod.quiet + _load_api 解析）
- `plugins/zimeiti/api.toml`（invoke_mat_save 条目）
- `app/src-tauri/src/lib.rs`（两新窗 + 两命令组 + ⌘⇧I 注册 + clamp_bar_pos/snip_abs_rect 纯函数 + cargo 单测）
- `app/vite.config.ts`（invoke/snip 入口）
- `app/src/lib/brain.ts`（onInvokeAction/onSnipCaptured/visionQuery）
- `app/src/App.vue`（onInvokeAction、mat_save 回执气泡、snipCtx chip + submit 分支、onPetInvokeSelection 不再强制展开）
- `app/src/components/SettingsView.vue`（热键清单补两行）
- `sidecar/tests/test_host_mac.py`（或既有文件追加）、`sidecar/tests/test_llm.py`、`sidecar/tests/test_server.py`

---

### Task 1: sidecar capture_region（Protocol + Mac 实现）

**Files:**
- Modify: `sidecar/src/yibao_brain/host.py`（Screenshotter Protocol，约 :12-15）
- Modify: `sidecar/src/yibao_brain/mac/host_mac.py`（MacScreenshotter，:121-153 之后追加方法）
- Test: `sidecar/tests/test_host_mac.py`（无则新建；有则追加）

**Interfaces:**
- Consumes: 现有 `MacScreenshotter.capture()`/`capture_window()` 的 mss grab 模式（host_mac.py:125-153）
- Produces: `Screenshotter.capture_region(left: int, top: int, width: int, height: int) -> str`（返回 PNG 绝对路径）；Task 3 的 server 分支调用它

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_host_mac.py`（追加或新建，文件头部按需 `import os` / `from yibao_brain.mac import host_mac`）：

```python
def test_capture_region_grabs_exact_rect(monkeypatch, tmp_path):
    """capture_region：region 原样传给 mss.grab，落盘 PNG 返回路径。"""
    calls = {}

    class FakeRaw:
        size = (10, 20)
        bgra = b"\x00" * 10 * 20 * 4

    class FakeSct:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, region):
            calls["region"] = region
            return FakeRaw()

    monkeypatch.setattr(host_mac.mss, "mss", lambda: FakeSct())
    s = host_mac.MacScreenshotter(dir_=str(tmp_path))
    path = s.capture_region(5, 6, 10, 20)
    assert calls["region"] == {"left": 5, "top": 6, "width": 10, "height": 20}
    assert path.endswith(".png") and os.path.exists(path)


def test_capture_region_clamps_tiny_size(monkeypatch, tmp_path):
    """宽高超小（拖拽抖动）钳到 1px，不炸。"""
    calls = {}

    class FakeRaw:
        size = (1, 1)
        bgra = b"\x00" * 4

    class FakeSct:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, region):
            calls["region"] = region
            return FakeRaw()

    monkeypatch.setattr(host_mac.mss, "mss", lambda: FakeSct())
    s = host_mac.MacScreenshotter(dir_=str(tmp_path))
    s.capture_region(0, 0, 0, 0)
    assert calls["region"]["width"] == 1 and calls["region"]["height"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_host_mac.py -x -q`
Expected: FAIL（`MacScreenshotter` 无 `capture_region` 属性 / host.py Protocol 无此方法）

- [ ] **Step 3: 实现**

`sidecar/src/yibao_brain/host.py`：在 `Screenshotter` Protocol 现有 `capture` 方法声明后追加：

```python
    def capture_region(self, left: int, top: int, width: int, height: int) -> str:
        """任意矩形区域截图（物理像素，mss 坐标系=虚拟桌面）。返回 PNG 绝对路径。"""
        ...
```

`sidecar/src/yibao_brain/mac/host_mac.py`：在 `MacScreenshotter.capture_window` 之后追加（与 capture/capture_window 同模式）：

```python
    def capture_region(self, left: int, top: int, width: int, height: int) -> str:
        """任意矩形区域截图（截图即问 overlay 选区）。返回 PNG 绝对路径。"""
        region = {
            "left": int(left),
            "top": int(top),
            "width": max(1, int(width)),
            "height": max(1, int(height)),
        }
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"yibao-snip-{time.time_ns()}.png")
        with mss.mss() as sct:
            raw = sct.grab(region)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.save(path)
        return path
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_host_mac.py -x -q`
Expected: 新增 2 个 PASS

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/host.py sidecar/src/yibao_brain/mac/host_mac.py sidecar/tests/test_host_mac.py
git commit -m "feat(invoke): Screenshotter.capture_region（mss 区域截图，截图即问底座）"
```

---

### Task 2: sidecar llm.answer_image_query

**Files:**
- Modify: `sidecar/src/yibao_brain/llm.py`（`describe_screen` 附近，:309 之后追加）
- Test: `sidecar/tests/test_llm.py`（追加）

**Interfaces:**
- Consumes: `_vision_create_with_retry`（llm.py:270-284）、`ComputerUseClient`（.model/.client 属性）
- Produces: `SNIP_QA_PROMPT: str`、`answer_image_query(client, b64: str, question: str) -> str | None`；Task 3 的 vision_query 分支调用

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_llm.py` 追加（文件顶部若无则补 `from types import SimpleNamespace`）：

```python
class _FakeVisionCompletions:
    def __init__(self, content):
        self._content = content
        self.last_messages = None

    def create(self, model, messages, **kw):
        self.last_messages = messages
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))])


class _FakeVisionClient:
    """ComputerUseClient 形状假件：.model + .client.chat.completions.create。"""

    def __init__(self, content):
        self.model = "fake-v"
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeVisionCompletions(content)))


def test_answer_image_query_ok():
    from yibao_brain.llm import answer_image_query

    c = _FakeVisionClient("图上是一个对话框")
    ans = answer_image_query(c, "data:image/png;base64,AAA", "这是什么？")
    assert ans == "图上是一个对话框"
    msgs = c.client.chat.completions.last_messages
    assert msgs[1]["content"][0] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
    assert msgs[1]["content"][1] == {"type": "text", "text": "这是什么？"}


def test_answer_image_query_failure_returns_none():
    from yibao_brain.llm import answer_image_query

    class Boom:
        def create(self, *a, **k):
            raise RuntimeError("boom")

    c = SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=Boom())))
    assert answer_image_query(c, "data:image/png;base64,AAA", "q") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_llm.py -k answer_image_query -x -q`
Expected: FAIL（`cannot import name 'answer_image_query'`）

- [ ] **Step 3: 实现**

`sidecar/src/yibao_brain/llm.py` 在 `describe_screen` 函数之后追加：

```python
SNIP_QA_PROMPT = (
    "你是屏幕问答助手。根据这张屏幕截图回答用户的问题：简洁直接（200 字以内），"
    "只依据截图里可见的内容作答；截图里看不到答案就明说「截图中看不到」。"
)


def answer_image_query(client, b64: str, question: str) -> str | None:
    """区域截图问答（截图即问）。client 为 ComputerUseClient；失败返 None。"""
    try:
        resp = _vision_create_with_retry(lambda: client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": SNIP_QA_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": b64}},
                    {"type": "text", "text": question},
                ]},
            ],
        ))
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return text or None
    except Exception as e:
        print(f"[yibao] 截图问答失败（已跳过）：{e}", file=sys.stderr)
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_llm.py -k answer_image_query -x -q`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/llm.py sidecar/tests/test_llm.py
git commit -m "feat(invoke): answer_image_query（截图问答 prompt + vision 直答）"
```

---

### Task 3: server snip_capture / vision_query 分支

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py`（invoke_ctx 附近加 snip_ctx/_peek_snip；:1150 invoke_context 分支后加两分支；:719-726 `_wvision` 初始化改注入优先）
- Test: `sidecar/tests/test_server.py`（追加，复用 `make_reader`/`FakeProvider`/`_run_async`/`_RecSkill`/`_pa_factory`/`_patch_api`）

**Interfaces:**
- Consumes: Task 1 `capture_region`、Task 2 `answer_image_query`；`_offload`（server.py:260 用法）；`Event`（kind="final_reply"/"error"）；`_consume_invoke_context`（background.py:173-178）的 stash 模式
- Produces:
  - 模块级 `snip_ctx = {"b64": None, "ts": 0.0}`、`SNIP_TTL_S = 300.0`
  - `_peek_snip(stash: dict) -> str | None`（新鲜返回 b64 不清空，过期丢弃返回 None）
  - IPC `snip_capture {left,top,width,height}`（物理像素整数）
  - IPC `vision_query {id, question}` → final_reply/error 事件 + `run_done`
  - `serve_async(..., vision_client=None)` 注入参数（provider= 注入先例）

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_server.py` 追加（文件头部若无则补 `import time`）：

```python
def test_peek_snip_fresh_stale_empty():
    """_peek_snip：新鲜→返回且不清空（可追问）；过期→None 并清空；空→None。"""
    from yibao_brain.server import _peek_snip

    stash = {"b64": "data:image/png;base64,AAA", "ts": time.time()}
    assert _peek_snip(stash) == "data:image/png;base64,AAA"
    assert _peek_snip(stash) == "data:image/png;base64,AAA"  # 不清空，可多次提问
    stale = {"b64": "data:image/png;base64,BBB", "ts": time.time() - 9999}
    assert _peek_snip(stale) is None
    assert stale["b64"] is None
    assert _peek_snip({"b64": None, "ts": 0.0}) is None


class _FakeVisionCompletions:
    def __init__(self, content):
        self._content = content

    def create(self, model, messages, **kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))])


class _FakeVisionClient:
    def __init__(self, content):
        self.model = "fake-v"
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeVisionCompletions(content)))


def test_serve_async_vision_query_answers_with_stashed_snip(tmp_path):
    """vision_query：暂存截图 + 问题 → final_reply 事件带答案 + run_done 复位。"""
    import yibao_brain.server as srv

    srv.snip_ctx.update({"b64": "data:image/png;base64,AAA", "ts": time.time()})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 7, "type": "vision_query", "question": "这是什么？"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            vision_client=_FakeVisionClient("图上是一个对话框"),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    fr = next(e for e in evs if e["kind"] == "final_reply")
    assert "对话框" in fr["text"]
    assert out[-1] == {"type": "run_done", "id": 7}


def test_serve_async_vision_query_stale_snip_errors(tmp_path):
    """vision_query：无暂存截图 → error 事件提示重新框选 + run_done。"""
    import yibao_brain.server as srv

    srv.snip_ctx.update({"b64": None, "ts": 0.0})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 8, "type": "vision_query", "question": "q"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            vision_client=_FakeVisionClient("不应被调用"),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    err = next(e for e in evs if e["kind"] == "error")
    assert "重新框选" in err["text"]
    assert out[-1] == {"type": "run_done", "id": 8}


def test_serve_async_snip_capture_silent_without_host(tmp_path):
    """use_real=False（无 host）：snip_capture 分支静默跳过，不炸。"""
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "snip_capture", "left": 0, "top": 0, "width": 10, "height": 10},
                {"type": "ping"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert any(m.get("type") == "pong" for m in out)
```

（`SimpleNamespace`：test_server.py 顶部若无则补 `from types import SimpleNamespace`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_server.py -k "snip or vision_query" -x -q`
Expected: FAIL（`cannot import name '_peek_snip'` / serve_async 无 vision_client 参数）

- [ ] **Step 3: 实现**

`sidecar/src/yibao_brain/server.py`：

① 在 `invoke_ctx` 定义附近（模块级）加：

```python
snip_ctx: dict = {"b64": None, "ts": 0.0}  # 截图即问：区域截图 b64 暂存（可多次提问，过期丢弃）
SNIP_TTL_S = 300.0


def _peek_snip(stash: dict) -> str | None:
    """暂存区域截图 b64：新鲜→返回（不清空，同一截图可追问）；过期→丢弃返 None。"""
    b64 = stash.get("b64")
    if b64 is None:
        return None
    if time.time() - float(stash.get("ts") or 0.0) >= SNIP_TTL_S:
        stash["b64"] = None
        return None
    return b64
```

② `serve_async` 签名加 `vision_client=None` 参数；`_wvision` 初始化段（:719-726）改为注入优先：

```python
    _wvision = None
    if vision_client is not None:
        _wvision = vision_client  # 测试注入（provider= 注入先例）
    elif vision_api_key() and computer_use_enabled():
        try:
            from .llm import ComputerUseClient

            _wvision = ComputerUseClient()
        except Exception as e:
            print(f"[yibao] watch 视觉不可用（主动搭话禁用）：{e}", file=sys.stderr)
```

③ 消息分发在 `invoke_context` 分支（:1150-1163）之后加两分支：

```python
        elif rtype == "snip_capture":
            # 截图即问（E）：壳侧 overlay 选区（物理像素）→ 区域截图 → b64 暂存待 vision_query。
            # 无截图能力/失败一律静默跳过。
            if agent.host is not None:
                try:
                    shot = agent.host.screenshotter.capture_region(
                        int(msg.get("left", 0)), int(msg.get("top", 0)),
                        int(msg.get("width", 1)), int(msg.get("height", 1)),
                    )
                    import base64 as _b64snip

                    with open(shot, "rb") as f:
                        snip_ctx.update({
                            "b64": "data:image/png;base64," + _b64snip.b64encode(f.read()).decode(),
                            "ts": time.time(),
                        })
                except Exception as e:
                    print(f"[yibao] 区域截图失败（已跳过）：{e}", file=sys.stderr)
        elif rtype == "vision_query":
            # 截图即问：暂存区域截图 + 问题 → vision 直答（不走 run，不占对话历史）。
            # 复用 run 的事件/run_done 协议（id 与 run_input 同为 0），壳侧状态机零改动。
            rid_vq = msg.get("id", 0)
            question = str(msg.get("question") or "").strip()[:500]

            def _vq_emit(ev: Event) -> None:
                write_msg({"type": "event", "surface": "pet", "event": ev.model_dump(mode="json")})

            if not question:
                _vq_emit(Event(kind="error", text="空问题"))
            elif _wvision is None:
                _vq_emit(Event(kind="error", text="视觉端点未配置（YIBAO_VISION_*），无法截图问答"))
            else:
                b64 = _peek_snip(snip_ctx)
                if b64 is None:
                    _vq_emit(Event(kind="error", text="截图已过期或尚未框选，请 ⌘⇧I 重新框选"))
                else:
                    from .llm import answer_image_query

                    ans = await _offload(answer_image_query, _wvision, b64, question)
                    if ans:
                        _vq_emit(Event(kind="final_reply", text=ans))
                    else:
                        _vq_emit(Event(kind="error", text="截图问答失败，请重试"))
            write_msg({"type": "run_done", "id": rid_vq})
```

（`Event` 若该作用域不可见则从既有 import 处补；`agent.host`、`write_msg`、`_offload` 与 invoke_context 分支同作用域。）

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_server.py -k "snip or vision_query" -x -q && uv run pytest -q`
Expected: 新 4 PASS；全量 821+新增 全绿

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/server.py sidecar/tests/test_server.py
git commit -m "feat(invoke): snip_capture/vision_query 分支 + vision_client 注入（截图即问服务端）"
```

---

### Task 4: api quiet 抑制 + zimeiti invoke_mat_save

**Files:**
- Modify: `sidecar/src/yibao_brain/plugins.py`（ApiMethod :388-399、_load_api :444-448）
- Modify: `sidecar/src/yibao_brain/server.py`（handle_panel_action :313-323）
- Modify: `plugins/zimeiti/api.toml`（:106 之后追加条目）
- Test: `sidecar/tests/test_server.py`（追加，复用 `_patch_api`/`_pa_factory`/`_RecSkill`）

**Interfaces:**
- Consumes: `ApiMethod`（frozen dataclass）、`panel_payload`、`handle_panel_action` 现有 refresh/panel 分支
- Produces: `ApiMethod.quiet: bool = False`；api.toml `quiet = true` 解析；zimeiti 方法全名 `zimeiti.invoke_mat_save`（direct+quiet）；Task 7 前端 `panelAction("zimeiti.invoke_mat_save", {text})`

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_server.py` 追加：

```python
def test_panel_action_quiet_suppresses_panel_event(tmp_path, monkeypatch):
    """quiet=true 的 api 方法：直调执行 + action_result 照发，但不发 panel 事件（不弹面板窗）。"""
    executed = []
    _patch_api(monkeypatch, quiet=True)
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "tdel:list", {"type": "list"})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed, ref="tdel:list"),  # tool 自带 panel 引用，应被 quiet 抑制
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    kinds = [e["kind"] for e in evs]
    assert executed == [{"id": "r1"}]           # tool 真的被执行
    assert "action_result" in kinds             # 回执照发（壳侧气泡用）
    assert "panel" not in kinds                 # panel 事件被抑制（不弹窗）
    assert out[-1] == {"type": "run_done", "id": 1}


def test_load_api_parses_quiet(tmp_path):
    """api.toml quiet = true 解析进 ApiMethod.quiet（缺省 False）。"""
    from yibao_brain import plugins
    from yibao_brain.skills import SkillRegistry

    reg = SkillRegistry()
    reg.register(_RecSkill.make([]), plugin="tdel")
    api = tmp_path / "api.toml"
    api.write_text(
        '[[method]]\nname = "save"\nhandler = "tdel.delete"\ndirect = true\nquiet = true\n'
        '[[method]]\nname = "loud"\nhandler = "tdel.delete"\ndirect = true\n',
        encoding="utf-8",
    )
    plugins._load_api("tdel", api, reg)
    try:
        assert plugins.get_api("tdel.save").quiet is True
        assert plugins.get_api("tdel.loud").quiet is False
    finally:
        plugins._API.pop("tdel.save", None)
        plugins._API.pop("tdel.loud", None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_server.py -k "quiet" -x -q`
Expected: FAIL（ApiMethod 无 quiet 字段 → `_patch_api(monkeypatch, quiet=True)` TypeError）

- [ ] **Step 3: 实现**

`sidecar/src/yibao_brain/plugins.py`：`ApiMethod` 加字段（注意 frozen dataclass 带默认值字段必须在最后）：

```python
    refresh: str | None = None  # 直调成功后跟一次查询 tool，面板拿刷新数据而非操作回执
    panel: str | None = None    # 直调成功后改用该面板发 panel 事件（覆盖 tool 自带引用，如 webview 编辑器）
    quiet: bool = False         # 直调成功后不发 panel 事件（唤起条等静默场景，action_result 照发）
```

`_load_api` 的 ApiMethod 构造（:444-448）加 `quiet=bool(m.get("quiet", False)),`。

`sidecar/src/yibao_brain/server.py` `handle_panel_action`（:315-323）改为：

```python
        if result.success and api.refresh is not None:
            # 声明式刷新：删除类操作后跟一次查询，面板拿新数据而不是操作回执
            await _emit_refresh_panel(agent, emit, api.refresh)
        else:
            if result.success and api.panel is not None:
                result.panel = api.panel  # method 声明的面板优先于 tool 自带引用（如 webview 编辑器）
            if not api.quiet:  # quiet：不弹面板（唤起条存素材等静默直调）
                payload = panel_payload(result)
                if payload is not None:
                    emit(Event(kind="panel", payload=payload))
```

`plugins/zimeiti/api.toml` 在 `hot_mat_save` 条目后追加：

```toml
# 唤起条「存素材」：划词文本直存（LLM 摘要打标）；quiet 不弹素材库面板，回执由壳侧气泡给
[[method]]
name = "invoke_mat_save"
handler = "zimeiti.mat_save"
direct = true
quiet = true
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_server.py -k quiet -x -q && uv run pytest -q`
Expected: 新 2 PASS；全量全绿

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/plugins.py sidecar/src/yibao_brain/server.py plugins/zimeiti/api.toml sidecar/tests/test_server.py
git commit -m "feat(invoke): api quiet 抑制 panel 事件 + zimeiti.invoke_mat_save（唤起条静默存素材）"
```

---

### Task 5: Rust invoke-bar 窗口 + 落位 + ⌘⇧U 展示

**Files:**
- Modify: `app/src-tauri/src/lib.rs`（新命令、setup 预创建窗、⌘⇧U 分支扩展、注册热键处不动、纯函数 + `#[cfg(test)]` 单测）
- Modify: `app/vite.config.ts`（加 invoke 入口——Task 6 需要 html 存在才能 build，本任务先建占位 html）

**Interfaces:**
- Consumes: 现有 ⌘⇧U 分支（lib.rs:1317-1330）、`device_query::DeviceState`（:1189）、窗口 builder 模式（:1420-1433）
- Produces:
  - 窗口 label `invoke-bar`（328×56，预创建隐藏）
  - `clamp_bar_pos(mx: f64, my: f64, bar_w: f64, bar_h: f64, mon: (f64,f64,f64,f64)) -> (f64, f64)`（纯函数）
  - 命令 `hide_invoke_bar(app: AppHandle) -> Result<(), String>`
  - Task 6/7 前端依赖：窗 label、`hide_invoke_bar` 命令、`invoke.html` 入口

- [ ] **Step 1: 写失败测试（cargo）**

`app/src-tauri/src/lib.rs` 文件末尾（既有 `#[cfg(test)]` mod 内追加，或新建 `#[cfg(test)] mod invoke_tests`）：

```rust
#[cfg(test)]
mod invoke_tests {
    use super::*;

    #[test]
    fn bar_pos_bottom_right_offset() {
        // 屏幕中央：光标右下偏移
        let (x, y) = clamp_bar_pos(500.0, 400.0, 328.0, 56.0, (0.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (514.0, 418.0));
    }

    #[test]
    fn bar_pos_flips_at_right_and_bottom_edges() {
        // 右边缘：翻到光标左侧；下边缘：翻到光标上方
        let (x, y) = clamp_bar_pos(1400.0, 880.0, 328.0, 56.0, (0.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (1400.0 - 328.0 - 14.0, 880.0 - 56.0 - 18.0));
    }

    #[test]
    fn bar_pos_respects_monitor_origin() {
        // 副屏（原点在 1440,0）：越界判断相对该屏
        let (x, y) = clamp_bar_pos(1500.0, 100.0, 328.0, 56.0, (1440.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (1514.0, 118.0));
    }

    #[test]
    fn snip_rect_scales_and_offsets() {
        // scale=2  retina：逻辑 (100,50,200,120) + 屏原点 (0,0) → 物理 (200,100,400,240)
        let r = snip_abs_rect((100.0, 50.0, 200.0, 120.0), (0, 0), 2.0);
        assert_eq!(r, (200, 100, 400, 240));
        // 副屏负原点（主屏左侧 1440 宽）：逻辑 (10,10,50,50) → 物理 (-1420+20, 20, 100, 100)
        let r2 = snip_abs_rect((10.0, 10.0, 50.0, 50.0), (-2880, 0), 2.0);
        assert_eq!(r2, (-2860, 20, 100, 100));
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd app && cargo test --manifest-path src-tauri/Cargo.toml invoke_tests`
Expected: FAIL（`clamp_bar_pos`/`snip_abs_rect` 未定义，编译错误）

- [ ] **Step 3: 实现**

`app/src-tauri/src/lib.rs`：

① `grab_selected_text` 之后（:1307 附近）加纯函数：

```rust
/** 唤起条落位：光标右下偏移（14,18），越出所在屏右/下缘时翻转到左/上；纯函数便于单测。
 *  mon = (屏原点x, 屏原点y, 屏宽, 屏高)，全部逻辑坐标。 */
fn clamp_bar_pos(mx: f64, my: f64, bar_w: f64, bar_h: f64, mon: (f64, f64, f64, f64)) -> (f64, f64) {
    let (mx0, my0, mw, mh) = mon;
    let mut x = mx + 14.0;
    let mut y = my + 18.0;
    if x + bar_w > mx0 + mw {
        x = mx - bar_w - 14.0;
    }
    if y + bar_h > my0 + mh {
        y = my - bar_h - 18.0;
    }
    (x.max(mx0), y.max(my0))
}

/** 选区换算：overlay 窗口逻辑坐标 → 虚拟桌面物理像素（mss 坐标系）。
 *  mon_px_origin = 屏物理原点（可为负：副屏在主屏左侧）；scale = 屏 scale_factor。纯函数便于单测。 */
fn snip_abs_rect(r: (f64, f64, f64, f64), mon_px_origin: (i64, i64), scale: f64) -> (i64, i64, i64, i64) {
    let (l, t, w, h) = r;
    (
        mon_px_origin.0 + (l * scale).round() as i64,
        mon_px_origin.1 + (t * scale).round() as i64,
        (w * scale).round().max(1.0) as i64,
        (h * scale).round().max(1.0) as i64,
    )
}
```

② `close_panel_window` 命令（:1111-1118）之后加：

```rust
/// 隐藏唤起条（主窗处理完 invoke-action 后兜底调用；条本身点击/Esc/blur 已自隐，幂等）。
#[tauri::command]
fn hide_invoke_bar(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("invoke-bar") {
        win.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}
```

③ setup 里 home 窗创建（:1444）之后加 invoke-bar 预创建：

```rust
            // 唤起条（划词动作菜单）：预创建隐藏，⌘⇧U 抓到文字后光标旁落位展示
            tauri::WebviewWindowBuilder::new(app, "invoke-bar", tauri::WebviewUrl::App("invoke.html".into()))
                .title("")
                .transparent(true)
                .decorations(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .resizable(false)
                .inner_size(328.0, 56.0)
                .visible(false)
                .build()
                .map_err(|e| format!("创建唤起条失败：{e}"))?;
```

④ ⌘⇧U 分支（:1321-1329）扩展——`emit("pet-invoke-selection", …)` 之后追加：

```rust
                std::thread::spawn(move || {
                    let text = grab_selected_text();
                    if let Some(win) = handle.get_webview_window("main") {
                        let _ = win.show().and_then(|_| win.set_focus());
                    }
                    let has_text = text.is_some();
                    let _ = handle.emit("pet-invoke-selection", serde_json::json!({ "text": text }));
                    // 动作条：有选中文字才弹（无选中退化为旧唤起，不弹空菜单）
                    if has_text {
                        if let Some(bar) = handle.get_webview_window("invoke-bar") {
                            let (cmx, cmy) = device_query::DeviceState::new().get_mouse().coords;
                            if let Ok(Some(mon)) = bar.current_monitor() {
                                let s = mon.scale_factor();
                                let mon_rect = (
                                    mon.position().x as f64 / s,
                                    mon.position().y as f64 / s,
                                    mon.size().width as f64 / s,
                                    mon.size().height as f64 / s,
                                );
                                let (bx, by) = clamp_bar_pos(cmx as f64, cmy as f64, 328.0, 56.0, mon_rect);
                                let _ = bar.set_position(tauri::LogicalPosition::new(bx, by));
                            }
                            let _ = bar.show().and_then(|_| bar.set_focus());
                        }
                    }
                });
```

（注意：`device_query` 在 lib.rs 顶部的 use 若只导了 DeviceState 就沿用；：1189 处用法是 `DeviceState::new()`，保持一致。）

⑤ `hide_invoke_bar` 注册进 `tauri::generate_handler![…]`（在 lib.rs 内搜索 `generate_handler`，与 `close_panel_window` 同列追加 `hide_invoke_bar`）。

⑥ `app/vite.config.ts` 的 `build.rollupOptions.input` 加一行（:19 design 之后）：

```typescript
        invoke: fileURLToPath(new URL("./invoke.html", import.meta.url)),
```

⑦ 占位 `app/invoke.html`（Task 6 会替换为完整入口；本任务先让 build 过）：

```html
<!doctype html>
<html lang="zh-CN">
  <head><meta charset="UTF-8" /><title>唤起</title></head>
  <body><div id="app"></div><script type="module" src="/src/invoke.ts"></script></body>
</html>
```

并建占位 `app/src/invoke.ts`：

```typescript
// 唤起条入口（Task 6 替换为完整实现）
import { createApp } from "vue";
import InvokeBar from "./components/InvokeBar.vue";
import "./assets/tokens.css";

createApp(InvokeBar).mount("#app");
```

占位 `app/src/components/InvokeBar.vue`（Task 6 替换）：

```vue
<template><div /></template>
```

- [ ] **Step 4: 验证**

Run: `cd app && cargo test --manifest-path src-tauri/Cargo.toml invoke_tests && cargo check --manifest-path src-tauri/Cargo.toml && npx vite build`
Expected: 4 个 invoke_tests PASS（含 snip_rect——本任务顺带实现了 snip_abs_rect，Task 8 复用）；cargo check exit 0；vite build exit 0

- [ ] **Step 5: Commit**

```bash
git add app/src-tauri/src/lib.rs app/vite.config.ts app/invoke.html app/src/invoke.ts app/src/components/InvokeBar.vue
git commit -m "feat(invoke): invoke-bar 窗口 + 光标旁落位（clamp_bar_pos 纯函数单测）+ ⌘⇧U 弹动作条"
```

---

### Task 6: InvokeBar.vue 完整实现（按钮/自隐/Esc/blur）

**Files:**
- Modify: `app/src/components/InvokeBar.vue`（替换 Task 5 占位）
- Modify: `app/invoke.html`（窗口级 reset，对齐 panel.html 结构）

**Interfaces:**
- Consumes: Task 5 的 `invoke-bar` 窗/入口；`Avatar.vue`（:state/:size props）；`YbIcon.vue`（name prop，可用名：clock/chat/lock/pin/doc/wrench/check/x/stop/alert/inbox/sparkle/wave/plug/settings）
- Produces: 广播事件 `invoke-action`（payload `{action: "explain"|"translate"|"save"}`）——Task 7 主窗监听

- [ ] **Step 1: 实现 InvokeBar.vue**

```vue
<script setup lang="ts">
// 唤起条：⌘⇧U 划词后光标旁弹出——团子探出 + 动作直达。点按钮广播 invoke-action 后自隐；
// Esc / 失焦自隐。主窗 App.vue 负责真正的动作（解释/翻译走 run，存素材走 panel_action）。
import { onMounted, onUnmounted } from "vue";
import { emit } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import Avatar from "./Avatar.vue";
import YbIcon from "./YbIcon.vue";

const acts = [
  { id: "explain", label: "解释", icon: "chat" },
  { id: "translate", label: "翻译", icon: "doc" },
  { id: "save", label: "存素材", icon: "pin" },
] as const;

async function pick(id: string) {
  await emit("invoke-action", { action: id });
  await getCurrentWindow().hide();
}

function hide() {
  void getCurrentWindow().hide();
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") hide();
}

let unlistenBlur: (() => void) | null = null;
onMounted(async () => {
  window.addEventListener("keydown", onKey);
  unlistenBlur = await getCurrentWindow().listen("tauri://blur", hide);
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  unlistenBlur?.();
});
</script>

<template>
  <div class="ib">
    <div class="ib-av"><Avatar state="notify" :size="30" /></div>
    <button v-for="a in acts" :key="a.id" class="ib-btn" @click="pick(a.id)">
      <YbIcon :name="a.icon" :size="14" />{{ a.label }}
    </button>
    <button class="ib-x" title="忽略 (Esc)" @click="hide"><YbIcon name="x" :size="13" /></button>
  </div>
</template>

<style scoped>
.ib {
  height: 100vh;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-glass);
  backdrop-filter: blur(18px);
  border: 1px solid var(--yb-border-base);
  box-shadow: var(--yb-shadow-3);
  font-family: var(--yb-font);
}
.ib-av {
  width: 30px;
  height: 30px;
  flex: none;
  margin-right: 2px;
  overflow: hidden;
}
.ib-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  border-radius: var(--yb-radius-pill);
  padding: 5px 10px;
  background: transparent;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  cursor: pointer;
  transition: background var(--yb-dur-fast);
  white-space: nowrap;
}
.ib-btn:hover {
  background: var(--yb-surface-2);
}
.ib-btn:active {
  transform: scale(0.97);
}
.ib-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--yb-text-dim);
  cursor: pointer;
}
.ib-x:hover {
  background: var(--yb-surface-2);
  color: var(--yb-text);
}
</style>
```

- [ ] **Step 2: invoke.html 窗口级 reset**（对齐 panel.html 头部结构）：

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>唤起</title>
    <style>
      html,
      body,
      #app {
        margin: 0;
        height: 100%;
        background: transparent;
        overflow: hidden;
      }
    </style>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/invoke.ts"></script>
  </body>
</html>
```

- [ ] **Step 3: capabilities 验证**

`ls app/src-tauri/capabilities/` 看现有能力文件：确认 `windows` 字段覆盖新 label（`"invoke-bar"`）或被 `"*"` 通配；invoke-bar 窗需要 `core:event:default`（emit/listen）与 `core:window:default`（hide/set_focus）级别权限，对照 panel/home 窗现有条目补。若现有文件是全局 `windows: ["*"]` 则无需改。

- [ ] **Step 4: 验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: 两者 exit 0

- [ ] **Step 5: Commit**

```bash
git add app/src/components/InvokeBar.vue app/invoke.html app/src-tauri/capabilities/
git commit -m "feat(invoke): InvokeBar 动作条（团子探出 + 三动作广播 + Esc/blur 自隐）"
```

（若 capabilities 无改动，去掉第三个 add 路径。）

---

### Task 7: 主窗动作处理（解释/翻译/存素材 + 回执气泡 + 静默化）

**Files:**
- Modify: `app/src/lib/brain.ts`（加 onInvokeAction）
- Modify: `app/src/App.vue`（:300-326 onPetInvokeSelection 不再强制展开；新增 handleInvokeAction；onEvent 的 action_result 分支加 mat_save 回执；onMounted 挂监听）

**Interfaces:**
- Consumes: Task 4 `zimeiti.invoke_mat_save`（quiet 直调）、Task 5 `hide_invoke_bar`、Task 6 `invoke-action` 事件；现有 `selectionCtx`/`submit`/`expand`/`flashValence`/`pushWarn`/`panelAction`
- Produces: 无新接口（终点任务）

- [ ] **Step 1: brain.ts 加监听封装**（`panelAction` 之后追加）：

```typescript
/** 唤起条动作广播（invoke-bar 窗 emit）：解释/翻译/存素材。 */
export function onInvokeAction(cb: (action: string) => void): Promise<UnlistenFn> {
  return listen<{ action: string }>("invoke-action", (e) => cb(e.payload.action));
}
```

（`UnlistenFn`/`listen` 顶部已有 import 则复用；`UnlistenFn` 来自 `@tauri-apps/api/event` 类型导出。）

- [ ] **Step 2: App.vue 改 onPetInvokeSelection（静默优先）**——:320-326 改为：

```typescript
async function onPetInvokeSelection(text: string | null) {
  const t = text?.trim();
  // 上下文截断 4000 字：够一整页文档，又不至于一条消息烧穿上下文
  if (t) selectionCtx.value = t.length > 4000 ? t.slice(0, 4000) : t;
  if (t) return; // 有选中文字：动作条在光标旁待选，不展开宠物窗（静默优先；选动作才展开）
  await expand(); // 无选中文字：退化为旧唤起（展开 + 聚焦输入）
  void nextTick(() => inputBarRef.value?.focus());
}
```

- [ ] **Step 3: App.vue 加 handleInvokeAction + 挂监听**

`onPetInvokeSelection` 之后加：

```typescript
// 唤起条动作：解释/翻译走现有 run（selectionCtx 自动拼入）；存素材 quiet 直调（不弹面板）
async function handleInvokeAction(action: string) {
  void invoke("hide_invoke_bar").catch(() => {}); // 兜底（条本身已自隐）
  const sel = selectionCtx.value;
  if (action === "explain" || action === "translate") {
    if (!expanded.value) await expand();
    const q =
      action === "explain"
        ? "解释这段文字，讲清要点"
        : "把这段文字翻译成中文（如果它已经是中文，就翻译成英文）";
    if (sel) {
      await submit(q);
    } else {
      pushWarn("没有取到选中文字，请选中后再试");
    }
  } else if (action === "save") {
    if (!sel) {
      pushWarn("没有可存的选中文字");
      return;
    }
    await panelAction("zimeiti.invoke_mat_save", { text: sel });
    selectionCtx.value = null;
    flashValence("success"); // 400ms 成功闪现当回执（不展开、不弹面板，静默优先）
  }
}
```

在 `onMounted`（或现有 `onPetInvokeSelection` 监听挂载处同列）加：

```typescript
  void onInvokeAction((action) => { void handleInvokeAction(action); });
```

（同时顶部 import 区给 brain.ts 的 import 列表加 `onInvokeAction`；`invoke` 来自 `@tauri-apps/api/core` 顶部已有。）

- [ ] **Step 4: App.vue action_result 分支加 mat_save 回执**

`onEvent` 的 `case "action_result"`（:340-352）内，在既有逻辑后追加：

```typescript
      // 唤起条存素材回执：LLM 摘要打标完成后到标题，补一条 sys 气泡（quiet 不弹面板，气泡即凭证）
      if (e.action?.skill_id === "zimeiti.mat_save" && e.result?.success) {
        const title = (e.result as { data?: { title?: string } }).data?.title;
        bubbles.value.push({ role: "sys", text: title ? `已存素材：《${title}》` : "已存素材", icon: "doc" });
      }
```

（`e.result` 的 TS 类型若无 `success`/`data` 字段，按上例用窄化断言；保持 vue-tsc 过。）

- [ ] **Step 5: 验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: 两者 exit 0

- [ ] **Step 6: Commit**

```bash
git add app/src/lib/brain.ts app/src/App.vue
git commit -m "feat(invoke): 主窗动作处理——解释/翻译走 run、存素材 quiet 直调 + 回执气泡、划词唤起静默化"
```

---

### Task 8: Rust snip overlay 窗口 + ⌘⇧I + finish_snip/cancel_snip/vision_query

**Files:**
- Modify: `app/src-tauri/src/lib.rs`（snip 窗预创建、⌘⇧I 注册 + 分支、三命令、generate_handler 注册）
- Modify: `app/vite.config.ts`（加 snip 入口 + 占位 html/ts/vue，同 Task 5 模式）

**Interfaces:**
- Consumes: Task 5 `snip_abs_rect`（已带单测）、窗口 builder 模式、`write_to_brain`
- Produces:
  - 窗口 label `snip`（预创建隐藏，唤起时铺满光标所在显示器）
  - 命令 `finish_snip(app, state, rect: {left,top,width,height})`（隐藏 overlay → 发 sidecar `snip_capture` → emit `snip-captured {width,height}`）
  - 命令 `cancel_snip(app)`
  - 命令 `vision_query(state, question)` → sidecar `vision_query {id:0, question}`
  - 广播事件 `snip-start`（overlay 复位）、`snip-captured {width,height}`（主窗展开 chip）——Task 9 消费

- [ ] **Step 1: 实现（本任务 Rust 逻辑已在 Task 5 备好纯函数 + 单测，直接写窗口与命令）**

`app/src-tauri/src/lib.rs`：

① `hide_invoke_bar` 之后加三命令：

```rust
#[derive(serde::Deserialize)]
struct SnipRect {
    left: f64,
    top: f64,
    width: f64,
    height: f64,
}

/// 框选完成：隐藏 overlay → 逻辑选区换算物理像素 → 通知大脑区域截图 → 广播 snip-captured 让主窗展开。
#[tauri::command]
fn finish_snip(app: AppHandle, state: tauri::State<Brain>, rect: SnipRect) -> Result<(), String> {
    if let Some(snip) = app.get_webview_window("snip") {
        let _ = snip.hide();
        if let Ok(Some(mon)) = snip.current_monitor() {
            let scale = mon.scale_factor();
            let origin = (mon.position().x as i64, mon.position().y as i64);
            let (l, t, w, h) = snip_abs_rect((rect.left, rect.top, rect.width, rect.height), origin, scale);
            write_to_brain(
                &state,
                serde_json::json!({
                    "id": 0, "type": "snip_capture",
                    "left": l, "top": t, "width": w, "height": h,
                }),
            )?;
            let _ = app.emit("snip-captured", serde_json::json!({ "width": w, "height": h }));
        }
    }
    Ok(())
}

/// 取消框选（Esc / 单击 / 过小选区）：只收 overlay，不打扰。
#[tauri::command]
fn cancel_snip(app: AppHandle) -> Result<(), String> {
    if let Some(snip) = app.get_webview_window("snip") {
        let _ = snip.hide();
    }
    Ok(())
}

/// 截图即问：问题转发大脑（暂存的区域截图 + 问题 → vision 直答）。
#[tauri::command]
fn vision_query(state: tauri::State<Brain>, question: String) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "vision_query", "question": question }),
    )
}
```

② setup 里 invoke-bar 创建之后加 snip overlay 预创建：

```rust
            // 截图框选层：预创建隐藏；⌘⇧I 时铺满光标所在显示器（drag 选区 → finish_snip）
            tauri::WebviewWindowBuilder::new(app, "snip", tauri::WebviewUrl::App("snip.html".into()))
                .title("")
                .transparent(true)
                .decorations(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .resizable(false)
                .inner_size(800.0, 600.0) // 占位，唤起时按显示器重设
                .visible(false)
                .build()
                .map_err(|e| format!("创建截图层失败：{e}"))?;
```

③ 热键 handler 的 ⌘⇧U 分支之后（`return;` 前同级）加 ⌘⇧I 分支：

```rust
            // 截图即问：overlay 铺满光标所在显示器，等前端拖拽选区（finish_snip/cancel_snip）
            if shortcut == &tauri_plugin_global_shortcut::Shortcut::new(
                Some(tauri_plugin_global_shortcut::Modifiers::SUPER | tauri_plugin_global_shortcut::Modifiers::SHIFT),
                tauri_plugin_global_shortcut::Code::KeyI,
            ) {
                let handle = app.clone();
                std::thread::spawn(move || {
                    let (cmx, cmy) = device_query::DeviceState::new().get_mouse().coords;
                    if let Some(snip) = handle.get_webview_window("snip") {
                        if let Ok(mons) = snip.available_monitors() {
                            let hit = mons.into_iter().find(|m| {
                                let s = m.scale_factor();
                                let x = m.position().x as f64 / s;
                                let y = m.position().y as f64 / s;
                                let w = m.size().width as f64 / s;
                                let h = m.size().height as f64 / s;
                                (cmx as f64) >= x && (cmx as f64) < x + w && (cmy as f64) >= y && (cmy as f64) < y + h
                            });
                            if let Some(mon) = hit {
                                let s = mon.scale_factor();
                                let _ = snip.set_position(tauri::LogicalPosition::new(
                                    mon.position().x as f64 / s,
                                    mon.position().y as f64 / s,
                                ));
                                let _ = snip.set_size(tauri::LogicalSize::new(
                                    mon.size().width as f64 / s,
                                    mon.size().height as f64 / s,
                                ));
                            }
                        }
                        let _ = snip.show().and_then(|_| snip.set_focus());
                        let _ = handle.emit("snip-start", ());
                    }
                });
                return;
            }
```

④ setup 热键注册（:1449-1454）追加：

```rust
                if let Err(e) = app.global_shortcut().register("Super+Shift+I") {
                    eprintln!("[yibao] 注册热键失败：{e}");
                }
```

⑤ `finish_snip`/`cancel_snip`/`vision_query` 注册进 `tauri::generate_handler![…]`。

⑥ `app/vite.config.ts` input 加：

```typescript
        snip: fileURLToPath(new URL("./snip.html", import.meta.url)),
```

⑦ 占位 `app/snip.html`（同 Task 5 invoke.html 结构，src 改 `/src/snip.ts`）、占位 `app/src/snip.ts`：

```typescript
// 截图框选层入口（Task 9 替换为完整实现）
import { createApp } from "vue";
import SnipOverlay from "./components/SnipOverlay.vue";
import "./assets/tokens.css";

createApp(SnipOverlay).mount("#app");
```

占位 `app/src/components/SnipOverlay.vue`（Task 9 替换）：

```vue
<template><div /></template>
```

- [ ] **Step 2: 验证**

Run: `cd app && cargo check --manifest-path src-tauri/Cargo.toml && cargo test --manifest-path src-tauri/Cargo.toml && npx vite build`
Expected: 全 exit 0（invoke_tests 4 个仍 PASS）

- [ ] **Step 3: Commit**

```bash
git add app/src-tauri/src/lib.rs app/vite.config.ts app/snip.html app/src/snip.ts app/src/components/SnipOverlay.vue
git commit -m "feat(invoke): snip overlay 窗口 + ⌘⇧I + finish_snip/cancel_snip/vision_query 命令"
```

---

### Task 9: SnipOverlay.vue + 主窗 snipCtx + visionQuery + 设置页热键清单

**Files:**
- Modify: `app/src/components/SnipOverlay.vue`（替换占位）、`app/snip.html`（窗口级 reset）
- Modify: `app/src/lib/brain.ts`（onSnipCaptured + visionQuery）
- Modify: `app/src/App.vue`（snipCtx chip + submit 分支 + onSnipCaptured 挂载）
- Modify: `app/src/components/SettingsView.vue`（:733-736 热键清单补两行）

**Interfaces:**
- Consumes: Task 8 命令与事件（`finish_snip`/`cancel_snip`/`vision_query`/`snip-start`/`snip-captured`）、Task 3 sidecar vision_query 分支；现有 `selectionCtx` chip 样式（ctx-chip，App.vue:749-754）
- Produces: 无新接口（终点任务）

- [ ] **Step 1: SnipOverlay.vue 完整实现**

```vue
<script setup lang="ts">
// 截图框选层：⌘⇧I 后铺满显示器，拖拽画矩形 → finish_snip；Esc/单击/过小选区 → cancel_snip。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

const start = ref<{ x: number; y: number } | null>(null);
const cur = ref<{ x: number; y: number } | null>(null);

const rect = computed(() => {
  if (!start.value || !cur.value) return null;
  const left = Math.min(start.value.x, cur.value.x);
  const top = Math.min(start.value.y, cur.value.y);
  const width = Math.abs(cur.value.x - start.value.x);
  const height = Math.abs(cur.value.y - start.value.y);
  return { left, top, width, height };
});

function down(e: MouseEvent) {
  start.value = { x: e.clientX, y: e.clientY };
  cur.value = { x: e.clientX, y: e.clientY };
}
function move(e: MouseEvent) {
  if (start.value) cur.value = { x: e.clientX, y: e.clientY };
}
async function up() {
  const r = rect.value;
  start.value = null;
  cur.value = null;
  if (r && r.width > 8 && r.height > 8) {
    await invoke("finish_snip", { rect: r });
  } else {
    await invoke("cancel_snip"); // 单击/抖动选区 = 取消
  }
}
function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") void invoke("cancel_snip");
}

let unlisten: UnlistenFn | null = null;
onMounted(async () => {
  window.addEventListener("keydown", onKey);
  unlisten = await listen("snip-start", () => {
    start.value = null;
    cur.value = null;
  });
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  unlisten?.();
});
</script>

<template>
  <div class="cover" @mousedown="down" @mousemove="move" @mouseup="up">
    <div
      v-if="rect"
      class="sel"
      :style="{ left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px' }"
    >
      <span class="size">{{ Math.round(rect.width) }} × {{ Math.round(rect.height) }}</span>
    </div>
    <div v-if="!rect" class="hint">拖拽框选要问的区域，Esc 取消</div>
  </div>
</template>

<style scoped>
.cover {
  position: fixed;
  inset: 0;
  cursor: crosshair;
  background: rgba(15, 23, 42, 0.28);
  font-family: var(--yb-font);
}
.sel {
  position: absolute;
  border: 1.5px solid #38bdf8;
  border-radius: 2px;
  box-shadow: 0 0 0 9999px rgba(15, 23, 42, 0.28);
  background: transparent;
}
.size {
  position: absolute;
  right: 0;
  bottom: -24px;
  font-size: 11px;
  color: #e2e8f0;
  background: rgba(15, 23, 42, 0.75);
  padding: 2px 6px;
  border-radius: 4px;
  font-variant-numeric: tabular-nums;
}
.hint {
  position: absolute;
  top: 12%;
  left: 50%;
  transform: translateX(-50%);
  font-size: 13px;
  color: #e2e8f0;
  background: rgba(15, 23, 42, 0.75);
  padding: 6px 12px;
  border-radius: var(--yb-radius-pill);
  pointer-events: none;
}
</style>
```

`app/snip.html` 窗口级 reset 同 invoke.html（title 改「框选」，src 改 `/src/snip.ts`）。

capabilities 验证同 Task 6 Step 3（label `snip`，需 invoke 命令权限——`core:default` 覆盖则无需改）。

- [ ] **Step 2: brain.ts 加封装**（`onInvokeAction` 之后追加）：

```typescript
/** 框选完成广播（finish_snip 后 Rust emit）：主窗展开 + chip 提示提问。 */
export function onSnipCaptured(cb: (r: { width: number; height: number }) => void): Promise<UnlistenFn> {
  return listen<{ width: number; height: number }>("snip-captured", (e) => cb(e.payload));
}

/** 截图即问：问题 → 大脑（暂存的区域截图 + vision 直答，不走 run）。 */
export function visionQuery(question: string): Promise<void> {
  return invoke("vision_query", { question });
}
```

- [ ] **Step 3: App.vue snipCtx + submit 分支 + 挂载**

`selectionCtx` 声明（:302）之后加：

```typescript
const snipCtx = ref<{ width: number; height: number } | null>(null); // 截图即问：框选完成待提问

// 命名注意：brain.ts 的监听封装叫 onSnipCaptured，处理函数另名 onSnipReady 避免撞名
async function onSnipReady(r: { width: number; height: number }) {
  snipCtx.value = r;
  if (!expanded.value) await expand();
  void nextTick(() => inputBarRef.value?.focus());
}
```

onMounted 挂载（与 onInvokeAction 同列）：

```typescript
  void onSnipCaptured((r) => { void onSnipReady(r); });
```

（import 列表加 `onSnipCaptured`、`visionQuery`。）

`submit`（:503-519）在函数体最前（`bubbles.value.push({ role: "user", text })` 之后、`selectionCtx` 拼接之前）插入 snip 分支：

```typescript
  // 截图即问：有框选待提问 → 走 vision 直答（不占 run/对话历史）
  if (snipCtx.value) {
    bubbles.value.push({ role: "sys", text: `已附带区域截图 ${snipCtx.value.width}×${snipCtx.value.height}`, icon: "doc" });
    snipCtx.value = null;
    try {
      await visionQuery(text);
    } catch (err) {
      pushWarn("发送失败：" + String(err));
      state.value = "idle";
    }
    return;
  }
```

（注意保持 `state.value = "think"` 已在分支前执行——即把该分支放在 `state.value = "think"` 行之后；vision_query 的 run_done 会经现有 onRunDone 复位。）

chip UI：`ctx-chip` 块（:749-754）之后加：

```html
        <!-- 截图即问 chip：⌘⇧I 框选后等待提问，可 × 掉；发送后自消 -->
        <div v-if="snipCtx" class="ctx-chip">
          <YbIcon class="ctx-ic" name="doc" :size="13" />
          <span class="ctx-text">区域截图 {{ snipCtx.width }}×{{ snipCtx.height }}，想问什么？</span>
          <button class="ctx-x" title="去掉截图" @click="snipCtx = null">×</button>
        </div>
```

- [ ] **Step 4: SettingsView.vue 热键清单**（:733-736 现有 ⌘⇧Y 行同格式补两行）：

```html
          <span class="hk-key">⌘⇧U</span><span class="hk-desc">划词唤起（选中文字 → 动作条）</span>
          <span class="hk-key">⌘⇧I</span><span class="hk-desc">截图即问（框选区域 → 提问）</span>
```

（类名以该文件现有热键行为准——若是无类纯文本行，按现有格式写「⌘⇧U 划词唤起」「⌘⇧I 截图即问」。）

- [ ] **Step 5: 验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build && cargo check --manifest-path src-tauri/Cargo.toml`
Expected: 全 exit 0

- [ ] **Step 6: Commit**

```bash
git add app/src/components/SnipOverlay.vue app/snip.html app/src/lib/brain.ts app/src/App.vue app/src/components/SettingsView.vue
git commit -m "feat(invoke): 框选层拖拽选区 + 主窗 snipCtx 提问通道 + 设置页热键清单"
```

---

### Task 10: 验收

- [ ] **Step 1: 自动化全绿**

```bash
cd sidecar && uv run pytest -q          # 821 + 新增 ≈ 831 passed
cd ../app && npx vue-tsc --noEmit && npx vite build && cargo check --manifest-path src-tauri/Cargo.toml && cargo test --manifest-path src-tauri/Cargo.toml
```

Expected: 全 exit 0

- [ ] **Step 2: 真机（人工）验收清单**

`npm run tauri dev` 后逐项：
1. 任意 app（浏览器/编辑器）选中一段文字 → ⌘⇧U → 光标旁弹动作条（团子探出），宠物窗不展开；点「解释」→ 宠物窗展开、气泡流式出解释；Esc/blur 能收条。
2. 英文段落 ⌘⇧U →「翻译」→ 出中文；中文段落 → 出英文。
3. 选中文字 ⌘⇧U →「存素材」→ 团子 success 闪现、不弹面板；稍后气泡/素材库面板（插件页 → 自媒体 → 素材库）能看到该条（LLM 起的标题）。
4. 无选中时 ⌘⇧U → 不弹动作条，退化为旧唤起。
5. ⌘⇧I → overlay 出现 → 拖一个区域 → 宠物窗展开带「区域截图 WxH」chip → 输入「这是什么」→ 气泡出 vision 回答；同一截图追问第二个问题也能答。
6. ⌘⇧I → Esc 取消；⌘⇧I → 单击（不拖）也取消。
7. 副屏（如有）：光标在副屏按 ⌘⇧U/⌘⇧I，动作条/overlay 落在副屏。
8. 设置页热键清单三行（⌘⇧Y/⌘⇧U/⌘⇧I）可见。

- [ ] **Step 3: 收尾 commit（如有验收修小补）**

```bash
git add -p   # 仅验收相关小修
git commit -m "fix(invoke): 真机验收小修"
```

---

## 自审

- spec 覆盖：动作条（Task 5/6/7）✅、截图即问（Task 8/9 + sidecar 1/2/3）✅、quiet 存素材（Task 4）✅、静默化（Task 7 Step 2）✅、设置页清单（Task 9 Step 4）✅、真机清单含多屏（Task 10）✅
- 类型一致：`capture_region(left, top, width, height)`（Task 1 定义 = Task 3 调用）✅；`answer_image_query(client, b64, question)`（Task 2 = Task 3）✅；`_peek_snip(stash)` ✅；`ApiMethod.quiet`（Task 4 定义 = `_patch_api(monkeypatch, quiet=True)` 使用）✅；`clamp_bar_pos`/`snip_abs_rect`（Task 5 定义 + 单测，Task 8 消费）✅；`invoke-action`/`snip-captured`/`snip-start` 事件名跨端一致 ✅；`zimeiti.invoke_mat_save` 全名一致 ✅；`hide_invoke_bar`/`finish_snip`/`cancel_snip`/`vision_query` 命令名跨端一致 ✅
- 无占位：每步含完整代码/命令/预期 ✅
