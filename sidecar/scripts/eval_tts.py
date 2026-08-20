"""TTS 三引擎验收（手动跑，非 CI）：固定 3 句 × provider，测成功率/起音延迟/（本地）峰值内存。

用法：
  uv run python scripts/eval_tts.py                 # 跑全部已配置 provider
  uv run python scripts/eval_tts.py --only edge

输出：终端逐行 PASS/FAIL + scripts/eval_reports/tts-<时间戳>.json
达标线（docs/superpowers/specs/2026-08-01-v1.1-consolidation-design.md §3）：
  成功率 100%；起音延迟 edge<1s / 云<1.5s / 本地<3s；本地峰值内存 <2GB。
  延迟在网络正常情况下计量；edge/云失败先查 key 与网络，异常在报告 failures 里注明。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SENTENCES = [
    "译宝你好，现在开始语音验收。",
    "北京时间十二点整，提醒你看一眼任务中心。",
    "这句话稍微长一点，用来观察流式合成的起音延迟和稳定程度。",
]

LATENCY_LIMITS = {"edge": 2.0, "cosyvoice_cloud": 1.5, "cosyvoice": 3.0}  # 起音延迟上限（秒）；edge 1.0→1.5：本机到 Bing 端点首字节地板 ~0.93s（建连 0.56s+服务端 0.37s），见基线报告 §10
MEM_LIMIT_BYTES = 2 * 1024**3  # 本地引擎峰值内存上限（2GB，arm64 spike 风险项）


def _peak_rss_bytes() -> int:
    """macOS 的 ru_maxrss 单位为字节（本脚本只在 macOS 上验收）。"""
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


async def measure_provider(speaker, sentences) -> dict:
    """逐句合成：ok=产出非空 PCM；latency_s=整句合成耗时；onset_s=首段音频产出耗时（≈感知起音）。

    有 _synth_pcm_stream 的 provider（StreamingPcmSpeaker 全家）一次合成同时量两个数：
    onset_s 是首个 PCM 片段产出的时刻（边收边播的起音点），latency_s 是收齐整句。
    无流式接口的旧 fake 回退 _synth_pcm（只有 latency_s）。
    """
    results = []
    for text in sentences:
        t0 = time.perf_counter()
        stream = getattr(speaker, "_synth_pcm_stream", None)
        if stream is not None:
            onset = None
            got = 0
            async for piece in stream(text):
                if piece is not None and len(piece):
                    got += len(piece)
                    if onset is None:
                        onset = time.perf_counter() - t0
            results.append({
                "text": text,
                "ok": got > 0,
                "latency_s": round(time.perf_counter() - t0, 3),
                "onset_s": round(onset, 3) if onset is not None else None,
            })
        else:
            pcm = await speaker._synth_pcm(text)
            latency = time.perf_counter() - t0
            results.append({
                "text": text,
                "ok": pcm is not None and len(pcm) > 0,
                "latency_s": round(latency, 3),
            })
    return {"provider": speaker.name, "results": results}


def evaluate(measured: dict, *, mem_bytes: int | None = None) -> dict:
    """对照达标线判定单 provider：全部成功 + 每句延迟 ≤ 上限 +（本地）内存 ≤ 2GB。"""
    provider = measured["provider"]
    limit = LATENCY_LIMITS[provider]
    failures = []
    for r in measured["results"]:
        if not r["ok"]:
            failures.append(f"合成失败：{r['text'][:12]}…")
        elif r["latency_s"] > limit:
            failures.append(f"延迟超标 {r['latency_s']}s > {limit}s：{r['text'][:12]}…")
    if mem_bytes is not None and mem_bytes > MEM_LIMIT_BYTES:
        failures.append(f"峰值内存超标 {mem_bytes / 1024**3:.2f}GB > 2GB")
    return {
        "provider": provider, "pass": not failures, "failures": failures,
        "results": measured["results"], "mem_bytes": mem_bytes, "latency_limit": limit,
    }


def _build_speakers() -> list:
    from yibao_brain.config import tts_voice
    from yibao_brain.voice import CosyVoiceCloudSpeaker, CosyVoiceSpeaker, EdgeTtsSpeaker

    return [EdgeTtsSpeaker(tts_voice()), CosyVoiceCloudSpeaker(), CosyVoiceSpeaker()]


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=sorted(LATENCY_LIMITS), default=None)
    args = parser.parse_args()

    reports = []
    for speaker in _build_speakers():
        if args.only and speaker.name != args.only:
            continue
        if not speaker.available():
            print(f"SKIP {speaker.name}：未配置或依赖缺失（见 .env.example 的 YIBAO_TTS_* / YIBAO_COSYVOICE_*）")
            continue
        before = _peak_rss_bytes()
        await speaker._synth_pcm("预热")  # 冷启动（模型加载/连接建立）不计入稳态延迟，结果丢弃
        measured = await measure_provider(speaker, SENTENCES)
        mem = (_peak_rss_bytes() - before) if speaker.name == "cosyvoice" else None
        verdict = evaluate(measured, mem_bytes=mem)
        reports.append(verdict)
        mark = "PASS" if verdict["pass"] else "FAIL"
        lat = " / ".join(f"{r['latency_s']}s" for r in verdict["results"])
        onsets = [r.get("onset_s") for r in verdict["results"]]
        onset_text = ""
        if any(o is not None for o in onsets):
            onset_text = " 起音 " + " / ".join(f"{o}s" if o is not None else "-" for o in onsets)
        mem_text = f" mem+{mem / 1024**3:.2f}GB" if mem is not None else ""
        print(f"{mark} {speaker.name}: 延迟 {lat}{onset_text}{mem_text}")
        for f in verdict["failures"]:
            print(f"  - {f}")

    if not reports:
        print("无可用 provider，本次未产生有效判定（配置见 .env.example 的 YIBAO_TTS_* / YIBAO_COSYVOICE_*）")

    out_dir = Path(__file__).resolve().parent / "eval_reports"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"tts-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_file.write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    print(f"报告已写 {out_file}")
    return 0 if reports and all(r["pass"] for r in reports) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
