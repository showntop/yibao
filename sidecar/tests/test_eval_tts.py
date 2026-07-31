"""eval_tts 判定逻辑单测（真机合成不进 pytest，只测 measure/evaluate 纯逻辑）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import eval_tts  # noqa: E402


class _FakeSpeaker:
    def __init__(self, name, latencies):
        self.name = name
        self._latencies = list(latencies)

    async def _synth_pcm(self, text):
        await asyncio.sleep(self._latencies.pop(0))
        return b"\x00\x01" * 100


class _FailSpeaker:
    name = "edge"

    async def _synth_pcm(self, text):
        return None


def test_measure_provider_records_latency_and_ok():
    speaker = _FakeSpeaker("edge", [0.01, 0.01, 0.01])
    measured = asyncio.run(eval_tts.measure_provider(speaker, ["a", "b", "c"]))
    assert measured["provider"] == "edge"
    assert all(r["ok"] for r in measured["results"])
    assert all(r["latency_s"] >= 0.01 for r in measured["results"])


def test_measure_provider_marks_synth_failure():
    measured = asyncio.run(eval_tts.measure_provider(_FailSpeaker(), ["a"]))
    assert measured["results"][0]["ok"] is False


def test_evaluate_pass_within_limits():
    measured = {"provider": "edge", "results": [
        {"text": t, "ok": True, "latency_s": 0.5} for t in ("a", "b", "c")]}
    verdict = eval_tts.evaluate(measured)
    assert verdict["pass"] is True and verdict["failures"] == []


def test_evaluate_fails_on_synth_failure_slow_and_mem():
    measured = {"provider": "cosyvoice", "results": [
        {"text": "a", "ok": False, "latency_s": 0.1},
        {"text": "b", "ok": True, "latency_s": 9.9},
        {"text": "c", "ok": True, "latency_s": 0.1},
    ]}
    verdict = eval_tts.evaluate(measured, mem_bytes=3 * 1024**3)
    assert verdict["pass"] is False
    assert len(verdict["failures"]) == 3  # 合成失败 + 延迟超标 + 内存超标
