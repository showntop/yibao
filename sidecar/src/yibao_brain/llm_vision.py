"""视觉域：屏幕描述/截图问答/B 源概括/SoM 操作（ComputerUseClient）+ 主动观察判定。

R-32b 从 llm.py 拆出（2026-08-22）：provider 抽象（llm.py）与视觉客户端分离。
llm.py 保留 provider/协议/Fake/parse_observe 之外的通用件并 re-export 本域符号
（server/background/cli 与测试的 `from .llm import ComputerUseClient` 路径不变；
引用面均为函数内延迟 import，monkeypatch yibao_brain.llm.* 约定不受影响）。
"""
from __future__ import annotations

import json
import re

from .config import vision_api_key, vision_base_url, vision_model
from .log import log


OBSERVE_SYSTEM_PROMPT = (
    "你看用户当前屏幕截图。只在有明显、值得主动帮忙的点时才开口："
    "例如报错、编译失败、卡住的对话框、明显困惑。没有值得说的就别说。"
    '只回一个 JSON：{"speak": true/false, "text": "≤20字中文建议；没有则空串"}。'
)


def parse_observe(content: str) -> dict | None:
    """从视觉模型回复里取 {"speak","text"}；非法返回 None。"""
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or type(obj.get("speak")) is not bool:
        return None
    if obj["speak"] is False:
        return {"speak": False, "text": ""}
    text = " ".join(str(obj.get("text", "")).split())[:20]
    if not text:
        return None
    return {"speak": True, "text": text}


def _vision_create_with_retry(create_fn, *, retries: int = 2, base_delay: float = 0.8):
    """视觉模型远端调用：对连接/超时错误短退避重试。

    GLM 视觉端点偶发 Connection error；若不重试，一次抖动就让整步 computer_use 报废、
    模型只好从头再来（重新开应用+截图），放大延迟。仅对网络类错误重试，其余立即抛出。
    """
    import time

    try:
        from openai import APIConnectionError, APITimeoutError

        retryable = (APIConnectionError, APITimeoutError, TimeoutError)
    except Exception:
        retryable = (TimeoutError,)
    for attempt in range(retries + 1):
        try:
            return create_fn()
        except retryable:
            if attempt >= retries:
                raise
            time.sleep(base_delay * (attempt + 1))


CHOOSE_TEMPERATURE = 0.1  # SoM 选号要确定性，低温降抖动

SCREEN_DESCRIBE_PROMPT = (
    "列出这张屏幕截图里可见的应用窗口：每个窗口一行，给出应用名、大致位置（左/右/上/下/全屏）"
    "和大致内容（80 字以内）。不要遗漏占画面比例大的窗口。只输出清单本身。"
)


def describe_screen(client, b64: str) -> str | None:
    """屏幕可见窗口枚举（截屏看屏幕/截图唤起共用）。client 为 ComputerUseClient；失败返 None。"""
    try:
        resp = _vision_create_with_retry(lambda: client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": SCREEN_DESCRIBE_PROMPT},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": b64}}]},
            ],
        ))
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return text[:200] or None
    except Exception as e:
        log(f"屏幕描述失败（已跳过）：{e}")
        return None


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
        log(f"截图问答失败（已跳过）：{e}")
        return None


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
        log(f"B 源截图概括失败（已跳过）：{e}")
        return None


class ComputerUseClient:
    """GLM-4.6V 视觉 grounding 兜底：截图 + 任务 → 下一步动作 JSON。

    动作 JSON: {"action":"click|type|scroll|finish","box":[x1,y1,x2,y2],"text":"..."}
    box 为截图绝对像素 bbox。client_factory 注入便于测试。
    """

    SYSTEM_PROMPT = (
        "你是桌面 GUI 操作助手。观察截图，根据用户任务输出【下一个动作】的 JSON：\n"
        '{"action":"click|type|scroll|finish","box":[x1,y1,x2,y2],"text":"..."}\n'
        "规则：box 是目标元素在截图中的绝对像素 bbox（左上角 0,0，基于原图分辨率）；"
        "click 用 box 中心点；type 时 text 为要输入的文字；"
        "任务完成或无法继续时 action=finish。只输出这一个 JSON，不要多余文字。"
    )

    MARK_SYSTEM_PROMPT = (
        "你是桌面 GUI 操作助手。屏幕上红色数字框是可交互元素(1..N)，灰色字母框是区域(A..F)。"
        "根据用户任务给出【下一个动作】：目标在某个红框元素上就输出它的数字编号（一个整数）；"
        "目标不在任何红框元素上（如网页、画布等自绘内容），输出它所在的字母区域（一个字母）；"
        "需要输入文字时输出 JSON {\"action\":\"type\",\"text\":\"...\"}；"
        "任务完成时输出 finish。只输出整数编号或一个字母，不要任何其他文字。"
    )

    def __init__(self, api_key=None, model=None, base_url=None, client_factory=None):
        from openai import OpenAI

        self.model = model or vision_model()
        self.prefers_raw_bbox = self.model.startswith("glm-4.1v-thinking-")
        factory = client_factory or OpenAI
        self.client = factory(
            api_key=api_key or vision_api_key(),
            base_url=base_url or vision_base_url(),
        )

    def next_action(self, screenshot_b64: str, task: str, history: list | None = None) -> dict | None:
        messages: list[dict] = [{"role": "system", "content": self.SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": screenshot_b64}},
                {"type": "text", "text": f"任务：{task}\n请给出下一步动作 JSON。"},
            ],
        })
        resp = _vision_create_with_retry(lambda: self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            extra_body={"thinking": {"type": "enabled"}},  # GLM 特有参数走 extra_body（openai SDK 不认顶层 kwargs）
        ))
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        action = self._parse_action(content)
        return self._convert_model_box(action, screenshot_b64)

    def _convert_model_box(self, action: dict | None, screenshot_b64: str) -> dict | None:
        """把特定视觉模型的 grounding 坐标还原为截图物理像素。"""
        if not action or not self.model.startswith("glm-4.1v-thinking-"):
            return action
        box = action.get("box") or []
        if len(box) != 4:
            return action
        try:
            import base64
            import io

            from PIL import Image


            payload = screenshot_b64.split(",", 1)[1]
            with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
                width, height = image.size
            x1, y1, x2, y2 = (float(v) for v in box)
        except (IndexError, TypeError, ValueError, OSError):
            return None
        converted = dict(action)
        converted["box"] = [
            x1 * width / 1000,
            y1 * height / 1000,
            x2 * width / 1000,
            y2 * height / 1000,
        ]
        return converted

    @staticmethod
    def _parse_action(content: str) -> dict | None:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    def choose_action(self, marked_image_b64: str, task: str, n_marks: int,
                      history: list | None = None, n_zones: int = 0):
        messages: list[dict] = [{"role": "system", "content": self.MARK_SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        zone_hint = f"和 {n_zones} 个灰框字母区域(A-{chr(64 + n_zones)})" if n_zones else ""
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": marked_image_b64}},
                {"type": "text", "text": f"任务：{task}\n共有 {n_marks} 个红框数字标记(1-{n_marks}){zone_hint}。给出下一个动作。"},
            ],
        })
        resp = _vision_create_with_retry(lambda: self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=CHOOSE_TEMPERATURE,
        ))
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        return self._parse_marked_action(content, n_marks, n_zones)

    def observe(self, screenshot_b64: str, app: str) -> dict | None:
        """视觉模型看一眼：是否有值得主动搭话的点。返 {"speak":bool,"text":str} 或 None。"""
        try:
            resp = _vision_create_with_retry(lambda: self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": OBSERVE_SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": screenshot_b64}},
                        {"type": "text", "text": f"前台应用：{app}。判断是否值得搭话。"},
                    ]},
                ],
            ))
            content = (resp.choices[0].message.content or "") if resp.choices else ""
        except Exception as e:
            log(f"主动搭话视觉调用失败：{e}")
            return None
        return parse_observe(content)

    @staticmethod
    def _parse_marked_action(content: str, n_marks: int, n_zones: int = 0) -> dict | None:
        s = (content or "").strip()
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                if obj.get("action") in ("click", "type", "finish"):
                    mk = obj.get("mark")
                    if mk is not None and not (isinstance(mk, int) and 1 <= mk <= n_marks):
                        return None
                    return obj
                if obj.get("action") == "zoom":
                    zone = str(obj.get("zone") or "").upper()
                    if n_zones and len(zone) == 1 and "A" <= zone < chr(65 + n_zones):
                        return {"action": "zoom", "zone": zone}
                    return None
            except json.JSONDecodeError:
                pass
        if "finish" in s.lower():
            return {"action": "finish"}
        lm = re.fullmatch(r"([A-Za-z])\.?", s)
        if lm and n_zones:
            zone = lm.group(1).upper()
            if "A" <= zone < chr(65 + n_zones):
                return {"action": "zoom", "zone": zone}
            return None
        m2 = re.search(r"\d+", s)
        if m2:
            val = int(m2.group(0))
            if 1 <= val <= n_marks:
                return {"action": "click", "mark": val}
        return None
