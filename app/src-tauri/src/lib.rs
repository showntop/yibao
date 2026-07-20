// 译宝桌面壳：拉起 Python 大脑 sidecar + stdio 桥 + 守护（崩溃重启/看门狗）+ 全局热键 + 输入/确认命令。
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

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

/// 解析 sidecar 目录：优先 `YIBAO_SIDECAR_DIR`，否则 dev 默认 <repo>/sidecar。
/// 生产期应改为 PyInstaller externalBin（见 Plan 2 Task B4）。
fn sidecar_dir() -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("YIBAO_SIDECAR_DIR") {
        return std::path::PathBuf::from(dir);
    }
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("sidecar")
}

/// 拉起 Python sidecar。
/// dev：sidecar/.venv/bin/python（绝对路径，避免 GUI 应用 PATH 缺失）
/// 回退：uv run（依赖 PATH 能找到 uv）
fn spawn_brain(
    app: &AppHandle,
) -> Result<(tauri::async_runtime::Receiver<CommandEvent>, CommandChild), String> {
    let dir = sidecar_dir();
    let python = dir.join(".venv").join("bin").join("python");
    let spawn_result = if python.exists() {
        app.shell()
            .command(python.to_string_lossy().to_string())
            .args(["-u", "-m", "yibao_brain.server"])
            .current_dir(&dir)
            .env("PYTHONUNBUFFERED", "1")
            .spawn()
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
                                let payload = v.get("event").cloned().unwrap_or(Value::Null);
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
fn run_input(state: tauri::State<Brain>, text: String) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "run", "text": text }),
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
) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": id, "type": "panel_action", "method": method, "params": params }),
    )
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
#[tauri::command]
fn close_panel_window(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("panel") {
        win.hide().map_err(|e| e.to_string())?;
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
fn voice_start(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "voice_start" }))
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
        .plugin(shortcuts)
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Brain(Mutex::new(BrainState::new())))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // 桌宠常驻：关窗只隐藏，真正退出走托盘菜单
                api.prevent_close();
                let _ = window.hide();
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
                    let _ = win.set_position(tauri::LogicalPosition::new(mx + sw - 132.0 - 24.0, my + 40.0));
                }
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
            let tray_img = tauri::image::Image::from_bytes(include_bytes!("../icons/icon.png"))
                .expect("加载托盘图标失败");
            TrayIconBuilder::with_id("main-tray")
                .icon(tray_img)
                .icon_as_template(false)
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

            // 拉起 Python sidecar + 守护（stdout 桥管重启、看门狗管僵死）
            let (rx, child) = spawn_brain(&app.handle())?;
            app.state::<Brain>().0.lock().unwrap().child = Some(child);
            spawn_bridge(app.handle().clone(), rx);
            spawn_watchdog(app.handle().clone());

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            run_input,
            confirm,
            panel_action,
            open_panel_window,
            close_panel_window,
            get_current_panel,
            voice_start,
            interrupt,
            report_panel_context,
            check_permissions,
            prompt_permission
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
