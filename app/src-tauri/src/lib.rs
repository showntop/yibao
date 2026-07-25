// 译宝桌面壳：拉起 Python 大脑 sidecar + stdio 桥 + 守护（崩溃重启/看门狗）+ 全局热键 + 输入/确认命令。
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
#[cfg(desktop)]
use device_query::{DeviceQuery, DeviceState};
use std::sync::atomic::{AtomicBool, Ordering};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// 前端通知点击穿透模式：true=整窗可交互（展开/气泡中），false=仅团子热区可交互、其余穿透到桌面。
/// 启动默认 true（整窗可交互）：前端挂载前若辅助功能未授权、读不到光标，收起态分支会回退为「不穿透」，
/// 避免团子被锁死点不到；前端 onMounted 后由 setInteractiveFull 按需切到 false。
static PET_INTERACTIVE_FULL: AtomicBool = AtomicBool::new(true);

#[tauri::command]
fn set_interactive_full(full: bool) {
    PET_INTERACTIVE_FULL.store(full, Ordering::Relaxed);
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
    /// 最近一次 panel 事件载荷（panel/schema/data）：面板窗首开时事件已发完，
    /// 窗口挂载后靠 get_current_panel 拉这份缓存补渲染（解首开竞态）。
    last_panel: Option<Value>,
}

impl BrainState {
    fn new() -> Self {
        Self {
            child: None,
            last_pong: Instant::now(),
            seen_pong: false,
            warned: false,
            restarts: 0,
            last_restart: None,
            shutting_down: false,
            last_panel: None,
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

/// 首启引导（仅生产）：备齐 Python 运行时（uv sync，约 300MB）+ 语音模型（234MB）。
/// 幂等：已就绪的部分直接跳过；失败返回错误，前端提示重启重试。
fn ensure_runtime(app: &AppHandle) -> Result<(), String> {
    if !is_prod() {
        return Ok(());
    }
    let home = runtime_root();
    let runtime = home.join("runtime").join("sidecar");
    let venv_py = runtime.join(".venv").join("bin").join("python");
    let resource = app.path().resource_dir().map_err(|e| format!("取资源目录失败：{e}"))?;

    if !venv_py.exists() {
        emit_setup(app, "python", "首次初始化：安装 Python 环境（约 300MB，需联网，几分钟）…");
        copy_dir(&resource.join("sidecar"), &runtime)?;
        let mut cmd = std::process::Command::new(resource.join("bin").join("uv"));
        cmd.arg("sync")
            .arg("--extra")
            .arg("memory")
            .arg("--project")
            .arg(&runtime)
            .env("PYTHONUNBUFFERED", "1");
        run_cmd(cmd, "Python 环境安装")?;
    }

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
                                // panel 事件顺带缓存载荷，供面板窗首开竞态下补拉
                                if payload.get("kind").and_then(|k| k.as_str()) == Some("panel") {
                                    if let Some(p) = payload.get("payload") {
                                        let state = app.state::<Brain>();
                                        state.0.lock().unwrap().last_panel = Some(p.clone());
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

/// 进程掉线统一入口：清槽 → brain-status(down) → 退避重启（退出中则不动）。
async fn on_brain_down(app: AppHandle, detail: Option<String>) {
    {
        let state = app.state::<Brain>();
        let mut g = state.0.lock().unwrap();
        if g.shutting_down {
            return;
        }
        g.child = None;
        g.restarts += 1;
        g.last_restart = Some(Instant::now());
    }
    let mut msg = serde_json::json!({"status": "down"});
    if let Some(d) = detail {
        msg["detail"] = Value::String(d);
    }
    let _ = app.emit("brain-status", msg);
    restart_brain(app).await;
}

/// 退避重启：1s → 2s → 5s → 10s 封顶；失败继续退避重试，永不放弃（常驻 agent）。
async fn restart_brain(app: AppHandle) {
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
    if app.state::<Brain>().0.lock().unwrap().shutting_down {
        return;
    }
    match spawn_brain(&app) {
        Ok((rx, child)) => {
            {
                let state = app.state::<Brain>();
                let mut g = state.0.lock().unwrap();
                g.child = Some(child);
                g.last_pong = Instant::now(); // 给新进程启动留窗口
                g.seen_pong = false; // 启动宽限期内不启用 15s 心跳超时
                g.warned = false;
            }
            spawn_bridge(app.clone(), rx);
        }
        Err(e) => {
            eprintln!("[brain] 重启失败：{e}");
            {
                let state = app.state::<Brain>();
                let mut g = state.0.lock().unwrap();
                g.restarts += 1;
                g.last_restart = Some(Instant::now());
            }
            let _ = app.emit(
                "brain-status",
                serde_json::json!({"status": "down", "detail": e}),
            );
            Box::pin(restart_brain(app)).await;
        }
    }
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
fn run_input(state: tauri::State<Brain>, text: String, surface: Option<String>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "run", "text": text, "surface": surface.unwrap_or_else(|| "pet".into()) }),
    )
}

#[tauri::command]
fn confirm(state: tauri::State<Brain>, confirmation_id: String, approved: bool) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "confirm", "confirmation_id": confirmation_id, "approved": approved }),
    )
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

/// 插件启动器（双击团子）：扫各插件 manifest.toml 拿 id/name。
/// 行级解析、只取首个 section 之前的顶层键（[[tool]] 里也有 id，不能误抓），不引 toml 依赖。
#[tauri::command]
fn list_plugins() -> Result<Vec<Value>, String> {
    let rd = std::fs::read_dir(plugins_dir()).map_err(|e| format!("读插件目录失败：{e}"))?;
    let mut out = Vec::new();
    for entry in rd.flatten() {
        let path = entry.path().join("manifest.toml");
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        let pick = |key: &str| {
            for line in text.lines() {
                let l = line.trim();
                if l.starts_with('[') {
                    break; // 进了 section 就停（顶层键只在文件头）
                }
                if let Some(rest) = l.strip_prefix(key) {
                    if let Some(v) = rest.trim_start().strip_prefix('=') {
                        return Some(v.trim().trim_matches('"').to_string());
                    }
                }
            }
            None
        };
        if let (Some(id), Some(name)) = (pick("id"), pick("name")) {
            out.push(serde_json::json!({ "id": id, "name": name }));
        }
    }
    out.sort_by(|a, b| {
        a["id"].as_str().unwrap_or("").cmp(b["id"].as_str().unwrap_or(""))
    });
    Ok(out)
}

/// 打开/聚焦面板窗：已存在则 show+focus（关闭只是隐藏，状态保留）；
/// 首次用 builder 创建（无装饰+透明与主窗一致，不需 always_on_top），位置取屏幕中央偏右（避开宠物球常驻角）。
/// 注：CloseRequested → hide 由全局 on_window_event 统一拦截（对所有窗生效，面板窗同享）。
#[tauri::command]
fn open_panel_window(app: AppHandle) -> Result<(), String> {
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

/// 面板窗挂载后补拉最近一次的 panel 载荷（首开时 brain-event 先于窗口订阅发出）。
#[tauri::command]
fn get_current_panel(state: tauri::State<Brain>) -> Result<Option<Value>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    Ok(g.last_panel.clone())
}

#[tauri::command]
fn voice_start(state: tauri::State<Brain>, surface: Option<String>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "voice_start", "surface": surface.unwrap_or_else(|| "pet".into()) }),
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

/// 重新检测 macOS 权限（辅助功能/屏幕录制），结果经 brain-permissions 事件回前端。
#[tauri::command]
fn check_permissions(state: tauri::State<Brain>) -> Result<(), String> {
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

/// 鼠标穿透轮询：Tauri v2 的 JS API 无 forward，无法在忽略事件后收到 mousemove 切回；
/// 改由 Rust 侧每 40ms 读全局光标位置，落在团子区/展开窗内 = 可交互，否则 set_ignore_cursor_events(true) 穿透到桌面。
/// ⚠️ device_query 与 Tauri 的坐标单位（macOS 点 vs 物理像素）已按 scale 换算；首次真机需核对团子热区。
#[cfg(desktop)]
fn spawn_click_through(handle: tauri::AppHandle) {
    std::thread::spawn(move || {
        let dev = DeviceState::new();
        let mut last_inside: Option<bool> = None;
        loop {
            let (mx, my) = dev.get_mouse().coords;
            if let Some(win) = handle.get_webview_window("main") {
                let scale = win.scale_factor().unwrap_or(1.0);
                if let (Ok(pos), Ok(size)) = (win.outer_position(), win.outer_size()) {
                    let wx = pos.x as f64 / scale;
                    let wy = pos.y as f64 / scale;
                    let ww = size.width as f64 / scale;
                    let wh = size.height as f64 / scale;
                    let (cx, cy) = (mx as f64, my as f64);
                    // PET_INTERACTIVE_FULL（展开/气泡中）= true → 整窗可交互；
                    // false → 仅团子可视热区（56×56，约贴身体、留小余量；团子中心≈winW-66,56）、其余穿透。
                    // 不用整个 88px 元素框——那圈透明边会拦住桌面点击。
                    let full = PET_INTERACTIVE_FULL.load(Ordering::Relaxed);
                    let inside = if full {
                        cx >= wx && cx <= wx + ww && cy >= wy && cy <= wy + wh
                    } else if mx == 0 && my == 0 {
                        // 读不到光标（多半辅助功能未授权）→ 不穿透，避免团子点不到
                        true
                    } else {
                        cx >= wx + ww - 94.0 && cx <= wx + ww - 38.0
                            && cy >= wy + 28.0 && cy <= wy + 84.0
                    };
                    if last_inside != Some(inside) {
                        let _ = win.set_ignore_cursor_events(!inside);
                        last_inside = Some(inside);
                    }
                }
            }
            std::thread::sleep(std::time::Duration::from_millis(40));
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let shortcuts = tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                if let Some(win) = app.get_webview_window("main") {
                    let _ = if win.is_visible().unwrap_or(false) {
                        win.hide()
                    } else {
                        win.show().and_then(|_| win.set_focus())
                    };
                }
            }
        })
        .build();

    tauri::Builder::default()
        // 单实例：第二个实例拉起时聚焦既有窗口并退出（防多实例 → 多 brain → qdrant 锁互踩）
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show().and_then(|_| w.set_focus());
            }
        }))
        .plugin(shortcuts)
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Brain(Mutex::new(BrainState::new())))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // 桌宠常驻：关窗只隐藏，真正退出走托盘菜单
                api.prevent_close();
                let _ = window.hide();
                if window.label() == "panel" {
                    let _ = window.app_handle().emit("panel-closed", ());
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
                    let _ = win.set_position(tauri::LogicalPosition::new(mx + sw - 360.0 - 24.0, my + 40.0));
                }
                // 启动即显示（conf 里 visible:false 只是避免定位前闪屏，别让用户按热键找宠物）
                let _ = win.show();
            }

            // 注册全局热键：Super+Shift+Y 显隐主窗（macOS 上 Super=Cmd）
            #[cfg(desktop)]
            if let Err(e) = app.global_shortcut().register("Super+Shift+Y") {
                eprintln!("[yibao] 注册热键失败：{e}");
            }

            // 系统托盘：关窗隐藏后靠它重新显示/退出。左键点图标切换显隐，右键菜单。
            let show_item = MenuItem::with_id(app, "show", "显示译宝", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "隐藏译宝", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出译宝", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &hide_item, &quit_item])?;
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
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show().and_then(|_| w.set_focus());
                        }
                    }
                    "hide" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.hide();
                        }
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
                match spawn_brain(&handle) {
                    Ok((rx, child)) => {
                        handle.state::<Brain>().0.lock().unwrap().child = Some(child);
                        spawn_bridge(handle.clone(), rx);
                        spawn_watchdog(handle.clone());
                    }
                    Err(e) => {
                        let _ = handle.emit("setup-error", format!("大脑启动失败：{e}"));
                    }
                }
            });

            // 鼠标穿透轮询（Tauri v2 JS API 无 forward，Rust 侧读全局光标切换 set_ignore_cursor_events）
            #[cfg(desktop)]
            spawn_click_through(app.handle().clone());

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            run_input,
            confirm,
            panel_action,
            list_plugins,
            open_panel_window,
            close_panel_window,
            get_current_panel,
            voice_start,
            interrupt,
            report_panel_context,
            check_permissions,
            prompt_permission,
            set_interactive_full
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
