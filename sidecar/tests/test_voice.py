"""Plan 4a 语音：serve voice_start 端到端（FakeVoice + FakeProvider）。不碰真麦克风/sherpa/edge-tts。"""
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.server import build_loop, serve
from fakes import FakeVoice


class _TwoStep:
    def __init__(self, first, second):
        self._f, self._s, self._n = first, second, 0

    def chat(self, messages, tools=None):
        self._n += 1
        return self._f.chat(messages, tools) if self._n == 1 else self._s.chat(messages, tools)


def _reader(msgs):
    it = iter(msgs + [None])
    return lambda: next(it)


def test_serve_voice_start_speaks_final_reply(tmp_path):
    provider = _TwoStep(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="你好，我是译宝"),
    )
    loop = build_loop(_reader([]), use_real=False, db_path=str(tmp_path / "a.db"), provider=provider)
    voice = FakeVoice("你好")
    out = []
    serve(loop, _reader([{"id": 1, "type": "voice_start"}]), lambda m: out.append(m), voice=voice)

    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "listening" in kinds
    assert "listening_done" in kinds
    # listening_done 带识别出的文字
    assert any(
        m["type"] == "event" and m["event"]["kind"] == "listening_done" and m["event"]["text"] == "你好"
        for m in out
    )
    assert "final_reply" in kinds
    assert "speaking" in kinds  # final_reply 后触发
    assert voice.speak_calls == ["你好，我是译宝"]


def test_serve_voice_start_empty_text_skips_run(tmp_path):
    # STT 返空 → 不进 run，直接 run_done
    provider = _TwoStep(FakeProvider(text="x"), FakeProvider(text="y"))
    loop = build_loop(_reader([]), use_real=False, db_path=str(tmp_path / "a.db"), provider=provider)
    voice = FakeVoice("")
    out = []
    serve(loop, _reader([{"id": 1, "type": "voice_start"}]), lambda m: out.append(m), voice=voice)

    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "listening" in kinds
    assert "listening_done" in kinds
    assert "final_reply" not in kinds
    assert voice.speak_calls == []
