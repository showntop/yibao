"""Workspace / Session 正交切片：同一项目库按 conversation_id 独立绑定。"""
from yibao_brain.projects import ProjectStore
from yibao_brain.project_tools import make_project_tools
from yibao_brain.session_contexts import SessionContextStore
from yibao_brain.tools.core import ToolContext


def make_store(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain import config

    monkeypatch.setattr(config, "settings_path", lambda: str(tmp_path / "settings.json"))
    contexts = SessionContextStore(str(tmp_path / "session_contexts.json"))
    return ProjectStore(str(tmp_path / "projects.json"), session_contexts=contexts), contexts


def test_two_sessions_bind_independently_and_persist(tmp_path, monkeypatch):
    store, contexts = make_store(tmp_path, monkeypatch)
    video = store.create("Agent 视频", conversation_id="cv-video")
    deck = store.create("Agent PPT", conversation_id="cv-deck")

    assert store.current("cv-video")["id"] == video["id"]
    assert store.current("cv-deck")["id"] == deck["id"]
    assert store.current("cv-new") is None
    assert store.current_id() == ""  # scoped create 不污染遗留全局 setting

    reloaded = SessionContextStore(str(tmp_path / "session_contexts.json"))
    assert reloaded.workspace_id("cv-video") == video["id"]
    assert reloaded.workspace_id("cv-deck") == deck["id"]


def test_switching_one_session_does_not_move_another(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, monkeypatch)
    first = store.create("一号", conversation_id="cv-a")
    second = store.create("二号", conversation_id="cv-b")

    assert store.switch(second["id"], "cv-a")
    assert store.current_id("cv-a") == second["id"]
    assert store.current_id("cv-b") == second["id"]
    assert store.switch(first["id"], "cv-b")
    assert store.current_id("cv-a") == second["id"]
    assert store.current_id("cv-b") == first["id"]
    assert first["id"] != second["id"]


def test_agent_project_tools_read_conversation_scope(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, monkeypatch)
    tools = {tool.id: tool for tool in make_project_tools(store)}
    ctx_a = ToolContext(meta={"conversation_id": "cv-a"})
    ctx_b = ToolContext(meta={"conversation_id": "cv-b"})

    tools["project.create"].run({"name": "A 的工作语境"}, ctx_a)
    tools["project.create"].run({"name": "B 的工作语境"}, ctx_b)

    assert tools["project.current"].run({}, ctx_a).data["project"]["name"] == "A 的工作语境"
    assert tools["project.current"].run({}, ctx_b).data["project"]["name"] == "B 的工作语境"
