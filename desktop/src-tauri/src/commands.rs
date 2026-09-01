//! Tauri command 层（按域分组）：文件系统命令 + 全局窗口状态命令。
//! 薄层约定：只做参数解析 + 调底层能力（config / window / brain），业务编排在 lib.rs / services。

use crate::{event_recorder, plugin_manifest, session_db, snip};
use crate::braind::{Brain, runtime_root};
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_opener::OpenerExt;

#[derive(serde::Deserialize)]
pub struct HotRect {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    kind: String,
}

/// 单窗三态热区矩形组上报（idle 只报团子盒，quick 附加热面板；隐藏时 None 清除）。
/// 相对坐标 + Rust 每次判定叠加当前窗口位置，拖动窗口后热区自动跟随。
#[tauri::command]
pub fn set_hot_rects(rects: Option<Vec<HotRect>>) {
    *crate::system::PET_RECTS.lock().unwrap() = rects
        .map(|rs| rs.iter().map(|r| (r.x, r.y, r.w, r.h, r.kind.clone())).collect())
        .unwrap_or_default();
}

/// 前端通知点击穿透模式：true=整窗可交互（展开/气泡中），false=仅团子热区可交互。
#[tauri::command]
pub fn set_interactive_full(full: bool) {
    crate::system::PET_INTERACTIVE_FULL.store(full, std::sync::atomic::Ordering::Relaxed);
}

/// 说话气泡显示中：气泡带成为第二热区（点气泡=展开），不像展开态整窗拦点击。
#[tauri::command]
pub fn set_bubble_on(on: bool) {
    crate::system::PET_BUBBLE_ON.store(on, std::sync::atomic::Ordering::Relaxed);
}

/// 在 Finder 中打开数据目录（.env 配置 / 记忆 / 历史都在这）。
#[tauri::command]
pub fn open_data_dir(app: AppHandle) -> Result<(), String> {
    let dir = runtime_root();
    std::fs::create_dir_all(&dir).map_err(|e| format!("建数据目录失败：{e}"))?;
    app.opener()
        .open_path(dir.to_string_lossy().as_ref(), None::<&str>)
        .map_err(|e| format!("打开数据目录失败：{e}"))
}

/// macOS：transparent 浮窗圆角裁切 + 阴影。transparent 窗体默认方角，webview 矩形内容外露系统背景灰；
/// WKWebView layer cornerRadius + masksToBounds 把内容裁圆角（根除方角内容露灰），
/// NSWindow setCornerRadius + setHasShadow 让窗口外观与内容一致。
/// radius 0 = 不设圆角（snip 全屏框选层等）。
#[cfg(target_os = "macos")]
pub(crate) fn apply_window_chrome(win: &tauri::WebviewWindow, radius: f64) {
    use objc2::runtime::{AnyObject, Bool};
    use objc2::{msg_send, sel};
    use objc2_app_kit::NSView;
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    if let Ok(h) = win.window_handle() {
        if let RawWindowHandle::AppKit(h) = h.as_raw() {
            unsafe {
                let view = &*(h.ns_view.as_ptr().cast::<NSView>());
                let _: () = msg_send![view, setWantsLayer: true];
                if radius > 0.0 {
                    let layer: *mut AnyObject = msg_send![view, layer];
                    let _: () = msg_send![layer, setCornerRadius: radius];
                    let _: () = msg_send![layer, setMasksToBounds: true];
                }
                if let Some(window) = view.window() {
                    if radius > 0.0 {
                        // macOS 26 起 NSWindow 不再响应 setCornerRadius:（圆角已由上面
                        // view 的 layer 裁切承担）。盲发会抛 NSInvalidArgumentException，
                        // 异常穿过 Rust 帧直接 abort——先问再发。
                        let can: Bool = msg_send![&*window, respondsToSelector: sel!(setCornerRadius:)];
                        if can.as_bool() {
                            let _: () = msg_send![&*window, setCornerRadius: radius];
                        }
                    }
                    let _: () = msg_send![&*window, setHasShadow: true];
                }
            }
        }
    }
}

/// 非 macOS 平台：no-op 占位（透明度由 wry/platform 处理）。
#[cfg(not(target_os = "macos"))]
pub(crate) fn apply_window_chrome(_win: &tauri::WebviewWindow, _radius: f64) {}

/// 用系统默认浏览器打开外链（娱乐等插件的「看视频/听音乐」跳转通道）。
/// webview iframe 无 Tauri IPC，面板经 WebviewPanel `native:` 白名单旁路直调本命令。
/// 只放 http/https，防任意 scheme（file:/yibao: 等）被利用。
#[tauri::command]
pub fn open_url(app: AppHandle, url: String) -> Result<(), String> {
    let trimmed = url.trim().to_string();
    let lower = trimmed.to_lowercase();
    if !(lower.starts_with("https://") || lower.starts_with("http://")) {
        return Err("只允许打开 http/https 链接".into());
    }
    if trimmed.len() > 4096 {
        return Err("链接过长".into());
    }
    app.opener()
        .open_url(trimmed.as_str(), None::<&str>)
        .map_err(|e| format!("打开链接失败：{e}"))
}

/// 原生文件夹选择器（coding 面板 cwd 药丸用）：webview iframe 无 Tauri IPC，
/// 面板经 WebviewPanel `native:` 白名单旁路直调本命令，对话框必须 Rust 侧开。
/// 返回所选绝对路径；用户取消返回 None。
/// 必须 async：同步命令内联跑在 macOS 主线程，而 blocking_pick_folder 内部先
/// run_on_main_thread 派发创建对话框、再 recv() 原地阻塞——主线程被堵死则派发永不执行，
/// 硬死锁（点击后整窗卡死、对话框根本不出现）。async 命令跑在异步运行时线程，主线程自由。
/// .parent(&window) 把对话框挂到发起窗口（home/panel），避免被常驻置顶宠物窗压住。
#[tauri::command]
pub async fn pick_folder(window: tauri::Window) -> Result<Option<String>, String> {
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

/// 目录内文件模糊搜索（输入条 @ 引用用）：在指定 cwd 下按文件名模糊匹配。
/// 与 coding 插件完全解耦的原生能力——搜索根由用户用 pick_folder 显式选择并 sticky 记忆，
/// 不依赖任何 coding 会话。限深 6 层、限 200 条、排除依赖/构建/隐藏目录（对齐原 coding.files 语义）。
/// async + spawn_blocking：文件遍历可能涉及大目录，避免阻塞 IPC 线程。
#[tauri::command]
pub async fn search_files(cwd: String, q: String) -> Result<serde_json::Value, String> {
    const EXCLUDE: &[&str] = &[
        ".git", "node_modules", "dist", "target", ".venv", "build", "out",
        "__pycache__", ".next", ".cache",
    ];
    tokio::task::spawn_blocking(move || {
        let root = std::path::PathBuf::from(cwd);
        let query = q.trim().to_lowercase();
        let mut out: Vec<serde_json::Value> = Vec::new();
        if !root.is_dir() {
            return Ok(serde_json::json!({ "files": [] }));
        }
        // 显式栈式 DFS（不随依赖版本走）：depth ≤ 6 层，root 自身 depth=0
        let mut stack: Vec<(std::path::PathBuf, usize)> = vec![(root.clone(), 0)];
        while let Some((dir, depth)) = stack.pop() {
            if depth > 6 || out.len() >= 200 {
                continue;
            }
            let entries = match std::fs::read_dir(&dir) {
                Ok(e) => e,
                Err(_) => continue, // 无权限/不存在子目录跳过，不炸整体
            };
            let mut dirs: Vec<(std::path::PathBuf, usize)> = Vec::new();
            for entry in entries.flatten() {
                let path = entry.path();
                let name = match path.file_name().and_then(|n| n.to_str()) {
                    Some(n) => n.to_string(),
                    None => continue,
                };
                if name.starts_with('.') || EXCLUDE.contains(&name.as_str()) {
                    continue;
                }
                if path.is_dir() {
                    dirs.push((path, depth + 1));
                    continue;
                }
                let rel = match path.strip_prefix(&root) {
                    Ok(r) => r.to_string_lossy().into_owned(),
                    Err(_) => continue,
                };
                if !query.is_empty() && !rel.to_lowercase().contains(&query) {
                    continue;
                }
                out.push(serde_json::json!({ "rel": rel }));
                if out.len() >= 200 {
                    break;
                }
            }
            // 目录入栈：先收集后推栈，保证同级顺序稳定（可选；数量有限无顺序要求）
            stack.extend(dirs.into_iter().rev());
        }
        Ok(serde_json::json!({ "files": out }))
    })
    .await
    .map_err(|e| format!("搜索文件失败：{e}"))?
}

/// 粘贴图片/附件落盘（输入条 chips 化）：base64 → runtime_root()/attachments/<毫秒>-<rand>.<ext>，
/// 返回绝对路径。InputBar paste 直调；coding iframe 经 WebviewPanel `native:` 白名单旁路。
/// 同步命令即可：纯内存解码 + 一次小文件写，无对话框无主线程阻塞（对照 pick_folder 必须 async 的注释）。
#[tauri::command]
pub fn save_attachment(data: String, ext: String) -> Result<String, String> {
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data.trim())
        .map_err(|e| format!("附件 base64 解码失败：{e}"))?;
    if bytes.len() > 20 * 1024 * 1024 {
        return Err("附件过大（>20MB）".into());
    }
    // ext 白名单化：只留 ASCII 字母数字，防路径注入；空则按 png
    let ext: String = ext.chars().filter(|c| c.is_ascii_alphanumeric()).take(8).collect();
    let ext = if ext.is_empty() { "png".to_string() } else { ext };
    let dir = runtime_root().join("attachments");
    std::fs::create_dir_all(&dir).map_err(|e| format!("建附件目录失败：{e}"))?;
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let rand: String = uuid::Uuid::new_v4().simple().to_string().chars().take(6).collect();
    let path = dir.join(format!("att-{millis}-{rand}.{ext}"));
    std::fs::write(&path, bytes).map_err(|e| format!("写附件失败：{e}"))?;
    Ok(path.to_string_lossy().into_owned())
}

/// 保存文件到用户指定位置（图片转 PDF 等面板产物落盘）：base64 → 保存对话框选位 → 写文件。
/// webview iframe 无 Tauri IPC，面板经 WebviewPanel `native:` 白名单旁路直调本命令；
/// 对话框必须 Rust 侧开（对照 pick_folder 的 async 死锁注释：blocking_save_file 同样
/// 内部 run_on_main_thread + 原地阻塞，必须 async 跑在异步线程上）。
/// 用户取消返回 None。
#[tauri::command]
pub async fn save_file(
    window: tauri::Window,
    data: String,
    default_name: String,
) -> Result<Option<String>, String> {
    use base64::Engine;
    use tauri_plugin_dialog::DialogExt;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data.trim())
        .map_err(|e| format!("文件 base64 解码失败：{e}"))?;
    if bytes.len() > 50 * 1024 * 1024 {
        return Err("文件过大（>50MB）".into());
    }
    // 默认名只取文件名部分（去路径分隔符），防路径注入；空则兜底 untitled
    let name = default_name
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or("untitled")
        .to_string();
    let name = if name.is_empty() { "untitled".to_string() } else { name };
    let file = window
        .app_handle()
        .dialog()
        .file()
        .set_title("保存文件")
        .set_parent(&window)
        .set_file_name(&name)
        .blocking_save_file();
    let Some(path) = file.and_then(|p| p.into_path().ok()) else {
        return Ok(None); // 用户取消
    };
    std::fs::write(&path, &bytes).map_err(|e| format!("写文件失败：{e}"))?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

/// ---------- window 域命令 ----------

/// 打开/聚焦大窗（home）：与小窗互斥——藏宠物窗；面板浮窗若开着也临时藏起
///（记 panel_hidden_by_home，关大窗时还原）。宠物窗 header「扩充」钮与托盘「设置…」共用本命令。
#[tauri::command]
pub fn open_home_window(app: AppHandle) -> Result<(), String> {
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
pub(crate) fn restore_after_home(app: &AppHandle) {
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
pub fn close_home_window(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("home") {
        win.hide().map_err(|e| e.to_string())?;
    }
    restore_after_home(&app);
    Ok(())
}

/// 打开/聚焦面板窗：已存在则 show+focus（关闭只是隐藏，状态保留）；
/// 首次用 builder 创建（无装饰+透明与主窗一致，不需 always_on_top），位置取屏幕中央偏右。
/// 大窗模式下不弹浮窗：面板嵌入大窗主区渲染（panel 事件大窗同样收到）。
#[tauri::command]
pub fn open_panel_window(app: AppHandle) -> Result<(), String> {
    show_panel_window_impl(&app, false)
}

/// 面板窗 show + focus + emit panel-shown（open_panel_window 与 braind 兜底共用）。
/// force=true 跳过大窗守卫：宠物窗对话（surface=pet）触发的面板事件必弹浮窗——
/// 用户注意力在小窗，大窗内嵌渲染用户看不见（「说了打开却没弹」的根）。
pub fn show_panel_window_impl(app: &AppHandle, force: bool) -> Result<(), String> {
    if !force {
        if let Some(home) = app.get_webview_window("home") {
            if home.is_visible().unwrap_or(false) {
                return Ok(()); // 大窗模式不弹浮窗
            }
        }
    }
    if let Some(win) = app.get_webview_window("panel") {
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())?;
        // show 成功→通知前端面板窗重推最新 init 数据（解决「收起后再次打开时 iframe
        // 停在旧数据」：隐藏期间 WebviewPanel 的 postMessage 可能因 WKWebView 挂起丢失）
        let _ = app.emit("panel-shown", ());
        return Ok(());
    }
    let win =
        tauri::WebviewWindowBuilder::new(app, "panel", tauri::WebviewUrl::App("panel.html".into()))
            .title("译宝面板")
            .transparent(true)
            .decorations(false)
            .inner_size(780.0, 580.0)
            .build()
            .map_err(|e| format!("创建面板窗失败：{e}"))?;
    // macOS：transparent 窗体默认方角，圆角内容外会露出系统背景灰——原生裁圆角 + 开阴影。
    #[cfg(target_os = "macos")]
    crate::commands::apply_window_chrome(&win, 18.0);
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
    // 新建面板窗同样发：前端 onMounted 时 iframe 已 load，事件保险无意义但保持一致
    let _ = app.emit("panel-shown", ());
    Ok(())
}

/// 关闭面板窗 = 隐藏（不销毁，保状态、二次打开快）。
/// 顺带广播 panel-closed：宠物窗靠它给「⇢ 协作中」的气泡收尾（⇠ 协作结束）。
#[tauri::command]
pub fn close_panel_window(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("panel") {
        win.hide().map_err(|e| e.to_string())?;
        let _ = app.emit("panel-closed", ());
    }
    Ok(())
}

/// 隐藏唤起条（主窗处理完 invoke-action 后兜底调用；条本身点击/Esc/blur 已自隐，幂等）。
#[tauri::command]
pub fn hide_invoke_bar(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("invoke-bar") {
        win.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// 宠物窗展开态（前端同步）：全局热键据此在 Rust 侧决定 显示/展开/隐藏，显隐不经过前端。
#[derive(Default)]
pub(crate) struct PetExpanded(pub(crate) std::sync::atomic::AtomicBool);

/// 宠物窗展开态同步（前端 set_pet_expanded 命令）。
#[tauri::command]
pub fn set_pet_expanded(state: tauri::State<PetExpanded>, expanded: bool) {
    state
        .0
        .store(expanded, std::sync::atomic::Ordering::Relaxed);
}

/// ---------- brain 命令域 ----------
/// 薄命令层：参数解析 → 经 write_to_brain 发大脑（回包经对应 brain-* 事件广播）。

pub fn write_to_brain(state: &Brain, msg: Value) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    let child = guard.child.as_mut().ok_or("大脑不在线（重启中）")?;
    let line = format!("{}\n", msg);
    child.write(line.as_bytes()).map_err(|e| e.to_string())
}

/// 向大脑发无参命令（id=0：一次性查询/动作，回包经对应事件回）。
/// 契约化辅助：type 字符串是协议面，收敛到一处声明，调用点不再手拼 json。
pub fn brain_cmd(state: &Brain, cmd: &str) -> Result<(), String> {
    write_to_brain(state, serde_json::json!({ "id": 0, "type": cmd }))
}

/// 向大脑发带载荷命令：payload 合并进 {id, type}（字段名保持 snake_case 协议面）。
pub fn brain_cmd_with(state: &Brain, cmd: &str, payload: Value) -> Result<(), String> {
    let mut msg = serde_json::json!({ "id": 0, "type": cmd });
    if let (Some(m), Some(p)) = (msg.as_object_mut(), payload.as_object()) {
        for (k, v) in p {
            m.insert(k.clone(), v.clone());
        }
    }
    write_to_brain(state, msg)
}


#[tauri::command]
pub fn run_input(
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
    brain_cmd_with(
        &state,
        "run",
        serde_json::json!({
            "text": text, "surface": surface_str,
            "conversation_id": conversation_id.unwrap_or_default(),
        }),
    )
}

/// 截图唤起（v1.1）：⌘⇧Y 唤起主窗时前端触发，通知大脑抓屏描述（下次 run 注入屏幕上下文）。
#[tauri::command]
pub fn invoke_context(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "invoke_context")
}

/// 批量确认条目（前端 Task 5 传 camelCase：{id, approved, remember}）。
/// id = confirmation_needed 事件里 action.id（单条时等于 confirmation_id）。
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConfirmItem {
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
pub fn confirm_batch(state: tauri::State<Brain>, items: Vec<ConfirmItem>) -> Result<(), String> {
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
    brain_cmd_with(&state, "confirm_batch", serde_json::json!({ "items": items_json }))
}

/// 主屏 Feed 查询：大脑回 {"type":"feed","items":…,"stats":…}，经 brain-feed 事件广播。
#[tauri::command]
pub fn get_feed(state: tauri::State<Brain>, limit: Option<u32>) -> Result<(), String> {
    brain_cmd_with(&state, "feed", serde_json::json!({ "limit": limit.unwrap_or(60) }))
}

/// 手动触发昨日提炼：大脑回 {"type":"distill_now","ok":…,"result":…}，经 brain-distill-now 事件广播。
#[tauri::command]
pub fn distill_now(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "distill_now")
}

/// 设置页信任统计：大脑回 {"type":"feed_stats","stats":…}，经 brain-feed-stats 事件广播。
#[tauri::command]
pub fn get_feed_stats(state: tauri::State<Brain>, days: Option<u32>) -> Result<(), String> {
    brain_cmd_with(&state, "feed_stats", serde_json::json!({ "days": days.unwrap_or(7) }))
}

/// 晨间反刍探测：开窗时前端调用，大脑自行决定推不推（fire-and-forget）。
#[tauri::command]
pub fn recap_check(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "recap_check")
}

/// 每日回顾查询：大脑回 {"type":"distill_timeline","days":[…]}，经 brain-distill-timeline 事件广播。
#[tauri::command]
pub fn get_distill_timeline(state: tauri::State<Brain>, days: Option<u32>) -> Result<(), String> {
    brain_cmd_with(&state, "distill_timeline", serde_json::json!({ "days": days.unwrap_or(14) }))
}

/// 主屏 widget 查询：大脑回 {"type":"widgets","widgets":…}，经 brain-widgets 事件广播。
#[tauri::command]
pub fn get_widgets(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "widgets")
}

/// 主屏 Feed：点掉单条（大脑回 {"type":"feed_marked_read","id":N,"ok":bool}
/// 经 brain-feed-marked-read 广播）。
/// 注：sidecar 直接读 msg["id"] 作 feed 条目 id（非信封序号），故不写 "id":0 占位。
#[tauri::command]
pub fn feed_mark_read(state: tauri::State<Brain>, id: i64) -> Result<(), String> {
    // id 是业务字段（feed 条目 id），payload 覆盖辅助函数的 id=0 占位
    brain_cmd_with(&state, "feed_mark_read", serde_json::json!({ "id": id }))
}

/// 主屏 Feed：全部已读（大脑回 {"type":"feed_all_read","n":N} 经 brain-feed-all-read 广播）。
#[tauri::command]
pub fn feed_mark_all_read(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "feed_mark_all_read")
}

/// 主屏 Feed：处置态（follow/ignore/none，与 read 正交）。前端走乐观更新。
#[tauri::command]
pub fn feed_mark_status(
    state: tauri::State<Brain>,
    id: i64,
    status: String,
) -> Result<(), String> {
    brain_cmd_with(&state, "feed_mark_status", serde_json::json!({ "id": id, "status": status }))
}

/// 误报反馈（信任仪表写侧）：👍/👎 落 meta.feedback（大脑回 {"type":"feed_feedback_set",…}
/// 经 brain-feed-feedback-set 广播）。id 是业务字段（feed 条目 id），覆盖辅助函数的 id=0 占位。
#[tauri::command]
pub fn feed_feedback(state: tauri::State<Brain>, id: i64, feedback: String) -> Result<(), String> {
    brain_cmd_with(&state, "feed_feedback", serde_json::json!({ "id": id, "feedback": feedback }))
}

/// 主屏 Dock 查询：pinned 优先 + 频率补齐（回 {"type":"dock_list","dock":[...]}
/// 经 brain-dock-list 广播）。
#[tauri::command]
pub fn dock_list(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "dock_list")
}

/// 主屏 Dock：固定/取消固定（回 {"type":"dock_pin_set","pid":...,"ok":bool,"dock":[...]}
/// 经 brain-dock-pin-set 广播）。
#[tauri::command]
pub fn set_dock_pin(state: tauri::State<Brain>, pid: String, on: bool) -> Result<(), String> {
    brain_cmd_with(&state, "set_dock_pin", serde_json::json!({ "pid": pid, "on": on }))
}

/// 记忆管理：列出全部记忆（回 {"type":"mem_list"} 经 brain-mem-list 广播）。
#[tauri::command]
pub fn get_mem_list(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "mem_list")
}

/// 记忆管理：按 id 删除一条（回 {"type":"mem_deleted"} 经 brain-mem-deleted 广播）。
#[tauri::command]
pub fn mem_delete(state: tauri::State<Brain>, id: String) -> Result<(), String> {
    brain_cmd_with(&state, "mem_delete", serde_json::json!({ "mem_id": id }))
}

/// 记忆管理：按 id 编辑文本（回 {"type":"mem_edited"} 经 brain-mem-edited 广播）。
#[tauri::command]
pub fn mem_edit(state: tauri::State<Brain>, id: String, text: String) -> Result<(), String> {
    brain_cmd_with(&state, "mem_edit", serde_json::json!({ "mem_id": id, "text": text }))
}

/// Workspace façade：列表 + 当前 Session 绑定（conversation_id 为空时兼容旧全局语境）。
#[tauri::command]
pub fn get_projects(
    state: tauri::State<Brain>,
    conversation_id: Option<String>,
) -> Result<(), String> {
    brain_cmd_with(
        &state,
        "projects",
        serde_json::json!({ "conversation_id": conversation_id.unwrap_or_default() }),
    )
}

/// 项目实体：新建（回 {"type":"project_created","ok":...} 经 brain-project-created 广播，带最新视图）。
#[tauri::command]
pub fn project_create(
    state: tauri::State<Brain>,
    name: String,
    conversation_id: Option<String>,
) -> Result<(), String> {
    brain_cmd_with(&state, "project_create", serde_json::json!({
        "name": name,
        "conversation_id": conversation_id.unwrap_or_default(),
    }))
}

/// 项目实体：切换（id 或名字；回 project_switched 经 brain-project-switched 广播，带最新视图）。
#[tauri::command]
pub fn project_switch(
    state: tauri::State<Brain>,
    id: String,
    conversation_id: Option<String>,
) -> Result<(), String> {
    brain_cmd_with(&state, "project_switch", serde_json::json!({
        "id": id,
        "conversation_id": conversation_id.unwrap_or_default(),
    }))
}

/// 项目实体：挂对象进项目（id 省略=当前项目；成功时 sidecar 直接广播 projects 视图，
/// 失败回 project_object_added ok:false 经 brain-project-object-added 广播）。
/// 注：ref 是协议字段名，Rust 侧用 r#ref 转义关键字。
#[tauri::command]
pub fn project_add_object(
    state: tauri::State<Brain>,
    obj_type: String,
    r#ref: String,
    id: Option<String>,
    conversation_id: Option<String>,
) -> Result<(), String> {
    brain_cmd_with(
        &state,
        "project_add_object",
        serde_json::json!({
            "id": id,
            "obj_type": obj_type,
            "ref": r#ref,
            "conversation_id": conversation_id.unwrap_or_default(),
        }),
    )
}

/// 项目实体：从项目摘除对象（同上：成功走 brain-projects 广播，
/// 失败回 project_object_removed ok:false 经 brain-project-object-removed 广播）。
#[tauri::command]
pub fn project_remove_object(
    state: tauri::State<Brain>,
    obj_type: String,
    r#ref: String,
    id: Option<String>,
    conversation_id: Option<String>,
) -> Result<(), String> {
    brain_cmd_with(
        &state,
        "project_remove_object",
        serde_json::json!({
            "id": id,
            "obj_type": obj_type,
            "ref": r#ref,
            "conversation_id": conversation_id.unwrap_or_default(),
        }),
    )
}

/// 用户设置查询（回 {"type":"settings"} 经 brain-settings 广播）。
#[tauri::command]
pub fn get_settings(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "settings_get")
}

/// 手机伴生端配对信息（回 {"type":"http_pair_info"} 经 brain-http-pair-info 广播）。
#[tauri::command]
pub fn get_http_pair_info(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "http_pair_info")
}

/// 用户设置写入（仅已知键生效；回 {"type":"settings"} 经 brain-settings 广播）。
#[tauri::command]
pub fn set_settings(state: tauri::State<Brain>, values: serde_json::Value) -> Result<(), String> {
    brain_cmd_with(&state, "settings_set", serde_json::json!({ "values": values }))
}

/// 面板事件上行（design §3 事件通道）：iframe 经 yibao.emitEvent 上报（如 zimeiti 选区变化），
/// sidecar 按 api.toml [[event]] 白名单校验后路由（surface_result 协议保留名除外）。
#[tauri::command]
pub fn panel_event(
    state: tauri::State<Brain>,
    panel: String,
    name: String,
    payload: serde_json::Value,
) -> Result<(), String> {
    brain_cmd_with(&state, "panel_event", serde_json::json!({ "panel": panel, "name": name, "payload": payload }))
}

/// surface 命令回执（design §3 表面层 tool 化）：前端代为执行 editor.* 等命令后，
/// 把结果按 sid 送回 sidecar 的 SurfaceBridge，tool 调用随之收敛。
#[tauri::command]
pub fn surface_result(
    state: tauri::State<Brain>,
    sid: String,
    ok: bool,
    result: Option<serde_json::Value>,
    error: Option<String>,
) -> Result<(), String> {
    brain_cmd_with(
        &state,
        "surface_result",
        serde_json::json!({ "sid": sid, "ok": ok, "result": result, "error": error }),
    )
}

/// 感知日志：分页查询（回 perception，经 brain-perception 广播）。
#[tauri::command]
pub fn get_perception(
    state: tauri::State<Brain>,
    limit: Option<u32>,
    before_id: Option<i64>,
) -> Result<(), String> {
    brain_cmd_with(
        &state,
        "perception_list",
        serde_json::json!({
            "limit": limit.unwrap_or(60),
            "before_id": before_id,
        }),
    )
}

/// 感知日志：按观察 id 删除（信封 id 已占用，sidecar 字段必须是 per_id）。
#[tauri::command]
pub fn perception_delete(state: tauri::State<Brain>, id: i64) -> Result<(), String> {
    brain_cmd_with(&state, "perception_delete", serde_json::json!({ "per_id": id }))
}

/// 感知日志：清空全部观察。
#[tauri::command]
pub fn perception_clear(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "perception_clear")
}

/// 面板动作（v2 §7）：壳不懂 panel 语义，透传 api.toml 白名单方法给大脑裁决。
#[tauri::command]
pub fn panel_action(
    state: tauri::State<Brain>,
    id: i64,
    method: String,
    params: Value,
    surface: Option<String>,
) -> Result<(), String> {
    brain_cmd_with(
        &state,
        "panel_action",
        serde_json::json!({ "id": id, "method": method, "params": params, "surface": surface.unwrap_or_else(|| "pet".into()) }),
    )
}

/// 插件目录：YIBAO_PLUGINS_DIR 优先，否则 <repo>/plugins（与 brain 加载同源）。
pub fn plugins_dir() -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("YIBAO_PLUGINS_DIR") {
        return std::path::PathBuf::from(dir);
    }
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("plugins")
}

/// 插件启动器（双击团子）：扫各插件 manifest.toml 拿 id/name + 面板级入口（带 open 的 [[panel]]）。
/// 附带 /命令 用的插件动作（api.toml 里 command = true 的 direct 方法）。
#[tauri::command]
pub fn list_plugins() -> Result<Vec<Value>, String> {
    let rd = std::fs::read_dir(plugins_dir()).map_err(|e| format!("读插件目录失败：{e}"))?;
    let mut out = Vec::new();
    for entry in rd.flatten() {
        let path = entry.path().join("manifest.toml");
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        if let Some((id, name, panels)) = plugin_manifest::parse_manifest(&text) {
            let mut item = serde_json::json!({ "id": id, "name": name, "panels": panels });
            let api_path = entry.path().join("api.toml");
            let cmds = match std::fs::read_to_string(&api_path) {
                Ok(api_text) => plugin_manifest::parse_api_commands(&api_text),
                Err(_) => Vec::new(),
            };
            item["commands"] = serde_json::Value::Array(cmds);
            out.push(item);
        }
    }
    out.sort_by(|a, b| {
        a["id"].as_str().unwrap_or("").cmp(b["id"].as_str().unwrap_or(""))
    });
    Ok(out)
}

#[derive(serde::Deserialize)]
pub struct SnipRect {
    left: f64,
    top: f64,
    width: f64,
    height: f64,
}

/// 框选完成：隐藏 overlay → 逻辑选区换算物理像素 → 通知大脑区域截图 → 广播 snip-captured 让主窗展开。
#[tauri::command]
pub fn finish_snip(app: AppHandle, state: tauri::State<Brain>, rect: SnipRect) -> Result<(), String> {
    if let Some(snip) = app.get_webview_window("snip") {
        let _ = snip.hide();
        if let Ok(Some(mon)) = snip.current_monitor() {
            let scale = mon.scale_factor();
            let origin = (mon.position().x as i64, mon.position().y as i64);
            let (l, t, w, h) = snip::snip_abs_rect((rect.left, rect.top, rect.width, rect.height), origin, scale);
            brain_cmd_with(
                &state,
                "snip_capture",
                serde_json::json!({
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
pub fn cancel_snip(app: AppHandle) -> Result<(), String> {
    if let Some(snip) = app.get_webview_window("snip") {
        let _ = snip.hide();
    }
    Ok(())
}

/// 截图即问：问题转发大脑（暂存的区域截图 + 问题 → vision 直答）。
#[tauri::command]
pub fn vision_query(state: tauri::State<Brain>, question: String) -> Result<(), String> {
    brain_cmd_with(&state, "vision_query", serde_json::json!({ "question": question }))
}

/// 面板窗挂载后补拉最近一次的 panel 载荷（首开时 brain-event 先于窗口订阅发出）。
#[tauri::command]
pub fn get_current_panel(state: tauri::State<Brain>) -> Result<Option<Value>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    Ok(g.last_panel.clone())
}

/// 面板载荷回填：大窗从 localStorage 快照恢复工作面后，把同一份面板数据写回
/// last_panel（内存缓存）。last_panel 是内存态，重启即失——前端快照是持久层，
/// 恢复时回填保证面板窗/宠物窗在竞态下也能补拉到同一份数据（多窗一致）。
#[tauri::command]
pub fn remember_panel(state: tauri::State<Brain>, payload: Value) -> Result<(), String> {
    let mut g = state.0.lock().map_err(|e| e.to_string())?;
    g.last_panel = Some(payload);
    Ok(())
}

/// 读取 sidecar 已持久化的近期会话，供主屏恢复协作时间线。
/// 这是只读 UI 投影：不把历史重新送回大脑，也不会重放任何插件动作。
#[tauri::command]
pub fn get_conversation_history(limit: Option<usize>) -> Result<Vec<Value>, String> {
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
pub fn list_conversations(state: tauri::State<Brain>) -> Result<Vec<session_db::ConversationMeta>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    match g.session_db.as_ref() {
        Some(db) => db.list_conversations(),
        None => Ok(Vec::new()),
    }
}

/// 拉取某会话的消息（恢复用；只读，不重放任何动作）。
#[tauri::command]
pub fn get_conversation_messages(
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
pub fn create_conversation(
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
pub fn set_active_conversation(
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
pub fn get_active_conversation(state: tauri::State<Brain>) -> Result<Option<String>, String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    match g.session_db.as_ref() {
        Some(db) => db.get_active_conversation(),
        None => Ok(None),
    }
}

/// 确保存在活跃会话：无则新建并设为活跃（大窗首启直接输入时兜底）。
/// 大窗专用；小窗走 ensure_pet_conversation（固定会话，不镜像活跃会话）。
#[tauri::command]
pub fn ensure_active_conversation(
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
pub fn ensure_pet_conversation(
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
pub fn update_conversation_title(state: tauri::State<Brain>, id: String, title: String) -> Result<(), String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(db) = g.session_db.as_ref() {
        db.update_conversation_title(&id, &title, session_db::now_ms())?;
    }
    Ok(())
}

/// 删除会话（级联清消息；若删的是活跃会话则清指针）。
#[tauri::command]
pub fn delete_conversation(state: tauri::State<Brain>, id: String) -> Result<(), String> {
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
pub fn clear_conversations(state: tauri::State<Brain>) -> Result<(), String> {
    let g = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(db) = g.session_db.as_ref() {
        db.clear_all()?;
    }
    Ok(())
}

/// 截断会话到前 keep_count 条（重新生成/编辑重发：其后对话作废）。
#[tauri::command]
pub fn truncate_conversation_messages(
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
pub fn voice_start(
    state: tauri::State<Brain>,
    surface: Option<String>,
    continuous: Option<bool>,
    conversation_id: Option<String>,
) -> Result<(), String> {
    brain_cmd_with(
        &state,
        "voice_start",
        serde_json::json!({
            "surface": surface.unwrap_or_else(|| "pet".into()),
            "continuous": continuous.unwrap_or(false),
            "conversation_id": conversation_id.unwrap_or_default(),
        }),
    )
}

/// 打断进行中的生成/播报（Plan 4b 三连取消：停 TTS + 终止 LLM + 清队列）。
/// 并发对话（spec §E）：带 conversation_id → 只打断该会话槽；不带/空 → 全停（旧行为）。
#[tauri::command]
pub fn interrupt(state: tauri::State<Brain>, conversation_id: Option<String>) -> Result<(), String> {
    brain_cmd_with(
        &state,
        "interrupt",
        serde_json::json!({ "conversation_id": conversation_id.unwrap_or_default() }),
    )
}

/// 面板焦点上报（v2 §5 focus）：壳面板窗内容变化时透传给大脑，run 时注入 LLM 上下文。
#[tauri::command]
pub fn report_panel_context(state: tauri::State<Brain>, focus: Value) -> Result<(), String> {
    brain_cmd_with(&state, "panel_context", serde_json::json!({ "focus": focus }))
}

/// 重新检测 macOS 权限（辅助功能/屏幕录制），结果经 brain-permissions 事件回前端。
#[tauri::command]
pub fn check_permissions(state: tauri::State<Brain>) -> Result<(), String> {
    brain_cmd(&state, "check_permissions")
}

/// 触发系统授权引导弹窗（which = "ax" | "screen"）。
#[tauri::command]
pub fn prompt_permission(state: tauri::State<Brain>, which: String) -> Result<(), String> {
    brain_cmd_with(&state, "prompt_permission", serde_json::json!({ "which": which }))
}
