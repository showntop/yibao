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


# ---------- computer-use 兜底（Plan 3b）----------

def _size_obj(w, h):
    return type("S", (), {"width": w, "height": h})()


def _make_shot(tmp_path, physical_w=100, physical_h=100):
    from PIL import Image

    p = tmp_path / "shot.png"
    Image.new("RGB", (physical_w, physical_h), "white").save(p)
    return str(p)


def _make_shots(tmp_path, count, size=(100, 100)):
    """count 张内容不同的截图（避免无变化检测误触发）。"""
    from PIL import Image

    paths = []
    for i in range(count):
        p = tmp_path / f"shot{i}.png"
        Image.new("RGB", size, (i * 20 % 256, i * 30 % 256, i * 40 % 256)).save(p)
        paths.append(str(p))
    return paths


def test_computer_use_client_parse_action():
    from yibao_brain.llm import ComputerUseClient

    assert ComputerUseClient._parse_action('前缀 {"action":"click","box":[1,2,3,4]} 后缀') == {
        "action": "click",
        "box": [1, 2, 3, 4],
    }
    assert ComputerUseClient._parse_action("没有 JSON") is None
    assert ComputerUseClient._parse_action('{"action":broken') is None


def test_computer_use_loop_click_type_finish(tmp_path, monkeypatch):
    import pyautogui

    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 3))  # 每步不同截图
    client = FakeComputerUseClient(
        [
            {"action": "click", "box": [10, 10, 30, 30]},
            {"action": "type", "text": "hi"},
            {"action": "finish"},
        ]
    )
    r = ComputerUseSkill(client).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 2
    assert host.input.clicks == [(20.0, 20.0)]  # box 中心，scale=1.0
    assert host.input.types == ["hi"]
    assert len(client.calls) == 3


def test_computer_use_finish_stops_immediately(tmp_path, monkeypatch):
    import pyautogui

    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter.path = _make_shot(tmp_path)
    r = ComputerUseSkill(FakeComputerUseClient([{"action": "finish"}])).run(
        {"task": "t"}, SkillContext(host=host)
    )
    assert r.success and r.data["steps"] == 0


def test_computer_use_none_action_stops(tmp_path, monkeypatch):
    # client 返 None（模型输出非法）→ 立即停，不失控
    import pyautogui

    from yibao_brain.skills_real import ComputerUseSkill

    class _NoneClient:
        def next_action(self, b, t, history=None):
            return None

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter.path = _make_shot(tmp_path)
    r = ComputerUseSkill(_NoneClient()).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 0


def test_computer_use_hidpi_coordinate(tmp_path, monkeypatch):
    # 物理 200px / 逻辑 100px → scale 2.0；box 中心 20 → click 10
    import pyautogui

    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter.path = _make_shot(tmp_path, 200, 200)  # 物理宽 200
    client = FakeComputerUseClient(
        [{"action": "click", "box": [10, 10, 30, 30]}, {"action": "finish"}]
    )
    ComputerUseSkill(client).run({"task": "t"}, SkillContext(host=host))
    assert host.input.clicks == [(10.0, 10.0)]  # 20 / scale(2.0)


def test_computer_use_missing_task():
    from yibao_brain.skills_real import ComputerUseSkill

    r = ComputerUseSkill(client=None).run({}, SkillContext(host=FakeHost()))
    assert not r.success


def test_computer_use_max_steps_cap(tmp_path, monkeypatch):
    # 无 finish、每步不同截图 → 不触发无变化，靠 max_steps 截断
    import pyautogui

    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 5))
    client = FakeComputerUseClient([{"action": "click", "box": [0, 0, 2, 2]}] * 5)
    r = ComputerUseSkill(client, max_steps=3).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 3
