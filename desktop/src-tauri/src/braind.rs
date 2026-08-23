//! sidecar 守护域：进程生命周期（spawn/stdout 桥/看门狗/退避重启）+ 运行时引导 + 共享状态 Brain/BrainState。
//! 唯一职责：让 Python 大脑进程「活着、健康、版本正确」；业务命令（run/feed/…）在 commands.rs。

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

use crate::event_recorder;
use crate::session_db;
use crate::setup_config::get_setup_config;

/// sidecar 守护状态：子进程句柄 + 心跳/重启计数/退出标记。
pub(crate) struct BrainState {
    pub(crate) child: Option<CommandChild>,
    last_pong: Instant,
    seen_pong: bool, // 本代进程是否已回过一个 pong/hello（区分启动中与运行中）
    warned: bool,    // 已超时一轮：两轮确认才 kill（App Nap/休眠苏醒后时间跳变不误杀）
    restarts: u32, // 连续掉线次数（稳定运行 60s 后清零）
    last_restart: Option<Instant>,
    pub(crate) shutting_down: bool,
    /// 计划内重启标记（设置保存/清数据前主动 kill）：桥任务收到退出后走正常拉起管线，
    /// 但这次掉线不算「崩溃」，退避计数不升级。
    manual_restart: bool,
    /// 维护模式（清大脑数据）：停脑→删文件→拉起期间，掉线只清槽广播、退避管线暂停，
    /// 防止 qdrant 锁还没释放就被重新拉起，也防与维护流程的拉起撞车。
    hold_restart: bool,
    /// 最近一次 panel 事件载荷（panel/schema/data）：面板窗首开时事件已发完，
    /// 窗口挂载后靠 get_current_panel 拉这份缓存补渲染（解首开竞态）。
    pub(crate) last_panel: Option<Value>,
    /// 面板浮窗被大窗临时藏起（大小窗互斥）：关大窗时凭它还原。
    pub(crate) panel_hidden_by_home: bool,
    /// 会话持久化库（conversation 域唯一权威存储；打开失败降级为 None 不落库，
    /// 此时对话仅内存态，不阻塞大脑主流程）。
    pub(crate) session_db: Option<session_db::SessionDb>,
    /// 对话事件落库器（流式缓冲 + proc 索引，瞬态随 run / sidecar 重启而清）。
    recorder: event_recorder::EventRecorder,
}

impl BrainState {
    pub(crate) fn new() -> Self {
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

pub(crate) struct Brain(pub(crate) Mutex<BrainState>);

/// 运行时根目录：与 Python config.data_dir 一致（~/Library/Application Support/yibao）。
/// 用户数据、Python 运行时副本、语音模型都在这里（.app Resources 只读，不能往里装 venv）。
pub(crate) fn runtime_root() -> std::path::PathBuf {
    if let Ok(d) = std::env::var("YIBAO_DATA_DIR") {
        return std::path::PathBuf::from(d);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    std::path::PathBuf::from(home).join("Library/Application Support/yibao")
}

/// 是否生产模式（打包 .app）：dev（debug 构建或显式 YIBAO_SIDECAR_DIR）走仓库 sidecar。
pub(crate) fn is_prod() -> bool {
    std::env::var("YIBAO_SIDECAR_DIR").is_err() && !cfg!(debug_assertions)
}

/// 解析 sidecar 工程目录：优先 `YIBAO_SIDECAR_DIR`；dev 默认 <repo>/sidecar；
/// 生产用数据目录里的可写副本（首启 ensure_runtime 从 Resources 拷入）。
pub(crate) fn sidecar_dir() -> std::path::PathBuf {
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

/// 拉起大脑（幂等：已在跑直接返回）。setup() 与配置保存共用。
pub(crate) fn boot_brain(app: &AppHandle) -> Result<(), String> {
    if app.state::<Brain>().0.lock().unwrap().child.is_some() {
        return Ok(());
    }
    let (rx, child) = spawn_brain(app)?;
    app.state::<Brain>().0.lock().unwrap().child = Some(child);
    spawn_bridge(app.clone(), rx);
    spawn_watchdog(app.clone());
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
pub(crate) fn ensure_runtime(app: &AppHandle) -> Result<(), String> {
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
        crate::setup_config::emit_setup(app, "python", "首次初始化：安装 Python 环境（约 300MB，需联网，几分钟）…");
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
        crate::setup_config::emit_setup(app, "models", "首次初始化：下载语音模型（234MB）…");
        let mut cmd = std::process::Command::new(&venv_py);
        cmd.arg(runtime.join("scripts").join("download_models.py"))
            .env("YIBAO_MODELS_DIR", &models);
        run_cmd(cmd, "语音模型下载")?;
    }
    crate::setup_config::emit_setup(app, "done", "初始化完成，大脑启动中…");
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
                                // 归属主会话：其余仅 pet/无 surface 的主对话，面板工作台（surface=panel:xxx）
                                // 的瞬时消息不持久化。（panel 事件已不落 panelLink 协作气泡）
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
                                let _ = app.emit("brain-event", payload.clone());
                                // 兜底：不论前端 PetWindow 是否裁决通过（conversationId 过滤/未重编译等
                                // 导致 openPanelWindow 没被调的面板弹不出场景），Rust 主动 show 面板窗。
                                // 用户体验层：对话里点名的面板操作必弹；裁决仍由前端做（精细控制），
                                // 这里只在 Rust 这层补漏。
                                if payload.get("kind").and_then(|k| k.as_str()) == Some("panel") {
                                    // 兜底：panel 事件到达时确保面板窗显示（大窗可见时按设计不弹浮窗——
                                    // 面板由大窗内嵌渲染；宠物窗/小窗模式才弹浮窗）。
                                    let _ = crate::commands::show_panel_window_impl(&app, false);
                                }
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
pub fn restart_brain(app: AppHandle) -> Result<(), String> {
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
pub async fn clear_brain_data(app: AppHandle, kind: String) -> Result<(), String> {
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
