// 译宝桌面壳：拉起 Python 大脑 sidecar + stdio 桥 + 守护（崩溃重启/看门狗）+ 全局热键 + 输入/确认命令。
mod event_recorder;
mod session_db;

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_opener::OpenerExt;
#[cfg(desktop)]
use device_query::{DeviceQuery, DeviceState};
use std::sync::atomic::{AtomicBool, Ordering};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// 前端通知点击穿透模式：true=整窗可交互（展开/气泡中），false=仅团子热区可交互、其余穿透到桌面。
/// 启动默认 true（整窗可交互）：前端挂载前若辅助功能未授权、读不到光标，收起态分支会回退为「不穿透」，
/// 避免团子被锁死点不到；前端 onMounted 后由 setInteractiveFull 按需切到 false。
static PET_INTERACTIVE_FULL: AtomicBool = AtomicBool::new(true);

/// 说话气泡显示中：气泡带（贴团子左侧一条）成为第二热区（点气泡=展开），
/// 不像展开态那样整窗拦点击——气泡是瞬态的，透明区必须照常穿透到桌面。
static PET_BUBBLE_ON: AtomicBool = AtomicBool::new(false);

/// 单窗三态热区矩形组（窗口相对 CSS 像素，kind 区分用途）：
///  - "pet"：团子元素盒——enter 信号只由它驱动（历史教训：面板/气泡热区触发 enter 会误弹）；
///  - "ui"：快捷面板（3 圆 + 输入条）——quick 态附加热区，否则鼠标移到面板上被穿透点不到。
/// idle 态只上报团子盒，quick 态上报团子盒 ∪ 面板；隐藏时上报 None 清除。
/// 相对坐标 + Rust 每次判定叠加当前窗口位置，拖动窗口后热区自动跟随。
static PET_RECTS: Mutex<Vec<(f64, f64, f64, f64, String)>> = Mutex::new(Vec::new());

#[derive(serde::Deserialize)]
struct HotRect {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    kind: String,
}

#[tauri::command]
fn set_hot_rects(rects: Option<Vec<HotRect>>) {
    *PET_RECTS.lock().unwrap() = rects
        .map(|rs| rs.iter().map(|r| (r.x, r.y, r.w, r.h, r.kind.clone())).collect())
        .unwrap_or_default();
}

#[tauri::command]
fn set_interactive_full(full: bool) {
    PET_INTERACTIVE_FULL.store(full, Ordering::Relaxed);
}

#[tauri::command]
fn set_bubble_on(on: bool) {
    PET_BUBBLE_ON.store(on, Ordering::Relaxed);
}

/// 团子窗尺寸：收起/快捷 320×300（恒窗，内容层切换，零 resize 闪烁）/ 展开对话 360×520。
/// 入参为 CSS 像素，内部按 scale 换算物理像素。以左上角为锚（团子原地不动，向右下展开）。
#[tauri::command]
fn set_main_size(app: AppHandle, width: f64, height: f64) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("main") {
        let scale = win.scale_factor().unwrap_or(1.0);
        win.set_size(tauri::PhysicalSize::new(
            (width * scale).round() as u32,
            (height * scale).round() as u32,
        ))
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// 展开对话窗（360×520）：先定位再缩放。
/// 团子盒在收起/快捷态窗口内固定 (112,100)，展开后 chat header 小头像 ≈ 窗口内 (12,8)——
/// 窗口左上定位到「团子屏幕位置 - (12,8)」让团子视觉不动，再 clamp 进当前显示器内
/// （团子贴边时 chat 窗不至于出屏）。团子锚点是前端布局常量，与此处保持一致。
#[tauri::command]
fn expand_chat(app: AppHandle) -> Result<(), String> {
    let Some(win) = app.get_webview_window("main") else {
        return Ok(());
    };
    let scale = win.scale_factor().unwrap_or(1.0);
    let (wx, wy) = match win.outer_position() {
        Ok(p) => (p.x as f64, p.y as f64),
        Err(_) => (0.0, 0.0),
    };
    let pet_x = wx + 144.0 * scale;
    let pet_y = wy + 100.0 * scale;
    let mut x = pet_x - 12.0 * scale;
    let mut y = pet_y - 8.0 * scale;
    if let Ok(Some(mon)) = win.current_monitor() {
        let s = mon.scale_factor();
        let mx = mon.position().x as f64;
        let my = mon.position().y as f64;
        let sw = mon.size().width as f64;
        let sh = mon.size().height as f64;
        let pad = 8.0 * s;
        x = x.clamp(mx + pad, (mx + sw - 360.0 * s - pad).max(mx + pad));
        y = y.clamp(my + pad, (my + sh - 520.0 * s - pad).max(my + pad));
    }
    win.set_position(tauri::PhysicalPosition::new(x.round() as i32, y.round() as i32))
        .map_err(|e| e.to_string())?;
    win.set_size(tauri::PhysicalSize::new(
        (360.0 * scale).round() as u32,
        (520.0 * scale).round() as u32,
    ))
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// sidecar 守护状态：子进程句柄 + 心跳/重启计数/退出标记。
struct BrainState {
    child: Option<CommandChild>,
    last_pong: Instant,
    seen_pong: bool, // 本代进程是否已回过一个 pong/hello（区分启动中与运行中）
    warned: bool,    // 已超时一轮：两轮确认才 kill（App Nap/休眠苏醒后时间跳变不误杀）
    restarts: u32, // 连续掉线次数（稳定运行 60s 后清零）
    last_restart: Option<Instant>,
    shutting_down: bool,
    /// 计划内重启标记（设置保存/清数据前主动 kill）：桥任务收到退出后走正常拉起管线，
    /// 但这次掉线不算「崩溃」，退避计数不升级。
    manual_restart: bool,
    /// 维护模式（清大脑数据）：停脑→删文件→拉起期间，掉线只清槽广播、退避管线暂停，
    /// 防止 qdrant 锁还没释放就被重新拉起，也防与维护流程的拉起撞车。
    hold_restart: bool,
    /// 最近一次 panel 事件载荷（panel/schema/data）：面板窗首开时事件已发完，
    /// 窗口挂载后靠 get_current_panel 拉这份缓存补渲染（解首开竞态）。
    last_panel: Option<Value>,
    /// 面板浮窗被大窗临时藏起（大小窗互斥）：关大窗时凭它还原。
    panel_hidden_by_home: bool,
    /// 会话持久化库（conversation 域唯一权威存储；打开失败降级为 None 不落库，
    /// 此时对话仅内存态，不阻塞大脑主流程）。
    session_db: Option<session_db::SessionDb>,
    /// 对话事件落库器（流式缓冲 + proc 索引，瞬态随 run / sidecar 重启而清）。
    recorder: event_recorder::EventRecorder,
}

impl BrainState {
    fn new() -> Self {
        let db_path = runtime_root().join("session.db");
        if let Some(parent) = db_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let session_db = session_db::SessionDb::open(&db_path)
            .map_err(|e| eprintln!("[session] 会话库打开失败（降级内存态）：{e}"))
            .ok();
        Self {
            child: None,
            last_pong: Instant::now(),
            seen_pong: false,
            warned: false,
            restarts: 0,
            last_restart: None,
            shutting_down: false,
            manual_restart: false,
            hold_restart: false,
            last_panel: None,
            panel_hidden_by_home: false,
            session_db,
            recorder: event_recorder::EventRecorder::new(),
        }
    }
}

struct Brain(Mutex<BrainState>);

/// 运行时根目录：与 Python config.data_dir 一致（~/Library/Application Support/yibao）。
/// 用户数据、Python 运行时副本、语音模型都在这里（.app Resources 只读，不能往里装 venv）。
fn runtime_root() -> std::path::PathBuf {
    if let Ok(d) = std::env::var("YIBAO_DATA_DIR") {
        return std::path::PathBuf::from(d);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    std::path::PathBuf::from(home).join("Library/Application Support/yibao")
}

/// 是否生产模式（打包 .app）：dev（debug 构建或显式 YIBAO_SIDECAR_DIR）走仓库 sidecar。
fn is_prod() -> bool {
    std::env::var("YIBAO_SIDECAR_DIR").is_err() && !cfg!(debug_assertions)
}

/// 解析 sidecar 工程目录：优先 `YIBAO_SIDECAR_DIR`；dev 默认 <repo>/sidecar；
/// 生产用数据目录里的可写副本（首启 ensure_runtime 从 Resources 拷入）。
fn sidecar_dir() -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("YIBAO_SIDECAR_DIR") {
        return std::path::PathBuf::from(dir);
    }
    if cfg!(debug_assertions) {
        return std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("sidecar");
    }
    runtime_root().join("runtime").join("sidecar")
}

/// 首启引导进度事件：前端据此显示「首次初始化」状态（大脑还没起来，走 Tauri 事件而非 brain 桥）。
fn emit_setup(app: &AppHandle, stage: &str, detail: &str) {
    let _ = app.emit(
        "setup-progress",
        serde_json::json!({ "stage": stage, "detail": detail }),
    );
}

/// 首启/设置配置（LLM key/模型/音色/语音开关）：缺 key 时大脑不启动，前端弹设置向导。
#[derive(serde::Serialize, Clone)]
struct SetupConfig {
    has_key: bool,
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
fn get_setup_config() -> SetupConfig {
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

/// 拉起大脑（幂等：已在跑直接返回）。setup() 与配置保存共用。
fn boot_brain(app: &AppHandle) -> Result<(), String> {
    if app.state::<Brain>().0.lock().unwrap().child.is_some() {
        return Ok(());
    }
    let (rx, child) = spawn_brain(app)?;
    app.state::<Brain>().0.lock().unwrap().child = Some(child);
    spawn_bridge(app.clone(), rx);
    spawn_watchdog(app.clone());
    Ok(())
}

/// 保存首启/设置配置：upsert 进数据目录 .env（保留其它行），然后拉起大脑。
/// key 留空 = 不改动已有 key（设置页复用本命令；首启向导已在前端拦空 key）。
/// voice_enabled 为 None 时不动 YIBAO_VOICE（向导不传，保持缺省开）。
#[tauri::command]
fn save_setup_config(
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

/// 递归拷贝目录（跳过 .venv/__pycache__/models 大文件——Resources 里本就没有，防御而已）。
fn copy_dir(src: &std::path::Path, dst: &std::path::Path) -> Result<(), String> {
    std::fs::create_dir_all(dst).map_err(|e| format!("建目录失败 {}：{e}", dst.display()))?;
    let rd = std::fs::read_dir(src).map_err(|e| format!("读目录失败 {}：{e}", src.display()))?;
    for entry in rd.flatten() {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if matches!(name.as_ref(), ".venv" | "__pycache__" | "models" | ".pytest_cache" | ".env") {
            continue;
        }
        let (s, d) = (entry.path(), dst.join(name.as_ref()));
        let ft = entry.file_type().map_err(|e| e.to_string())?;
        if ft.is_dir() {
            copy_dir(&s, &d)?;
        } else if ft.is_file() {
            std::fs::copy(&s, &d).map_err(|e| format!("拷贝失败 {}：{e}", s.display()))?;
        }
    }
    Ok(())
}

/// 跑外部命令并收敛错误（带 stderr 尾部，方便定位首启失败原因）。
fn run_cmd(mut cmd: std::process::Command, what: &str) -> Result<(), String> {
    let out = cmd.output().map_err(|e| format!("{what} 启动失败：{e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let tail: String = err.chars().rev().take(500).collect::<String>().chars().rev().collect();
        return Err(format!("{what} 失败（code {:?}）：{}", out.status.code(), tail.trim()));
    }
    Ok(())
}

/// 首启引导（仅生产）：备齐 Python 运行时（uv sync，首启约 300MB）+ 语音模型（234MB）。
/// 每次启动都重拷源码 + sync（空转秒级）保证 runtime 跟 app 版本走；模型在则跳过。
/// 失败返回错误，前端提示重启重试。
fn ensure_runtime(app: &AppHandle) -> Result<(), String> {
    if !is_prod() {
        return Ok(());
    }
    let home = runtime_root();
    let runtime = home.join("runtime").join("sidecar");
    let venv_py = runtime.join(".venv").join("bin").join("python");
    let resource = app.path().resource_dir().map_err(|e| format!("取资源目录失败：{e}"))?;

    // 运行时副本每次启动都刷新（源文件小、拷贝快；copy 跳过 .env，用户配置不丢），
    // 否则 app 更新带了新 sidecar 代码而用户还跑旧的。uv sync 幂等：lock 没变是秒级空转。
    let first_boot = !venv_py.exists();
    if first_boot {
        emit_setup(app, "python", "首次初始化：安装 Python 环境（约 300MB，需联网，几分钟）…");
    }
    copy_dir(&resource.join("sidecar"), &runtime)?;
    let mut cmd = std::process::Command::new(resource.join("bin").join("uv"));
    cmd.arg("sync")
        .arg("--project")
        .arg(&runtime)
        .env("PYTHONUNBUFFERED", "1");
    // 国内直连 PyPI 常超时：用户没自配 index 时默认走清华镜像
    if std::env::var_os("UV_DEFAULT_INDEX").is_none() {
        cmd.env("UV_DEFAULT_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple");
    }
    run_cmd(cmd, "Python 环境安装")?;

    let models = home.join("models");
    if !models.join("paraformer-zh").join("model.int8.onnx").exists() {
        emit_setup(app, "models", "首次初始化：下载语音模型（234MB）…");
        let mut cmd = std::process::Command::new(&venv_py);
        cmd.arg(runtime.join("scripts").join("download_models.py"))
            .env("YIBAO_MODELS_DIR", &models);
        run_cmd(cmd, "语音模型下载")?;
    }
    emit_setup(app, "done", "初始化完成，大脑启动中…");
    Ok(())
}

/// 拉起 Python sidecar。
/// dev：sidecar/.venv/bin/python（绝对路径，避免 GUI 应用 PATH 缺失）
/// 回退：uv run（依赖 PATH 能找到 uv）
/// 生产：数据目录 runtime 副本的 venv + 插件/模型路径指到 Resources 与数据目录
fn spawn_brain(
    app: &AppHandle,
) -> Result<(tauri::async_runtime::Receiver<CommandEvent>, CommandChild), String> {
    let dir = sidecar_dir();
    let python = dir.join(".venv").join("bin").join("python");
    let spawn_result = if python.exists() {
        let mut cmd = app
            .shell()
            .command(python.to_string_lossy().to_string())
            .args(["-u", "-m", "yibao_brain.server"])
            .current_dir(&dir)
            .env("PYTHONUNBUFFERED", "1");
        if is_prod() {
            let home = runtime_root();
            if let Ok(resource) = app.path().resource_dir() {
                // 插件随包在 Resources（只读，插件业务数据落 data_dir 不受影响）
                cmd = cmd.env("YIBAO_PLUGINS_DIR", resource.join("plugins"));
            }
            cmd = cmd
                .env("YIBAO_STT_MODEL_DIR", home.join("models").join("paraformer-zh"))
                .env("YIBAO_VAD_MODEL", home.join("models").join("silero_vad.onnx"));
        }
        cmd.spawn()
    } else {
        app.shell()
            .command("uv")
            .args([
                "run",
                "--directory",
                &dir.to_string_lossy(),
                "yibao-brain-server",
            ])
            .env("PYTHONUNBUFFERED", "1")
            .spawn()
    };
    spawn_result.map_err(|e| format!("拉起 sidecar 失败：{e}"))
}

fn write_to_brain(state: &Brain, msg: Value) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    let child = guard.child.as_mut().ok_or("大脑不在线（重启中）")?;
    let line = format!("{}\n", msg);
    child.write(line.as_bytes()).map_err(|e| e.to_string())
}

/// 每代 sidecar 一个 stdout 桥任务：行分隔 JSON → Tauri 事件。
/// 进程结束（Terminated / stdout 关闭）→ on_brain_down 统一接管重启。
fn spawn_bridge(app: AppHandle, mut rx: tauri::async_runtime::Receiver<CommandEvent>) {
    tauri::async_runtime::spawn(async move {
        let mut down_detail: Option<String> = None;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).trim().to_string();
                    if line.is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<Value>(&line) {
                        Ok(v) => match v.get("type").and_then(|t| t.as_str()) {
                            Some("event") => {
                                let mut payload = v.get("event").cloned().unwrap_or(Value::Null);
                                // 会话分流：surface 提到事件顶层随广播走，各窗按自身场景过滤
                                if let Some(s) = v.get("surface") {
                                    payload["surface"] = s.clone();
                                }
                                // M3 会话归属：sidecar 事件透传 conversation_id（run 发起时确定），
                                // 提到事件顶层供落库/各窗过滤——流式中切会话不影响在途 run 的归属。
                                // 双 key：snake_case 供 Rust 落库读；camelCase 供前端 BrainEvent.conversationId 读
                                // （前端类型是 camelCase，只写 snake_case 会让前端过滤永远拿不到 id → 串台）。
                                if let Some(c) = v.get("conversation_id").and_then(|c| c.as_str()) {
                                    payload["conversation_id"] = serde_json::Value::String(c.to_string());
                                    payload["conversationId"] = serde_json::Value::String(c.to_string());
                                }
                                // panel 事件顺带缓存载荷，供面板窗首开竞态下补拉
                                if payload.get("kind").and_then(|k| k.as_str()) == Some("panel") {
                                    if let Some(p) = payload.get("payload") {
                                        let state = app.state::<Brain>();
                                        state.0.lock().unwrap().last_panel = Some(p.clone());
                                    }
                                }
                                // 对话事件落库（Rust 是 conversation 域唯一写者；webview 只读渲染）。
                                // 归属主会话：panel 事件（panelLink）全收；其余仅 pet/无 surface 的主对话，
                                // 面板工作台（surface=panel:xxx）的瞬时消息不持久化。
                                {
                                    let kind = payload.get("kind").and_then(|k| k.as_str()).unwrap_or("");
                                    let surface = payload.get("surface").and_then(|s| s.as_str()).unwrap_or("");
                                    let belongs_main = kind == "panel" || surface.is_empty() || surface == "pet";
                                    if belongs_main {
                                        let state = app.state::<Brain>();
                                        let mut g = state.0.lock().unwrap();
                                        // 字段解构拆分借用：db 不可变 + recorder 可变共存
                                        let BrainState { session_db, recorder, .. } = &mut *g;
                                        if let Some(db) = session_db.as_ref() {
                                            // M3：优先用事件透传的 conversation_id（流式切会话归属仍准确）；
                                            // 空则回退活跃会话指针（reminder/proactive 等无归属事件兜底）。
                                            let conv_id = payload
                                                .get("conversation_id")
                                                .and_then(|c| c.as_str())
                                                .filter(|c| !c.is_empty())
                                                .map(|c| c.to_string())
                                                .or_else(|| db.get_active_conversation().ok().flatten())
                                                .unwrap_or_default();
                                            recorder.record(db, &conv_id, &payload);
                                        }
                                    }
                                }
                                let _ = app.emit("brain-event", payload);
                            }
                            Some("run_done") => {
                                let _ = app.emit("brain-run-done", v);
                            }
                            Some("hello") => {
                                {
                                    let state = app.state::<Brain>();
                                    let mut g = state.0.lock().unwrap();
                                    g.last_pong = Instant::now();
                                    g.seen_pong = true; // hello 意味着分发循环即将就绪
                                    g.warned = false;
                                    // 稳定运行 60s+ 后的重启视为已恢复，清零退避计数
                                    if g
                                        .last_restart
                                        .is_some_and(|t| t.elapsed() > Duration::from_secs(60))
                                    {
                                        g.restarts = 0;
                                    }
                                    // 新进程接管：前一代残留流式缓冲按 interrupted 兜底落库（防"说了半句消失"）
                                    {
                                        let BrainState { session_db, recorder, .. } = &mut *g;
                                        if let Some(db) = session_db.as_ref() {
                                            let conv_id = db.get_active_conversation().ok().flatten().unwrap_or_default();
                                            recorder.flush_stream_as_interrupted(db, &conv_id);
                                        } else {
                                            recorder.reset_run();
                                        }
                                    }
                                }
                                if let Some(perms) = v.get("permissions") {
                                    let _ = app.emit("brain-permissions", perms.clone());
                                }
                                let _ =
                                    app.emit("brain-status", serde_json::json!({"status": "up"}));
                            }
                            Some("pong") => {
                                let state = app.state::<Brain>();
                                let mut g = state.0.lock().unwrap();
                                g.last_pong = Instant::now();
                                g.seen_pong = true;
                                g.warned = false;
                            }
                            Some("permissions") => {
                                if let Some(perms) = v.get("permissions") {
                                    let _ = app.emit("brain-permissions", perms.clone());
                                }
                            }
                            // 主屏 Feed 响应（动态列表 + 问候统计）：整体转发，前端一次性取用
                            Some("feed") => {
                                let _ = app.emit("brain-feed", v);
                            }
                            // 手动提炼响应（distill_now）：整体转发，设置页一次性取用
                            Some("distill_now") => {
                                let _ = app.emit("brain-distill-now", v);
                            }
                            // 设置页信任统计响应：整体转发
                            Some("feed_stats") => {
                                let _ = app.emit("brain-feed-stats", v);
                            }
                            // 每日回顾响应：整体转发
                            Some("distill_timeline") => {
                                let _ = app.emit("brain-distill-timeline", v);
                            }
                            // 主屏 widget 响应（插件一瞥卡列表）：整体转发
                            Some("widgets") => {
                                let _ = app.emit("brain-widgets", v);
                            }
                            // 主屏 Feed 已读回执：整体转发（前端按 id 局部更新 read）
                            Some("feed_marked_read") => {
                                let _ = app.emit("brain-feed-marked-read", v);
                            }
                            // 主屏 Feed 全部已读回执：整体转发（前端刷统计 + 列表 read）
                            Some("feed_all_read") => {
                                let _ = app.emit("brain-feed-all-read", v);
                            }
                            // Feed 处置态回执（C 子项目）：整体转发（前端按 id/status 局部对齐）
                            Some("feed_status_set") => {
                                let _ = app.emit("brain-feed-status-set", v);
                            }
                            // Feed 误报反馈回执：整体转发
                            Some("feed_feedback_set") => {
                                let _ = app.emit("brain-feed-feedback-set", v);
                            }
                            // 主屏 Dock 列表：整体转发
                            Some("dock_list") => {
                                let _ = app.emit("brain-dock-list", v);
                            }
                            // 主屏 Dock 固定/取消回执：整体转发（含最新 dock 数组）
                            Some("dock_pin_set") => {
                                let _ = app.emit("brain-dock-pin-set", v);
                            }
                            // 记忆管理/设置响应：整体转发（前端 once 竞速取用）
                            Some("mem_list") => {
                                let _ = app.emit("brain-mem-list", v);
                            }
                            Some("mem_deleted") => {
                                let _ = app.emit("brain-mem-deleted", v);
                            }
                            Some("mem_edited") => {
                                let _ = app.emit("brain-mem-edited", v);
                            }
                            Some("settings") => {
                                let _ = app.emit("brain-settings", v);
                            }
                            // 手机伴生端配对信息（设置页二维码用）
                            Some("http_pair_info") => {
                                let _ = app.emit("brain-http-pair-info", v);
                            }
                            // 感知日志与删除回执：sidecar 解密后整体转发，壳不持有密钥。
                            Some("perception") => {
                                let _ = app.emit("brain-perception", v);
                            }
                            Some("perception_deleted") => {
                                let _ = app.emit("brain-perception-deleted", v);
                            }
                            Some("perception_cleared") => {
                                let _ = app.emit("brain-perception-cleared", v);
                            }
                            _ => {}
                        },
                        Err(_) => eprintln!("[brain] 非 JSON：{line}"),
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    eprintln!(
                        "[brain] stderr: {}",
                        String::from_utf8_lossy(&bytes).trim_end()
                    );
                }
                CommandEvent::Error(err) => eprintln!("[brain] error：{err}"),
                CommandEvent::Terminated(payload) => {
                    eprintln!("[brain] 进程退出：{payload:?}");
                    down_detail = Some(format!("code={:?} signal={:?}", payload.code, payload.signal));
                    break;
                }
                _ => {}
            }
        }
        on_brain_down(app, down_detail).await;
    });
}

/// 进程掉线统一入口：清槽 → brain-status(down) → 退避重启（退出中/维护中则不动）。
async fn on_brain_down(app: AppHandle, detail: Option<String>) {
    let hold = {
        let state = app.state::<Brain>();
        let mut g = state.0.lock().unwrap();
        if g.shutting_down {
            return;
        }
        g.child = None;
        // 计划内重启（设置保存/清数据前的主动 kill）不算崩溃，退避计数不升级
        if !std::mem::take(&mut g.manual_restart) {
            g.restarts += 1;
        }
        g.last_restart = Some(Instant::now());
        g.hold_restart
    };
    let mut msg = serde_json::json!({"status": "down"});
    if let Some(d) = detail {
        msg["detail"] = Value::String(d);
    }
    let _ = app.emit("brain-status", msg);
    // 维护模式（清数据）：只广播掉线，由维护流程删完文件后自行拉起
    if hold {
        return;
    }
    restart_with_backoff(app).await;
}

/// 退避重启：1s → 2s → 5s → 10s 封顶；失败继续退避重试，永不放弃（常驻 agent）。
async fn restart_with_backoff(app: AppHandle) {
    let attempts = app.state::<Brain>().0.lock().unwrap().restarts;
    let backoff = match attempts {
        0 | 1 => 1,
        2 => 2,
        3 => 5,
        _ => 10,
    };
    let _ = app.emit(
        "brain-status",
        serde_json::json!({"status": "restarting", "attempt": attempts}),
    );
    tokio::time::sleep(Duration::from_secs(backoff)).await;
    // 睡醒后与清数据流程/退出协调，spawn 与状态登记在同一把锁内完成，防并发双拉
    let spawned = {
        let state = app.state::<Brain>();
        let mut g = state.0.lock().unwrap();
        if g.shutting_down || g.hold_restart {
            return; // 维护模式：维护流程会自己拉起；退出中：不再复活
        }
        if g.child.is_some() {
            return; // 已有人拉起（清理流程/并发路径），不重复 spawn
        }
        match spawn_brain(&app) {
            Ok((rx, child)) => {
                g.child = Some(child);
                g.last_pong = Instant::now(); // 给新进程启动留窗口
                g.seen_pong = false; // 启动宽限期内不启用 15s 心跳超时
                g.warned = false;
                Some(rx)
            }
            Err(e) => {
                eprintln!("[brain] 重启失败：{e}");
                g.restarts += 1;
                g.last_restart = Some(Instant::now());
                let _ = app.emit(
                    "brain-status",
                    serde_json::json!({"status": "down", "detail": e}),
                );
                None
            }
        }
    };
    match spawned {
        Some(rx) => spawn_bridge(app.clone(), rx),
        None => Box::pin(restart_with_backoff(app)).await,
    }
}

/// 手动重启大脑（设置保存后让新配置当场生效）。
/// 只负责「杀」：退出事件走桥任务 → on_brain_down → 退避管线统一拉起，
/// manual_restart 标记让这次计划内掉线不升退避计数、1s 即回。
/// 幂等：大脑本就不在线时退避管线本就在跑，无需插手；也不与进行中的重启打架
///（看门狗/清理流程先把 child 取走的话，这里拿到 None 直接返回）。
#[tauri::command]
fn restart_brain(app: AppHandle) -> Result<(), String> {
    let state = app.state::<Brain>();
    let mut g = state.0.lock().map_err(|e| e.to_string())?;
    if g.shutting_down {
        return Ok(());
    }
    if let Some(child) = g.child.take() {
        g.manual_restart = true;
        let _ = child.kill();
    }
    Ok(())
}

/// 清空大脑数据：kind = "memory"（长期记忆 mem0_store/）/ "history"（对话历史 history.json）/ "all"。
/// 顺序 = 先进维护模式停大脑 → 删 → 交还退避管线拉起。
/// 必须停脑再删：mem0_store 有 qdrant 文件锁（活进程手里删不掉/会损坏），
/// history.json 大脑也可能在运行中回写，删完重启才能让它以空历史重新加载。
#[tauri::command]
async fn clear_brain_data(app: AppHandle, kind: String) -> Result<(), String> {
    if !matches!(kind.as_str(), "memory" | "history" | "all") {
        return Err(format!("未知清理类型：{kind}"));
    }
    {
        let state = app.state::<Brain>();
        let mut g = state.0.lock().map_err(|e| e.to_string())?;
        if g.shutting_down {
            return Err("应用退出中，无法清理".into());
        }
        if g.hold_restart {
            return Err("另一个清理操作进行中".into());
        }
        g.hold_restart = true; // 维护期：掉线只清槽广播，退避管线暂停（防与删除撞车）
        g.manual_restart = true; // 计划内停脑，掉线计数不升级
        if let Some(child) = g.child.take() {
            let _ = child.kill();
        }
    }
    // 等进程离场：文件锁/句柄随进程死亡释放（kill 为 SIGKILL，秒级；留足余量）
    tokio::time::sleep(Duration::from_millis(1500)).await;
    let root = runtime_root();
    let mut result: Result<(), String> = Ok(());
    if kind != "history" {
        let mem = root.join("mem0_store");
        if mem.exists() {
            result = std::fs::remove_dir_all(&mem).map_err(|e| format!("删除长期记忆失败：{e}"));
        }
    }
    if result.is_ok() && kind != "memory" {
        let hist = root.join("history.json");
        if hist.exists() {
            result = std::fs::remove_file(&hist).map_err(|e| format!("删除对话历史失败：{e}"));
        }
        // 联动清会话持久化库（消息权威已前移到 Rust SQLite）：清历史必须连会话一起清，
        // 否则用户「清空对话」后气泡仍从 session.db 恢复。
        if result.is_ok() {
            let state = app.state::<Brain>();
            let g = state.0.lock().map_err(|e| e.to_string())?;
            if let Some(db) = g.session_db.as_ref() {
                if let Err(e) = db.clear_all() {
                    result = Err(format!("清空会话库失败：{e}"));
                }
            }
        }
    }
    // 无论删除成败都退出维护模式——回到可拉起状态优先，别把大脑卡在停止态
    {
        let state = app.state::<Brain>();
        let mut g = state.0.lock().map_err(|e| e.to_string())?;
        g.hold_restart = false;
    }
    result?;
    // 交还退避重启管线统一拉起（child 槽为空，spawn 路径独享）；配置不齐（首启未配 key/venv 未备）保持停止
    if get_setup_config().has_key && sidecar_dir().join(".venv").join("bin").join("python").exists()
    {
        restart_with_backoff(app).await;
    }
    Ok(())
}

/// 在 Finder 中打开数据目录（.env 配置 / 记忆 / 历史都在这）。
#[tauri::command]
fn open_data_dir(app: AppHandle) -> Result<(), String> {
    let dir = runtime_root();
    std::fs::create_dir_all(&dir).map_err(|e| format!("建数据目录失败：{e}"))?;
    app.opener()
        .open_path(dir.to_string_lossy().as_ref(), None::<&str>)
        .map_err(|e| format!("打开数据目录失败：{e}"))
}

/// 原生文件夹选择器（coding 面板 cwd 药丸用）：webview iframe 无 Tauri IPC，
/// 面板经 WebviewPanel `native:` 白名单旁路直调本命令，对话框必须 Rust 侧开。
/// 返回所选绝对路径；用户取消返回 None。
/// 必须 async：同步命令内联跑在 macOS 主线程，而 blocking_pick_folder 内部先
/// run_on_main_thread 派发创建对话框、再 recv() 原地阻塞——主线程被堵死则派发永不执行，
/// 硬死锁（点击后整窗卡死、对话框根本不出现）。async 命令跑在异步运行时线程，主线程自由。
/// .parent(&window) 把对话框挂到发起窗口（home/panel），避免被常驻置顶宠物窗压住。
#[tauri::command]
async fn pick_folder(window: tauri::Window) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let folder = window
        .app_handle()
        .dialog()
        .file()
        .set_title("选择项目文件夹")
        .set_parent(&window)
        .blocking_pick_folder();
    Ok(folder
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().into_owned()))
}

/// 看门狗：每 5s 发 ping；运行中 >15s 无 pong 视为疑似僵死。
/// 两轮确认：第一轮只补发 ping 并标记 warned，下一轮仍无 pong 才 kill（由桥任务 Terminated 统一重启）——
/// macOS App Nap/休眠会把整个壳挂起，苏醒后 last_pong 时间跳变，单轮判断会误杀健康大脑。
/// 启动宽限：首个 pong/hello 之前按 90s 启动窗口算（torch/mem0/sherpa 冷启动可能数十秒）。
fn spawn_watchdog(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(5)).await;
            let state = app.state::<Brain>();
            let mut g = match state.0.lock() {
                Ok(g) => g,
                Err(_) => continue,
            };
            if g.shutting_down {
                return;
            }
            let timeout = if g.seen_pong { 15 } else { 90 };
            if g.child.is_some() && g.last_pong.elapsed() > Duration::from_secs(timeout) {
                if g.warned {
                    eprintln!("[brain] 看门狗：{timeout}s 无 pong（两轮确认），kill 重启");
                    g.warned = false;
                    if let Some(child) = g.child.take() {
                        let _ = child.kill();
                    }
                } else {
                    eprintln!("[brain] 看门狗：{timeout}s 无 pong，补发 ping 观察一轮");
                    g.warned = true;
                    if let Some(child) = g.child.as_mut() {
                        let _ = child.write(b"{\"type\":\"ping\"}\n");
                    }
                }
            } else if let Some(child) = g.child.as_mut() {
                let _ = child.write(b"{\"type\":\"ping\"}\n");
            }
        }
    });
}

#[tauri::command]
fn run_input(
    window: tauri::WebviewWindow,
    app_handle: tauri::AppHandle,
    state: tauri::State<Brain>,
    text: String,
    surface: Option<String>,
    conversation_id: Option<String>,
) -> Result<(), String> {
    let surface_str = surface.unwrap_or_else(|| "pet".into());
    // 用户消息落库（Rust 是 conversation 域唯一写者）：M3 显式归属——
    // 前端传入 conversation_id 即落库到该会话（大小窗各自传自己的会话）；
    // 空 id（面板工作台 surface=panel:xxx 的瞬时输入）不持久化。
    if let Some(conv_id) = conversation_id.as_deref().filter(|c| !c.is_empty()) {
        let g = state.0.lock().map_err(|e| e.to_string())?;
        if let Some(db) = g.session_db.as_ref() {
            let _ = db.append_message(
                conv_id,
                &event_recorder::new_id(),
                "user",
                serde_json::json!({ "text": text }),
                session_db::now_ms(),
                false,
            );
            // 跨窗同步：用户消息无事件流（其他窗口收不到），广播轻量信号让
            // 正在看同一会话的窗口刷新（不主动抢流式——订阅方自己判断）。
            let _ = app_handle.emit(
                "conversation-updated",
                serde_json::json!({ "conversationId": conv_id, "from": window.label() }),
            );
        }
    }
    write_to_brain(
        &state,
        serde_json::json!({
            "id": 0, "type": "run", "text": text, "surface": surface_str,
            "conversation_id": conversation_id.unwrap_or_default(),
        }),
    )
}

/// 截图唤起（v1.1）：⌘⇧Y 唤起主窗时前端触发，通知大脑抓屏描述（下次 run 注入屏幕上下文）。
#[tauri::command]
fn invoke_context(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "invoke_context" }),
    )
}

/// 批量确认条目（前端 Task 5 传 camelCase：{id, approved, remember}）。
/// id = confirmation_needed 事件里 action.id（单条时等于 confirmation_id）。
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct ConfirmItem {
    id: String,
    approved: bool,
    #[serde(default)]
    remember: bool,
}

/// 批量确认（Task 4 桥到 sidecar 新 IPC）：一次回 N 条 verdict。
/// 写 {"type":"confirm_batch","items":[{"id":...,"approved":...,"remember":...}, ...]}，
/// sidecar 回 confirm_batched ok（按 id 匹配 pending_confirms / early_answers）。
/// confirmation_needed 转发：sidecar 已把 actions 放进 event 载荷，桥任务 Some("event") 分支
/// 透传整个 v，前端读 actions 即可——本命令只管回批。
#[tauri::command]
fn confirm_batch(state: tauri::State<Brain>, items: Vec<ConfirmItem>) -> Result<(), String> {
    let items_json: Vec<Value> = items
        .iter()
        .map(|it| {
            serde_json::json!({
                "id": it.id,
                "approved": it.approved,
                "remember": it.remember,
            })
        })
        .collect();
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "confirm_batch", "items": items_json }),
    )
}

/// 主屏 Feed 查询：大脑回 {"type":"feed","items":…,"stats":…}，经 brain-feed 事件广播。
#[tauri::command]
fn get_feed(state: tauri::State<Brain>, limit: Option<u32>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "feed", "limit": limit.unwrap_or(60) }),
    )
}

/// 手动触发昨日提炼：大脑回 {"type":"distill_now","ok":…,"result":…}，经 brain-distill-now 事件广播。
#[tauri::command]
fn distill_now(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "distill_now" }),
    )
}

/// 设置页信任统计：大脑回 {"type":"feed_stats","stats":…}，经 brain-feed-stats 事件广播。
#[tauri::command]
fn get_feed_stats(state: tauri::State<Brain>, days: Option<u32>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "feed_stats", "days": days.unwrap_or(7) }),
    )
}

/// 晨间反刍探测：开窗时前端调用，大脑自行决定推不推（fire-and-forget）。
#[tauri::command]
fn recap_check(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "recap_check" }))
}

/// 每日回顾查询：大脑回 {"type":"distill_timeline","days":[…]}，经 brain-distill-timeline 事件广播。
#[tauri::command]
fn get_distill_timeline(state: tauri::State<Brain>, days: Option<u32>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "distill_timeline", "days": days.unwrap_or(14) }),
    )
}

/// 主屏 widget 查询：大脑回 {"type":"widgets","widgets":…}，经 brain-widgets 事件广播。
#[tauri::command]
fn get_widgets(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "widgets" }))
}

/// 主屏 Feed：点掉单条（大脑回 {"type":"feed_marked_read","id":N,"ok":bool}
/// 经 brain-feed-marked-read 广播）。
/// 注：sidecar 直接读 msg["id"] 作 feed 条目 id（非信封序号），故不写 "id":0 占位。
#[tauri::command]
fn feed_mark_read(state: tauri::State<Brain>, id: i64) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "type": "feed_mark_read", "id": id }))
}

/// 主屏 Feed：全部已读（大脑回 {"type":"feed_all_read","n":N} 经 brain-feed-all-read 广播）。
#[tauri::command]
fn feed_mark_all_read(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "feed_mark_all_read" }),
    )
}

/// 主屏 Feed：处置态（follow/ignore/none，与 read 正交）。前端走乐观更新。
#[tauri::command]
fn feed_mark_status(
    state: tauri::State<Brain>,
    id: i64,
    status: String,
) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "type": "feed_mark_status", "id": id, "status": status }),
    )
}

/// 误报反馈（信任仪表写侧）：👍/👎 落 meta.feedback（大脑回 {"type":"feed_feedback_set",…}
/// 经 brain-feed-feedback-set 广播）。注：sidecar 直读 msg["id"]，故不写 "id":0 占位。
#[tauri::command]
fn feed_feedback(state: tauri::State<Brain>, id: i64, feedback: String) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "type": "feed_feedback", "id": id, "feedback": feedback }),
    )
}

/// 主屏 Dock 查询：pinned 优先 + 频率补齐（回 {"type":"dock_list","dock":[...]}
/// 经 brain-dock-list 广播）。
#[tauri::command]
fn dock_list(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "dock_list" }))
}

/// 主屏 Dock：固定/取消固定（回 {"type":"dock_pin_set","pid":...,"ok":bool,"dock":[...]}
/// 经 brain-dock-pin-set 广播）。
#[tauri::command]
fn set_dock_pin(state: tauri::State<Brain>, pid: String, on: bool) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "set_dock_pin", "pid": pid, "on": on }),
    )
}

/// 记忆管理：列出全部记忆（回 {"type":"mem_list"} 经 brain-mem-list 广播）。
#[tauri::command]
fn get_mem_list(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "mem_list" }))
}

/// 记忆管理：按 id 删除一条（回 {"type":"mem_deleted"} 经 brain-mem-deleted 广播）。
#[tauri::command]
fn mem_delete(state: tauri::State<Brain>, id: String) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "mem_delete", "mem_id": id }))
}

/// 记忆管理：按 id 编辑文本（回 {"type":"mem_edited"} 经 brain-mem-edited 广播）。
#[tauri::command]
fn mem_edit(state: tauri::State<Brain>, id: String, text: String) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "mem_edit", "mem_id": id, "text": text }))
}

/// 用户设置查询（回 {"type":"settings"} 经 brain-settings 广播）。
#[tauri::command]
fn get_settings(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "settings_get" }))
}

/// 手机伴生端配对信息（回 {"type":"http_pair_info"} 经 brain-http-pair-info 广播）。
#[tauri::command]
fn get_http_pair_info(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "http_pair_info" }))
}

/// 用户设置写入（仅已知键生效；回 {"type":"settings"} 经 brain-settings 广播）。
#[tauri::command]
fn set_settings(state: tauri::State<Brain>, values: serde_json::Value) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "settings_set", "values": values }))
}

/// 感知日志：分页查询（回 perception，经 brain-perception 广播）。
#[tauri::command]
fn get_perception(
    state: tauri::State<Brain>,
    limit: Option<u32>,
    before_id: Option<i64>,
) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({
            "id": 0,
            "type": "perception_list",
            "limit": limit.unwrap_or(60),
            "before_id": before_id,
        }),
    )
}

/// 感知日志：按观察 id 删除（信封 id 已占用，sidecar 字段必须是 per_id）。
#[tauri::command]
fn perception_delete(state: tauri::State<Brain>, id: i64) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "perception_delete", "per_id": id }),
    )
}

/// 感知日志：清空全部观察。
#[tauri::command]
fn perception_clear(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "perception_clear" }))
}

/// 面板动作（v2 §7）：壳不懂 panel 语义，透传 api.toml 白名单方法给大脑裁决。
#[tauri::command]
fn panel_action(
    state: tauri::State<Brain>,
    id: i64,
    method: String,
    params: Value,
    surface: Option<String>,
) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": id, "type": "panel_action", "method": method, "params": params, "surface": surface.unwrap_or_else(|| "pet".into()) }),
    )
}

/// 插件目录：YIBAO_PLUGINS_DIR 优先，否则 <repo>/plugins（与 brain 加载同源）。
fn plugins_dir() -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("YIBAO_PLUGINS_DIR") {
        return std::path::PathBuf::from(dir);
    }
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("plugins")
}

/// 解析插件 manifest.toml：顶层 id/name + 带 open 的 [[panel]] 条目（面板级入口，如素材库）。
/// 行级解析、不引 toml 依赖：顶层键只在任何 section 之前读（[[tool]] 里也有 id，不能误抓）；
/// [[panel]] 段内只取 name/label/open，无 open 的 panel 收成不了入口（detail/editor 需参数，本就不能直接开）。
fn parse_manifest(text: &str) -> Option<(String, String, Vec<Value>)> {
    // 取 `key = "值"` 的引号内内容：第一个引号到闭引号，之后的东西（如行尾注释）一律不混进值
    let val = |l: &str, key: &str| -> Option<String> {
        let rest = l.trim().strip_prefix(key)?;
        let rest = rest.trim_start().strip_prefix('=')?.trim_start();
        let rest = rest.strip_prefix('"')?;
        let end = rest.find('"')?;
        Some(rest[..end].to_string())
    };
    let mut id: Option<String> = None;
    let mut name: Option<String> = None;
    let mut panels: Vec<Value> = Vec::new();
    let mut seen_section = false;
    let mut in_panel = false;
    let mut pname = String::new();
    let mut plabel: Option<String> = None;
    let mut popen: Option<String> = None;
    // 只负责「收」：take 走字段（留下空态），不做重置赋值——重置由 section 分支显式做，EOF 后无人再读
    macro_rules! flush_panel {
        () => {
            if in_panel && !pname.is_empty() {
                if let Some(open) = popen.take() {
                    let label = plabel.take().filter(|s| !s.is_empty()).unwrap_or_else(|| pname.clone());
                    panels.push(serde_json::json!({
                        "name": std::mem::take(&mut pname), "label": label, "open": open,
                    }));
                }
            }
        };
    }
    for line in text.lines() {
        let l = line.trim();
        if l.starts_with('[') {
            flush_panel!();
            seen_section = true;
            in_panel = l == "[[panel]]";
            pname.clear();
            plabel = None;
            popen = None;
            continue;
        }
        if !seen_section {
            if id.is_none() {
                id = val(l, "id");
            }
            if name.is_none() {
                name = val(l, "name");
            }
            continue;
        }
        if in_panel {
            if pname.is_empty() {
                if let Some(v) = val(l, "name") {
                    pname = v;
                    continue;
                }
            }
            if plabel.is_none() {
                if let Some(v) = val(l, "label") {
                    plabel = Some(v);
                    continue;
                }
            }
            if popen.is_none() {
                if let Some(v) = val(l, "open") {
                    popen = Some(v);
                }
            }
        }
    }
    flush_panel!();
    Some((id?, name?, panels))
}

/// 插件启动器（双击团子）：扫各插件 manifest.toml 拿 id/name + 面板级入口（带 open 的 [[panel]]）。
#[tauri::command]
fn list_plugins() -> Result<Vec<Value>, String> {
    let rd = std::fs::read_dir(plugins_dir()).map_err(|e| format!("读插件目录失败：{e}"))?;
    let mut out = Vec::new();
    for entry in rd.flatten() {
        let path = entry.path().join("manifest.toml");
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        if let Some((id, name, panels)) = parse_manifest(&text) {
            out.push(serde_json::json!({ "id": id, "name": name, "panels": panels }));
        }
    }
    out.sort_by(|a, b| {
        a["id"].as_str().unwrap_or("").cmp(b["id"].as_str().unwrap_or(""))
    });
    Ok(out)
}

/// 打开/聚焦大窗（home）：与小窗互斥——藏宠物窗；面板浮窗若开着也临时藏起
///（记 panel_hidden_by_home，关大窗时还原）。宠物窗 header「扩充」钮与托盘「设置…」共用本命令。
#[tauri::command]
fn open_home_window(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("home") {
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())?;
    }
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.hide();
    }
    if let Some(panel) = app.get_webview_window("panel") {
        let was_visible = panel.is_visible().unwrap_or(false);
        if was_visible {
            let _ = panel.hide();
        }
        let state = app.state::<Brain>();
        if let Ok(mut g) = state.0.lock() {
            g.panel_hidden_by_home = was_visible;
        };
    }
    Ok(())
}

/// 大窗收起后还原小窗模式：亮宠物窗；面板浮窗若是被大窗临时藏起的，一并还原。
fn restore_after_home(app: &AppHandle) {
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.show();
    }
    let state = app.state::<Brain>();
    let restore = state
        .0
        .lock()
        .map(|mut g| std::mem::take(&mut g.panel_hidden_by_home))
        .unwrap_or(false);
    if restore {
        if let Some(panel) = app.get_webview_window("panel") {
            let _ = panel.show();
        }
    }
}

/// 关闭大窗 = 隐藏（不销毁，保状态）+ 还原小窗模式（互斥：宠物窗回来）。
/// 大窗前端的 × 走本命令；OS 级关闭由全局 CloseRequested 拦截后走同一还原。
#[tauri::command]
fn close_home_window(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("home") {
        win.hide().map_err(|e| e.to_string())?;
    }
    restore_after_home(&app);
    Ok(())
}

/// 打开/聚焦面板窗：已存在则 show+focus（关闭只是隐藏，状态保留）；
/// 首次用 builder 创建（无装饰+透明与主窗一致，不需 always_on_top），位置取屏幕中央偏右（避开宠物球常驻角）。
/// 大窗模式下不弹浮窗：面板嵌入大窗主区渲染（panel 事件大窗同样收到）。
/// 注：CloseRequested → hide 由全局 on_window_event 统一拦截（对所有窗生效，面板窗同享）。
#[tauri::command]
fn open_panel_window(app: AppHandle) -> Result<(), String> {
    if let Some(home) = app.get_webview_window("home") {
        if home.is_visible().unwrap_or(false) {
            return Ok(());
        }
    }
    if let Some(win) = app.get_webview_window("panel") {
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())?;
        return Ok(());
    }
    let win =
        tauri::WebviewWindowBuilder::new(&app, "panel", tauri::WebviewUrl::App("panel.html".into()))
            .title("译宝面板")
            .transparent(true)
            .decorations(false)
            .visible_on_all_workspaces(true)
            .resizable(true)
            .inner_size(780.0, 580.0)
            .build()
            .map_err(|e| format!("创建面板窗失败：{e}"))?;
    if let Ok(Some(mon)) = win.current_monitor() {
        let s = mon.scale_factor();
        let mx = mon.position().x as f64 / s;
        let my = mon.position().y as f64 / s;
        let sw = mon.size().width as f64 / s;
        let sh = mon.size().height as f64 / s;
        // 屏幕中央偏右：宠物球多在屏幕角落，面板居中偏右避让
        let x = mx + (sw - 780.0) / 2.0 + 80.0;
        let y = my + (sh - 580.0) / 2.0;
        let _ = win.set_position(tauri::LogicalPosition::new(x, y));
    }
    Ok(())
}

/// 关闭面板窗 = 隐藏（不销毁，保状态、二次打开快）。
/// 顺带广播 panel-closed：宠物窗靠它给「⇢ 协作中」的气泡收尾（⇠ 协作结束）。
#[tauri::command]
fn close_panel_window(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("panel") {
        win.hide().map_err(|e| e.to_string())?;
        let _ = app.emit("panel-closed", ());
    }
    Ok(())
}

/// 隐藏唤起条（主窗处理完 invoke-action 后兜底调用；条本身点击/Esc/blur 已自隐，幂等）。
#[tauri::command]
fn hide_invoke_bar(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("invoke-bar") {
        win.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[derive(serde::Deserialize)]
struct SnipRect {
    left: f64,
    top: f64,
    width: f64,
    height: f64,
}

/// 框选完成：隐藏 overlay → 逻辑选区换算物理像素 → 通知大脑区域截图 → 广播 snip-captured 让主窗展开。
#[tauri::command]
fn finish_snip(app: AppHandle, state: tauri::State<Brain>, rect: SnipRect) -> Result<(), String> {
    if let Some(snip) = app.get_webview_window("snip") {
        let _ = snip.hide();
        if let Ok(Some(mon)) = snip.current_monitor() {
            let scale = mon.scale_factor();
            let origin = (mon.position().x as i64, mon.position().y as i64);
            let (l, t, w, h) = snip_abs_rect((rect.left, rect.top, rect.width, rect.height), origin, scale);
            write_to_brain(
                &state,
                serde_json::json!({
                    "id": 0, "type": "snip_capture",
                    "left": l, "top": t, "width": w, "height": h,
                }),
            )?;
            let _ = app.emit("snip-captured", serde_json::json!({ "width": w, "height": h }));
        }
    }
    Ok(())
}

/// 取消框选（Esc / 单击 / 过小选区）：只收 overlay，不打扰。
#[tauri::command]
fn cancel_snip(app: AppHandle) -> Result<(), String> {
    if let Some(snip) = app.get_webview_window("snip") {
        let _ = snip.hide();
    }
    Ok(())
}

/// 截图即问：问题转发大脑（暂存的区域截图 + 问题 → vision 直答）。
#[tauri::command]
fn vision_query(state: tauri::State<Brain>, question: String) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "vision_query", "question": question }),
    )
}

/// 面板窗挂载后补拉最近一次的 panel 载荷（首开时 brain-event 先于窗口订阅发出）。
#[tauri::command]
fn get_current_panel(state: tauri::State<Brain>) -> Result<Option<Value>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    Ok(g.last_panel.clone())
}

/// 面板载荷回填：大窗从 localStorage 快照恢复工作面后，把同一份面板数据写回
/// last_panel（内存缓存）。last_panel 是内存态，重启即失——前端快照是持久层，
/// 恢复时回填保证面板窗/宠物窗在竞态下也能补拉到同一份数据（多窗一致）。
#[tauri::command]
fn remember_panel(state: tauri::State<Brain>, payload: Value) -> Result<(), String> {
    let mut g = state.0.lock().map_err(|e| e.to_string())?;
    g.last_panel = Some(payload);
    Ok(())
}

/// 读取 sidecar 已持久化的近期会话，供主屏恢复协作时间线。
/// 这是只读 UI 投影：不把历史重新送回大脑，也不会重放任何插件动作。
#[tauri::command]
fn get_conversation_history(limit: Option<usize>) -> Result<Vec<Value>, String> {
    let path = runtime_root().join("history.json");
    let raw = match std::fs::read_to_string(path) {
        Ok(raw) => raw,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("读取会话历史失败：{e}")),
    };
    let parsed: Value = serde_json::from_str(&raw).map_err(|e| format!("会话历史格式损坏：{e}"))?;
    let messages = parsed
        .as_array()
        .ok_or_else(|| "会话历史格式不是数组".to_string())?;
    let cap = limit.unwrap_or(80).clamp(1, 200);
    let start = messages.len().saturating_sub(cap);
    Ok(messages[start..]
        .iter()
        .filter_map(|message| {
            let role = message.get("role")?.as_str()?;
            if !matches!(role, "user" | "assistant" | "tool") {
                return None;
            }
            let content = message.get("content")?.as_str()?;
            let mut projected = serde_json::json!({ "role": role, "content": content });
            if let Some(surface) = message.get("surface").and_then(Value::as_str) {
                projected["surface"] = Value::String(surface.to_string());
            }
            if let Some(tool_call_id) = message.get("tool_call_id").and_then(Value::as_str) {
                projected["tool_call_id"] = Value::String(tool_call_id.to_string());
            }
            if let Some(calls) = message.get("tool_calls").and_then(Value::as_array) {
                projected["tool_calls"] = Value::Array(calls.iter().filter_map(|call| {
                    let id = call.get("id").and_then(Value::as_str);
                    let name = call.get("function")?.get("name")?.as_str()?;
                    Some(serde_json::json!({ "id": id, "function": { "name": name } }))
                }).collect());
            }
            Some(projected)
        })
        .collect())
}

// ---- 会话持久化恢复 API（conversation 域；Rust SQLite 是唯一权威，webview 只读）----

/// 会话列表（updated_at 倒序）。
#[tauri::command]
fn list_conversations(state: tauri::State<Brain>) -> Result<Vec<session_db::ConversationMeta>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    match g.session_db.as_ref() {
        Some(db) => db.list_conversations(),
        None => Ok(Vec::new()),
    }
}

/// 拉取某会话的消息（恢复用；只读，不重放任何动作）。
#[tauri::command]
fn get_conversation_messages(
    state: tauri::State<Brain>,
    id: String,
    limit: Option<i64>,
) -> Result<Vec<session_db::Message>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    match g.session_db.as_ref() {
        Some(db) => db.get_messages(&id, limit.unwrap_or(500)),
        None => Ok(Vec::new()),
    }
}

/// 新建会话（id 由 Rust 生成返回，保证多端一致）；自动设为活跃会话。
#[tauri::command]
fn create_conversation(
    state: tauri::State<Brain>,
    title: Option<String>,
) -> Result<session_db::ConversationMeta, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    let db = g.session_db.as_ref().ok_or("会话库不可用")?;
    let id = event_recorder::new_id();
    let meta = db.create_conversation(&id, title.as_deref().unwrap_or("新对话"), session_db::now_ms())?;
    let _ = db.set_active_conversation(&id);
    Ok(meta)
}

/// 设置活跃会话指针（事件落库归属兜底 + 大小窗镜面同步）。
/// 广播 active-conversation-changed：大窗切会话后小窗跟随切换（两窗镜面同一条会话）。
#[tauri::command]
fn set_active_conversation(
    app_handle: tauri::AppHandle,
    state: tauri::State<Brain>,
    id: String,
) -> Result<(), String> {
    {
        let g = state.0.lock().map_err(|e| e.to_string())?;
        if let Some(db) = g.session_db.as_ref() {
            db.set_active_conversation(&id)?;
        }
    }
    let _ = app_handle.emit("active-conversation-changed", serde_json::json!({ "conversationId": id }));
    Ok(())
}

/// 读活跃会话指针（前端恢复时定位当前会话）。
#[tauri::command]
fn get_active_conversation(state: tauri::State<Brain>) -> Result<Option<String>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    match g.session_db.as_ref() {
        Some(db) => db.get_active_conversation(),
        None => Ok(None),
    }
}

/// 确保存在活跃会话：无则新建并设为活跃（大窗首启直接输入时兜底）。
/// 大窗专用；小窗走 ensure_pet_conversation（固定会话，不镜像活跃会话）。
#[tauri::command]
fn ensure_active_conversation(
    app_handle: tauri::AppHandle,
    state: tauri::State<Brain>,
) -> Result<Option<session_db::ConversationMeta>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    let Some(db) = g.session_db.as_ref() else { return Ok(None) };
    if let Some(id) = db.get_active_conversation()?.filter(|c| !c.is_empty()) {
        // 指针存在但会话已被删 → 视为无效，重建
        if let Some(meta) = db.list_conversations()?.into_iter().find(|m| m.id == id) {
            return Ok(Some(meta));
        }
    }
    let id = event_recorder::new_id();
    let meta = db.create_conversation(&id, "新对话", session_db::now_ms())?;
    db.set_active_conversation(&id)?;
    let _ = app_handle.emit("active-conversation-changed", serde_json::json!({ "conversationId": id }));
    Ok(Some(meta))
}

/// 小窗固定会话（方案 A）：小窗永远用同一个会话，不镜像活跃会话。
/// 无指针或指针指向的会话已被删 → 自动新建并返回（幂等）。固定性从架构上消灭串台：
/// 小窗 run 永远带这个 id，事件归属恒定，大窗切会话完全不影响小窗。
#[tauri::command]
fn ensure_pet_conversation(
    state: tauri::State<Brain>,
) -> Result<Option<session_db::ConversationMeta>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    let Some(db) = g.session_db.as_ref() else { return Ok(None) };
    if let Some(id) = db.get_pet_conversation()?.filter(|c| !c.is_empty()) {
        if let Some(meta) = db.list_conversations()?.into_iter().find(|m| m.id == id) {
            return Ok(Some(meta));
        }
    }
    let id = event_recorder::new_id();
    let meta = db.create_conversation(&id, "小窗对话", session_db::now_ms())?;
    db.set_pet_conversation(&id)?;
    Ok(Some(meta))
}

/// 更新会话标题（首条用户消息自动生成）。
#[tauri::command]
fn update_conversation_title(state: tauri::State<Brain>, id: String, title: String) -> Result<(), String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(db) = g.session_db.as_ref() {
        db.update_conversation_title(&id, &title, session_db::now_ms())?;
    }
    Ok(())
}

/// 删除会话（级联清消息；若删的是活跃会话则清指针）。
#[tauri::command]
fn delete_conversation(state: tauri::State<Brain>, id: String) -> Result<(), String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(db) = g.session_db.as_ref() {
        db.delete_conversation(&id)?;
        if db.get_active_conversation().ok().flatten().as_deref() == Some(id.as_str()) {
            let _ = db.set_active_conversation("");
        }
    }
    Ok(())
}

/// 清空全部会话（联动 clear_brain_data）。
#[tauri::command]
fn clear_conversations(state: tauri::State<Brain>) -> Result<(), String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(db) = g.session_db.as_ref() {
        db.clear_all()?;
    }
    Ok(())
}

/// 截断会话到前 keep_count 条（重新生成/编辑重发：其后对话作废）。
#[tauri::command]
fn truncate_conversation_messages(
    state: tauri::State<Brain>,
    id: String,
    keep_count: i64,
) -> Result<(), String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(db) = g.session_db.as_ref() {
        db.truncate_messages(&id, keep_count)?;
    }
    Ok(())
}

#[tauri::command]
fn voice_start(
    state: tauri::State<Brain>,
    surface: Option<String>,
    continuous: Option<bool>,
) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({
            "id": 0,
            "type": "voice_start",
            "surface": surface.unwrap_or_else(|| "pet".into()),
            "continuous": continuous.unwrap_or(false),
        }),
    )
}

/// 打断当前进行中的生成/播报（Plan 4b 三连取消：停 TTS + 终止 LLM + 清队列）。
#[tauri::command]
fn interrupt(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "interrupt" }))
}

/// 面板焦点上报（v2 §5 focus）：壳面板窗内容变化时透传给大脑，run 时注入 LLM 上下文。
#[tauri::command]
fn report_panel_context(state: tauri::State<Brain>, focus: Value) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "panel_context", "focus": focus }),
    )
}

/// 宠物窗展开态（前端同步）：全局热键据此在 Rust 侧决定 显示/展开/隐藏，显隐不经过前端。
struct PetExpanded(std::sync::atomic::AtomicBool);

#[tauri::command]
fn set_pet_expanded(state: tauri::State<PetExpanded>, expanded: bool) {
    state
        .0
        .store(expanded, std::sync::atomic::Ordering::Relaxed);
}

/// 重新检测 macOS 权限（辅助功能/屏幕录制），结果经 brain-permissions 事件回前端。
#[tauri::command]fn check_permissions(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "check_permissions" }))
}

/// 触发系统授权引导弹窗（which = "ax" | "screen"）。
#[tauri::command]
fn prompt_permission(state: tauri::State<Brain>, which: String) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "prompt_permission", "which": which }),
    )
}

/// 鼠标穿透轮询（单窗三态架构）：每 40ms 读全局光标位置——
///  * 收起/快捷态（idle/quick，窗口恒 320×300，纯内容层切换，不 resize）：
///    - 仅前端上报的热区矩形可交互（kind=pet 团子盒 / kind=ui 快捷面板），其余穿透；
///    - 光标进入团子盒 → emit pet-cursor-enter（前端显示快捷面板）；
///    - 光标离开（团子 ∪ 面板）连续 ~480ms → emit pet-cursor-leave（前端收起面板）。
///  * 对话态（chat，360×520，set_interactive_full(true)）：整窗可交互，无 hover 逻辑。
/// 坐标单位（macOS 点 vs 物理像素）已按 scale 换算；热区为窗口相对坐标，拖动自动跟随。
#[cfg(desktop)]
fn spawn_click_through(handle: tauri::AppHandle) {
    std::thread::spawn(move || {
        let dev = DeviceState::new();
        let mut last_pet = false;
        let mut out_counter = 0u32; // 离开热区连续帧计数（延迟收起快捷面板）
        loop {
            let (mx, my) = dev.get_mouse().coords;
            let full = PET_INTERACTIVE_FULL.load(Ordering::Relaxed);

            // ---- main 窗：按模式放行热区 ----
            let (in_pet, in_ui) = if let Some(win) = handle.get_webview_window("main") {
                let scale = win.scale_factor().unwrap_or(1.0);
                if let (Ok(pos), _) = (win.outer_position(), win.outer_size()) {
                    let wx = pos.x as f64 / scale;
                    let wy = pos.y as f64 / scale;
                    let (cx, cy) = (mx as f64, my as f64);
                    if full {
                        // 对话态：整窗可交互
                        let _ = win.set_ignore_cursor_events(false);
                        (false, false)
                    } else if mx == 0 && my == 0 {
                        // 读不到光标（多半辅助功能未授权）→ 不穿透，避免团子点不到
                        let _ = win.set_ignore_cursor_events(false);
                        (true, false)
                    } else {
                        let rects = PET_RECTS.lock().unwrap();
                        if rects.is_empty() {
                            // 前端尚未上报（挂载瞬间）→ 先整窗可交互，避免团子被锁死点不到
                            let _ = win.set_ignore_cursor_events(false);
                            (true, false)
                        } else {
                            let inside = |kind: &str| {
                                rects.iter().any(|(rx, ry, rw, rh, k)| {
                                    k == kind
                                        && cx >= wx + rx
                                        && cx <= wx + rx + rw
                                        && cy >= wy + ry
                                        && cy <= wy + ry + rh
                                })
                            };
                            let p = inside("pet");
                            let u = inside("ui");
                            let _ = win.set_ignore_cursor_events(!(p || u));
                            (p, u)
                        }
                    }
                } else {
                    (false, false)
                }
            } else {
                (false, false)
            };

            // ---- hover 信号：进团子盒 → enter；离开（团子 ∪ 面板热区）→ 延迟 leave ----
            if !(mx == 0 && my == 0) && !full {
                if in_pet && !last_pet {
                    let _ = handle.emit("pet-cursor-enter", ());
                    out_counter = 0;
                }
                if in_pet || in_ui {
                    out_counter = 0;
                } else {
                    out_counter += 1;
                    // 40ms × 12 ≈ 480ms 不在热区 → 收快捷面板
                    if out_counter >= 12 {
                        let _ = handle.emit("pet-cursor-leave", ());
                        out_counter = 0;
                    }
                }
            }
            last_pet = in_pet;
            std::thread::sleep(std::time::Duration::from_millis(40));
        }
    });
}

// ---- 全局唤起（OS 感 §5：一个反射键 + 划词上下文唤起）----
// ⌘⇧Y 反射键：大窗开着=收大窗；否则宠物窗 隐藏→唤起 / 收起→展开就绪 / 展开→隐藏（展开态由前端判，Rust 只发事件）。
// ⌘⇧U 划词唤起：抓前台应用选中文字（剪贴板接力，用后还原）→ 宠物窗展开 + 上下文 chip。
fn pb_paste() -> Option<String> {
    let out = std::process::Command::new("pbpaste").output().ok()?;
    String::from_utf8(out.stdout).ok()
}

fn pb_copy(text: &str) {
    use std::io::Write;
    if let Ok(mut child) = std::process::Command::new("pbcopy")
        .stdin(std::process::Stdio::piped())
        .spawn()
    {
        if let Some(mut s) = child.stdin.take() {
            let _ = s.write_all(text.as_bytes());
        }
        let _ = child.wait();
    }
}

/** 合成 ⇧ keyup（kVK_Shift_L=56 / Shift_R=60）：物理按住 ⇧ 时直接发 ⌘C 会被 WindowServer 与物理 ⇧
 *  状态合并成 ⇧⌘C（调色板就是这么被打开的）——曾用「等用户松手」规避，按下到松手多久就卡多久（实测烧满 2s）。
 *  正解：先把事件流里的 ⇧ 抬成松开态，⌘C 就是纯 ⌘C；用户物理松手时硬件 keyup 自然到来（重复 keyup 无害）。 */
fn post_shift_keyup() -> Result<(), String> {
    use core_graphics::event::{CGEvent, CGEventTapLocation};
    use core_graphics::event_source::{CGEventSource, CGEventSourceStateID};
    let src = CGEventSource::new(CGEventSourceStateID::CombinedSessionState)
        .map_err(|_| "创建 CGEventSource 失败".to_string())?;
    for key in [56u16, 60u16] {
        // keyup：flags 默认空（不带 Shift），事件流即视为 ⇧ 已松开
        let up = CGEvent::new_keyboard_event(src.clone(), key, false).map_err(|_| "创建 ⇧keyup 失败")?;
        up.post(CGEventTapLocation::HID);
    }
    Ok(())
}

/** 直接经 CGEvent 发 ⌘C（kVK_ANSI_C=8）：与应用本体同一个辅助功能信任域（pyautogui 能注入靠的就是它），
 *  不绕 System Events/osascript（responsible process 归属成疑时静默不投递）。 */
fn post_cmd_c() -> Result<(), String> {
    use core_graphics::event::{CGEvent, CGEventFlags, CGEventTapLocation};
    use core_graphics::event_source::{CGEventSource, CGEventSourceStateID};
    let src = CGEventSource::new(CGEventSourceStateID::CombinedSessionState)
        .map_err(|_| "创建 CGEventSource 失败".to_string())?;
    let down = CGEvent::new_keyboard_event(src.clone(), 8, true).map_err(|_| "创建 keydown 失败")?;
    down.set_flags(CGEventFlags::CGEventFlagCommand);
    let up = CGEvent::new_keyboard_event(src, 8, false).map_err(|_| "创建 keyup 失败")?;
    up.set_flags(CGEventFlags::CGEventFlagCommand);
    down.post(CGEventTapLocation::HID);
    up.post(CGEventTapLocation::HID);
    Ok(())
}

/** 等剪贴板从 old 变掉：⌘C 注入后系统写板通常 30-80ms——每 25ms 轮询，变了立即返回新值（感知延迟从固定 300ms 降到几十 ms）；
 *  超时返回 None（= 无选中/前台不可拷贝，调用方退化）。read 注入便于单测。 */
fn wait_clipboard_change<F: FnMut() -> Option<String>>(
    old: &Option<String>,
    mut read: F,
    max_ms: u64,
) -> Option<String> {
    let step = std::time::Duration::from_millis(25);
    let mut waited = 0;
    loop {
        let new = read();
        if new != *old {
            return new;
        }
        waited += 25;
        if waited >= max_ms {
            return None;
        }
        std::thread::sleep(step);
    }
}

/** 读前台应用选中文字：暂存剪贴板 → 合成 ⇧ keyup（防 ⇧⌘C 合并）→ 模拟 ⌘C → 轮询读回（变了即返，超时 400ms）→ 还原。
 *  已知限制：pbpaste/pbcopy 只保真文本——剪贴板里是图片等富格式且被覆盖时还原不回原类型。
 *  无选中（剪贴板未变）或权限缺失（⌘C 静默失败）→ None，调用方退化为普通唤起。 */
fn grab_selected_text() -> Option<String> {
    let old = pb_paste();
    if post_shift_keyup().is_err() {
        return None;
    }
    if post_cmd_c().is_err() {
        return None;
    }
    let new = wait_clipboard_change(&old, pb_paste, 400)?;
    match &old {
        Some(o) => pb_copy(o),
        None => pb_copy(""),
    }
    if new.trim().is_empty() {
        None
    } else {
        Some(new)
    }
}

/** 唤起条落位：光标右下偏移（14,18），越出所在屏右/下缘时翻转到左/上；纯函数便于单测。
 *  mon = (屏原点x, 屏原点y, 屏宽, 屏高)，全部逻辑坐标。 */
fn clamp_bar_pos(mx: f64, my: f64, bar_w: f64, bar_h: f64, mon: (f64, f64, f64, f64)) -> (f64, f64) {
    let (mx0, my0, mw, mh) = mon;
    let mut x = mx + 14.0;
    let mut y = my + 18.0;
    if x + bar_w > mx0 + mw {
        x = mx - bar_w - 14.0;
    }
    if y + bar_h > my0 + mh {
        y = my - bar_h - 18.0;
    }
    (x.max(mx0), y.max(my0))
}

/** 选区换算：overlay 窗口逻辑坐标 → 虚拟桌面物理像素（mss 坐标系）。
 *  mon_px_origin = 屏物理原点（可为负：副屏在主屏左侧）；scale = 屏 scale_factor。纯函数便于单测。 */
fn snip_abs_rect(r: (f64, f64, f64, f64), mon_px_origin: (i64, i64), scale: f64) -> (i64, i64, i64, i64) {
    let (l, t, w, h) = r;
    (
        mon_px_origin.0 + (l * scale).round() as i64,
        mon_px_origin.1 + (t * scale).round() as i64,
        (w * scale).round().max(1.0) as i64,
        (h * scale).round().max(1.0) as i64,
    )
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let shortcuts = tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, shortcut, event| {
            if event.state != ShortcutState::Pressed {
                return;
            }
            // 划词唤起：抓选中文字（剪贴板接力 + CGEvent ⌘C，挪线程别卡热键线程）；有选中 → 动作条静默待选，无选中 → 展开
            if shortcut == &tauri_plugin_global_shortcut::Shortcut::new(
                Some(tauri_plugin_global_shortcut::Modifiers::SUPER | tauri_plugin_global_shortcut::Modifiers::SHIFT),
                tauri_plugin_global_shortcut::Code::KeyU,
            ) {
                let handle = app.clone();
                std::thread::spawn(move || {
                    let text = grab_selected_text();
                    if let Some(win) = handle.get_webview_window("main") {
                        let _ = win.show().and_then(|_| win.set_focus());
                    }
                    let has_text = text.is_some();
                    let _ = handle.emit("pet-invoke-selection", serde_json::json!({ "text": text }));
                    // 动作条：有选中文字才弹（无选中退化为旧唤起，不弹空菜单）
                    if has_text {
                        if let Some(bar) = handle.get_webview_window("invoke-bar") {
                            let (cmx, cmy) = device_query::DeviceState::new().get_mouse().coords;
                            // 落位光标所在屏：遍历显示器找逻辑矩形包含光标的屏，找不到退回窗口当前屏
                            let mon = bar.available_monitors().ok().and_then(|mons| {
                                mons.into_iter().find(|m| {
                                    let s = m.scale_factor();
                                    let x = m.position().x as f64 / s;
                                    let y = m.position().y as f64 / s;
                                    let w = m.size().width as f64 / s;
                                    let h = m.size().height as f64 / s;
                                    (cmx as f64) >= x && (cmx as f64) < x + w && (cmy as f64) >= y && (cmy as f64) < y + h
                                })
                            });
                            let mon = match mon {
                                Some(m) => Some(m),
                                None => bar.current_monitor().ok().flatten(),
                            };
                            if let Some(mon) = mon {
                                let s = mon.scale_factor();
                                let mon_rect = (
                                    mon.position().x as f64 / s,
                                    mon.position().y as f64 / s,
                                    mon.size().width as f64 / s,
                                    mon.size().height as f64 / s,
                                );
                                let (bx, by) = clamp_bar_pos(cmx as f64, cmy as f64, 328.0, 56.0, mon_rect);
                                let _ = bar.set_position(tauri::LogicalPosition::new(bx, by));
                            }
                            let _ = bar.show().and_then(|_| bar.set_focus());
                        }
                    }
                });
                return;
            }
            // 截图即问：overlay 铺满光标所在显示器，等前端拖拽选区（finish_snip/cancel_snip）
            if shortcut == &tauri_plugin_global_shortcut::Shortcut::new(
                Some(tauri_plugin_global_shortcut::Modifiers::SUPER | tauri_plugin_global_shortcut::Modifiers::SHIFT),
                tauri_plugin_global_shortcut::Code::KeyI,
            ) {
                let handle = app.clone();
                std::thread::spawn(move || {
                    let (cmx, cmy) = device_query::DeviceState::new().get_mouse().coords;
                    if let Some(snip) = handle.get_webview_window("snip") {
                        if let Ok(mons) = snip.available_monitors() {
                            let hit = mons.into_iter().find(|m| {
                                let s = m.scale_factor();
                                let x = m.position().x as f64 / s;
                                let y = m.position().y as f64 / s;
                                let w = m.size().width as f64 / s;
                                let h = m.size().height as f64 / s;
                                (cmx as f64) >= x && (cmx as f64) < x + w && (cmy as f64) >= y && (cmy as f64) < y + h
                            });
                            if let Some(mon) = hit {
                                let s = mon.scale_factor();
                                let _ = snip.set_position(tauri::LogicalPosition::new(
                                    mon.position().x as f64 / s,
                                    mon.position().y as f64 / s,
                                ));
                                let _ = snip.set_size(tauri::LogicalSize::new(
                                    mon.size().width as f64 / s,
                                    mon.size().height as f64 / s,
                                ));
                            }
                        }
                        let _ = snip.show().and_then(|_| snip.set_focus());
                        let _ = handle.emit("snip-start", ());
                    }
                });
                return;
            }
            // 反射键：大窗开着 = 收起大窗回小窗（互斥）；否则按 可见性×展开态 决策（显隐全在 Rust 侧）
            if let Some(home) = app.get_webview_window("home") {
                if home.is_visible().unwrap_or(false) {
                    let _ = home.hide();
                    restore_after_home(app);
                    return;
                }
            }
            if let Some(win) = app.get_webview_window("main") {
                let vis = win.is_visible().unwrap_or(false);
                let expanded = app
                    .state::<PetExpanded>()
                    .0
                    .load(std::sync::atomic::Ordering::Relaxed);
                if !vis {
                    // 隐藏 → 唤起：显示并聚焦；pet-show 让前端确保展开 + 输入聚焦
                    let _ = win.show().and_then(|_| win.set_focus());
                    let _ = app.emit("pet-show", ());
                } else if !expanded {
                    // 收起球 → 展开就绪
                    let _ = app.emit("pet-show", ());
                } else {
                    // 展开中 → 收回隐藏（第二段）
                    let _ = win.hide();
                }
            }
        })
        .build();

    tauri::Builder::default()
        // 单实例：第二个实例拉起时聚焦既有窗口并退出（防多实例 → 多 brain → qdrant 锁互踩）
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 大窗开着就聚焦大窗（互斥，别两边都亮）；否则亮宠物窗
            if let Some(h) = app.get_webview_window("home") {
                if h.is_visible().unwrap_or(false) {
                    let _ = h.set_focus();
                    return;
                }
            }
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show().and_then(|_| w.set_focus());
            }
        }))
        .plugin(shortcuts)
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        // 原生文件/文件夹对话框（pick_folder 命令经 DialogExt 使用）
        .plugin(tauri_plugin_dialog::init())
        // 开机启动（macOS LaunchAgent）；前端直接调插件 API（enable/disable/isEnabled）
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None::<Vec<&str>>,
        ))
        .manage(Brain(Mutex::new(BrainState::new())))
        .manage(PetExpanded(std::sync::atomic::AtomicBool::new(false)))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // 桌宠常驻：关窗只隐藏，真正退出走托盘菜单
                api.prevent_close();
                let _ = window.hide();
                if window.label() == "panel" {
                    let _ = window.app_handle().emit("panel-closed", ());
                }
                if window.label() == "home" {
                    // 大小窗互斥：大窗关了把小窗模式还原回来
                    restore_after_home(window.app_handle());
                }
            }
        })
        .setup(|app| {
            // 主窗默认停靠屏幕右上角（菜单栏下方留边距）；用户可拖动，展开方向自适应
            if let Some(win) = app.get_webview_window("main") {
                if let Ok(Some(mon)) = win.current_monitor() {
                    let s = mon.scale_factor();
                    let mx = mon.position().x as f64 / s;
                    let my = mon.position().y as f64 / s;
                    let sw = mon.size().width as f64 / s;
                    let _ = win.set_position(tauri::LogicalPosition::new(mx + sw - 320.0 - 24.0, my + 40.0));
                }
                // 启动即显示（conf 里 visible:false 只是避免定位前闪屏，别让用户按热键找宠物）
                let _ = win.show();
            }

            // 大窗（home，完整 APP 主界面）：随 setup 预创建但隐藏——首开快、状态保留；
            // 关窗=隐藏由全局 CloseRequested 拦截。正常窗口：不置顶、不 skipTaskbar（Dock/⌘⇥ 可切换）。
            //
            // 材质用「原生 macOS 应用窗口」而非浮层：TitleBarStyle::Overlay 保留系统红绿灯
            // 与系统级窗口阴影/圆角/缩放边框，前端不再自绘壳。大窗是可缩放、进 Dock、
            // 能 ⌘⇥ 切换的正式窗口，用小窗那套玻璃 HUD 语言会与系统观感违和，
            // 且全屏尺寸下 backdrop-filter 采样面积大、常驻白耗 GPU。
            // 注意：Overlay 下红绿灯浮在内容上，前端侧栏顶部须留出约 28px 安全区。
            let home = tauri::WebviewWindowBuilder::new(
                app,
                "home",
                tauri::WebviewUrl::App("home.html".into()),
            )
            // Overlay 模式下 macOS 仍会渲染窗口标题文字在标题栏中央；置空隐藏。
            // Dock 切换时的窗口名仍由 tauri.conf.json productName="译宝" 兜底。
            .title("")
            .title_bar_style(tauri::TitleBarStyle::Overlay)
            .decorations(true)
            .resizable(true)
            .inner_size(1040.0, 700.0)
            .min_inner_size(820.0, 560.0)
            .visible_on_all_workspaces(true)
            .visible(false)
            .build()
            .map_err(|e| format!("创建大窗失败：{e}"))?;
            if let Ok(Some(mon)) = home.current_monitor() {
                let s = mon.scale_factor();
                let mx = mon.position().x as f64 / s;
                let my = mon.position().y as f64 / s;
                let sw = mon.size().width as f64 / s;
                let sh = mon.size().height as f64 / s;
                let _ = home.set_position(tauri::LogicalPosition::new(
                    mx + (sw - 1040.0) / 2.0,
                    my + (sh - 700.0) / 2.0,
                ));
            }

            // 唤起条（划词动作菜单）：预创建隐藏，⌘⇧U 抓到文字后光标旁落位展示
            tauri::WebviewWindowBuilder::new(app, "invoke-bar", tauri::WebviewUrl::App("invoke.html".into()))
                .title("")
                .transparent(true)
                .decorations(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .visible_on_all_workspaces(true)
                .resizable(false)
                .inner_size(328.0, 56.0)
                .visible(false)
                .build()
                .map_err(|e| format!("创建唤起条失败：{e}"))?;

            // 截图框选层：预创建隐藏；⌘⇧I 时铺满光标所在显示器（drag 选区 → finish_snip）
            tauri::WebviewWindowBuilder::new(app, "snip", tauri::WebviewUrl::App("snip.html".into()))
                .title("")
                .transparent(true)
                .decorations(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .visible_on_all_workspaces(true)
                .resizable(false)
                .inner_size(800.0, 600.0) // 占位，唤起时按显示器重设
                .visible(false)
                .build()
                .map_err(|e| format!("创建截图层失败：{e}"))?;

            // 注册全局热键：⌘⇧Y 反射键（唤起/收起）；⌘⇧U 划词唤起（带选中文字上下文）；⌘⇧I 截图即问
            #[cfg(desktop)]
            {
                if let Err(e) = app.global_shortcut().register("Super+Shift+Y") {
                    eprintln!("[yibao] 注册热键失败：{e}");
                }
                if let Err(e) = app.global_shortcut().register("Super+Shift+U") {
                    eprintln!("[yibao] 注册热键失败：{e}");
                }
                if let Err(e) = app.global_shortcut().register("Super+Shift+I") {
                    eprintln!("[yibao] 注册热键失败：{e}");
                }
            }

            // 系统托盘：关窗隐藏后靠它重新显示/退出。左键点图标切换显隐，右键菜单。
            let show_item = MenuItem::with_id(app, "show", "显示译宝", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "隐藏译宝", true, None::<&str>)?;
            let settings_item = MenuItem::with_id(app, "settings", "设置…", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出译宝", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &hide_item, &settings_item, &quit_item])?;
            let tray_img = tauri::image::Image::from_bytes(include_bytes!("../icons/icon-tray.png"))
                .expect("加载托盘图标失败");
            TrayIconBuilder::with_id("main-tray")
                .icon(tray_img)
                .icon_as_template(true)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .tooltip("译宝")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        // 亮宠物窗 = 回小窗模式：大窗若开着先收（互斥）
                        if let Some(h) = app.get_webview_window("home") {
                            if h.is_visible().unwrap_or(false) {
                                let _ = h.hide();
                                restore_after_home(app);
                                return;
                            }
                        }
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show().and_then(|_| w.set_focus());
                        }
                    }
                    "hide" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.hide();
                        }
                    }
                    "settings" => {
                        // 打开设置大窗（与宠物窗 header「扩充」钮同一命令）
                        let _ = open_home_window(app.clone());
                    }
                    "quit" => {
                        // 标记退出，避免守护在退出途中重启大脑；顺手杀掉 sidecar
                        let state = app.state::<Brain>();
                        if let Ok(mut g) = state.0.lock() {
                            g.shutting_down = true;
                            if let Some(child) = g.child.take() {
                                let _ = child.kill();
                            }
                        }
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = if w.is_visible().unwrap_or(false) {
                                w.hide()
                            } else {
                                w.show().and_then(|_| w.set_focus())
                            };
                        }
                    }
                })
                .build(app)?;

            // 拉起 Python sidecar + 守护（stdout 桥管重启、看门狗管僵死）。
            // 生产首启先跑 ensure_runtime（装 Python 环境/下语音模型，可能几分钟）：
            // 异步引导不卡 setup——窗口先出来，进度经 setup-progress 事件上前端。
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let boot = tauri::async_runtime::spawn_blocking({
                    let h = handle.clone();
                    move || ensure_runtime(&h)
                })
                .await;
                match boot {
                    Ok(Ok(())) => {}
                    Ok(Err(e)) => {
                        let _ = handle.emit("setup-error", format!("首次初始化失败：{e}（重启译宝可重试）"));
                        return;
                    }
                    Err(e) => {
                        let _ = handle.emit("setup-error", format!("首次初始化中断：{e}"));
                        return;
                    }
                }
                // 没配 LLM key：不启大脑（起了也只会报错），前端弹设置向导，保存后由 save_setup_config 拉起
                if !get_setup_config().has_key {
                    let _ = handle.emit("setup-config-needed", "首次使用：请配置 LLM API Key");
                    return;
                }
                if let Err(e) = boot_brain(&handle) {
                    let _ = handle.emit("setup-error", format!("大脑启动失败：{e}"));
                }
            });

            // 鼠标穿透轮询（Tauri v2 JS API 无 forward，Rust 侧读全局光标切换 set_ignore_cursor_events）
            #[cfg(desktop)]
            spawn_click_through(app.handle().clone());

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            run_input,
            invoke_context,
            confirm_batch,
            panel_action,
            list_plugins,
            get_feed,
            distill_now,
            get_feed_stats,
            recap_check,
            get_distill_timeline,
            get_widgets,
            feed_mark_read,
            feed_mark_all_read,
            feed_mark_status,
            feed_feedback,
            dock_list,
            set_dock_pin,
            get_mem_list,
            mem_delete,
            mem_edit,
            get_settings,
            set_settings,
            get_http_pair_info,
            get_perception,
            perception_delete,
            perception_clear,
            open_panel_window,
            close_panel_window,
            hide_invoke_bar,
            finish_snip,
            cancel_snip,
            vision_query,
            get_current_panel,
            remember_panel,
            get_conversation_history,
            list_conversations,
            get_conversation_messages,
            create_conversation,
            set_active_conversation,
            get_active_conversation,
            ensure_active_conversation,
            ensure_pet_conversation,
            update_conversation_title,
            delete_conversation,
            clear_conversations,
            truncate_conversation_messages,
            voice_start,
            interrupt,
            report_panel_context,
            check_permissions,
            prompt_permission,
            set_interactive_full,
            set_bubble_on,
            set_hot_rects,
            set_main_size,
            expand_chat,
            get_setup_config,
            save_setup_config,
            restart_brain,
            clear_brain_data,
            open_data_dir,
            open_home_window,
            close_home_window,
            set_pet_expanded,
            pick_folder
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod invoke_tests {
    use super::*;

    #[test]
    fn bar_pos_bottom_right_offset() {
        // 屏幕中央：光标右下偏移
        let (x, y) = clamp_bar_pos(500.0, 400.0, 328.0, 56.0, (0.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (514.0, 418.0));
    }

    #[test]
    fn bar_pos_flips_at_right_and_bottom_edges() {
        // 右边缘：翻到光标左侧；下边缘：翻到光标上方
        let (x, y) = clamp_bar_pos(1400.0, 880.0, 328.0, 56.0, (0.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (1400.0 - 328.0 - 14.0, 880.0 - 56.0 - 18.0));
    }

    #[test]
    fn bar_pos_respects_monitor_origin() {
        // 副屏（原点在 1440,0）：越界判断相对该屏
        let (x, y) = clamp_bar_pos(1500.0, 100.0, 328.0, 56.0, (1440.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (1514.0, 118.0));
    }

    #[test]
    fn snip_rect_scales_and_offsets() {
        // scale=2  retina：逻辑 (100,50,200,120) + 屏原点 (0,0) → 物理 (200,100,400,240)
        let r = snip_abs_rect((100.0, 50.0, 200.0, 120.0), (0, 0), 2.0);
        assert_eq!(r, (200, 100, 400, 240));
        // 副屏负原点（主屏左侧 1440 宽）：逻辑 (10,10,50,50) → 物理 (-1420+20, 20, 100, 100)
        let r2 = snip_abs_rect((10.0, 10.0, 50.0, 50.0), (-2880, 0), 2.0);
        assert_eq!(r2, (-2860, 20, 100, 100));
    }

    #[test]
    fn clipboard_change_returns_immediately_when_already_changed() {
        let old = Some("a".to_string());
        let new = wait_clipboard_change(&old, || Some("b".to_string()), 400);
        assert_eq!(new, Some("b".to_string()));
    }

    #[test]
    fn clipboard_change_polls_until_change() {
        let old = Some("a".to_string());
        let n = std::cell::Cell::new(0);
        let new = wait_clipboard_change(
            &old,
            || {
                n.set(n.get() + 1);
                if n.get() >= 3 {
                    Some("b".to_string())
                } else {
                    Some("a".to_string())
                }
            },
            400,
        );
        assert_eq!(new, Some("b".to_string()));
        assert!(n.get() >= 3); // 真的轮询了多次，不是一次蒙对
    }

    #[test]
    fn clipboard_change_times_out_unchanged() {
        let old = Some("a".to_string());
        let start = std::time::Instant::now();
        let new = wait_clipboard_change(&old, || Some("a".to_string()), 100);
        assert_eq!(new, None);
        assert!(start.elapsed().as_millis() >= 50); // 轮询了一段才超时，非立即放弃
    }

    #[test]
    fn parse_manifest_picks_panels_with_open() {
        // 面板级入口：只有带 open 的 [[panel]] 才被收成子入口；顶层 id/name 照常
        let text = r#"
id = "zimeiti"
name = "自媒体"
capabilities = ["db", "llm"]

[[panel]]
type = "schema"
name = "board"
label = "选题看板"
src = "panel/board.schema.json"

[[panel]]
type = "schema"
name = "materials"
label = "素材库"
src = "panel/materials.schema.json"
open = "mat_list"

[[tool]]
id = "add"
"#;
        let (id, name, panels) = parse_manifest(text).expect("应解析出插件");
        assert_eq!(id, "zimeiti");
        assert_eq!(name, "自媒体");
        assert_eq!(panels.len(), 1);
        assert_eq!(panels[0]["name"], "materials");
        assert_eq!(panels[0]["label"], "素材库");
        assert_eq!(panels[0]["open"], "mat_list");
    }

    #[test]
    fn parse_manifest_without_panels_or_open() {
        // 无 [[panel]] / panel 无 open → panels 为空；缺 id/name → None
        let (id, name, panels) = parse_manifest("id = \"notes\"\nname = \"笔记\"\n").unwrap();
        assert_eq!((id.as_str(), name.as_str()), ("notes", "笔记"));
        assert!(panels.is_empty());
        let no_open = "id = \"z\"\nname = \"Z\"\n\n[[panel]]\nname = \"board\"\nlabel = \"看板\"\n";
        assert!(parse_manifest(no_open).unwrap().2.is_empty());
        assert!(parse_manifest("name = \"孤儿\"\n").is_none()); // 缺 id
    }

    #[test]
    fn parse_manifest_strips_trailing_comment_after_value() {
        // 行尾注释：open = "mat_list"   # 说明 —— 值只能取到第一个闭引号，注释不许混进方法名
        let text = "id = \"zimeiti\"\nname = \"自媒体\"\n\n[[panel]]\nname = \"materials\"\nlabel = \"素材库\"\nopen = \"mat_list\"   # 面板级入口\n";
        let (_, _, panels) = parse_manifest(text).expect("应解析出插件");
        assert_eq!(panels[0]["open"], "mat_list");
        assert_eq!(panels[0]["label"], "素材库");
    }
}
