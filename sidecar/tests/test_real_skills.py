"""真实原子技能单测：用 FakeHost 注入，断言编排逻辑（不触真机 a11y/键鼠）。"""
from yibao_brain.skills import SkillContext, SkillRegistry
from yibao_brain.skills_real import (
    ClickControlSkill,
    OpenAppSkill,
    ReadTreeSkill,
    ScreenshotSkill,
    TypeTextSkill,
    register_real_skills,
)
from fakes import FakeHost, _FakeHandle


def _ctx(host: FakeHost) -> SkillContext:
    return SkillContext(host=host)


def test_screenshot_captures_and_sets_path():
    host = FakeHost()
    r = ScreenshotSkill().run({}, _ctx(host))
    assert r.success
    assert r.data["path"] == host.screenshotter.path
    assert r.screenshot_path == host.screenshotter.path
    assert host.screenshotter.calls == ["capture"]


def test_read_tree_returns_frontmost_tree():
    host = FakeHost()
    host.a11y.tree = {"role": "AXApp", "title": "Calculator", "children": []}
    r = ReadTreeSkill().run({"max_depth": 3}, _ctx(host))
    assert r.success
    assert r.data["tree"]["title"] == "Calculator"


def test_open_app_returns_pid():
    host = FakeHost()
    host.a11y.launch_pid = 4321
    r = OpenAppSkill().run({"app": "Calculator"}, _ctx(host))
    assert r.success
    assert r.data == {"app": "Calculator", "pid": 4321}
    assert host.a11y.launch_calls == ["Calculator"]


def test_open_app_missing_param():
    r = OpenAppSkill().run({}, _ctx(FakeHost()))
    assert not r.success
    assert "app" in r.error


def test_open_app_launch_fail():
    host = FakeHost()
    host.a11y.launch_pid = None
    r = OpenAppSkill().run({"app": "Ghost"}, _ctx(host))
    assert not r.success


def test_click_control_ax_press():
    host = FakeHost()
    h = _FakeHandle("AXButton", "等于")
    host.a11y.handles[("AXButton", "等于")] = h
    r = ClickControlSkill().run({"role": "AXButton", "title": "等于"}, _ctx(host))
    assert r.success and r.data["method"] == "ax"
    assert host.a11y.press_calls == [h]
    assert host.input.clicks == []  # 没走坐标回退


def test_click_control_coord_fallback():
    host = FakeHost()
    r = ClickControlSkill().run({"x": 100, "y": 200}, _ctx(host))
    assert r.success and r.data["method"] == "coord"
    assert host.input.clicks == [(100.0, 200.0)]


def test_click_control_ax_fail_then_no_coord_returns_error():
    # 给了 role/title 但查不到、又没给坐标 → 失败
    r = ClickControlSkill().run({"role": "AXButton", "title": "不存在"}, _ctx(FakeHost()))
    assert not r.success


def test_click_control_ax_fail_then_coord_fallback():
    # 给了 role/title 查不到，但同时给了坐标 → 回退坐标
    host = FakeHost()
    r = ClickControlSkill().run({"role": "AXButton", "title": "不存在", "x": 5, "y": 6}, _ctx(host))
    assert r.success and r.data["method"] == "coord"
    assert host.input.clicks == [(5.0, 6.0)]


def test_type_text_injects():
    host = FakeHost()
    r = TypeTextSkill().run({"text": "hello 你好"}, _ctx(host))
    assert r.success
    assert r.data["chars"] == len("hello 你好")
    assert host.input.types == ["hello 你好"]


def test_type_text_missing():
    r = TypeTextSkill().run({}, _ctx(FakeHost()))
    assert not r.success


def test_register_real_skills_order():
    reg = SkillRegistry()
    register_real_skills(reg)
    assert [s.id for s in reg.list()] == [
        "screenshot",
        "read_tree",
        "open_app",
        "click_control",
        "type_text",
    ]


def test_real_skills_declare_openai_params():
    # 模型要靠 parameters 才能正确调用；默认 schema 的 properties 是空的，真实技能必须覆盖
    reg = SkillRegistry()
    register_real_skills(reg)
    for skill in reg.list():
        schema = skill.openai_schema()
        assert schema["name"] == skill.id
        assert "parameters" in schema
        assert isinstance(schema["parameters"]["properties"], dict)


def test_real_skills_have_host_guard():
    # 无 host 时优雅失败，不抛异常
    for skill_cls in (ScreenshotSkill, ReadTreeSkill, OpenAppSkill, ClickControlSkill, TypeTextSkill):
        r = skill_cls().run({}, SkillContext(host=None))
        assert not r.success
