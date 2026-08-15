"""面板 vendor 占位注入（R3）：<!--inject:vendor/xxx.js--> → 插件 panel/vendor 文件内容内联。"""
from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import pytest

from yibao_brain.plugins import _inline_vendor, _load_panels, get_panel
from yibao_brain.skills import SkillRegistry

REPO_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"
CODING_DIR = REPO_PLUGINS_DIR / "coding"


# ---------- 测试素材 ----------


def _make_plugin(root: Path, vendor_files: dict, html: str) -> Path:
    """造一个最小插件目录：panel/chat.html + panel/vendor/*。"""
    child = root / "injplug"
    (child / "panel" / "vendor").mkdir(parents=True)
    (child / "panel" / "chat.html").write_text(html, encoding="utf-8")
    for name, content in vendor_files.items():
        (child / "panel" / "vendor" / name).write_text(content, encoding="utf-8")
    return child


# ---------- _inline_vendor 单测 ----------


def test_placeholder_replaced_with_real_vendor_files():
    """真 vendor 三库：占位被替换且内容含标志全局名（marked / DOMPurify / hljs）。"""
    html = (
        "<script><!--inject:vendor/marked.min.js--></script>"
        "<script><!--inject:vendor/dompurify.min.js--></script>"
        "<script><!--inject:vendor/highlight.min.js--></script>"
    )
    out = _inline_vendor(html, CODING_DIR)
    assert "<!--inject:" not in out
    assert 'g["marked"]' in out          # marked UMD 挂载全局 marked
    assert "DOMPurify" in out
    assert "var hljs=" in out            # cdn 构建挂载全局 hljs


def test_script_close_tag_escaped(tmp_path):
    """vendor 内容里字面 </script 一律转义为 <\\/script（防提前闭合宿主 script 标签）。"""
    child = _make_plugin(tmp_path, {"x.js": 'var s="</script>";\n'}, "<script><!--inject:vendor/x.js--></script>")
    out = _inline_vendor((child / "panel" / "chat.html").read_text(encoding="utf-8"), child)
    assert 'var s="<\\/script>";' in out
    assert out.count("</script>") == 1   # 仅剩 html 自己的闭合标签


def test_no_placeholder_passthrough(tmp_path):
    """无占位符的 html 原样透传。"""
    child = _make_plugin(tmp_path, {}, "<html><body>hi</body></html>")
    html = (child / "panel" / "chat.html").read_text(encoding="utf-8")
    assert _inline_vendor(html, child) == html


def test_missing_vendor_file_keeps_placeholder(tmp_path, caplog):
    """vendor 文件缺失：保留占位注释 + 告警，不抛异常。"""
    child = _make_plugin(tmp_path, {}, "<script><!--inject:vendor/ghost.js--></script>")
    with caplog.at_level(logging.WARNING, logger="yibao_brain.plugins"):
        out = _inline_vendor("<script><!--inject:vendor/ghost.js--></script>", child)
    assert out == "<script><!--inject:vendor/ghost.js--></script>"
    assert "vendor/ghost.js" in caplog.text


def test_path_traversal_not_matched(tmp_path):
    """占位文件名白名单字符（无 /）：../ 之类路径穿越写法不匹配、原样保留。"""
    child = _make_plugin(tmp_path, {}, "x")
    html = "<!--inject:vendor/../secret.js-->"
    assert _inline_vendor(html, child) == html


# ---------- _load_panels 集成 ----------


def test_load_panels_inlines_vendor(tmp_path):
    """webview 面板加载时占位内联生效（走 _load_panels 真路径）。"""
    child = _make_plugin(
        tmp_path,
        {"lib.js": "var libLoaded=true;"},
        "<html><script><!--inject:vendor/lib.js--></script></html>",
    )
    manifest = {"name": "注入测试", "panel": [{"type": "webview", "name": "main", "src": "panel/chat.html"}]}
    _load_panels(child, "inj", manifest, SkillRegistry())
    html = get_panel("inj:main")["html"]
    assert "var libLoaded=true;" in html and "<!--inject:" not in html


@pytest.mark.parametrize("pid,panel_ref,src", [
    ("toolbox", "toolbox:main", "panel/tools.html"),
    ("zimeiti", "zimeiti:editor", "panel/editor.html"),
])
def test_repo_webview_panels_passthrough(pid, panel_ref, src):
    """回归：仓内无占位符的 webview 面板（tools.html / editor.html）加载后 html 与磁盘原文逐字节一致。"""
    child = REPO_PLUGINS_DIR / pid
    manifest = tomllib.loads((child / "manifest.toml").read_text(encoding="utf-8"))
    _load_panels(child, pid, manifest, SkillRegistry())
    assert get_panel(panel_ref)["html"] == (child / src).read_text(encoding="utf-8")
