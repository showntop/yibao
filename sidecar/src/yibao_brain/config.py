"""配置：从环境变量读取，带默认值。"""
from __future__ import annotations

from .log import log
import json
import os
import re
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """自动加载 .env（若存在），不覆盖已有 env（真 env 优先）。
    候选：sidecar 工程根（dev）→ 数据目录（生产，应用更新/运行时重拷后仍保留）。"""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path(data_dir()) / ".env",
    ]
    for env_file in candidates:
        if not env_file.is_file():
            continue
        for line in env_file.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def data_dir() -> str:
    """用户数据根目录（v2：代码与数据分离，数据不再落仓库）。默认 macOS 应用支持目录。"""
    d = os.environ.get("YIBAO_DATA_DIR")
    if d:
        return d
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/yibao")
    return os.path.expanduser("~/.yibao")


_load_dotenv()


def audit_db_path() -> str:
    return os.environ.get("YIBAO_AUDIT_DB", os.path.join(data_dir(), "audit.db"))


def plugin_data_dir(plugin_id: str) -> str:
    """某插件的业务数据目录（data.db / assets/ 放这里）。"""
    return os.path.join(data_dir(), "plugins", plugin_id)


def migrate_legacy_data(legacy_dir: str) -> None:
    """把仓库时代的用户数据（sidecar/ 下的 mem0_store/audit.db/history.json）迁到数据目录。

    新位置已存在则保留旧文件不覆盖（交给用户处理）；move 语义。
    """
    import shutil


    for name in ("mem0_store", "audit.db", "history.json"):
        src = os.path.join(legacy_dir, name)
        dst = os.path.join(data_dir(), name)
        if not os.path.exists(src) or os.path.exists(dst):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.move(src, dst)
            log(f"已迁移 {name} → {dst}")
        except OSError as e:
            log(f"迁移 {name} 失败（保留原位）：{e}")


def _env(new: str, old: str = "", default: str = "") -> str:
    """读新名 env，回退旧名（YIBAO_GLM_* 向后兼容），再回退默认值。"""
    return os.environ.get(new) or (os.environ.get(old, "") if old else "") or default


def llm_api_key() -> str:
    # 主 LLM provider 的 key（任意 OpenAI 兼容端点：智谱 GLM / DeepSeek / OpenAI …）
    return _env("YIBAO_LLM_API_KEY", "YIBAO_GLM_API_KEY")


def llm_model() -> str:
    return _env("YIBAO_LLM_MODEL", "YIBAO_GLM_MODEL", "glm-4.6")


def llm_base_url() -> str:
    return _env("YIBAO_LLM_BASE_URL", "YIBAO_GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")


def a11y_enabled() -> bool:
    """是否启用真实 a11y/执行基座（默认开；置 0 关闭，仅走 fake）。"""
    return os.environ.get("YIBAO_A11Y", "1") != "0"


_SEARCH_PROVIDERS = ("browser", "ddg", "searxng", "brave", "tavily", "serper")


def search_provider() -> str:
    """web_search 的搜索通道（settings 可改，即时生效）：
    browser=打开浏览器给人看（默认，免配置）；ddg=DuckDuckGo 免费免 key；
    searxng=自建元搜索；brave/tavily/serper=商用 API（key 走 .env）。
    兼容旧 YIBAO_SEARCH_ENGINE=baidu/bing/google → browser。
    """
    p = (os.environ.get("YIBAO_SEARCH_PROVIDER") or "").strip().lower()
    if not p:
        legacy = (os.environ.get("YIBAO_SEARCH_ENGINE") or "").strip().lower()
        p = "browser" if legacy in ("baidu", "bing", "google") else legacy
    if not p:
        p = str(load_settings().get("search.provider") or "browser").strip().lower()
    return p if p in _SEARCH_PROVIDERS else "browser"


def search_searxng_url() -> str:
    """自建 SearXNG 实例地址（settings 可改）；env YIBAO_SEARCH_SEARXNG_URL 优先。"""
    return (
        os.environ.get("YIBAO_SEARCH_SEARXNG_URL")
        or str(load_settings().get("search.searxng_url") or "").strip()
    )


def search_api_key(provider: str) -> str:
    """商用搜索 API key：设置页配的 search.keys 优先（覆盖 .env）；
    env 兜底：brave→YIBAO_SEARCH_BRAVE_KEY / tavily→YIBAO_SEARCH_TAVILY_KEY / serper→YIBAO_SEARCH_SERPER_KEY。
    """
    keys = load_settings().get("search.keys") or {}
    val = str(keys.get(provider) or "").strip()
    if val:
        return val
    return os.environ.get(f"YIBAO_SEARCH_{provider.upper()}_KEY", "")


def search_engine() -> str:
    """browser 模式用的搜索引擎（baidu/bing/google，默认 baidu）。"""
    return os.environ.get("YIBAO_SEARCH_ENGINE", "baidu")


def screenshot_dir() -> str:
    return os.environ.get("YIBAO_SCREENSHOT_DIR", "/tmp")


def vision_model() -> str:
    """computer-use 视觉兜底模型（目前仅 GLM-4.6V 支持；DeepSeek 等无视觉模型时该兜底自动禁用）。"""
    return _env("YIBAO_VISION_MODEL", "YIBAO_GLM_VISION_MODEL", "glm-4.6v-flash")


def vision_api_key() -> str:
    """视觉 provider key：独立配置优先，兼容旧 GLM 配置，最后回退主 provider。"""
    return (
        os.environ.get("YIBAO_VISION_API_KEY")
        or os.environ.get("YIBAO_GLM_API_KEY")
        or llm_api_key()
    )


def vision_base_url() -> str:
    """视觉 provider 端点：支持主 LLM=DeepSeek、视觉 LLM=GLM 的双 provider 组合。"""
    return (
        os.environ.get("YIBAO_VISION_BASE_URL")
        or os.environ.get("YIBAO_GLM_BASE_URL")
        or llm_base_url()
    )


def computer_use_enabled() -> bool:
    """computer-use 视觉兜底仅 GLM 端点可用（DeepSeek 等无视觉模型时自动禁用）。"""
    return "bigmodel" in vision_base_url()


def computer_use_max_steps() -> int:
    """computer_use 单次调用最多连续执行步数。

    默认 8：一次调用连续完成一个多步子任务（视觉模型输出 finish 即提前停），
    避免每一步都触发一次主模型往返 + 重新截图/开应用——这是 computer_use 慢的主因。
    环境可调（YIBAO_COMPUTER_USE_MAX_STEPS）便于测试时收紧。
    """
    try:
        return max(1, int(os.environ.get("YIBAO_COMPUTER_USE_MAX_STEPS", "8")))
    except ValueError:
        return 8


def voice_enabled() -> bool:
    return os.environ.get("YIBAO_VOICE", "1") != "0"


def stt_model_dir() -> str:
    """Paraformer 中文模型目录（含 model.int8.onnx + tokens.txt）。"""
    return os.environ.get(
        "YIBAO_STT_MODEL_DIR",
        os.path.join(os.path.dirname(__file__), "models", "paraformer-zh"),
    )


def vad_model_path() -> str:
    return os.environ.get(
        "YIBAO_VAD_MODEL",
        os.path.join(os.path.dirname(__file__), "models", "silero_vad.onnx"),
    )


def vad_min_silence() -> float:
    """VAD 判定「说完」的静音时长（秒）。太短会把说话中途的自然停顿切成两句话
    （0.9s 实测仍抢话：想一句说一句时的停顿被误判为说完）。"""
    return float(os.environ.get("YIBAO_VAD_MIN_SILENCE", "1.2"))


def vad_max_seconds() -> int:
    """单次录音最长时长（秒）。"""
    return int(os.environ.get("YIBAO_VAD_MAX_SECONDS", "30"))


def tts_voice() -> str:
    """edge-tts 中文音色（默认 XiaoxiaoNeural 女声，最自然）。"""
    return os.environ.get("YIBAO_TTS_VOICE", "zh-CN-XiaoxiaoNeural")


def tts_provider() -> str:
    """TTS 引擎：edge | cosyvoice | cosyvoice_cloud。env 优先 → settings.json → 默认 edge。

    切换在下次大脑启动（build_speaker）时生效。
    """
    raw = os.environ.get("YIBAO_TTS_PROVIDER") or load_settings().get("tts.provider") or "edge"
    p = str(raw).strip().lower()
    return p if p in ("edge", "cosyvoice", "cosyvoice_cloud") else "edge"


def cosyvoice_model_path() -> str:
    """本地 CosyVoice2 模型目录（FunAudioLLM/CosyVoice2-0.5B 解压路径）。"""
    return os.environ.get("YIBAO_COSYVOICE_MODEL", "")


def cosyvoice_voice() -> str:
    """本地 CosyVoice2 预置音色（无克隆参考音频时用）。"""
    return os.environ.get("YIBAO_COSYVOICE_VOICE", "中文女")


def cosyvoice_prompt_audio() -> str:
    """零样本克隆参考音频路径（留空=用预置音色）。"""
    return os.environ.get("YIBAO_COSYVOICE_PROMPT_AUDIO", "")


def cosyvoice_prompt_text() -> str:
    """参考音频对应台词（克隆必需）。"""
    return os.environ.get("YIBAO_COSYVOICE_PROMPT_TEXT", "")


def cosyvoice_cloud_key() -> str:
    """阿里云百炼 DashScope API key（云 CosyVoice）。"""
    return os.environ.get("YIBAO_COSYVOICE_CLOUD_KEY", "")


def cosyvoice_cloud_model() -> str:
    return os.environ.get("YIBAO_COSYVOICE_CLOUD_MODEL", "cosyvoice-v1")


def cosyvoice_cloud_voice() -> str:
    return os.environ.get("YIBAO_COSYVOICE_CLOUD_VOICE", "")


def mem0_embedder_model() -> str:
    """mem0 本地 embedding 模型（fastembed/ONNX 跑 BAAI/bge-small-zh-v1.5，中文 512 维，量化版 ~50MB）。"""
    return os.environ.get("YIBAO_MEM0_EMBEDDER", "BAAI/bge-small-zh-v1.5")


def mem0_embedder_dim() -> int:
    """embedder 向量维度（须与 mem0_embedder_model 匹配；bge-small-zh-v1.5=512）。"""
    return int(os.environ.get("YIBAO_MEM0_EMBED_DIM", "512"))


def mem0_vector_path() -> str:
    """mem0 本地 qdrant 向量库存储路径（嵌入模式，免外部 server）。"""
    return os.environ.get("YIBAO_MEM0_VECTOR_PATH", os.path.join(data_dir(), "mem0_store"))


def history_path() -> str:
    """短期会话历史 JSON 落盘路径（大脑重启后恢复最近几轮对话）。"""
    return os.environ.get("YIBAO_HISTORY_PATH", os.path.join(data_dir(), "history.json"))


def perception_db_path() -> str:
    """加密观察数据库；内容密钥不在此处配置，只能来自 macOS Keychain。"""
    return os.environ.get("YIBAO_PERCEPTION_DB", os.path.join(data_dir(), "observations.db"))


def http_port() -> int:
    """浏览器扩展桥监听端口（只绑 127.0.0.1）。"""
    try:
        return int(os.environ.get("YIBAO_HTTP_PORT", "19527"))
    except ValueError:
        return 19527


# ---------- 用户设置（数据目录 settings.json：自主权旋钮等运行期可调项，区别于 .env 的部署配置） ----------

_SETTINGS_DEFAULTS: dict = {
    "proactive_voice": True,  # 主动开口：提醒触发时语音播报（关 = 只亮窗/气泡，不出声）
    # 自主权旋钮（OS 感 §4.4）：quiet 只落动态 / bubble 气泡轻提示 / full 亮窗+气泡（+语音）
    "proactive.level": "full",
    "perception.master": False,
    "perception.app": False,
    "perception.activity": False,
    "perception.model_access": False,
    # Distiller（每日离线提炼）：默认关；开=每日 04:17 将昨日全天感知内容发给当前模型提炼
    "perception.distill": False,
    "perception.recap": False,
    # B 源（屏幕内容，S3）：默认关；blacklist 为附加 bundle id 黑名单（内置含 1Password/钥匙串）
    "perception.screen": False,
    "perception.blacklist": [],
    # 用户手动固定到 Dock 的插件 id（上限 5，Task 8 Dock 排序用；空 = 不固定）
    "dock_pinned": [],
    # 联网搜索通道（即时生效）：browser=打开浏览器 / ddg=免 key / searxng=自建实例 / brave/tavily/serper=API
    "search.provider": "browser",
    "search.searxng_url": "",
    # 商用搜索 API key（设置页配置，覆盖 .env 的 YIBAO_SEARCH_*_KEY；空 = 用 .env）
    "search.keys": {},
    # TTS 引擎选择（UI 下拉；env YIBAO_TTS_PROVIDER 优先；切换下次启动生效）
    "tts.provider": "edge",
    # watch mode（主动观察；slice1=健康节律+在场陪伴，默认关）
    "watch.enabled": False,
    "watch.screen_enabled": False,
    "watch.cadence": 60,                  # watch 循环采样间隔（秒）
    "watch.idle_warn_minutes": 45,        # 连续活跃多久提醒久坐
    "watch.quiet_hours": "23:00-07:00",   # 静默时段（HH:MM-HH:MM，跨午夜；空串=关）
    "watch.observe_apps": [],              # 主动搭话允许观察的前台应用白名单（空=不观察）
    "watch.look_min_gap": 300,             # 主动搭话两次看之间最小间隔（秒）
    "watch.look_max_per_hour": 6,          # 主动搭话每小时上限
    "watch.look_max_per_day": 50,          # 主动搭话每日上限
    "http.token": "",  # 浏览器扩展桥共享 token（空 = 启动时生成并持久化）
    "http.mobile_token": "",  # 手机伴生端 token（与扩展桥隔离，可单独重置）
    "shell.first_seen": "",  # 首启时刻（ISO，本地时；空 = 启动时生成并持久化，日题"已陪伴你 N 天"用）
    "http.bind": "127.0.0.1",  # HTTP 面监听地址：127.0.0.1 仅本机；0.0.0.0 局域网（手机浏览器体验，token 把关）
    "http.public_url": "",    # 对外域名（VPS Caddy）；配对二维码用，空=仅局域网调试
    "push.devices": [],       # 已注册推送设备 [{registration_id, platform, added_at}]
}

# 枚举型设置的合法取值；非法值拒收保持原值（防前端/手滑写坏）
_SETTINGS_ENUMS: dict[str, tuple] = {
    "proactive.level": ("quiet", "bubble", "full"),
    "tts.provider": ("edge", "cosyvoice", "cosyvoice_cloud"),
    "search.provider": ("browser", "ddg", "searxng", "brave", "tavily", "serper"),
}


def settings_path() -> str:
    return os.path.join(data_dir(), "settings.json")


def load_settings() -> dict:
    """读设置并与默认值合并（新键后续加入时旧文件自动补默认）；文件坏/不存在 → 全默认。"""
    try:
        with open(settings_path(), encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("settings.json 不是对象")
    except Exception:
        raw = {}
    out = dict(_SETTINGS_DEFAULTS)
    for k in out:
        if k in raw:
            out[k] = raw[k]
    return out


def ensure_first_seen(settings: dict) -> str:
    """首启时刻（ISO 本地时）：空则生成并持久化。日题"已陪伴你 N 天"的数据源。
    与 http.token 同款语义——空 = 生成即落盘，之后只读不改。"""
    val = str(settings.get("shell.first_seen") or "")
    if not val:
        import datetime

        val = datetime.datetime.now().isoformat(timespec="seconds")
        save_settings({"shell.first_seen": val})
        settings["shell.first_seen"] = val
    return val


def save_settings(values: dict) -> None:
    """只落已知键且枚举键校验取值（防前端乱写）；目录不存在先建。"""
    os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
    cur = load_settings()
    for k in cur:
        if k in values:
            allowed = _SETTINGS_ENUMS.get(k)
            if allowed is not None and values[k] not in allowed:
                continue  # 非法枚举值拒收，保持原值
            if k == "watch.quiet_hours" and not _valid_quiet_hours(values[k]):
                continue
            if k == "watch.observe_apps":
                if not isinstance(values[k], list) or not all(
                    isinstance(item, str) and item.strip() for item in values[k]
                ):
                    continue
                values[k] = list(dict.fromkeys(item.strip() for item in values[k]))
            if k == "search.keys":
                # 只收已知 provider 的字符串值（trim 后落盘；空串 = 清空该 provider key）
                if not isinstance(values[k], dict):
                    continue
                clean = {}
                for p, v in values[k].items():
                    if p in ("brave", "tavily", "serper") and isinstance(v, str):
                        clean[p] = v.strip()
                values[k] = clean
            if k in {
                "watch.cadence", "watch.idle_warn_minutes", "watch.look_min_gap",
                "watch.look_max_per_hour", "watch.look_max_per_day",
            }:
                if not isinstance(values[k], (int, float)) or values[k] < 0:
                    continue
            cur[k] = values[k]
    tmp = settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, settings_path())  # 原子替换，别写半个文件


def _valid_quiet_hours(value) -> bool:
    if value == "":
        return True
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", value.strip())
    return bool(match) and all(
        number < limit
        for number, limit in zip((int(x) for x in match.groups()), (24, 60, 24, 60))
    )
