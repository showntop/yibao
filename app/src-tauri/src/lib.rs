// 译宝桌面壳：拉起 Python 大脑 sidecar + stdio 桥 + 全局热键 + 输入/确认命令。
use std::sync::Mutex;

use serde_json::Value;
use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// sidecar 子进程句柄（用来写 stdin：run / confirm）。
struct Brain(Mutex<Option<CommandChild>>);

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

fn write_to_brain(state: &Brain, msg: Value) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(child) = guard.as_mut() {
        let line = format!("{}\n", msg);
        child.write(line.as_bytes()).map_err(|e| e.to_string())?;
    }
    Ok(())
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
        .manage(Brain(Mutex::new(None)))
        .setup(|app| {
            // 注册全局热键：Super+Shift+Y 显隐主窗（macOS 上 Super=Cmd）
            #[cfg(desktop)]
            if let Err(e) = app.global_shortcut().register("Super+Shift+Y") {
                eprintln!("[yibao] 注册热键失败：{e}");
            }

            // 拉起 Python sidecar。
            // dev：sidecar/.venv/bin/python（绝对路径，避免 GUI 应用 PATH 缺失）
            // 回退：uv run（依赖 PATH 能找到 uv）
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
            let (mut rx, child) =
                spawn_result.map_err(|e| format!("拉起 sidecar 失败：{e}"))?;
            app.state::<Brain>().0.lock().unwrap().replace(child);

            // stdio 桥：sidecar stdout 的行分隔 JSON → Tauri 事件
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
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
                                        let _ = app_handle.emit("brain-event", payload);
                                    }
                                    Some("run_done") => {
                                        let _ = app_handle.emit("brain-run-done", v);
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
                            break;
                        }
                        _ => {}
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![run_input, confirm])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
