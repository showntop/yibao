# Coding 跨 agent 交接（Codex → Claude Code）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 给 coding 插件加「从 Codex session 接续到 Claude Code」：读 Codex JSONL + git → LLM 生成可审阅交接 Brief → 确认后作为 Claude Code 首条 prompt 起会话。

**Architecture:** 新 `codex_reader.py`（扫 `~/.codex/sessions/**/*.jsonl` 按 `session_meta.payload.cwd` 匹配 + 解析 `response_item` 对话尾段）+ `build_brief`（LLM 摘要对话+git）+ `coding.handoff_list`/`coding.handoff_brief` 直调 skill + chat.html 交接卡（源标记/可编辑/封存）。确认复用 `coding.start({cwd,prompt:brief,source})`。

**Tech Stack:** Python 3.12（sidecar，pytest，GLMProvider），HTML/JS webview，TDD（JSONL fixture + FakeProvider）。

## Global Constraints

- **Codex JSONL 事实（已核实）**：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`；**line 1 = `session_meta`**（`payload.cwd`/`payload.session_id`/`payload.timestamp`）；后续 `response_item` 事件 `payload.role ∈ {developer,user,assistant}`，或 `role=None`+`name`/`input`（工具调用）/+`output`（结果）。reader 测试用基于此形态造的 JSONL fixture，不依赖运行时 Codex。
- **cwd 匹配用 `os.path.realpath`** 规范化两端（防软链/相对路径不匹配）。
- **挂了不碍事**：reader/brief/交接任何失败只在交接卡内呈现（无 session/JSONL 损坏/LLM 失败 → 手动粘贴兜底），绝不抛主链路。
- **复用 coding.start**：确认 = `coding.start({cwd, prompt: brief, source: f"codex:{codex_session_id}"})`；多轮走现有 send。
- **TDD**：codex_reader / build_brief / handoff skills 用 JSONL fixture + FakeProvider 注入测。webview 以 build + 人工为门。
- **manifest capabilities**：coding 插件 `["db"]` → `["db","llm","process"]`（`llm`=ctx.llm 调主模型做 brief；`process`=声明会跑 git 子进程，仅审计用）。
- **sidecar 测试**：`cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_coding_handoff.py -v`；全量基线 809。
- **commit**：每任务一 commit，中文 scope（`feat(coding): ...`），仅 stage 本任务文件，不动 `.gitignore`；提交到 `main`。

## File Structure

**新建：**
- `plugins/coding/skills/codex_reader.py` — `list_sessions(cwd)` / `read_conversation(path)` / `git_summary(cwd)`。
- `plugins/coding/skills/_brief.py` — `build_brief(provider, conversation, git_summary) -> str | None`（拆出来便于单测；也可并入 coding.py，但独立更聚焦）。
- `sidecar/tests/test_coding_handoff.py` — reader/brief/handoff 测。

**修改：**
- `plugins/coding/manifest.toml` — capabilities 加 `llm`/`process`；sessions 加 `source` 列。
- `plugins/coding/skills/coding.py` — StartSkill 加 `source` 参数；新增 `HandoffListSkill`/`HandoffBriefSkill`；make_tools 加这两。
- `plugins/coding/api.toml` — 加 `coding.handoff_list`/`coding.handoff_brief`（direct）。
- `plugins/coding/panel/chat.html` — 顶栏按钮 + 选择器 + 交接卡。

---

### Task 1: codex_reader（list_sessions + read_conversation + git_summary）

**Files:** `plugins/coding/skills/codex_reader.py`、`sidecar/tests/test_coding_handoff.py`
**Interfaces:**
- Produces:
  - `list_sessions(cwd: str, *, root: str = "~/.codex/sessions") -> list[dict]`：返 `[{session_id, cwd, timestamp, path, first_line}]`，按 timestamp 倒序；realpath 匹配 cwd。
  - `read_conversation(path: str, tail: int = 8) -> dict`：返 `{"turns": [{role, text}], "incomplete": bool}`（收 response_item role∈{user,assistant}，取末尾 tail 轮）。
  - `git_summary(cwd: str) -> str`：`git -C cwd log --oneline -10` + `git -C cwd status --short`，失败返 `""`。

- [ ] **Step 1: 写失败测试**（新建 `tests/test_coding_handoff.py`）
```python
"""coding 交接：codex_reader + build_brief + handoff skills。"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))
from codex_reader import list_sessions, read_conversation, git_summary  # noqa: E402


def _write_session(root, rel, cwd, sid, ts, turns):
    """在 root 下造一个 Codex JSONL session 文件。rel=相对路径（含 年/月/日/文件名）。"""
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    lines = [json.dumps({"type": "session_meta",
                         "payload": {"session_id": sid, "cwd": cwd, "timestamp": ts}})]
    for role, text in turns:
        lines.append(json.dumps({"type": "response_item",
                                 "payload": {"role": role, "content": text}}))
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n")
    return p


def test_list_sessions_matches_cwd(tmp_path):
    root = str(tmp_path / "sessions")
    proj = str(tmp_path / "proj"); os.makedirs(proj)
    _write_session(root, "2026/08/05/a.jsonl", proj, "sid-a", "2026-08-05T10:00:00Z",
                   [("user", "实现登录"), ("assistant", "好的")])
    _write_session(root, "2026/08/05/b.jsonl", str(tmp_path / "other"), "sid-b", "2026-08-05T11:00:00Z",
                   [("user", "别的项目")])
    res = list_sessions(proj, root=root)
    assert [s["session_id"] for s in res] == ["sid-a"]   # 只命中 cwd 匹配的
    assert res[0]["first_line"] == "实现登录"


def test_read_conversation_tail_and_incomplete(tmp_path):
    root = str(tmp_path / "sessions"); proj = str(tmp_path / "p")
    turns = [("user", f"msg{i}") for i in range(12)] + [("assistant", "ans")]
    p = _write_session(root, "2026/08/05/x.jsonl", proj, "sid", "2026-08-05T09:00:00Z", turns)
    # 注入一行坏 JSON
    with open(p, "a") as f: f.write("{NOT JSON\n")
    out = read_conversation(p, tail=4)
    assert out["incomplete"] is True
    roles = [t["role"] for t in out["turns"]]
    assert roles[-1] == "assistant"          # 末尾 assistant 收到
    assert len(out["turns"]) <= 5            # tail=4 + 末 assistant

def test_git_summary_runs_or_empty(tmp_path):
    # 非 git 目录 → 返 ""，不抛
    assert git_summary(str(tmp_path / "nope")) == ""
```

- [ ] **Step 2: 跑测试验证失败** — `pytest tests/test_coding_handoff.py -v` → FAIL（codex_reader 不存在）。
- [ ] **Step 3: 实现 codex_reader.py**
```python
"""读 Codex session JSONL + git 摘要，供跨 agent 交接。"""
from __future__ import annotations
import glob, json, os, subprocess, sys

_META_TYPE = "session_meta"
_DIALOG_ROLES = {"user", "assistant"}


def _text(content) -> str:
    if isinstance(content, str): return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str): parts.append(b)
            elif isinstance(b, dict): parts.append(str(b.get("text") or b.get("content") or ""))
        return "".join(parts).strip()
    return str(content or "").strip()


def list_sessions(cwd: str, *, root: str = "~/.codex/sessions") -> list[dict]:
    target = os.path.realpath(os.path.expanduser(cwd))
    out = []
    for path in glob.glob(os.path.join(os.path.expanduser(root), "**", "*.jsonl"), recursive=True):
        try:
            with open(path) as f:
                first = f.readline()
            meta = json.loads(first)
        except Exception:
            continue
        if meta.get("type") != _META_TYPE:
            continue
        p = meta.get("payload") or {}
        scwd = p.get("cwd")
        if not scwd or os.path.realpath(os.path.expanduser(scwd)) != target:
            continue
        first_line = None
        try:
            with open(path) as f:
                for line in f:
                    try: o = json.loads(line)
                    except Exception: continue
                    pl = o.get("payload") or {}
                    if o.get("type") == "response_item" and pl.get("role") == "user":
                        first_line = _text(pl.get("content"))[:80] or None
                        break
        except Exception:
            pass
        out.append({"session_id": p.get("session_id"), "cwd": scwd,
                    "timestamp": p.get("timestamp", ""), "path": path, "first_line": first_line})
    out.sort(key=lambda s: s["timestamp"], reverse=True)
    return out


def read_conversation(path: str, tail: int = 8) -> dict:
    turns: list[dict] = []
    incomplete = False
    try:
        with open(path) as f:
            for line in f:
                try: o = json.loads(line)
                except Exception:
                    incomplete = True; continue
                pl = o.get("payload") or {}
                if o.get("type") == "response_item" and pl.get("role") in _DIALOG_ROLES:
                    t = _text(pl.get("content"))
                    if t:
                        turns.append({"role": pl["role"], "text": t})
    except Exception as e:
        print(f"[yibao/coding] codex session 读取失败：{e}", file=sys.stderr)
        incomplete = True
    return {"turns": turns[-(tail + 1):], "incomplete": incomplete}   # +1 容末 assistant


def git_summary(cwd: str) -> str:
    try:
        log = subprocess.run(["git", "-C", cwd, "log", "--oneline", "-10"],
                             capture_output=True, text=True, timeout=5)
        st = subprocess.run(["git", "-C", cwd, "status", "--short"],
                            capture_output=True, text=True, timeout=5)
        if log.returncode != 0:
            return ""
        return f"【近 10 条提交】\n{log.stdout.strip()}\n\n【工作区状态】\n{st.stdout.strip()}"
    except Exception:
        return ""
```

- [ ] **Step 4: 跑测试验证通过** — `pytest tests/test_coding_handoff.py -v` → 3 passed。
- [ ] **Step 5: 全量回归** — `pytest -q` → 812 passed（809+3）。
- [ ] **Step 6: commit** — `feat(coding): codex_reader（list_sessions/read_conversation/git_summary）`

---

### Task 2: build_brief（LLM 摘要）

**Files:** `plugins/coding/skills/_brief.py`、`sidecar/tests/test_coding_handoff.py`
**Interfaces:**
- Consumes: GLMProvider（`provider.chat(messages, timeout=60) -> resp(.text)`，与 distiller 一致）。
- Produces: `build_brief(provider, conversation: list[dict], git_summary: str) -> str | None`。

- [ ] **Step 1: 写失败测试**（追加）
```python
from _brief import build_brief  # noqa: E402


class _FakeProv:
    def __init__(self, text): self._t = text; self.calls = []
    def chat(self, msgs, timeout=None):
        self.calls.append(msgs); return type("R", (), {"text": self._t})()


def test_build_brief_returns_summary():
    prov = _FakeProv("任务：实现登录\n已完成：auth.py\n下一步：token 刷新")
    out = build_brief(prov, [{"role": "user", "text": "实现登录"}, {"role": "assistant", "text": "好的"}],
                      "【近提交】abc")
    assert out and "登录" in out


def test_build_brief_provider_failure_returns_none():
    class Boom:
        def chat(self, msgs, timeout=None): raise RuntimeError("llm down")
    assert build_brief(Boom(), [{"role": "user", "text": "x"}], "") is None
```

- [ ] **Step 2: 跑验证失败** — `pytest tests/test_coding_handoff.py -v -k build_brief` → FAIL。
- [ ] **Step 3: 实现 _brief.py**
```python
"""把 Codex 对话尾段 + git 摘要凝练成交接 Brief（给 Claude Code 当接续上下文）。"""
from __future__ import annotations

_PROMPT = """你是交接助手。下面是用户在 Codex（另一个 coding agent）里的工作记录尾段 + 该项目 git 状态。
请凝练成一段「交接 Brief」，给 Claude Code 当接续上下文。结构：任务 / 已完成 / 当前卡点 / 下一步建议（中文、具体带文件名，宁缺毋滥、别编造）。

【Codex 工作记录（近几轮）】
{dialog}

【git 状态】
{git}
"""


def build_brief(provider, conversation: list[dict], git_summary: str) -> str | None:
    dialog = "\n".join(f"{t['role']}: {t['text']}" for t in conversation) or "（无）"
    try:
        resp = provider.chat([{"role": "user", "content": _PROMPT.format(dialog=dialog, git=git_summary or "（无）")}],
                             timeout=60)
        text = (getattr(resp, "text", None) or "").strip()
        return text or None
    except Exception as e:
        import sys; print(f"[yibao/coding] brief 生成失败：{e}", file=sys.stderr)
        return None
```

- [ ] **Step 4: 跑验证通过** — `pytest tests/test_coding_handoff.py -v`。
- [ ] **Step 5: 全量回归** → 绿。
- [ ] **Step 6: commit** — `feat(coding): build_brief（LLM 凝练交接 Brief）`

---

### Task 3: manifest（capabilities + source 列）+ coding.start source 参数

**Files:** `plugins/coding/manifest.toml`、`plugins/coding/skills/coding.py`
**Interfaces:**
- Produces: manifest `capabilities=["db","llm","process"]`；sessions `source` 列（default ""）；`StartSkill.run` 接 `source: str = ""` 并写入行。

- [ ] **Step 1: manifest** — `capabilities = ["db", "llm", "process"]`；sessions columns 加 `{name = "source", type = "text", default = ""}`。
- [ ] **Step 2: StartSkill source 参数** — `start_session(db, *, agent, cwd, prompt, source="")` 写入 `source`；`StartSkill.run` 读 `params.get("source", "")` 透传。openai_schema 加 source（optional）。
- [ ] **Step 3: 测试** — 追加：`start_session(..., source="codex:sid")` → 行 source 字段写入；不传 → ""。
- [ ] **Step 4: 全量回归** → 绿。
- [ ] **Step 5: commit** — `feat(coding): capabilities(llm/process) + sessions.source + start source 参数`

---

### Task 4: HandoffListSkill + HandoffBriefSkill + api.toml + make_tools

**Files:** `plugins/coding/skills/coding.py`、`plugins/coding/api.toml`、`sidecar/tests/test_coding_handoff.py`
**Interfaces:**
- Consumes: Task1 `list_sessions`/`read_conversation`/`git_summary`；Task2 `build_brief`；ctx.llm（llm capability）。
- Produces: `coding.handoff_list({cwd}) -> {sessions:[...]}`；`coding.handoff_brief({session_id, cwd}) -> {brief, session_id, incomplete}`。

- [ ] **Step 1: 写失败测试**（追加；用 tmp sessions root + fake provider）
```python
import coding as codingmod  # noqa: E402
from coding import HandoffListSkill, HandoffBriefSkill  # noqa: E402

def test_handoff_list_skill(tmp_path, monkeypatch):
    root = str(tmp_path / "sessions"); proj = str(tmp_path / "p"); os.makedirs(proj)
    _write_session(root, "2026/08/05/a.jsonl", proj, "sid-a", "2026-08-05T10:00:00Z", [("user","hi")])
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    class _Ctx: 
        db=None; llm=None; emit_event=None
    r = HandoffListSkill().run({"cwd": proj}, _Ctx())
    assert r.success and r.data["sessions"][0]["session_id"] == "sid-a"

def test_handoff_brief_skill(tmp_path, monkeypatch):
    root = str(tmp_path / "sessions"); proj = str(tmp_path / "p"); os.makedirs(proj)
    p = _write_session(root, "2026/08/05/a.jsonl", proj, "sid-a", "2026-08-05T10:00:00Z",
                       [("user","实现登录"),("assistant","好的")])
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    monkeypatch.setattr(codingmod, "_build_brief", lambda prov, conv, git: "BRIEF")
    class _Ctx:
        class llm: 
            @staticmethod
            def chat(m,timeout=None): return type("R",(),{"text":"BRIEF"})()
        db=None; emit_event=None
    r = HandoffBriefSkill().run({"session_id":"sid-a","cwd":proj}, _Ctx())
    assert r.success and r.data["brief"] == "BRIEF" and r.data["session_id"] == "sid-a"
```
> 注：skill 内调 `list_sessions`/`build_brief` 经模块级间接（`_codex_sessions_root()`/`_build_brief()`）以便 monkeypatch；`_build_brief = build_brief`（默认指向 _brief.build_brief）。

- [ ] **Step 2: 跑验证失败** → FAIL（skill 不存在）。
- [ ] **Step 3: 实现**
  - `coding.py` 顶部 `from _brief import build_brief as _real_build_brief`（经 `_sibling`）+ `_build_brief = _real_build_brief`；`_codex_sessions_root` 默认返 `"~/.codex/sessions"`。
  - `HandoffListSkill`（id=`coding.handoff_list`, L0）：run 读 `params["cwd"]` → `list_sessions(cwd, root=_codex_sessions_root())` → `ActionResult(success=True, data={"sessions": sessions})`。cwd 空 → error。
  - `HandoffBriefSkill`（id=`coding.handoff_brief`, L0）：run 读 `session_id`+`cwd` → `list_sessions(cwd)` 找 path（或扫文件首行 session_id 匹配）→ `read_conversation(path)` + `git_summary(cwd)` → `_build_brief(ctx.llm, turns, git)` → 返 `{brief, session_id, incomplete}`。brief=None（LLM 失败）→ `ActionResult(success=True, data={"brief": None, ...})`（前端走手动粘贴兜底）；找不到 session → error。
  - `make_tools` 返 `[StartSkill(), SendSkill(), StopSkill(), ListSkill(), HandoffListSkill(), HandoffBriefSkill()]`。
- [ ] **Step 4: api.toml** — 加 `[[method]] name="coding.handoff_list" handler="coding.handoff_list" direct=true` + `[[method]] name="coding.handoff_brief" handler="coding.handoff_brief" direct=true`。
- [ ] **Step 5: 跑验证通过** — 全 test_coding_handoff.py 通过。
- [ ] **Step 6: 全量回归 + skills 冒烟**（`make_tools` 含 handoff 两个）→ 绿。
- [ ] **Step 7: commit** — `feat(coding): handoff_list/handoff_brief skills + api`

---

### Task 5: chat.html 交接卡（按钮 + 选择器 + 卡片 + 封存）

**Files:** `plugins/coding/panel/chat.html`
- [ ] **Step 1: 顶栏按钮** — 加 `[↩ 从Codex接续]`，点击 → `handoff()`。
- [ ] **Step 2: handoff() 流程** — `invoke("coding.handoff_list",{cwd})` → 0 个：setStatus 提示；1 个：直接 `handoffBrief(sid)`；N 个：渲染选择器（浮层，时间+first_line）→ 选 → `handoffBrief(sid)`。
- [ ] **Step 3: handoffBrief(sid)** — `invoke("coding.handoff_brief",{session_id:sid, cwd})` → 返 `{brief, session_id, incomplete}` → 渲染**交接卡**插入 #log：头（`↩ 来自 Codex · <sid短> · <时间>` + incomplete 标注）+ 可编辑 textarea（brief 或空+占位"自动生成失败，手动粘贴"）+ `[取消]` `[用它开始]`。
- [ ] **Step 4: 「用它开始」** — 取 textarea 值（brief）→ 移除卡片的编辑态/封存为只读起点标记（头改"↩ 已从 Codex 接续"）→ `invoke("coding.start",{cwd, prompt:brief, source:"codex:"+sid})` → 设 currentSession → 后续走现有流式渲染（CC 带 brief 接续）。「取消」→ 移除卡片。
- [ ] **Step 5: build** — `cd desktop && npx vite build` exit 0。
- [ ] **Step 6: commit** — `feat(coding): 交接卡——顶栏按钮/选择器/可编辑卡片/封存`

---

### Task 6: 验收

**Files:** 无（验证；记录回写 spec）
- [ ] **Step 1: 自动化** — sidecar 全量 + vite build 全绿；skills 含 handoff_list/handoff_brief。
- [ ] **Step 2: 真机（人工留用户）** — 某项目用 Codex 干一段 → 编码面板选同项目 →「从Codex接续」→ 选 session → 卡片显示 brief（任务/已完成/卡点/下一步带文件名）→ 可编辑 →「用它开始」→ Claude Code 带 brief 接续，问"刚才到哪"能答。验多 session 选择器 / 无 session 提示 / brief 失败手动粘贴兜底。
- [ ] **Step 3: spec 回写** — 验收记入 spec 末尾，commit。

---

## 自审（plan vs spec）
- spec §2 交互流（按钮/选择器/卡/封存）→ Task 5 ✅
- spec §3.1 codex_reader → Task 1 ✅
- spec §3.2 build_brief → Task 2 ✅
- spec §3.3 handoff_list/handoff_brief skill → Task 4 ✅
- spec §3.4 source 列 + start source 参数 → Task 3 ✅
- spec §3.5 前端交接卡 → Task 5 ✅
- spec §5 错误兜底（无 session/损坏/LLM 失败）→ Task 1(incomplete)/2(None)/4(brief=None)/5(手动粘贴) ✅
- spec §7 测试（fixture+FakeProvider）→ Task 1/2/4 ✅
- 类型一致：`list_sessions`/`read_conversation`/`git_summary`/`build_brief`/`HandoffListSkill`/`HandoffBriefSkill`/`source` 跨任务命名一致 ✅
- 无占位 ✅
