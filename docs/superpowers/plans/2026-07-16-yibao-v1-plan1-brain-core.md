# 译宝 v1 · Plan 1：Python 大脑核心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建译宝的 Python sidecar「大脑」核心——一个 CLI 可驱动、纯逻辑、headless 可全测的 agent 运行时，含 IPC schema、GLM provider、技能/执行抽象、风险闸门(L0–L4)、SQLite 操作日志、mem0 记忆与 agent 回路。

**Architecture:** 同步 Python 包 `yibao_brain`。`AgentLoop` 把「LLM provider + 技能注册表 + 风险分类/闸门 + 记忆 + 日志」编排成一条「输入→规划→逐步执行→结果」的回路，通过 `confirmer` 回调处理高风险确认。所有外部依赖（GLM HTTP、mem0）走接口抽象，测试用 fake 注入，故全部单测可在无网/无 GUI 的 Linux 上运行。一个 `cli.py` 文本壳端到端驱动它。

**Tech Stack:** Python 3.12+、`uv`、`pydantic` v2、`openai` SDK（指向智谱 GLM OpenAI-兼容端点）、`mem0ai`、`sqlite3`（标准库）、`pytest`。

## Global Constraints

（逐条抄自设计文档 `docs/superpowers/specs/2026-07-16-desktop-agent-design.md`）

- 平台：跨平台 Windows + macOS 起步；本 Plan 仅纯逻辑层，无平台/GUI 依赖。
- 大脑运行时：独立 Python sidecar。
- 默认云端 provider：**GLM（智谱）computer-use**，OpenAI-兼容端点 `https://open.bigmodel.cn/api/paas/v4/`，默认模型 `glm-4.6`（可配置）；provider 走抽象层。
- 授权：风险分级 L0–L4（L0/L1 自动、L2 通知、L3 弹窗确认、L4 二次确认/可禁用）。
- 可审计日志后端：**SQLite**（截图存文件、元数据入库）。
- 记忆：mem0（本 Plan 接入接口 + 实现 + fake）。
- DRY、YAGNI、TDD、频繁提交。
- 开发机为无显示器 Linux：所有「Run」命令必须可在无 GUI/无网络（除显式联网步骤）下运行；联网步骤显式标注并由开发者在本机执行。

## 开发环境说明

- 仓库根：`/data/dennyxiao/yibao`（已 `git init`）。
- Python 代码置于 `sidecar/` 子目录（后续 Plan 2 的 Tauri 壳置于仓库根的 `app/`）。
- 用 `uv` 管理 Python 项目与虚拟环境（已确认 `uv 0.8.14` 可用）。
- 测试一律在 `sidecar/` 下用 `pytest` 运行；本服务器可运行全部单测。

## File Structure

```
sidecar/
├── pyproject.toml              # uv 项目、依赖、pytest 配置
├── .env.example                # YIBAO_GLM_API_KEY=...
├── README.md                   # 如何安装/运行/测试
└── src/yibao_brain/
    ├── __init__.py
    ├── ipc.py                  # IPC schema：RiskLevel, Action, ActionResult, Event
    ├── config.py               # 配置（env 读取）+ 默认值
    ├── llm.py                  # LLMProvider 抽象 + LLMResponse/ToolCall + GLMProvider + FakeProvider
    ├── skills.py               # Skill 抽象 + SkillContext + SkillRegistry + 一个 EchoSkill
    ├── safety.py               # RiskClassifier + GatePolicy + Gate + Decision
    ├── audit.py                # AuditLog (sqlite3)
    ├── memory.py               # Memory 抽象 + FakeMemory + Mem0Memory
    ├── loop.py                 # AgentLoop（编排一切，产出 Event 流）
    └── cli.py                  # 文本壳：端到端驱动 AgentLoop
tests/
├── test_ipc.py
├── test_llm.py
├── test_skills.py
├── test_safety.py
├── test_audit.py
├── test_memory.py
└── test_loop.py
```

**职责边界**：`ipc.py` 是 shell↔脑 的契约（Plan 2 直接复用）；`llm.py`/`skills.py`/`memory.py` 各是可替换外部能力的接口；`safety.py` 是纯策略逻辑；`audit.py` 是持久化；`loop.py` 只做编排，不含业务规则。

---

### Task 1: Python sidecar 项目脚手架

**Files:**
- Create: `sidecar/pyproject.toml`
- Create: `sidecar/src/yibao_brain/__init__.py`
- Create: `sidecar/.env.example`
- Create: `sidecar/README.md`
- Create: `sidecar/tests/__init__.py`（空）

**Interfaces:**
- Produces: 可运行的 `uv` 项目，`pytest` 可调用（0 用例绿）。

- [ ] **Step 1: 创建 `sidecar/pyproject.toml`**

```toml
[project]
name = "yibao-brain"
version = "0.1.0"
description = "译宝 Python sidecar 大脑核心"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "openai>=1.40",
    "mem0ai>=0.1.20",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
]

[project.scripts]
yibao-brain = "yibao_brain.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/yibao_brain"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: 创建包初始化文件**

`sidecar/src/yibao_brain/__init__.py`:
```python
"""译宝 Python 大脑核心。"""

__version__ = "0.1.0"
```

`sidecar/tests/__init__.py`:（空文件，0 字节）

- [ ] **Step 3: 创建 `.env.example` 与 `README.md`**

`sidecar/.env.example`:
```
YIBAO_GLM_API_KEY=your_zhipu_api_key_here
YIBAO_GLM_MODEL=glm-4.6
```

`sidecar/README.md`:
````markdown
# 译宝 · Python 大脑核心（sidecar）

## 安装
```bash
cd sidecar
uv sync --extra dev
```

## 测试
```bash
uv run pytest -q
```

## 运行 CLI（文本壳）
```bash
export YIBAO_GLM_API_KEY=...
uv run yibao-brain
```

## 配置
环境变量见 `.env.example`。
````

- [ ] **Step 4: 安装并验证 pytest 可运行（0 用例通过）**

Run: `cd sidecar && uv sync --extra dev && uv run pytest -q`
Expected: `no tests ran`（退出码 0，无报错；表示环境就绪）。

- [ ] **Step 5: 提交**

```bash
git add sidecar/
git commit -m "feat(sidecar): scaffold python brain project (uv + pytest)"
```

---

### Task 2: IPC schema（`ipc.py`）

**Files:**
- Create: `sidecar/src/yibao_brain/ipc.py`
- Test: `sidecar/tests/test_ipc.py`

**Interfaces:**
- Produces: `RiskLevel`(IntEnum L0–L4)、`Action`、`ActionResult`、`Event`（后续所有任务依赖这些类型）。

- [ ] **Step 1: 写失败测试 `tests/test_ipc.py`**

```python
from yibao_brain.ipc import RiskLevel, Action, ActionResult, Event


def test_risk_level_ordering():
    assert RiskLevel.L0_READONLY < RiskLevel.L4_CRITICAL
    assert int(RiskLevel.L3_HIGH) == 3


def test_action_defaults():
    a = Action(skill_id="echo", params={"text": "hi"})
    assert a.skill_id == "echo"
    assert a.params == {"text": "hi"}
    assert a.risk == RiskLevel.L1_LOW
    assert a.id  # auto-assigned non-empty


def test_action_result_optional_fields():
    r = ActionResult(success=True)
    assert r.success is True
    assert r.data == {}
    assert r.error == ""
    assert r.screenshot_path is None


def test_event_kinds():
    e = Event(kind="final_reply", text="done")
    assert e.kind == "final_reply"
    assert e.action is None
    e2 = Event(kind="confirmation_needed", confirmation_id="c1")
    assert e2.confirmation_id == "c1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_ipc.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.ipc`）。

- [ ] **Step 3: 实现 `src/yibao_brain/ipc.py`**

```python
"""IPC schema：译宝 shell ↔ 脑 的契约（Plan 2 的 Tauri 壳直接复用）。"""
from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field


class RiskLevel(IntEnum):
    L0_READONLY = 0
    L1_LOW = 1
    L2_MEDIUM = 2
    L3_HIGH = 3
    L4_CRITICAL = 4


class Action(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("act"))
    skill_id: str
    params: dict = Field(default_factory=dict)
    description: str = ""
    risk: RiskLevel = RiskLevel.L1_LOW


class ActionResult(BaseModel):
    success: bool
    data: dict = Field(default_factory=dict)
    error: str = ""
    screenshot_path: str | None = None


EventKind = Literal[
    "thought",
    "action_proposed",
    "confirmation_needed",
    "action_result",
    "final_reply",
    "error",
]


class Event(BaseModel):
    kind: EventKind
    text: str = ""
    action: Action | None = None
    result: ActionResult | None = None
    confirmation_id: str | None = None


_id_counter = 0


def _new_id(prefix: str) -> str:
    global _id_counter
    _id_counter += 1
    return f"{prefix}_{_id_counter}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_ipc.py -q`
Expected: PASS（4 passed）。

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/ipc.py sidecar/tests/test_ipc.py
git commit -m "feat(sidecar): ipc schema (RiskLevel, Action, ActionResult, Event)"
```

---

### Task 3: LLM provider 抽象 + GLM provider（`llm.py`、`config.py`）

**Files:**
- Create: `sidecar/src/yibao_brain/config.py`
- Create: `sidecar/src/yibao_brain/llm.py`
- Test: `sidecar/tests/test_llm.py`

**Interfaces:**
- Consumes: 无。
- Produces: `LLMResponse`、`ToolCall`、`LLMProvider`(ABC `chat(messages, tools) -> LLMResponse`)、`GLMProvider`、`FakeProvider`。

- [ ] **Step 1: 写失败测试 `tests/test_llm.py`**

```python
from yibao_brain.llm import GLMProvider, FakeProvider, LLMResponse, ToolCall


def test_tool_call_fields():
    tc = ToolCall(id="t1", skill_id="echo", params={"text": "x"})
    assert tc.id == "t1" and tc.skill_id == "echo" and tc.params == {"text": "x"}


def test_fake_provider_returns_canned():
    p = FakeProvider(text="ok", tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})])
    resp = p.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "ok"
    assert resp.tool_calls[0].skill_id == "echo"


def test_glm_provider_parses_openai_response(monkeypatch):
    # 用假 client 注入，避免真实联网
    class FakeMsg:
        content = "hello"
        tool_calls = None

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return FakeResp()

    p = GLMProvider(api_key="x", model="glm-4.6", client_factory=FakeClient)
    resp = p.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "hello"
    assert resp.tool_calls == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_llm.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.llm`）。

- [ ] **Step 3: 实现 `src/yibao_brain/config.py`**

```python
"""配置：从环境变量读取，带默认值。"""
from __future__ import annotations

import os


def glm_api_key() -> str:
    return os.environ.get("YIBAO_GLM_API_KEY", "")


def glm_model() -> str:
    return os.environ.get("YIBAO_GLM_MODEL", "glm-4.6")


def glm_base_url() -> str:
    return os.environ.get("YIBAO_GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
```

- [ ] **Step 4: 实现 `src/yibao_brain/llm.py`**

```python
"""LLM provider 抽象 + GLM(智谱) 实现 + 测试用 Fake。"""
from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel, Field

from .config import glm_api_key, glm_base_url, glm_model


class ToolCall(BaseModel):
    id: str
    skill_id: str
    params: dict = Field(default_factory=dict)


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class LLMProvider(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse: ...


class FakeProvider:
    """测试用：返回预设响应。"""

    def __init__(self, text: str = "", tool_calls: list[ToolCall] | None = None):
        self._text = text
        self._tool_calls = tool_calls or []
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        return LLMResponse(text=self._text, tool_calls=list(self._tool_calls))


class GLMProvider:
    """智谱 GLM，走 OpenAI-兼容端点。client_factory 注入便于测试。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        client_factory=None,
    ):
        from openai import OpenAI

        self.model = model or glm_model()
        factory = client_factory or OpenAI
        self.client = factory(
            api_key=api_key or glm_api_key(),
            base_url=base_url or glm_base_url(),
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": t} if "function" not in t else t
                for t in tools
            ]
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        tool_calls: list[ToolCall] = []
        raw = getattr(msg, "tool_calls", None) or []
        for tc in raw:
            fn = tc.function
            try:
                params = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                params = {}
            tool_calls.append(ToolCall(id=tc.id, skill_id=fn.name, params=params))
        return LLMResponse(text=msg.content or "", tool_calls=tool_calls)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_llm.py -q`
Expected: PASS（3 passed）。

- [ ] **Step 6: 提交**

```bash
git add sidecar/src/yibao_brain/config.py sidecar/src/yibao_brain/llm.py sidecar/tests/test_llm.py
git commit -m "feat(sidecar): llm provider abstraction + GLM provider + fake"
```

---

### Task 4: 技能抽象 + 注册表 + EchoSkill（`skills.py`）

**Files:**
- Create: `sidecar/src/yibao_brain/skills.py`
- Test: `sidecar/tests/test_skills.py`

**Interfaces:**
- Consumes: `ipc.Action`、`ipc.ActionResult`、`ipc.RiskLevel`。
- Produces: `SkillContext`、`Skill`(ABC: `id`、`description`、`default_risk`、`run(params, ctx)->ActionResult`)、`SkillRegistry`（`register/get/list/openai_tools`）、`EchoSkill`。

- [ ] **Step 1: 写失败测试 `tests/test_skills.py`**

```python
from yibao_brain.skills import SkillRegistry, EchoSkill, SkillContext
from yibao_brain.ipc import RiskLevel


def test_echo_skill_runs():
    ctx = SkillContext()
    r = EchoSkill().run({"text": "hello"}, ctx)
    assert r.success and r.data == {"echo": "hello"}


def test_registry_register_get_list():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    assert reg.get("echo").id == "echo"
    assert [s.id for s in reg.list()] == ["echo"]


def test_registry_openai_tools_schema():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    tools = reg.openai_tools()
    assert tools[0]["name"] == "echo"
    assert "parameters" in tools[0]


def test_echo_skill_default_risk_is_low():
    assert EchoSkill().default_risk == RiskLevel.L1_LOW
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_skills.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.skills`）。

- [ ] **Step 3: 实现 `src/yibao_brain/skills.py`**

```python
"""技能/动作抽象 + 注册表 + 一个 EchoSkill（真实技能在 Plan 3）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .ipc import ActionResult, RiskLevel


@dataclass
class SkillContext:
    """执行上下文：留给真实技能放日志/截图等。Plan 1 暂为空壳。"""
    meta: dict = field(default_factory=dict)


class Skill(ABC):
    id: str = "base"
    description: str = ""
    default_risk: RiskLevel = RiskLevel.L1_LOW

    @abstractmethod
    def run(self, params: dict, ctx: SkillContext) -> ActionResult: ...

    def openai_schema(self) -> dict:
        """OpenAI function-calling 工具描述（子类按需覆盖 params 描述）。"""
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        }


class EchoSkill(Skill):
    id = "echo"
    description = "原样回显一段文本（占位技能，用于验证回路）。"

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        return ActionResult(success=True, data={"echo": params.get("text", "")})


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.id] = skill

    def get(self, skill_id: str) -> Skill:
        return self._skills[skill_id]

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def openai_tools(self) -> list[dict]:
        return [s.openai_schema() for s in self._skills.values()]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_skills.py -q`
Expected: PASS（4 passed）。

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/skills.py sidecar/tests/test_skills.py
git commit -m "feat(sidecar): skill abstraction + registry + echo skill"
```

---

### Task 5: 风险分类 + 闸门（`safety.py`）

**Files:**
- Create: `sidecar/src/yibao_brain/safety.py`
- Test: `sidecar/tests/test_safety.py`

**Interfaces:**
- Consumes: `ipc.Action`、`ipc.RiskLevel`、`skills.Skill`。
- Produces: `Decision`(AUTO/CONFIRM/DENY)、`GatePolicy`、`RiskClassifier`、`Gate`。

- [ ] **Step 1: 写失败测试 `tests/test_safety.py`**

```python
from yibao_brain.safety import Decision, GatePolicy, RiskClassifier, Gate
from yibao_brain.ipc import Action, RiskLevel
from yibao_brain.skills import EchoSkill


def make_action(risk):
    return Action(skill_id="x", risk=risk)


def test_classifier_uses_skill_default():
    c = RiskClassifier()
    assert c.classify(Action(skill_id="echo"), EchoSkill()) == RiskLevel.L1_LOW


def test_classifier_escalates_on_dangerous_params():
    c = RiskClassifier(dangerous_keywords=["delete", "format", "payment"])
    a = Action(skill_id="x", params={"target": "delete everything"}, risk=RiskLevel.L1_LOW)
    # 无 skill 也应能工作（仅靠 params 关键词升级）
    assert c.classify(a, None) == RiskLevel.L3_HIGH


def test_gate_auto_for_low_risk():
    gate = Gate(GatePolicy())  # 默认 auto_below=L2
    assert gate.decide(make_action(RiskLevel.L0_READONLY)) == Decision.AUTO
    assert gate.decide(make_action(RiskLevel.L2_MEDIUM)) == Decision.AUTO


def test_gate_confirm_for_high_risk():
    gate = Gate(GatePolicy())
    assert gate.decide(make_action(RiskLevel.L3_HIGH)) == Decision.CONFIRM


def test_gate_deny_for_critical_when_disabled():
    policy = GatePolicy(allow_critical=False)  # L4 直接拒绝
    gate = Gate(policy)
    assert gate.decide(make_action(RiskLevel.L4_CRITICAL)) == Decision.DENY
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_safety.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.safety`）。

- [ ] **Step 3: 实现 `src/yibao_brain/safety.py`**

```python
"""风险分级授权：分类器 + 闸门（纯策略逻辑）。"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from .ipc import Action, RiskLevel
from .skills import Skill


class Decision(str, Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


class GatePolicy(BaseModel):
    auto_below_or_equal: RiskLevel = RiskLevel.L2_MEDIUM  # <= 该级自动执行
    confirm_below_or_equal: RiskLevel = RiskLevel.L4_CRITICAL  # <= 该级可经确认执行
    allow_critical: bool = True  # False 时 L4 直接 DENY


_DEFAULT_DANGEROUS = [
    "delete", "remove", "rm ", "format", "payment", "pay", "send message",
    "email", "install", "sudo", "chmod", "reg add", "defaults write",
]


class RiskClassifier:
    """风险 = max(skill 默认级, 关键词命中升级级)。"""

    def __init__(self, dangerous_keywords: list[str] | None = None, escalate_to: RiskLevel = RiskLevel.L3_HIGH):
        self.keywords = [k.lower() for k in (dangerous_keywords or _DEFAULT_DANGEROUS)]
        self.escalate_to = escalate_to

    def classify(self, action: Action, skill: Skill | None) -> RiskLevel:
        base = skill.default_risk if skill is not None else action.risk
        text = " ".join(str(v) for v in action.params.values()).lower()
        if any(k in text for k in self.keywords):
            return max(base, self.escalate_to)
        return base


class Gate:
    def __init__(self, policy: GatePolicy):
        self.policy = policy

    def decide(self, action: Action) -> Decision:
        r = action.risk
        if r <= self.policy.auto_below_or_equal:
            return Decision.AUTO
        if r == RiskLevel.L4_CRITICAL and not self.policy.allow_critical:
            return Decision.DENY
        if r <= self.policy.confirm_below_or_equal:
            return Decision.CONFIRM
        return Decision.DENY
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_safety.py -q`
Expected: PASS（5 passed）。

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/safety.py sidecar/tests/test_safety.py
git commit -m "feat(sidecar): risk classifier + L0-L4 gate"
```

---

### Task 6: SQLite 操作日志（`audit.py`）

**Files:**
- Create: `sidecar/src/yibao_brain/audit.py`
- Test: `sidecar/tests/test_audit.py`

**Interfaces:**
- Consumes: `ipc.Action`、`ipc.ActionResult`。
- Produces: `AuditLog`（`record(action, result)`、`recent(n)`）。

- [ ] **Step 1: 写失败测试 `tests/test_audit.py`**

```python
from yibao_brain.audit import AuditLog
from yibao_brain.ipc import Action, ActionResult, RiskLevel


def test_record_and_recent(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    a = Action(skill_id="echo", params={"text": "hi"}, risk=RiskLevel.L1_LOW)
    r = ActionResult(success=True, data={"echo": "hi"})
    log.record(a, r, screenshot_path=None)
    rows = log.recent(10)
    assert len(rows) == 1
    assert rows[0]["skill_id"] == "echo"
    assert rows[0]["success"] == 1
    assert rows[0]["risk"] == int(RiskLevel.L1_LOW)


def test_recent_respects_limit(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    for i in range(5):
        log.record(Action(skill_id="echo"), ActionResult(success=True))
    assert len(log.recent(2)) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_audit.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.audit`）。

- [ ] **Step 3: 实现 `src/yibao_brain/audit.py`**

```python
"""可审计操作日志：SQLite（截图路径入库，截图文件由调用方存盘）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .ipc import Action, ActionResult


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY,
                ts TEXT DEFAULT (datetime('now')),
                skill_id TEXT,
                params TEXT,
                risk INTEGER,
                success INTEGER,
                error TEXT,
                data TEXT,
                screenshot_path TEXT
            )
            """
        )
        self.conn.commit()

    def record(self, action: Action, result: ActionResult, screenshot_path: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO actions (id, skill_id, params, risk, success, error, data, screenshot_path)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                action.id,
                action.skill_id,
                json.dumps(action.params, ensure_ascii=False),
                int(action.risk),
                1 if result.success else 0,
                result.error,
                json.dumps(result.data, ensure_ascii=False),
                screenshot_path,
            ),
        )
        self.conn.commit()

    def recent(self, n: int = 50) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT ?", (n,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_audit.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/audit.py sidecar/tests/test_audit.py
git commit -m "feat(sidecar): sqlite audit log"
```

---

### Task 7: 记忆接口 + FakeMemory + Mem0Memory（`memory.py`）

**Files:**
- Create: `sidecar/src/yibao_brain/memory.py`
- Test: `sidecar/tests/test_memory.py`

**Interfaces:**
- Consumes: 无。
- Produces: `Memory`(ABC: `add(text, user_id)`、`recall(query, user_id)->list[str]`)、`FakeMemory`、`Mem0Memory`。

- [ ] **Step 1: 写失败测试 `tests/test_memory.py`**

```python
from yibao_brain.memory import FakeMemory


def test_fake_add_and_recall():
    m = FakeMemory()
    m.add("用户喜欢深色模式", user_id="u1")
    hits = m.recall("偏好", user_id="u1")
    assert "用户喜欢深色模式" in hits
    assert m.recall("x", user_id="other") == []  # 隔离不同用户
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_memory.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.memory`）。

- [ ] **Step 3: 实现 `src/yibao_brain/memory.py`**

```python
"""长期记忆：接口 + Fake（测试）+ Mem0（生产）。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Memory(ABC):
    @abstractmethod
    def add(self, text: str, user_id: str) -> None: ...

    @abstractmethod
    def recall(self, query: str, user_id: str) -> list[str]: ...


class FakeMemory(Memory):
    """简单子串匹配；按 user_id 隔离。"""

    def __init__(self) -> None:
        self._by_user: dict[str, list[str]] = {}

    def add(self, text: str, user_id: str) -> None:
        self._by_user.setdefault(user_id, []).append(text)

    def recall(self, query: str, user_id: str) -> list[str]:
        items = self._by_user.get(user_id, [])
        q = query.lower()
        return [it for it in items if q and (q in it.lower() or it.lower() in q)]


class Mem0Memory(Memory):
    """mem0 封装；失败时优雅降级为空召回（不阻断回路）。"""

    def __init__(self) -> None:
        from mem0 import Memory as _Mem0

        self._m = _Mem0()

    def add(self, text: str, user_id: str) -> None:
        self._m.add(messages=[{"role": "user", "content": text}], user_id=user_id)

    def recall(self, query: str, user_id: str) -> list[str]:
        try:
            res = self._m.search(query=query, user_id=user_id)
        except Exception:
            return []
        out: list[str] = []
        for item in res if isinstance(res, list) else (res.get("results", []) if isinstance(res, dict) else []):
            mem = item.get("memory") if isinstance(item, dict) else str(item)
            if mem:
                out.append(mem)
        return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_memory.py -q`
Expected: PASS（1 passed）。

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/memory.py sidecar/tests/test_memory.py
git commit -m "feat(sidecar): memory interface + fake + mem0 wrapper"
```

---

### Task 8: Agent 回路（`loop.py`）

**Files:**
- Create: `sidecar/src/yibao_brain/loop.py`
- Test: `sidecar/tests/test_loop.py`

**Interfaces:**
- Consumes: `llm.LLMProvider`、`skills.SkillRegistry`/`SkillContext`、`safety.RiskClassifier`/`Gate`/`GatePolicy`、`audit.AuditLog`、`memory.Memory`、`ipc.*`。
- Produces: `AgentLoop`（`run(user_text) -> Iterator[Event]`）。`confirmer: Callable[[Action], bool]` 处理确认。

- [ ] **Step 1: 写失败测试 `tests/test_loop.py`**

```python
from yibao_brain.loop import AgentLoop
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.skills import SkillRegistry, EchoSkill
from yibao_brain.safety import RiskClassifier, Gate, GatePolicy
from yibao_brain.audit import AuditLog
from yibao_brain.memory import FakeMemory
from yibao_brain.ipc import Event


def build_loop(tmp_path, provider, confirmer=lambda a: True):
    reg = SkillRegistry()
    reg.register(EchoSkill())
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=confirmer,
    )


def test_loop_executes_tool_then_replies(tmp_path):
    # 第一轮模型调用 echo，第二轮给出最终回复
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="echoed: hi"),
    )
    loop = build_loop(tmp_path, provider)
    events = list(loop.run("请回显 hi"))
    kinds = [e.kind for e in events]
    assert "action_result" in kinds
    assert kinds[-1] == "final_reply"
    assert "echoed: hi" in events[-1].text


def test_loop_confirms_high_risk(tmp_path):
    # 一个高风险技能
    from yibao_brain.skills import Skill, SkillContext
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerSkill(Skill):
        id = "danger"
        description = "危险占位"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True})

    reg = SkillRegistry()
    reg.register(DangerSkill())
    loop = AgentLoop(
        provider=_TwoStepProvider(
            first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
            second=FakeProvider(text="done"),
        ),
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=lambda a: False,  # 用户拒绝
    )
    events = list(loop.run("做危险的事"))
    kinds = [e.kind for e in events]
    assert "confirmation_needed" in kinds
    # 拒绝后不执行 danger
    assert not any(e.kind == "action_result" and e.result and e.result.data.get("did") for e in events)


class _TwoStepProvider:
    """第一次返回 first，之后都返回 second。"""

    def __init__(self, first, second):
        self._first = first
        self._second = second
        self._n = 0

    def chat(self, messages, tools=None):
        self._n += 1
        return self._first.chat(messages, tools) if self._n == 1 else self._second.chat(messages, tools)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_loop.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.loop`）。

- [ ] **Step 3: 实现 `src/yibao_brain/loop.py`**

```python
"""Agent 回路：输入 -> 规划 -> 逐步执行 -> 结果，产出 Event 流。"""
from __future__ import annotations

from collections.abc import Callable, Iterator

from .audit import AuditLog
from .ipc import Action, Event, RiskLevel
from .llm import LLMProvider, LLMResponse
from .memory import Memory
from .safety import Decision, Gate, RiskClassifier
from .skills import SkillContext, SkillRegistry

Confirmer = Callable[[Action], bool]

SYSTEM_PROMPT = (
    "你是译宝，一个桌面 AI 助手。通过调用工具帮用户操作电脑。"
    "若无需调用工具，直接用自然语言回复。"
)


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        skills: SkillRegistry,
        classifier: RiskClassifier,
        gate: Gate,
        memory: Memory,
        log: AuditLog,
        confirmer: Confirmer | None = None,
        user_id: str = "default",
        max_steps: int = 8,
    ):
        self.provider = provider
        self.skills = skills
        self.classifier = classifier
        self.gate = gate
        self.memory = memory
        self.log = log
        self.confirmer = confirmer or (lambda _a: False)
        self.user_id = user_id
        self.max_steps = max_steps

    def run(self, user_text: str) -> Iterator[Event]:
        memories = self.memory.recall(user_text, self.user_id)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memories:
            messages.append({"role": "system", "content": "关于用户的记忆：\n" + "\n".join(memories)})
        messages.append({"role": "user", "content": user_text})
        tools = self.skills.openai_tools()

        for _ in range(self.max_steps):
            resp: LLMResponse = self.provider.chat(messages, tools=tools)
            if not resp.tool_calls:
                self.memory.add(user_text, self.user_id)
                yield Event(kind="final_reply", text=resp.text)
                return
            messages.append({"role": "assistant", "content": resp.text})
            proceeded = False
            for tc in resp.tool_calls:
                skill = self.skills.get(tc.skill_id)
                action = Action(
                    skill_id=tc.skill_id,
                    params=tc.params,
                    description=skill.description,
                    risk=self.classifier.classify(
                        Action(skill_id=tc.skill_id, params=tc.params), skill
                    ),
                )
                yield Event(kind="action_proposed", action=action)
                decision = self.gate.decide(action)
                if decision == Decision.CONFIRM:
                    yield Event(kind="confirmation_needed", action=action, confirmation_id=action.id)
                    if not self.confirmer(action):
                        yield Event(kind="error", text=f"用户拒绝执行 {tc.skill_id}")
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": "用户拒绝执行该操作"})
                        continue
                elif decision == Decision.DENY:
                    yield Event(kind="error", text=f"策略禁止执行 {tc.skill_id}（风险过高）")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "策略禁止该操作"})
                    continue
                ctx = SkillContext()
                result = skill.run(tc.params, ctx)
                self.log.record(action, result)
                yield Event(kind="action_result", action=action, result=result)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)}
                )
                proceeded = True
            if not proceeded:
                # 所有工具调用都被拒/禁，给模型一次机会换策略
                continue
        yield Event(kind="error", text="达到最大步数仍未完成")
```

并补充一个模块级小工具 `_stringify_result`（同文件末尾）：

```python
def _stringify_result(result) -> str:
    import json
    payload = {"success": result.success, "data": result.data, "error": result.error}
    return json.dumps(payload, ensure_ascii=False)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_loop.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: 跑全量测试确认无回归**

Run: `cd sidecar && uv run pytest -q`
Expected: 全部 PASS（ipc 4 + llm 3 + skills 4 + safety 5 + audit 2 + memory 1 + loop 2 = 21）。

- [ ] **Step 6: 提交**

```bash
git add sidecar/src/yibao_brain/loop.py sidecar/tests/test_loop.py
git commit -m "feat(sidecar): agent loop orchestrating provider/skills/gate/memory/log"
```

---

### Task 9: CLI 文本壳（`cli.py`，端到端驱动）

**Files:**
- Create: `sidecar/src/yibao_brain/cli.py`
- Test: 手动验证（无自动化断言；纯交互入口）。

**Interfaces:**
- Consumes: `loop.AgentLoop` + 各组件；`config.glm_api_key`。
- Produces: `main()`（`yibao-brain` 入口），REPL 读 stdin、打印 Event、对 `confirmation_needed` 在终端询问 y/n。

- [ ] **Step 1: 实现 `src/yibao_brain/cli.py`**

```python
"""CLI 文本壳：端到端驱动 AgentLoop（后续 Plan 2 的 Tauri 壳替换此入口）。"""
from __future__ import annotations

import sys

from .audit import AuditLog
from .config import glm_api_key
from .llm import FakeProvider, GLMProvider
from .loop import AgentLoop
from .memory import FakeMemory, Mem0Memory
from .safety import Gate, GatePolicy, RiskClassifier
from .skills import EchoSkill, SkillContext, SkillRegistry


def build_loop(use_real: bool, db_path: str = "audit.db"):
    reg = SkillRegistry()
    reg.register(EchoSkill())

    provider = GLMProvider() if (use_real and glm_api_key()) else FakeProvider(text="(未配置 GLM key，使用 fake 回复)")
    try:
        memory = Mem0Memory() if use_real else FakeMemory()
    except Exception:
        memory = FakeMemory()

    def confirmer(action) -> bool:
        print(f"\n⚠️ 高风险操作待确认：[{action.skill_id}] {action.description} params={action.params}")
        return input("允许执行？(y/N) ").strip().lower() == "y"

    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=memory,
        log=AuditLog(db_path),
        confirmer=confirmer,
    )


def main() -> int:
    use_real = "--fake" not in sys.argv
    loop = build_loop(use_real)
    print("译宝大脑 CLI（输入 exit 退出；加 --fake 用假模型）")
    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            return 0
        for event in loop.run(text):
            if event.kind == "final_reply":
                print(f"译宝> {event.text}")
            elif event.kind == "action_proposed":
                print(f"  · 提议操作：{event.action.skill_id}({event.action.params}) 风险={event.action.risk.name}")
            elif event.kind == "action_result":
                ok = "✓" if event.result.success else "✗"
                print(f"  {ok} 结果：{event.result.data} {event.result.error}")
            elif event.kind == "error":
                print(f"  ✗ {event.text}")
            # confirmation_needed 由 confirmer 在 run() 内部已交互处理
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 手动验证（无 GLM key，走 fake）**

Run: `cd sidecar && echo "exit" | uv run yibao-brain --fake`
Expected: 打印欢迎语后正常退出（退出码 0）。

- [ ] **Step 3: 提交**

```bash
git add sidecar/src/yibao_brain/cli.py
git commit -m "feat(sidecar): cli text harness driving the agent loop"
```

---

### Task 10: 联通真实 GLM 的冒烟测试（可选，需 key + 联网）

**Files:**
- 无新文件；手动验证。

**Interfaces:**
- Consumes: Task 9 的 CLI + 真实 `YIBAO_GLM_API_KEY`。

- [ ] **Step 1: 在开发者本机设置 key 并运行**

```bash
export YIBAO_GLM_API_KEY=<你的智谱 key>
cd sidecar && uv run yibao-brain
```
输入：`请用 echo 工具回显 "hello"`
Expected: 终端出现 `· 提议操作：echo(...)` 与 `✓ 结果：{'echo': 'hello'}`，随后 `译宝> ...` 最终回复。（若 GLM 未按预期触发工具调用，检查模型 id 与 system prompt，记录到 README「已知问题」。）

- [ ] **Step 2: 记录结果到 README「已知问题/验证记录」段**

若通过，在 `sidecar/README.md` 追加：
```
## 验证记录
- GLM 真机冒烟：模型=<填实际 id>，echo 工具触发正常，日期=<填>。
```

- [ ] **Step 3: 提交**

```bash
git add sidecar/README.md
git commit -m "docs(sidecar): record glm smoke test result"
```

---

## Self-Review（计划自检结果）

**1. Spec 覆盖**（对照设计文档 v1 范围）：
- Python 大脑运行时 / agent 回路 → Task 8 ✓
- IPC 契约 → Task 2 ✓
- GLM provider 抽象（可切 Claude）→ Task 3 ✓（Claude 实现留 Plan 4，接口已就绪）
- 技能优先执行抽象 → Task 4 ✓（真实 a11y/CU 技能留 Plan 3）
- 风险分级 L0–L4 → Task 5 ✓
- SQLite 操作日志 → Task 6 ✓
- mem0 记忆（自进化第①档）→ Task 7 ✓
- CLI 驱动（headless 验证）→ Task 9 ✓
- 本 Plan 范围外（明确推迟）：Tauri 壳/Vue 形象/全局热键（Plan 2）、真实执行层 a11y+CU（Plan 3）、STT/TTS 打磨（Plan 4）。已在开头声明。

**2. 占位符扫描**：无 TBD/TODO，无废弃占位代码 ✓。

**3. 类型/命名一致性**：`Action.risk`、`Skill.default_risk`、`GatePolicy.auto_below_or_equal`、`Decision`、`Event.kind` 在各 Task 间一致 ✓。
