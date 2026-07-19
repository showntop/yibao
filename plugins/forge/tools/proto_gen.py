"""forge.proto_gen：交互原型 HTML 落盘到插件数据目录 prototypes/，系统浏览器打开预览。

文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
opener 可注入：测试传假 opener 断言被调，避免真弹浏览器。
"""
import os
import subprocess
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


class ProtoGen(Skill):
    id = "forge.proto_gen"
    description = "把写好的交互原型 HTML 落盘到需求名下并在浏览器打开预览；原型 HTML 生成完毕后调用"
    default_risk = RiskLevel.L2_MEDIUM

    def __init__(self, data_dir: str, opener):
        self._proto_dir = Path(data_dir) / "prototypes"
        self._opener = opener
        self.refresh = "forge.get"  # 写后详情面板拿刷新数据（加载器会校验它已注册）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "需求 id"},
                    "html": {"type": "string", "description": "完整原型 HTML（单文件，原样落盘）"},
                },
                "required": ["id", "html"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        rid = str(params.get("id", "")).strip()
        html = params.get("html")
        if not rid or html is None or not str(html).strip():
            return ActionResult(success=False, error="id 和 html 均不能为空")
        # 先查行：既给「需求不存在」明确报错，也保证拼文件名的 id 是库里真实 id（路径安全）
        rows = ctx.db.query("requirements", where={"id": rid})
        if not rows:
            return ActionResult(success=False, error=f"需求不存在：{rid}")
        self._proto_dir.mkdir(parents=True, exist_ok=True)
        path = self._proto_dir / f"{rid}.html"
        try:
            path.write_text(str(html), encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"写文件失败：{e}")
        ctx.db.update("requirements", rid, {"proto_path": str(path), "updated_at": int(time.time())})
        data = {"id": rid, "path": str(path)}
        try:
            self._opener(str(path))
        except Exception as e:  # 预览是锦上添花：原型已落盘，浏览器开不了不算失败
            data["preview_error"] = str(e)
        result = ActionResult(success=True, data=data)
        result.panel = "forge:detail"
        return result


def make_tools(ctx, opener=None):
    # 默认 macOS open 拉起浏览器；测试注入假 opener
    return [ProtoGen(os.path.dirname(ctx.db.path), opener or (lambda p: subprocess.Popen(["open", p])))]
