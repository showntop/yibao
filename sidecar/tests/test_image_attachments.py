"""附件图片 vision 描述注入（_describe_image_attachments）：译宝「看」粘贴截图/附件图。"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yibao_brain import background, llm  # noqa: E402
from yibao_brain.background import _describe_image_attachments  # noqa: E402


def _mk_file(tmp_path, name, content=b"\x89png-fake"):
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


def _fake_answer(monkeypatch, ret="一只猫", recorder=None):
    def _ans(client, b64, question):
        if recorder is not None:
            recorder.append(b64[:30])
        return ret
    monkeypatch.setattr(llm, "answer_image_query", _ans)


def test_none_client_returns_none(tmp_path):
    img = _mk_file(tmp_path, "a.png")
    assert _describe_image_attachments(f"【附件：{img}】", None) is None


def test_image_attachment_described(tmp_path, monkeypatch):
    img = _mk_file(tmp_path, "shot.png")
    _fake_answer(monkeypatch, "报错弹窗截图")
    out = _describe_image_attachments(f"看下这个【附件：{img}】", object())
    assert out is not None and "报错弹窗截图" in out and img in out


def test_non_image_and_missing_skipped(tmp_path, monkeypatch):
    txt = _mk_file(tmp_path, "a.txt")
    seen = []
    _fake_answer(monkeypatch, "x", seen)
    out = _describe_image_attachments(f"【附件：{txt}】【文件：{tmp_path}/nope.png】", object())
    assert out is None and seen == []   # 非图片 + 不存在：一次 vision 都不调


def test_multiple_images_and_cap(tmp_path, monkeypatch):
    paths = [_mk_file(tmp_path, f"{i}.png") for i in range(5)]
    seen = []
    _fake_answer(monkeypatch, "图", seen)
    text = "".join(f"【附件：{p}】" for p in paths)
    out = _describe_image_attachments(text, object())
    assert len(seen) == 3               # _IMG_MAX_COUNT 上限
    assert out is not None and out.count("：图") == 3


def test_oversize_skipped(tmp_path, monkeypatch):
    big = tmp_path / "big.png"
    big.write_bytes(b"0" * (8 * 1024 * 1024 + 1))
    seen = []
    _fake_answer(monkeypatch, "x", seen)
    assert _describe_image_attachments(f"【附件：{big}】", object()) is None and seen == []


def test_vision_failure_per_image_isolated(tmp_path, monkeypatch):
    good = _mk_file(tmp_path, "g.png")
    bad = _mk_file(tmp_path, "b.png")

    def _ans(client, b64, question):
        raise RuntimeError("vision down")
    monkeypatch.setattr(llm, "answer_image_query", _ans)
    assert _describe_image_attachments(f"【附件：{good}】【附件：{bad}】", object()) is None
