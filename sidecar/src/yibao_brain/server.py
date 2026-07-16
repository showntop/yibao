"""stdio 行分隔 JSON 服务：把 AgentLoop 接到桌面壳（Phase B 的 Tauri 侧）。"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable

from .audit import AuditLog
from .config import a11y_enabled, glm_api_key, screenshot_dir
from .ipc import RiskLevel
from .llm import FakeProvider, GLMProvider
from .loop import AgentLoop
from .memory import FakeMemory, Mem0Memory
from .safety import Gate, GatePolicy, RiskClassifier
from .skills import EchoSkill, SkillRegistry
from .skills_real import register_real_skills

ReadMsg = Callable[[], dict | None]
WriteMsg = Callable[[dict], None]


def build_loop(
    read_msg: ReadMsg,
    use_real: bool,
    db_path: str,
    provider=None,
    skills_factory=None,
) -> AgentLoop:
    real_a11y = use_real and a11y_enabled() and sys.platform == "darwin"
    reg = skills_factory() if skills_factory else SkillRegistry()
    if not skills_factory:
        reg.register(EchoSkill())
        if real_a11y:
            register_real_skills(reg)

    if provider is not None:
        prov = provider
    else:
        prov = GLMProvider() if (use_real and glm_api_key()) else FakeProvider(text="(未配置 GLM key，使用 fake 回复)")

    try:
        memory = Mem0Memory() if use_real else FakeMemory()
    except Exception:
        memory = FakeMemory()

    host = None
    if real_a11y:
        try:
            from .mac.host_mac import MacHost

            host = MacHost(screenshot_dir=screenshot_dir())
        except Exception as e:  # pyobjc 未装 / 非 mac → 回退无基座（技能会优雅报错）
            print(f"[yibao] MacHost 不可用，回退无基座：{e}", file=sys.stderr)

    def confirmer(action) -> bool:
        # 由 serve 在 confirmation_needed 事件之后触发；阻塞读壳的回答
        ans = read_msg() or {}
        return bool(ans.get("approved", False))

    return AgentLoop(
        provider=prov,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),  # L2+ 走确认
        memory=memory,
        log=AuditLog(db_path),
        confirmer=confirmer,
        host=host,
    )


def serve(loop: AgentLoop, read_msg: ReadMsg, write_msg: WriteMsg) -> None:
    while True:
        req = read_msg()
        if req is None:
            return
        if req.get("type") == "run":
            for event in loop.run(req.get("text", "")):
                write_msg({"type": "event", "event": event.model_dump(mode="json")})
            write_msg({"type": "run_done", "id": req.get("id")})


def _line_reader() -> ReadMsg:
    def _r() -> dict | None:
        line = sys.stdin.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    return _r


def _line_writer() -> WriteMsg:
    def _w(msg: dict) -> None:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return _w


def main() -> int:
    reader, writer = _line_reader(), _line_writer()
    loop = build_loop(reader, use_real=True, db_path="audit.db")
    serve(loop, reader, writer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
