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


def test_open_app_not_blocked_by_user_input_lease():
    """open_app 只是启动/置前、不注入键鼠；即使用户正在操作也不该被租约拦死。"""
    host = FakeHost()
    host.a11y.launch_pid = 4321
    host.user_input.allowed = False
    host.user_input.reason = "检测到用户正在操作，AI 已让出控制"
    r = OpenAppSkill().run({"app": "Calculator"}, _ctx(host))
    assert r.success
    assert r.data == {"app": "Calculator", "pid": 4321}
    # 守卫压根没被调用——租约已不覆盖 open_app
    assert host.user_input.permit_calls == []
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


def test_click_control_yields_before_ax_press_when_user_is_active():
    host = FakeHost()
    h = _FakeHandle("AXButton", "等于")
    host.a11y.handles[("AXButton", "等于")] = h
    host.user_input.allowed = False
    host.user_input.reason = "检测到用户正在操作，AI 已让出控制"

    r = ClickControlSkill().run({"role": "AXButton", "title": "等于"}, _ctx(host))

    assert not r.success and "用户正在操作" in r.error
    assert host.a11y.press_calls == []


def test_click_control_no_blind_coord_and_hints_computer_use():
    # 不再盲点坐标：给了 x/y 但 a11y 查不到 → 失败 + 提示 computer_use
    r = ClickControlSkill().run({"x": 100, "y": 200}, _ctx(FakeHost()))
    assert not r.success
    assert "computer_use" in r.error


def test_click_control_ax_fail_still_coordless():
    host = FakeHost()
    r = ClickControlSkill().run({"role": "AXButton", "title": "不存在", "x": 5, "y": 6}, _ctx(host))
    assert not r.success and "computer_use" in r.error
    assert host.input.clicks == []  # 没走坐标点击


def test_click_control_ax_fail_then_no_coord_returns_error():
    # 给了 role/title 但查不到、又没给坐标 → 失败
    r = ClickControlSkill().run({"role": "AXButton", "title": "不存在"}, _ctx(FakeHost()))
    assert not r.success


def test_type_text_injects():
    host = FakeHost()
    r = TypeTextSkill().run({"text": "hello 你好"}, _ctx(host))
    assert r.success
    assert r.data["chars"] == len("hello 你好")
    assert host.input.types == ["hello 你好"]


def test_type_text_missing():
    r = TypeTextSkill().run({}, _ctx(FakeHost()))
    assert not r.success


def test_type_text_yields_when_user_is_active():
    host = FakeHost()
    host.user_input.allowed = False
    host.user_input.reason = "检测到用户正在操作，AI 已让出控制"

    r = TypeTextSkill().run({"text": "hello"}, _ctx(host))

    assert not r.success and "用户正在操作" in r.error
    assert host.input.types == []


def test_register_real_skills_order():
    reg = SkillRegistry()
    register_real_skills(reg)
    assert [s.id for s in reg.list()] == [
        "screenshot",
        "read_tree",
        "open_app",
        "click_control",
        "type_text",
        "watch_command",
        "watch_command_status",
        "cancel_watch_command",
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


def test_choose_action_parse_marked():
    from yibao_brain.llm import ComputerUseClient

    # 纯数字 → click + mark
    assert ComputerUseClient._parse_marked_action("第 3 号", 5) == {"action": "click", "mark": 3}
    # JSON 动作
    assert ComputerUseClient._parse_marked_action('{"action":"type","text":"hi"}', 5) == {"action": "type", "text": "hi"}
    assert ComputerUseClient._parse_marked_action('{"action":"click","mark":2}', 5) == {"action": "click", "mark": 2}
    # finish
    assert ComputerUseClient._parse_marked_action("做完了 finish", 5) == {"action": "finish"}
    # 越界 / 非法
    assert ComputerUseClient._parse_marked_action("第 9 号", 5) is None
    assert ComputerUseClient._parse_marked_action("乱码无数字", 5) is None


def test_computer_use_som_click_type_finish(tmp_path, monkeypatch):
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter, FakeHost, _FakeHandle

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 3))
    host.a11y.element_at_result = _FakeHandle("AXButton", "ok")  # 命中 → AX-press
    # tree 有一个 button 标记（mark 1）；网格补齐
    host.a11y.tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []}]}
    client = FakeComputerUseClient(marked_actions=[
        {"action": "click", "mark": 1},
        {"action": "type", "text": "hi"},
        {"action": "finish"},
    ])
    r = ComputerUseSkill(client, max_steps=3, som=SoMGrounding()).run(
        {"task": "t"}, SkillContext(host=host)
    )
    assert r.success and r.data["steps"] == 2
    assert host.a11y.press_calls and host.input.clicks == []  # mark1 走 AX
    assert host.input.types == ["hi"]
    assert len(client.choose_calls) == 3


def test_computer_use_som_coord_fallback_when_no_element(tmp_path, monkeypatch):
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 2))
    host.a11y.element_at_result = None  # 无 handle → 坐标回退
    host.a11y.tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [40, 40, 60, 60], "children": []}]}
    client = FakeComputerUseClient(marked_actions=[{"action": "click", "mark": 1}, {"action": "finish"}])
    ComputerUseSkill(client, som=SoMGrounding()).run({"task": "t"}, SkillContext(host=host))
    assert host.input.clicks == [(50.0, 50.0)]  # bbox 中心，逻辑坐标


def test_computer_use_raw_bbox_fallback_on_render_fail(tmp_path, monkeypatch):
    # build_marks 渲染失败（_render 返 None）但图可读 → 回退 next_action raw-bbox
    # 注意：不能用坏路径——_b64 也打不开图，next_action 永远不执行。故 monkeypatch _render。
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 2))  # 真实可读图
    host.a11y.tree = {"role": "AXApp", "children": []}
    som = SoMGrounding()
    monkeypatch.setattr(som, "_render", lambda *a, **k: None)  # 强制渲染失败
    client = FakeComputerUseClient(
        actions=[{"action": "click", "box": [10, 10, 30, 30]}, {"action": "finish"}])  # 走 raw-bbox
    r = ComputerUseSkill(client, som=som).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 1
    assert host.input.clicks == [(20.0, 20.0)]  # box 中心 / scale 1.0
    assert client.choose_calls == []  # 没走 SoM


def test_computer_use_prefers_native_bbox_for_capable_model(tmp_path, monkeypatch):
    """原生 grounding 更准的模型应直接走 bbox，不再叠 SoM。"""
    import pyautogui

    from yibao_brain.grounding import SoMGrounding
    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    shot = _make_shot(tmp_path, physical_w=198, physical_h=350)

    class WindowScreenshotter(FakeScreenshotter):
        def capture_window(self, app):
            self.calls.append(f"capture_window:{app}")
            return shot, (72.0, 354.0), 1.0

    host.screenshotter = WindowScreenshotter()
    som = SoMGrounding()
    monkeypatch.setattr(
        som,
        "build_marks",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应构建 SoM")),
    )
    client = FakeComputerUseClient(
        actions=[{"action": "click", "box": [148, 301, 188, 341]}],
    )
    client.prefers_raw_bbox = True
    host.a11y.element_at_result = _FakeHandle("AXButton", "等于")

    result = ComputerUseSkill(client, som=som).run(
        {"task": "点击等号", "app": "计算器"}, SkillContext(host=host)
    )

    assert result.success and result.data["steps"] == 1
    assert host.a11y.press_calls == [host.a11y.element_at_result]
    assert host.input.clicks == []
    assert host.screenshotter.calls == ["capture_window:计算器"]
    assert len(client.calls) == 1
    assert client.choose_calls == []


def test_computer_use_native_bbox_requires_target_app():
    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient

    client = FakeComputerUseClient(actions=[{"action": "click", "box": [1, 2, 3, 4]}])
    client.prefers_raw_bbox = True

    result = ComputerUseSkill(client).run({"task": "点击等号"}, SkillContext(host=FakeHost()))

    assert not result.success
    assert "目标应用" in result.error
    assert client.calls == []


def test_computer_use_defaults_to_one_step(tmp_path, monkeypatch):
    import pyautogui

    from yibao_brain.grounding import SoMGrounding
    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 3))
    host.a11y.tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []}]}
    client = FakeComputerUseClient(marked_actions=[{"action": "click", "mark": 1}] * 3)

    result = ComputerUseSkill(client, som=SoMGrounding()).run(
        {"task": "只点一次", "max_steps": 5}, SkillContext(host=host)
    )

    assert result.success and result.data["steps"] == 1
    assert len(client.choose_calls) == 1


def test_computer_use_cancel_after_model_response_prevents_click(tmp_path):
    import threading

    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient, FakeScreenshotter

    shot = _make_shot(tmp_path, physical_w=198, physical_h=350)
    cancel = threading.Event()

    class WindowScreenshotter(FakeScreenshotter):
        def capture_window(self, app):
            return shot, (72.0, 354.0), 1.0

    class CancelClient(FakeComputerUseClient):
        prefers_raw_bbox = True

        def next_action(self, screenshot_b64, task, history=None):
            cancel.set()
            return {"action": "click", "box": [148, 301, 188, 341]}

    host = FakeHost()
    host.screenshotter = WindowScreenshotter()

    result = ComputerUseSkill(CancelClient()).run(
        {"task": "点击等号", "app": "计算器"},
        SkillContext(host=host, meta={"cancel": cancel}),
    )

    assert not result.success and "中断" in result.error
    assert host.input.clicks == []


def test_computer_use_user_input_after_screenshot_preempts_click_and_run(tmp_path):
    import threading

    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient, FakeScreenshotter

    shot = _make_shot(tmp_path, physical_w=198, physical_h=350)
    preempted = threading.Event()

    class WindowScreenshotter(FakeScreenshotter):
        def capture_window(self, app):
            return shot, (72.0, 354.0), 1.0

    client = FakeComputerUseClient(
        actions=[{"action": "click", "box": [148, 301, 188, 341]}]
    )
    client.prefers_raw_bbox = True
    host = FakeHost()
    host.screenshotter = WindowScreenshotter()
    host.user_input.allowed = False
    host.user_input.reason = "检测到用户正在操作，AI 已让出控制"

    result = ComputerUseSkill(client).run(
        {"task": "点击等号", "app": "计算器"},
        SkillContext(host=host, meta={"request_cancel": preempted.set}),
    )

    assert not result.success and "用户正在操作" in result.error
    assert preempted.is_set()
    assert host.input.clicks == []
    assert len(host.user_input.checkpoints) == 1
    assert host.user_input.permit_calls == host.user_input.checkpoints


def test_computer_use_empty_dict_action_not_counted_as_step(tmp_path, monkeypatch):
    # raw-bbox 路径下，_parse_action 对 "{}" 模型输出返回 {} → 不应计为一步
    # （回归：旧守则 action.get("action") != "finish" 对 {} 为真 → 幻影 step）
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 2))  # 真实可读图
    host.a11y.tree = {"role": "AXApp", "children": []}
    som = SoMGrounding()
    monkeypatch.setattr(som, "_render", lambda *a, **k: None)  # 强制渲染失败 → raw-bbox
    client = FakeComputerUseClient(actions=[{}, {"action": "finish"}])  # 空 dict 后 finish
    r = ComputerUseSkill(client, som=som).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 0
    assert r.data["actions"] == []  # {} 未被记为步
    assert host.input.clicks == []  # 也没误执行
    assert client.choose_calls == []  # 没走 SoM


def test_computer_use_schema_default_tracks_step_cap():
    """放开多步批处理：schema 默认步数 = 构造上限；模型不传 max_steps 即按上限连续执行。"""
    from fakes import FakeComputerUseClient
    from yibao_brain.skills_real import ComputerUseSkill

    schema = ComputerUseSkill(FakeComputerUseClient(), max_steps=6).openai_schema()
    ms = schema["parameters"]["properties"]["max_steps"]
    assert ms["default"] == 6
    assert ms["maximum"] == 6


def test_computer_use_som_max_steps_cap(tmp_path, monkeypatch):
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 5))
    host.a11y.tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [0, 0, 2, 2], "children": []}]}
    client = FakeComputerUseClient(marked_actions=[{"action": "click", "mark": 1}] * 5)
    r = ComputerUseSkill(client, max_steps=3, som=SoMGrounding()).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 3


def test_computer_use_finish_stops_immediately(tmp_path, monkeypatch):
    import pyautogui

    from yibao_brain.skills_real import ComputerUseSkill
    from fakes import FakeComputerUseClient

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter.path = _make_shot(tmp_path)
    r = ComputerUseSkill(FakeComputerUseClient(marked_actions=[{"action": "finish"}])).run(
        {"task": "t"}, SkillContext(host=host)
    )
    assert r.success and r.data["steps"] == 0


def test_computer_use_none_action_stops(tmp_path, monkeypatch):
    # choose_action 返 None（模型输出非法）→ 立即停，不失控
    import pyautogui

    from yibao_brain.skills_real import ComputerUseSkill

    class _NoneClient:
        def choose_action(self, b, t, n, history=None, n_zones=0):
            return None

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter.path = _make_shot(tmp_path)
    r = ComputerUseSkill(_NoneClient()).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 0


def test_computer_use_missing_task():
    from yibao_brain.skills_real import ComputerUseSkill

    r = ComputerUseSkill(client=None).run({}, SkillContext(host=FakeHost()))
    assert not r.success


def test_computer_use_zoom_action_grounds_and_clicks(tmp_path, monkeypatch):
    """SoM 路径模型选字母区域 → zoom_ground 精化 → resolve_point 点击。"""
    from PIL import Image

    from yibao_brain import skills_real
    from yibao_brain.skills_real import ComputerUseSkill

    class FakeClient:
        prefers_raw_bbox = False

        def choose_action(self, marked, task, n_marks, history=None, n_zones=0):
            return {"action": "zoom", "zone": "A"}

    monkeypatch.setattr(skills_real, "zoom_ground", lambda client, shot, rect, scale, task: (50.0, 60.0))
    host = FakeHost()  # element_at 返回 None → 走坐标点击
    shot = tmp_path / "s.png"
    Image.new("RGB", (100, 80), "white").save(shot)
    host.screenshotter = type("S", (), {"capture": lambda self: str(shot)})()
    host.a11y.frontmost_tree = lambda: {"role": "AXApp", "children": []}
    ctx = SkillContext(host=host)
    skill = ComputerUseSkill(FakeClient())
    r = skill.run({"task": "点按钮", "app": "x"}, ctx)
    assert r.success, r.error
    assert host.input.clicks == [(50.0, 60.0)]


# ---------- watch_command：后台盯命令 ----------
def _wait_emit(events, timeout_s=2.0):
    import time

    for _ in range(int(timeout_s / 0.02)):
        if events:
            return
        time.sleep(0.02)


def test_watch_command_starts_and_emits_on_done(tmp_path):
    from yibao_brain.background_jobs import BackgroundJobManager
    from yibao_brain.skills_real import WatchCommandSkill

    events = []
    ctx = SkillContext()
    ctx.emit_event = events.append
    r = WatchCommandSkill(BackgroundJobManager()).run(
        {"command": "echo hello", "cwd": str(tmp_path)}, ctx
    )
    assert r.success and r.data["started"] == "echo hello"
    assert r.data["task_id"].startswith("job_")
    _wait_emit(events)
    assert events and events[0]["kind"] == "reminder"
    assert "完成" in events[0]["text"] and "hello" in events[0]["text"]


def test_watch_command_reports_failure_exit_code(tmp_path):
    from yibao_brain.background_jobs import BackgroundJobManager
    from yibao_brain.skills_real import WatchCommandSkill

    events = []
    ctx = SkillContext()
    ctx.emit_event = events.append
    WatchCommandSkill(BackgroundJobManager()).run(
        {"command": "sh -c 'exit 7'", "cwd": str(tmp_path)}, ctx
    )
    _wait_emit(events)
    assert events and "失败" in events[0]["text"] and "7" in events[0]["text"]


def test_watch_command_no_emit_does_not_crash(tmp_path):
    from yibao_brain.background_jobs import BackgroundJobManager
    from yibao_brain.skills_real import WatchCommandSkill

    r = WatchCommandSkill(BackgroundJobManager()).run(
        {"command": "echo hi", "cwd": str(tmp_path)}, SkillContext()
    )  # emit_event=None
    assert r.success


def test_watch_command_missing_command():
    from yibao_brain.background_jobs import BackgroundJobManager
    from yibao_brain.skills_real import WatchCommandSkill

    assert not WatchCommandSkill(BackgroundJobManager()).run({}, SkillContext()).success


def test_watch_command_requires_cwd():
    from yibao_brain.background_jobs import BackgroundJobManager
    from yibao_brain.skills_real import WatchCommandSkill

    assert not WatchCommandSkill(BackgroundJobManager()).run(
        {"command": "echo hi"}, SkillContext()
    ).success
