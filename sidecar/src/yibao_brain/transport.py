"""stdio 行分隔 JSON 协议：行读写 + 语音退出语判定（独立于业务编排，可单测）。

协议（脑→壳）：hello（启动握手，含权限状态）、pong、permissions、event、run_done、feed（主屏动态+统计）。
协议（壳→脑）：run、confirm、voice_start、interrupt、ping、check_permissions、prompt_permission、panel_context、feed。
"""
from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable

ReadMsg = Callable[[], dict | None]
WriteMsg = Callable[[dict], None]

# 连续语音会话（voice_start continuous）：退出语（确定性匹配，不过 LLM）、告别语、
# 开场提示、连续没听清几轮自动退（防无人时麦克风空转）。
_VOICE_EXIT_PHRASES = {"退出", "退出对话", "没事了", "没事", "不用了", "再见", "拜拜", "谢谢你", "谢谢", "先这样"}
_VOICE_SESSION_BYE = "好的，先聊到这儿，叫我随时来～"
_VOICE_SESSION_HINT = "连续对话中：你说完我答，答完接着听；说「退出」或点团子结束"
_VOICE_SESSION_MAX_EMPTY = 2
_EXIT_STRIP = "，。！？、~～… .!?,，"


def is_exit_phrase(text: str) -> bool:
    """退出语判定：剥掉语气标点/空白后整句命中词表（「先这样了谢谢」这类混合句不拦，交给 LLM）。"""
    return text.strip(_EXIT_STRIP).strip() in _VOICE_EXIT_PHRASES


def _run_done_msg(rid, conversation_id: str = "") -> dict:
    """run_done 载荷（spec §E）：conversation_id 非空才带——空 = 无归属（旧路径），
    保持与旧客户端/旧断言的逐字节兼容（信封 _with_envelope 同样只带非空归属）。"""
    msg = {"type": "run_done", "id": rid}
    if conversation_id:
        msg["conversation_id"] = conversation_id
    return msg


def line_reader() -> ReadMsg:
    """stdin 行读取器：EOF → None；坏行 → None（跳过，不中断会话）。"""

    def _r() -> dict | None:
        line = sys.stdin.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    return _r


def line_writer() -> WriteMsg:
    """stdout 行写入器：加锁防行交错（pong 由读线程直发，与主循环消息共享 stdout）。"""
    lock = threading.Lock()

    def _w(msg: dict) -> None:
        with lock:
            sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    return _w
