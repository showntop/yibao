// 译宝桌面壳（装配层）：Tauri Builder/setup/托盘/全局热键注册 + 各域模块组装。
// 域模块：commands（业务命令薄层）/ braind（sidecar 守护）/ setup_config（配置）/ system（系统集成）/ snip / plugin_* / session_db / event_recorder。
pub mod event_recorder;
mod plugin_manifest;
mod plugin_proto;
pub mod session_db;
mod snip;
mod commands;
pub(crate) mod braind;
pub(crate) mod setup_config;
pub mod system;

use std::sync::Mutex;

use braind::{boot_brain, ensure_runtime, restart_brain, clear_brain_data, Brain};
use setup_config::{get_setup_config, save_setup_config};
use system::{expand_chat, grab_selected_text, reveal_app_in_finder, set_main_size, spawn_click_through, start_snip};

use commands::{
    close_home_window, close_panel_window, hide_invoke_bar, open_data_dir, open_home_window,
    open_panel_window, open_url, pick_folder, restore_after_home, save_attachment, save_file,
    search_files,
    set_bubble_on, set_hot_rects, set_interactive_full, set_pet_expanded, run_input,
    invoke_context,
    confirm_batch,
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
    get_projects,
    open_artifacts,
    project_create,
    project_switch,
    project_add_object,
    project_remove_object,
    durable_cancel,
    durable_resume,
    get_settings,
    get_http_pair_info,
    set_settings,
    panel_event,
    surface_result,
    get_perception,
    perception_delete,
    perception_clear,
    panel_action,
    plugins_dir,
    list_plugins,
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
    prompt_permission,
    check_permissions,
    PetExpanded,
};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
#[cfg(desktop)]
use device_query::DeviceQuery;

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
                                let (bx, by) = snip::clamp_bar_pos(cmx as f64, cmy as f64, 328.0, 56.0, mon_rect);
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
                    let _ = crate::system::open_snip_overlay(&handle);
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
        // module 面板静态资源协议(R4 插件运行时):yibao-plugin://<pid>/<path>
        .register_uri_scheme_protocol("yibao-plugin", |_ctx, request| {
            plugin_proto::handle(&request, &plugins_dir())
        })
        // 开机启动（macOS LaunchAgent）；前端直接调插件 API（enable/disable/isEnabled）
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None::<Vec<&str>>,
        ))
        .manage(Brain(Mutex::new(braind::BrainState::new())))
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
            // plugins_dir() 的 CARGO_MANIFEST_DIR 相对路径在 prod 是构建机残留:
            // bundle.resources 打进资源目录的 plugins 才是真相。
            // 注意顺序(合并后实测事故):dev 下 Tauri 也会把 resources 同步进 target/debug/plugins,
            // 且拷贝只增不删——改名/删除的插件文件在里面留尸(如 codex_reader.py),
            // 本进程 set_var 后 brain 子进程继承,吃到陈旧插件树直接加载失败。
            // 故 dev(仓库 plugins 目录存在)绝不指 bundled,prod(构建机路径落空)才用 Resources。
            if std::env::var("YIBAO_PLUGINS_DIR").is_err() {
                let repo = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("..")
                    .join("..")
                    .join("plugins");
                if !repo.is_dir() {
                    if let Ok(rd) = app.path().resource_dir() {
                        let bundled = rd.join("plugins");
                        if bundled.is_dir() {
                            std::env::set_var("YIBAO_PLUGINS_DIR", bundled);
                        }
                    }
                }
            }
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
            // 本地真机验收入口：显式设置时直接开大窗，默认启动行为完全不变。
            // 避免靠浏览器或自动点托盘才能检查 Home / Stage 响应式效果。
            if std::env::var_os("YIBAO_DEV_SHOW_HOME").is_some() {
                let qa_width = 1320.0;
                let qa_height = 820.0;
                let _ = home.set_size(tauri::LogicalSize::new(qa_width, qa_height));
                if let Ok(Some(mon)) = home.current_monitor() {
                    let s = mon.scale_factor();
                    let mx = mon.position().x as f64 / s;
                    let my = mon.position().y as f64 / s;
                    let sw = mon.size().width as f64 / s;
                    let sh = mon.size().height as f64 / s;
                    let _ = home.set_position(tauri::LogicalPosition::new(
                        mx + (sw - qa_width) / 2.0,
                        my + (sh - qa_height) / 2.0,
                    ));
                }
                let _ = home.show().and_then(|_| home.set_focus());
                if let Some(main) = app.get_webview_window("main") {
                    let _ = main.hide();
                }
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
            get_projects,
            open_artifacts,
            project_create,
            project_switch,
            project_add_object,
            project_remove_object,
            durable_cancel,
            durable_resume,
            get_settings,
            set_settings,
            panel_event,
            surface_result,
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
            reveal_app_in_finder,
            start_snip,
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
            open_url,
            close_home_window,
            set_pet_expanded,
            pick_folder,
            save_attachment,
            save_file,
            search_files
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
