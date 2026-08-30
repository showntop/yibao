"""shell.first_seen：首启时刻幂等落盘（日题"已陪伴你 N 天"数据源）。"""

from yibao_brain import config


def test_ensure_first_seen_generates_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "settings_path", lambda: str(tmp_path / "settings.json"))
    settings = config.load_settings()
    assert settings["shell.first_seen"] == ""

    first = config.ensure_first_seen(settings)
    assert first  # 生成了
    assert settings["shell.first_seen"] == first

    # 落了盘
    reloaded = config.load_settings()
    assert reloaded["shell.first_seen"] == first

    # 再跑幂等：值不变，不重写
    again = config.ensure_first_seen(dict(reloaded))
    assert again == first


def test_ensure_first_seen_keeps_existing_value():
    settings = {"shell.first_seen": "2026-01-01T08:00:00"}
    assert config.ensure_first_seen(settings) == "2026-01-01T08:00:00"
