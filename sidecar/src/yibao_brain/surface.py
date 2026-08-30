"""表面层桥（design §3/§11.1）：agent 的 surface/editor tool ↔ 前端「器」之间的通道。

形态（已探明的约束决定的）：tool.run 是同步的、跑在事件循环上，阻塞等回执会死锁——
所以命令型 tool **发射即回执**：dispatch() 发 surface_command 事件立即返回；
写类命令（replace_range）由前端器弹自己的确认 UI（diff 琥珀"待你定"），人在器里裁决；
裁决结果经 panel_event 上行进 latest 缓存，surface.read 读缓存，不造假回包。

不变量在 sidecar 把守（§11.1 初步倾向：不变量 sidecar、呈现细节前端）：
- dispatch 只放行白名单命令，白名单外拒发；
- stage/focus 永不从 agent tool 侧发起——surface.open 的呈现档位限 inline/peek，
  stage/focus 必须用户亲手（前端 explicit 通路），tool 层连参数都不收。
"""

import secrets
import time

# tool 侧可发的表面命令白名单。写类命令在前端器内必须经过确认 UI，不静默落稿。
DISPATCHABLE = frozenset({
    "editor.reveal_anchor",  # 把批注/消息指到的锚点在器里点亮（滚动+选中）
    "editor.replace_range",  # 区间改写提案：器弹 diff 卡，人点接受才落稿
    "editor.insert_text",    # 光标处插入提案：同走确认 UI
    "editor.set_selection",  # 选中区间（无副作用，不需确认）
})

# 事件保留名（不走 api.toml [[event]] 白名单）：surface 往返协议自身
RESERVED_EVENTS = frozenset({"surface_result"})


class SurfaceBridge:
    """发射 + 上行缓存。server 启动时 bind(emit_event)；handler 收 panel_event 时 record。"""

    def __init__(self) -> None:
        self._emit = None  # ProactiveDispatcher.emit（线程安全），未 bind 前发射拒答
        # pid -> name -> {"payload","ts"}：每面板每类事件各留最新（doc 与 selection 互不顶掉）
        self.latest: dict[str, dict[str, dict]] = {}

    def bind(self, emit) -> None:
        self._emit = emit

    def dispatch(self, panel: str, command: str, params: dict | None = None) -> dict:
        """发一条表面命令给前端器。同步立即返回，不等待器内执行/裁决结果。"""
        if self._emit is None:
            return {"ok": False, "error": "表面桥未就绪（大脑还在启动）"}
        if command not in DISPATCHABLE:
            return {"ok": False, "error": f"命令不在白名单（不变量：写类必须走器内确认 UI）：{command}"}
        sid = f"sr_{secrets.token_hex(4)}"
        self._emit({
            "kind": "surface_command",
            "panel": panel,
            "command": command,
            "params": {**(params or {}), "sid": sid},
        })
        return {"ok": True, "sid": sid, "dispatched": True,
                "hint": "命令已呈给前端器；写类命令由人在器的确认 UI 裁决，结果走上行事件"}

    def record(self, pid: str, name: str, payload: dict) -> None:
        """上行事件入缓存（面板选区/文档快照/surface_result）。每类各留最新。"""
        self.latest.setdefault(pid, {})[name] = {"payload": payload, "ts": time.time()}

    def snapshot(self, pid: str, name: str) -> dict | None:
        """读某面板某类事件的最新缓存（surface.read 的数据源）。"""
        return (self.latest.get(pid) or {}).get(name, {}).get("payload") or None
