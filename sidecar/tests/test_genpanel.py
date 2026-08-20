"""gen 面板（LLM 生成 webview）：panel_gen/open/list/delete + 动态注册 + 落盘恢复。"""
import json

import pytest

from yibao_brain import genpanel, plugins
from yibao_brain.skills import SkillContext, SkillRegistry

GOOD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>天气看板</title>
<style>:root { --bg: #f0f4f8; } body { background: var(--bg); }</style>
</head>
<body><h1>天气</h1><script>console.log("hi");</script></body>
</html>"""

GOOD_HTML_V2 = GOOD_HTML.replace("<h1>天气</h1>", "<h1>天气 v2</h1>")

BAD_HTML_CDN = """<!DOCTYPE html>
<html><head><title>看板</title><script src="https://cdn.example.com/x.js"></script></head>
<body>hi</body></html>"""

BAD_HTML_FETCH = """<!DOCTYPE html>
<html><head><title>看板</title></head>
<body><script>fetch("https://api.example.com/data");</script></body></html>"""


class _FakeLlm:
    """队列式假 LLM：chat 依次弹出预设输出（剩最后一个时恒返它），记录全部 prompt。"""

    def __init__(self, *outputs: str):
        self._outputs = list(outputs)
        self.calls: list[str] = []

    def chat(self, prompt: str) -> str:
        self.calls.append(prompt)
        if len(self._outputs) > 1:
            return self._outputs.pop(0)
        return self._outputs[0] if self._outputs else ""


class _BoomLlm:
    def chat(self, prompt: str) -> str:
        raise RuntimeError("LLM 炸了")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """gen_panels 落盘指到 tmp（照 test_plugins.py 的 data_dir 范式）。"""
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture(autouse=True)
def _clean_gen_panels():
    """模块级 _PANELS 是全局态：每个测试后清掉 gen:* 条目，防互相污染。"""
    yield
    for ref in [r for r in plugins._PANELS if r.startswith("gen:")]:
        plugins.unregister_panel(ref)


def _ctx() -> SkillContext:
    return SkillContext()


def _gen(llm, name="weather", title="天气看板", purpose="展示一周天气", data=None):
    params = {"name": name, "title": title, "purpose": purpose}
    if data is not None:
        params["data"] = data
    return genpanel.PanelGenSkill(llm).run(params, _ctx())


# ---------- 注册口（plugins.register_panel/unregister_panel） ----------


def test_register_unregister_panel_roundtrip():
    plugins.register_panel("gen", "t1", "译宝 · 测试", "<html>x</html>")
    assert plugins.get_panel("gen:t1") == {"type": "webview", "html": "<html>x</html>"}
    assert plugins.get_panel_title("gen:t1") == "译宝 · 测试"
    plugins.unregister_panel("gen:t1")
    assert plugins.get_panel("gen:t1") is None
    assert plugins.get_panel_title("gen:t1") == "gen:t1"  # 退化为 ref
    plugins.unregister_panel("gen:t1")  # 不存在静默跳过


# ---------- panel_gen ----------


def test_gen_success_registers_panel_and_payload(data_dir):
    llm = _FakeLlm(GOOD_HTML)
    r = _gen(llm, data={"city": "上海"})

    assert r.success and r.panel == "gen:weather"
    assert r.data == {"city": "上海"}  # data 透传
    assert plugins.get_panel("gen:weather") == {"type": "webview", "html": GOOD_HTML}
    assert plugins.get_panel_title("gen:weather") == "译宝 · 天气看板"

    # panel 事件形状：webview 面板 {panel, title, schema: None, webview: {html}, data}
    payload = plugins.panel_payload(r)
    assert payload == {
        "panel": "gen:weather",
        "title": "译宝 · 天气看板",
        "schema": None,
        "webview": {"html": GOOD_HTML},
        "data": {"city": "上海"},
    }

    # 落盘：html + meta.json
    d = data_dir / "gen_panels"
    assert (d / "weather.html").read_text(encoding="utf-8") == GOOD_HTML
    meta = json.loads((d / "weather.meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "天气看板" and meta["purpose"] == "展示一周天气"
    assert isinstance(meta["created_at"], int) and meta["created_at"] > 0


def test_gen_prompt_carries_contract_and_oninit(data_dir):
    llm = _FakeLlm(GOOD_HTML)
    _gen(llm, data={"rows": [1]})
    prompt = llm.calls[0]
    assert "天气看板" in prompt and "展示一周天气" in prompt
    assert "禁止任何外链与网络请求" in prompt and "--bg: #f0f4f8" in prompt
    assert "yibao.onInit" in prompt  # 有 data 时教 onInit 接收

    llm2 = _FakeLlm(GOOD_HTML)
    _gen(llm2, name="w2")  # 无 data：不教 onInit
    assert "yibao.onInit" not in llm2.calls[0]


def test_gen_external_ref_rewritten_once(data_dir):
    llm = _FakeLlm(BAD_HTML_CDN, GOOD_HTML)
    r = _gen(llm)

    assert r.success and r.panel == "gen:weather"
    assert len(llm.calls) == 2
    assert "script 外链" in llm.calls[1]  # 重写 prompt 带了命中项
    assert plugins.get_panel("gen:weather")["html"] == GOOD_HTML


def test_gen_external_ref_twice_rejected(data_dir):
    llm = _FakeLlm(BAD_HTML_CDN, BAD_HTML_FETCH)
    r = _gen(llm)

    assert not r.success
    assert "已拒绝" in r.error and "不允许外链与网络请求" in r.error
    assert len(llm.calls) == 2  # 只重写一次
    assert plugins.get_panel("gen:weather") is None  # 不注册
    assert not (data_dir / "gen_panels" / "weather.html").exists()  # 不落盘


@pytest.mark.parametrize(
    "raw, slug",
    [
        ("weather", "weather"),
        ("Weather Board", "weather-board"),
        ("My--Panel__Test", "my-panel-test"),
        ("天气 看板", "panel"),  # 非字母数字全转掉 → 空 → panel
        ("  -Spaced-Out-  ", "spaced-out"),
        ("x" * 60, "x" * 40),  # 截 40
    ],
)
def test_gen_name_slugified(data_dir, raw, slug):
    r = _gen(_FakeLlm(GOOD_HTML), name=raw)
    assert r.success and r.panel == f"gen:{slug}"
    assert plugins.get_panel(f"gen:{slug}") is not None


def test_gen_without_data_defaults_empty(data_dir):
    r = _gen(_FakeLlm(GOOD_HTML))
    assert r.success and r.data == {}
    # 非 dict 的 data 不往 panel 事件里塞
    r2 = _gen(_FakeLlm(GOOD_HTML), name="w2", data=[1, 2])
    assert r2.success and r2.data == {}


def test_gen_llm_failure_modes(data_dir):
    assert not _gen(_BoomLlm()).success  # LLM 异常
    r = _gen(_FakeLlm(""))  # 返回空
    assert not r.success and plugins.get_panel("gen:weather") is None
    r2 = genpanel.PanelGenSkill(None).run(
        {"name": "w", "title": "t", "purpose": "p"}, _ctx())
    assert not r2.success and "LLM" in r2.error  # 底座无 LLM
    r3 = _gen(_FakeLlm(GOOD_HTML), title="", purpose="")  # 缺参数
    assert not r3.success and "title" in r3.error


def test_gen_unwraps_fence_and_prose(data_dir):
    fenced = "好的，这是面板：\n```html\n" + GOOD_HTML + "\n```\n希望你喜欢"
    r = _gen(_FakeLlm(fenced))
    assert r.success
    assert plugins.get_panel("gen:weather")["html"] == GOOD_HTML


def test_gen_same_name_overwrites(data_dir):
    assert _gen(_FakeLlm(GOOD_HTML)).success
    assert _gen(_FakeLlm(GOOD_HTML_V2)).success  # 同名再生成 = 覆盖（「改一下」场景）
    assert plugins.get_panel("gen:weather")["html"] == GOOD_HTML_V2
    assert (data_dir / "gen_panels" / "weather.html").read_text(encoding="utf-8") == GOOD_HTML_V2


# ---------- panel_open / panel_list / panel_delete ----------


def _gen_two(data_dir):
    assert _gen(_FakeLlm(GOOD_HTML), name="weather", title="天气看板", purpose="看天气").success
    assert _gen(_FakeLlm(GOOD_HTML), name="stocks", title="股价提醒", purpose="看股价").success


def test_open_list_delete_flow(data_dir):
    _gen_two(data_dir)

    lst = genpanel.PanelListSkill().run({}, _ctx())
    assert lst.success
    names = {p["name"] for p in lst.data["panels"]}
    assert names == {"weather", "stocks"}
    w = next(p for p in lst.data["panels"] if p["name"] == "weather")
    assert w["title"] == "天气看板" and w["purpose"] == "看天气" and w["created_at"] > 0

    # open 不存在：error 附现有面板清单
    r = genpanel.PanelOpenSkill().run({"name": "nope"}, _ctx())
    assert not r.success
    assert "weather" in r.error and "stocks" in r.error and "panel_list" in r.error

    # open 存在：从盘上读回注册并发 panel 事件
    plugins.unregister_panel("gen:weather")  # 模拟重启后未注册
    r = genpanel.PanelOpenSkill().run({"name": "weather"}, _ctx())
    assert r.success and r.panel == "gen:weather" and r.data == {}
    assert plugins.get_panel("gen:weather") == {"type": "webview", "html": GOOD_HTML}
    assert plugins.get_panel_title("gen:weather") == "译宝 · 天气看板"

    # delete：文件 + 注册一起清
    r = genpanel.PanelDeleteSkill().run({"name": "weather"}, _ctx())
    assert r.success
    assert plugins.get_panel("gen:weather") is None
    assert not (data_dir / "gen_panels" / "weather.html").exists()
    assert not (data_dir / "gen_panels" / "weather.meta.json").exists()
    assert {p["name"] for p in genpanel.PanelListSkill().run({}, _ctx()).data["panels"]} == {"stocks"}

    # delete 不存在 → error
    assert not genpanel.PanelDeleteSkill().run({"name": "weather"}, _ctx()).success


def test_list_empty(data_dir):
    r = genpanel.PanelListSkill().run({}, _ctx())
    assert r.success and r.data["panels"] == []


# ---------- 启动恢复 ----------


def test_load_saved_panels_restores_registrations(data_dir):
    _gen_two(data_dir)
    plugins.unregister_panel("gen:weather")
    plugins.unregister_panel("gen:stocks")
    assert plugins.get_panel("gen:weather") is None

    n = genpanel.load_saved_panels()
    assert n == 2
    assert plugins.get_panel("gen:weather") == {"type": "webview", "html": GOOD_HTML}
    assert plugins.get_panel_title("gen:weather") == "译宝 · 天气看板"
    assert plugins.get_panel_title("gen:stocks") == "译宝 · 股价提醒"


def test_load_saved_panels_missing_meta_falls_back_to_slug(data_dir, tmp_path):
    d = data_dir / "gen_panels"
    d.mkdir(parents=True)
    (d / "raw1.html").write_text(GOOD_HTML, encoding="utf-8")  # 无 meta.json

    assert genpanel.load_saved_panels() == 1
    assert plugins.get_panel("gen:raw1") is not None
    assert plugins.get_panel_title("gen:raw1") == "译宝 · raw1"  # meta 缺了用 slug 当 title


def test_load_saved_panels_no_dir(data_dir):
    assert genpanel.load_saved_panels() == 0


# ---------- 底座注册约束 ----------


def test_skills_register_as_base_skills_without_dots():
    reg = SkillRegistry()
    for sk in genpanel.make_skills(_FakeLlm(GOOD_HTML)):
        reg.register(sk)  # 底座 id 不带点号才过校验
    assert {s.id for s in reg.list()} == {"panel_gen", "panel_open", "panel_list", "panel_delete"}
