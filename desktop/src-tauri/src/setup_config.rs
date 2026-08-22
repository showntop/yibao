//! 首启/设置配置域：.env 读取合并（与 Python config._load_dotenv 同规则）+ get/save 命令 + 首启进度事件。
//! 独立于守护域：配置存在与否决定大脑要不要拉起（save 成功后调 braind::boot_brain）。

use tauri::{AppHandle, Emitter};

use crate::braind::{boot_brain, runtime_root, sidecar_dir};

/// 首启引导进度事件：前端据此显示「首次初始化」状态（大脑还没起来，走 Tauri 事件而非 brain 桥）。
pub(crate) fn emit_setup(app: &AppHandle, stage: &str, detail: &str) {
    let _ = app.emit(
        "setup-progress",
        serde_json::json!({ "stage": stage, "detail": detail }),
    );
}

/// 首启/设置配置（LLM key/模型/音色/语音开关）：缺 key 时大脑不启动，前端弹设置向导。
#[derive(serde::Serialize, Clone)]
pub(crate) struct SetupConfig {
    pub(crate) has_key: bool,
    model: String,
    base_url: String,
    voice: String,
    /// 语音总开关（YIBAO_VOICE）：语义对齐 config.py——仅 "0" 为关，缺省/其它值都算开
    voice_enabled: bool,
}

/// 解析 .env 文件为键值对（与 Python config._load_dotenv 同规则：去引号、跳注释）。
fn read_env_file(path: &std::path::Path) -> Vec<(String, String)> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return vec![];
    };
    text.lines()
        .filter_map(|l| {
            let s = l.trim();
            if s.is_empty() || s.starts_with('#') {
                return None;
            }
            let (k, v) = s.split_once('=')?;
            Some((
                k.trim().to_string(),
                v.trim().trim_matches('"').trim_matches('\'').to_string(),
            ))
        })
        .collect()
}

/// 合并配置来源：数据目录 .env < sidecar 工程 .env（dev 优先）< 真环境变量。
fn merged_env() -> std::collections::HashMap<String, String> {
    let mut m = std::collections::HashMap::new();
    for (k, v) in read_env_file(&runtime_root().join(".env")) {
        m.insert(k, v);
    }
    for (k, v) in read_env_file(&sidecar_dir().join(".env")) {
        m.insert(k, v);
    }
    for k in [
        "YIBAO_LLM_API_KEY",
        "YIBAO_LLM_MODEL",
        "YIBAO_LLM_BASE_URL",
        "YIBAO_TTS_VOICE",
        "YIBAO_VOICE",
    ] {
        if let Ok(v) = std::env::var(k) {
            m.insert(k.to_string(), v);
        }
    }
    m
}

#[tauri::command]
pub fn get_setup_config() -> SetupConfig {
    let m = merged_env();
    SetupConfig {
        has_key: m.get("YIBAO_LLM_API_KEY").is_some_and(|v| !v.is_empty()),
        model: m
            .get("YIBAO_LLM_MODEL")
            .filter(|v| !v.is_empty())
            .cloned()
            .unwrap_or_else(|| "glm-4.6".into()),
        base_url: m.get("YIBAO_LLM_BASE_URL").cloned().unwrap_or_default(),
        voice: m
            .get("YIBAO_TTS_VOICE")
            .filter(|v| !v.is_empty())
            .cloned()
            .unwrap_or_else(|| "zh-CN-XiaoxiaoNeural".into()),
        voice_enabled: m.get("YIBAO_VOICE").is_none_or(|v| v != "0"),
    }
}

/// 保存首启/设置配置：upsert 进数据目录 .env（保留其它行），然后拉起大脑。
/// key 留空 = 不改动已有 key（设置页复用本命令；首启向导已在前端拦空 key）。
/// voice_enabled 为 None 时不动 YIBAO_VOICE（向导不传，保持缺省开）。
#[tauri::command]
pub fn save_setup_config(
    app: AppHandle,
    key: String,
    model: String,
    base_url: String,
    voice: String,
    voice_enabled: Option<bool>,
) -> Result<(), String> {
    let key = key.trim().to_string();
    if key.is_empty()
        && !merged_env()
            .get("YIBAO_LLM_API_KEY")
            .is_some_and(|v| !v.is_empty())
    {
        return Err("API Key 不能为空".into());
    }
    let path = runtime_root().join(".env");
    let mut lines: Vec<String> = std::fs::read_to_string(&path)
        .map(|t| t.lines().map(|l| l.to_string()).collect())
        .unwrap_or_default();
    for (k, v) in [
        ("YIBAO_LLM_API_KEY", key),
        ("YIBAO_LLM_MODEL", model.trim().to_string()),
        ("YIBAO_LLM_BASE_URL", base_url.trim().to_string()),
        ("YIBAO_TTS_VOICE", voice.trim().to_string()),
    ] {
        if v.is_empty() {
            continue; // 可选项留空则不写（走 Python 侧默认值）
        }
        let prefix = format!("{k}=");
        match lines.iter_mut().find(|l| l.starts_with(&prefix)) {
            Some(line) => *line = format!("{k}={v}"),
            None => lines.push(format!("{k}={v}")),
        }
    }
    // 语音总开关：设置页显式传了才写（"0"=关 / "1"=开，语义对齐 config.py）
    if let Some(ve) = voice_enabled {
        let v = if ve { "1" } else { "0" };
        match lines.iter_mut().find(|l| l.starts_with("YIBAO_VOICE=")) {
            Some(line) => *line = format!("YIBAO_VOICE={v}"),
            None => lines.push(format!("YIBAO_VOICE={v}")),
        }
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("建数据目录失败：{e}"))?;
    }
    std::fs::write(&path, lines.join("\n") + "\n").map_err(|e| format!("写配置失败：{e}"))?;
    // venv 还没备好（首启 Python 环境仍在装）时先不拉大脑——setup() 装完会再查配置并拉起
    if sidecar_dir().join(".venv").join("bin").join("python").exists() {
        boot_brain(&app)?;
    }
    Ok(())
}
