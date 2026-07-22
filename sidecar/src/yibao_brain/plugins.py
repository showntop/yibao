"""插件加载器（v2 方案 §3）：扫描 plugins_dir → 校验 manifest → 建表 → 按 capability 注入 ctx → 注册 tool。

- 单插件失败隔离：任何异常 try 住记入返回 dict，继续下一个，不拖垮底座；
- `_` 开头的目录跳过（`_staging/` 暂存区）；
- 声明式 tool 四类型：db / http / prompt / composite，免代码；
- 代码插件（[code] entry）做最小支持：importlib 加载，模块提供 make_tools(ctx)。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .ipc import ActionResult, RiskLevel
from .plugindb import PluginDb
from .skills import Skill, SkillContext, SkillRegistry

# 合法 capability 集合（v2 §3.3）；host 不由加载器注入（invoker 执行时嫁接）
CAPABILITIES = {"db", "memory", "http", "llm", "host"}
# 声明式 tool 类型 → 所需 capability（加载期校验，manifest 未声明即加载失败）
TOOL_TYPE_CAPABILITY = {"db": "db", "http": "http", "prompt": "llm"}

_RISK = {
    "L0": RiskLevel.L0_READONLY,
    "L1": RiskLevel.L1_LOW,
    "L2": RiskLevel.L2_MEDIUM,
    "L3": RiskLevel.L3_HIGH,
    "L4": RiskLevel.L4_CRITICAL,
}

_PLUGIN_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TEMPLATE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


# ---------- 适配器 ----------


class HttpClient:
    """标准库 urllib 极简 http 客户端：get/post → 解析后的 json（非 json 返回原文）。10s 超时。"""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def get(self, url: str, **kw):
        return self._request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self._request("POST", url, **kw)

    def _request(self, method: str, url: str, json_body=None, headers=None, **_):
        hdrs = {"Accept": "application/json", **(headers or {})}
        data = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"http 请求失败：{method} {url}：{e}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body


class LlmChat:
    """把主 LLM provider 包成插件可用的单轮 chat(prompt) -> str。"""

    def __init__(self, provider):
        self._provider = provider

    def chat(self, prompt: str) -> str:
        return self._provider.chat([{"role": "user", "content": prompt}]).text


class ScopedMemory:
    """记忆命名空间隔离（v2 §3.3）：user_id 统一加「<namespace>:」前缀。"""

    def __init__(self, memory, namespace: str):
        self._memory = memory
        self._ns = namespace

    def _uid(self, user_id: str) -> str:
        return f"{self._ns}:{user_id}"

    def add(self, text: str, user_id: str) -> None:
        self._memory.add(text, self._uid(user_id))

    def recall(self, query: str, user_id: str) -> list[str]:
        return self._memory.recall(query, self._uid(user_id))


# ---------- 模板渲染 ----------


def _render(template: str, lookup) -> str:
    """{{key}} 简单替换；查不到的键渲染为空串。"""
    return _TEMPLATE.sub(lambda m: "" if (v := lookup(m.group(1))) is None else str(v), template)


def _render_params(obj, render):
    """递归渲染 params 里所有字符串值（composite 步骤参数）。"""
    if isinstance(obj, str):
        return render(obj)
    if isinstance(obj, dict):
        return {k: _render_params(v, render) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_render_params(v, render) for v in obj]
    return obj


# ---------- 声明式 tool ----------


class DeclarativeTool(Skill):
    """manifest [[tool]] 声明的免代码 tool：按 type 分发到 db/http/llm/composite 执行。"""

    def __init__(self, plugin_id: str, spec: dict, registry: SkillRegistry):
        tid = spec["id"]
        self.id = tid if tid.startswith(f"{plugin_id}.") else f"{plugin_id}.{tid}"
        self.description = spec.get("description", "")
        risk = str(spec.get("risk", "L1")).upper()
        if risk not in _RISK:
            raise ValueError(f"tool {self.id!r} 非法 risk：{risk!r}（L0~L4）")
        self.default_risk = _RISK[risk]
        self._type = spec.get("type", "db")
        self._spec = spec
        self._params_schema = spec.get("params") or {}
        self._required = list(spec.get("required") or [])
        self._panel_ref = spec.get("panel")  # 可选：执行成功时在结果上带面板引用
        # 可选 refresh：写操作（data 是回执非展示数据）成功后跟一次本插件只读 tool，
        # 面板事件拿刷新数据。短名自动补插件前缀；点号形式必须本插件前缀（防跨插件读）。
        refresh = spec.get("refresh")
        if refresh is not None:
            refresh = str(refresh)
            if "." in refresh:
                if not refresh.startswith(f"{plugin_id}."):
                    raise ValueError(f"refresh 必须指向本插件 tool：{refresh!r}")
            else:
                refresh = f"{plugin_id}.{refresh}"
        self.refresh = refresh
        self._registry = registry  # composite 顺序调用同 registry 的其他 tool

    def openai_schema(self) -> dict:
        """用 manifest 的 description 和 [tool.params]（required 列出必填参数）。

        声明了 panel 的 tool 在描述尾部追加「会打开面板」提示：模型不知道面板存在，
        不告诉它，「打开看板」这类请求它只会用文字列数据、不调工具。
        """
        desc = self.description
        if self._panel_ref:
            desc += f"（调用成功会在屏幕面板窗打开「{get_panel_title(self._panel_ref)}」）"
        return {
            "name": self.id,
            "description": desc,
            "parameters": {"type": "object", "properties": self._params_schema, "required": self._required},
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        handler = {
            "db": self._run_db,
            "http": self._run_http,
            "prompt": self._run_prompt,
            "composite": self._run_composite,
        }.get(self._type)
        if handler is None:
            return ActionResult(success=False, error=f"未知 tool 类型：{self._type!r}")
        result = handler(params, ctx)
        if result.success and self._panel_ref:  # 失败不放 panel 引用
            result.panel = self._panel_ref
        return result

    def _run_db(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.db is None:
            return ActionResult(success=False, error="未声明 db capability")
        spec = self._spec.get("db") or {}
        op, table = spec.get("op", "insert"), spec["table"]
        if op == "insert":
            row = dict(params)
            # auto 声明系统生成字段（当前仅 unixts = unix 秒）；覆盖入参防伪造
            for field_name, kind in (spec.get("auto") or {}).items():
                if kind != "unixts":
                    return ActionResult(success=False, error=f"未知 auto 类型：{kind!r}（仅支持 unixts）")
                row[field_name] = int(time.time())
            return ActionResult(success=True, data={"id": ctx.db.insert(table, row)})
        if op == "query":
            # where/order/limit 运行时参数优先，缺省回落 [tool.db] 里声明的默认值
            where = params.get("where", spec.get("where"))
            if where is None and params.get("id") is not None:
                # 面板 action 扁平传 {id: …} 的快捷映射（嵌套 where 过不了前端绑定解析）
                where = {"id": params["id"]}
            rows = ctx.db.query(
                table,
                where=where,
                order=params.get("order", spec.get("order")),
                limit=params.get("limit", spec.get("limit")),
            )
            return ActionResult(success=True, data={"rows": rows})
        if op == "update":
            row_id = params.get("id")
            fields = {k: v for k, v in params.items() if k != "id"}
            # auto 声明系统生成字段（同 insert；覆盖入参防伪造）
            for field_name, kind in (spec.get("auto") or {}).items():
                if kind != "unixts":
                    return ActionResult(success=False, error=f"未知 auto 类型：{kind!r}（仅支持 unixts）")
                fields[field_name] = int(time.time())
            ctx.db.update(table, row_id, fields)
            return ActionResult(success=True, data={"id": row_id})
        if op == "delete":
            ctx.db.delete(table, params.get("id"))
            return ActionResult(success=True, data={"id": params.get("id")})
        return ActionResult(success=False, error=f"未知 db op：{op!r}")

    def _run_http(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.http is None:
            return ActionResult(success=False, error="未声明 http capability")
        spec = self._spec.get("http") or {}
        method = str(spec.get("method", "GET")).upper()
        url = _render(spec["url"], params.get)  # url 支持 {{param}} 模板
        if method == "GET":
            resp = ctx.http.get(url)
        elif method == "POST":
            resp = ctx.http.post(url, json_body=params)
        else:
            return ActionResult(success=False, error=f"不支持的 http method：{method!r}")
        return ActionResult(success=True, data=resp if isinstance(resp, dict) else {"response": resp})

    def _run_prompt(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.llm is None:
            return ActionResult(success=False, error="未声明 llm capability")
        template = (self._spec.get("prompt") or {}).get("template", "")
        prompt = _render(template, params.get)
        return ActionResult(success=True, data={"text": ctx.llm.chat(prompt)})

    def _run_composite(self, params: dict, ctx: SkillContext) -> ActionResult:
        """顺序调同 registry 的其他 tool（直接 run 不过闸门——编排本身已过了一次闸）。

        params 模板支持 {{input.x}}（本 tool 入参）与 {{steps.N.data}}（前序步骤返回 data 的 json）。
        """
        steps = (self._spec.get("composite") or {}).get("steps", [])
        results: list[ActionResult] = []

        def lookup(path: str):
            parts = path.split(".")
            if parts[0] == "input" and len(parts) == 2:
                return params.get(parts[1])
            if parts[0] == "steps" and len(parts) == 3 and parts[2] == "data":
                try:
                    return json.dumps(results[int(parts[1])].data, ensure_ascii=False)
                except (ValueError, IndexError):
                    return None
            return None

        for i, step in enumerate(steps):
            name = step.get("tool", "?")
            try:
                sub = self._registry.get(name)
                rendered = _render_params(step.get("params") or {}, lambda t: _render(t, lookup))
                res = sub.run(rendered, sub.plugin_ctx or ctx)
            except Exception as e:
                return ActionResult(success=False, error=f"composite 第 {i} 步（{name}）异常：{e}")
            if not res.success:  # 任一步失败即停
                return ActionResult(success=False, error=f"composite 第 {i} 步（{name}）失败：{res.error}")
            results.append(res)
        return ActionResult(success=True, data=results[-1].data if results else {})


# ---------- panel schema 注册表（⑤a） ----------

_PANELS: dict[str, dict] = {}
_PANEL_TITLES: dict[str, str] = {}
_PLUGIN_INFO: dict[str, dict] = {}


def get_plugin_summaries() -> dict[str, dict]:
    """已加载插件摘要：pid → {name, description}（use_plugin 路由暴露用）。"""
    return {pid: dict(info) for pid, info in _PLUGIN_INFO.items()}


def get_panel(ref: str) -> dict | None:
    """按「plugin_id:name」查面板。schema 面板为 JSON dict；webview 面板为 {"type": "webview", "html": …}。"""
    return _PANELS.get(ref)


def get_panel_title(ref: str) -> str:
    """面板显示名（插件名 · 面板 label）；未加载时退化为 ref。"""
    return _PANEL_TITLES.get(ref, ref)


def panel_payload(result) -> dict | None:
    """result.panel 非空时构造 panel 事件 payload（loop 与 panel_action 共用）。

    schema 面板：{panel, title, schema, data}；webview 面板：{panel, title, schema: None, webview: {html}, data}
    （html 随事件发出，前端 iframe srcdoc 渲染；schema 面板 payload 其余形状保持不变）。
    """
    if not result.panel:
        return None
    title = get_panel_title(result.panel)
    panel = get_panel(result.panel)
    if isinstance(panel, dict) and panel.get("type") == "webview" and "html" in panel:
        return {"panel": result.panel, "title": title, "schema": None, "webview": {"html": panel["html"]}, "data": result.data}
    return {"panel": result.panel, "title": title, "schema": panel, "data": result.data}


def _load_panels(child: Path, pid: str, manifest: dict) -> None:
    """解析 manifest [[panel]]：schema 读 JSON、webview 读 HTML 文本存注册表；未知类型记错误跳过。
    显示名 = 插件 name · 面板 label（label 缺省用面板 name）。"""
    for p in manifest.get("panel") or []:
        name = p.get("name") or "main"
        ref = f"{pid}:{name}"
        ptype = p.get("type", "schema")
        if ptype not in ("schema", "webview"):
            print(f"[yibao] 插件 {pid} panel {ref} 类型 {ptype!r} 暂不支持（已跳过）", file=sys.stderr)
            continue
        _PANEL_TITLES[ref] = f"{manifest.get('name') or pid} · {p.get('label') or name}"
        try:
            text = (child / p["src"]).read_text(encoding="utf-8")
            _PANELS[ref] = {"type": "webview", "html": text} if ptype == "webview" else json.loads(text)
        except Exception as e:
            print(f"[yibao] 插件 {pid} panel {ref} 读取失败（已跳过）：{e}", file=sys.stderr)


# ---------- api.toml：面板可调方法白名单（⑦py） ----------


@dataclass(frozen=True)
class ApiMethod:
    """api.toml [[method]] 条目。direct=true 直调 handler（过闸门）；false 走 intent → agent。"""

    name: str            # 全局名（强制 <plugin_id>. 前缀）
    handler: str         # 本插件已注册 tool 的 id
    direct: bool
    intent: str | None
    risk: RiskLevel | None  # 非空时直调风险取 max(tool, api)——api.toml 只许收紧
    plugin_id: str
    refresh: str | None = None  # 直调成功后跟一次查询 tool，面板拿刷新数据而非操作回执
    panel: str | None = None    # 直调成功后改用该面板发 panel 事件（覆盖 tool 自带引用，如 webview 编辑器）


_API: dict[str, ApiMethod] = {}
_API_EVENTS: dict[str, list[str]] = {}


def get_api(method: str) -> ApiMethod | None:
    """查面板可调方法白名单；查不到返回 None（调用方拒绝执行）。"""
    return _API.get(method)


def get_plugin_events(plugin_id: str) -> list[str]:
    """api.toml [[event]] 声明的可订阅事件（推送通道后续做，先解析存着）。"""
    return list(_API_EVENTS.get(plugin_id, []))


def _load_api(pid: str, path: Path, registry: SkillRegistry) -> None:
    """解析 api.toml。单个 method 无效（handler 未注册/跨插件、risk 非法）记错误跳过，不拖垮其他 method。"""
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    for m in doc.get("method") or []:
        name = str(m.get("name", "?"))
        try:
            full = name if name.startswith(f"{pid}.") else f"{pid}.{name}"
            handler = str(m["handler"])
            if not handler.startswith(f"{pid}."):
                raise ValueError(f"handler 必须指向本插件 tool：{handler!r}")
            registry.get(handler)  # handler 必须指向本插件已注册的 tool
            risk = _RISK[str(m["risk"]).upper()] if m.get("risk") is not None else None
            refresh = None
            if m.get("refresh") is not None:
                refresh = str(m["refresh"])
                if not refresh.startswith(f"{pid}."):
                    raise ValueError(f"refresh 必须指向本插件 tool：{refresh!r}")
                registry.get(refresh)  # 必须已注册
            panel = None
            if m.get("panel") is not None:
                panel = str(m["panel"])
                if not panel.startswith(f"{pid}:"):
                    raise ValueError(f"panel 必须指向本插件面板：{panel!r}")
                if panel not in _PANELS:
                    raise ValueError(f"panel 指向未声明的面板：{panel!r}")
        except (KeyError, ValueError) as e:
            print(f"[yibao] 插件 {pid} api method {name!r} 无效（已跳过）：{e}", file=sys.stderr)
            continue
        _API[full] = ApiMethod(
            name=full, handler=handler,
            direct=bool(m.get("direct", False)), intent=m.get("intent"),
            risk=risk, plugin_id=pid, refresh=refresh, panel=panel,
        )
    _API_EVENTS[pid] = [str(e["name"]) for e in doc.get("event") or [] if e.get("name")]


# ---------- 加载器 ----------


def load_plugins(
    plugins_dir,
    registry: SkillRegistry,
    *,
    memory,
    http,
    llm,
    emit_panel=None,
    host_available: bool = True,
) -> dict[str, str]:
    """扫描加载所有插件，返回 {插件标识: "ok" 或错误信息}（失败插件的标识为目录名）。"""
    results: dict[str, str] = {}
    root = Path(plugins_dir)
    if not root.is_dir():
        return results
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):  # _staging/ 暂存区跳过
            continue
        if not (child / "manifest.toml").is_file():
            continue
        try:
            pid = _load_one(child, registry, memory=memory, http=http, llm=llm,
                            emit_panel=emit_panel, host_available=host_available)
            results[pid] = "ok"
        except Exception as e:  # 失败隔离：坏插件不拖累其他插件/底座
            results[child.name] = f"{type(e).__name__}: {e}"
    return results


def _load_one(child: Path, registry: SkillRegistry, *, memory, http, llm, emit_panel, host_available) -> str:
    manifest = tomllib.loads((child / "manifest.toml").read_text(encoding="utf-8"))
    pid = manifest["id"]  # id 必填；min_engine_version 只解析暂不校验（阶段 0）
    if not _PLUGIN_ID.match(pid):
        raise ValueError(f"非法插件 id：{pid!r}")
    # 插件摘要（use_plugin 路由暴露：LLM 靠它知道有哪些插件可展开）
    _PLUGIN_INFO[pid] = {
        "name": manifest.get("name") or pid,
        "description": manifest.get("description") or "",
    }

    caps = set(manifest.get("capabilities") or [])
    unknown = caps - CAPABILITIES
    if unknown:
        raise ValueError(f"未知 capabilities：{sorted(unknown)}")
    if "host" in caps and not host_available:
        raise ValueError("插件声明了 host capability，但底座无可用 host")
    tables = manifest.get("table") or []
    if tables and "db" not in caps:
        raise ValueError("声明了 [[table]] 但未声明 db capability")

    # 按 capabilities 构造 scoped ctx：未声明的能力对应属性保持 None
    ctx = SkillContext(emit_panel=emit_panel)
    if "db" in caps:
        ctx.db = PluginDb(pid)
        ctx.db.apply_schema(tables)
    if "memory" in caps:
        ctx.memory = ScopedMemory(memory, manifest.get("mem_namespace") or pid)
    if "http" in caps:
        ctx.http = http
    if "llm" in caps:
        ctx.llm = llm

    # 先收齐全部 tool，最后统一注册：任何一步失败都不留半成品
    skills: list[Skill] = []
    for spec in manifest.get("tool") or []:
        ttype = spec.get("type", "db")
        if ttype not in ("db", "http", "prompt", "composite"):
            raise ValueError(f"未知 tool 类型：{ttype!r}")
        need = TOOL_TYPE_CAPABILITY.get(ttype)
        if need and need not in caps:
            raise ValueError(f"tool {spec.get('id')!r} 类型 {ttype!r} 需要 capability「{need}」（manifest 未声明）")
        skills.append(DeclarativeTool(pid, spec, registry))
    skills.extend(_load_code_tools(child, manifest, ctx))

    for skill in skills:
        skill.plugin_ctx = ctx
        skill.plugin_capabilities = frozenset(caps)
        registry.register(skill, plugin=pid)  # 命名空间/重复 id 由 registry 强制
    # refresh 目标必须已注册（全部 tool 注册完统一校验，声明顺序随意）
    registered_ids = {s.id for s in registry.list()}
    for skill in skills:
        if skill.refresh is not None and skill.refresh not in registered_ids:
            raise ValueError(f"refresh 指向未注册的 tool：{skill.refresh!r}")
    _load_panels(child, pid, manifest)
    api_file = child / "api.toml"  # 面板可调方法白名单（可选）
    if api_file.is_file():
        _load_api(pid, api_file, registry)
    return pid


def _load_code_tools(child: Path, manifest: dict, ctx: SkillContext) -> list[Skill]:
    """代码插件最小支持：[code] entry = "tools" → 目录下每个 .py 提供 make_tools(ctx)。"""
    entry = (manifest.get("code") or {}).get("entry")
    if not entry:
        return []
    tools_dir = child / entry
    if not tools_dir.is_dir():
        raise ValueError(f"[code] entry 目录不存在：{entry!r}")
    out: list[Skill] = []
    for py in sorted(tools_dir.glob("*.py")):
        if py.name.startswith("_"):
            continue
        mod = _import_file(py)
        make_tools = getattr(mod, "make_tools", None)
        if not callable(make_tools):
            raise ValueError(f"{entry}/{py.name} 缺少 make_tools(ctx)")
        out.extend(make_tools(ctx))
    return out


def _import_file(path: Path):
    """按文件路径 import 插件模块（模块名加插件目录前缀防撞名）。"""
    name = f"yibao_plugin_{path.parent.parent.name}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
