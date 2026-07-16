"""CLI 文本壳：端到端驱动 AgentLoop（后续 Plan 2 的 Tauri 壳替换此入口）。"""
from __future__ import annotations

import sys

from .audit import AuditLog
from .config import a11y_enabled, glm_api_key, screenshot_dir
from .ipc import RiskLevel
from .llm import FakeProvider, GLMProvider
from .loop import AgentLoop
from .memory import FakeMemory, Mem0Memory
from .safety import Gate, GatePolicy, RiskClassifier
from .skills import EchoSkill, SkillRegistry
from .skills_real import ComputerUseSkill, register_real_skills


def build_loop(use_real: bool, db_path: str = "audit.db"):
    real_a11y = use_real and a11y_enabled() and sys.platform == "darwin"
    reg = SkillRegistry()
    reg.register(EchoSkill())
    if real_a11y:
        register_real_skills(reg)
        if glm_api_key():
            try:
                from .llm import ComputerUseClient

                reg.register(ComputerUseSkill(ComputerUseClient()))
            except Exception as e:
                print(f"[yibao] computer-use 兜底未启用：{e}", file=sys.stderr)

    provider = GLMProvider() if (use_real and glm_api_key()) else FakeProvider(text="(未配置 GLM key，使用 fake 回复)")
    try:
        memory = Mem0Memory() if use_real else FakeMemory()
    except Exception:
        memory = FakeMemory()

    host = None
    if real_a11y:
        try:
            from .mac.host_mac import MacHost

            host = MacHost(screenshot_dir=screenshot_dir())
        except Exception as e:
            print(f"[yibao] MacHost 不可用，回退无基座：{e}", file=sys.stderr)

    def confirmer(action) -> bool:
        print(f"\n⚠️ 高风险操作待确认：[{action.skill_id}] {action.description} params={action.params}")
        return input("允许执行？(y/N) ").strip().lower() == "y"

    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),
        memory=memory,
        log=AuditLog(db_path),
        confirmer=confirmer,
        host=host,
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
