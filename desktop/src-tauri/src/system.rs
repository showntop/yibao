//! 系统集成域：窗口尺寸/定位命令 + 鼠标穿透轮询（热区状态）+ 划词抓取（剪贴板接力 + CGEvent）。
//! 与 OS 窗口系统/输入系统交互的代码都在这；不涉及 brain 进程与业务命令。

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{AppHandle, Emitter, Manager};

#[cfg(desktop)]
use device_query::{DeviceQuery, DeviceState};

/// 前端通知点击穿透模式：true=整窗可交互（展开/气泡中），false=仅团子热区可交互、其余穿透到桌面。
/// 启动默认 true（整窗可交互）：前端挂载前若辅助功能未授权、读不到光标，收起态分支会回退为「不穿透」，
/// 避免团子被锁死点不到；前端 onMounted 后由 setInteractiveFull 按需切到 false。
pub(crate) static PET_INTERACTIVE_FULL: AtomicBool = AtomicBool::new(true);

/// 说话气泡显示中：气泡带（贴团子左侧一条）成为第二热区（点气泡=展开），
/// 不像展开态那样整窗拦点击——气泡是瞬态的，透明区必须照常穿透到桌面。
pub(crate) static PET_BUBBLE_ON: AtomicBool = AtomicBool::new(false);

/// 单窗三态热区矩形组（窗口相对 CSS 像素，kind 区分用途）：
///  - "pet"：团子元素盒——enter 信号只由它驱动（历史教训：面板/气泡热区触发 enter 会误弹）；
///  - "ui"：快捷面板（3 圆 + 输入条）——quick 态附加热区，否则鼠标移到面板上被穿透点不到。
/// idle 态只上报团子盒，quick 态上报团子盒 ∪ 面板；隐藏时上报 None 清除。
/// 相对坐标 + Rust 每次判定叠加当前窗口位置，拖动窗口后热区自动跟随。
pub(crate) static PET_RECTS: Mutex<Vec<(f64, f64, f64, f64, String)>> = Mutex::new(Vec::new());

/// 团子窗尺寸：收起/快捷 320×300（恒窗，内容层切换，零 resize 闪烁）/ 展开对话 360×520。
/// 入参为 CSS 像素，内部按 scale 换算物理像素。以左上角为锚（团子原地不动，向右下展开）。
#[tauri::command]
pub fn set_main_size(app: AppHandle, width: f64, height: f64) -> Result<(), String> {
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
pub fn expand_chat(app: AppHandle) -> Result<(), String> {
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

/// 鼠标穿透轮询（单窗三态架构）：每 40ms 读全局光标位置——
///  * 收起/快捷态（idle/quick，窗口恒 320×300，纯内容层切换，不 resize）：
///    - 仅前端上报的热区矩形可交互（kind=pet 团子盒 / kind=ui 快捷面板），其余穿透；
///    - 光标进入团子盒 → emit pet-cursor-enter（前端显示快捷面板）；
///    - 光标离开（团子 ∪ 面板）连续 ~480ms → emit pet-cursor-leave（前端收起面板）。
///  * 对话态（chat，360×520，set_interactive_full(true)）：整窗可交互，无 hover 逻辑。
/// 坐标单位（macOS 点 vs 物理像素）已按 scale 换算；热区为窗口相对坐标，拖动自动跟随。
#[cfg(desktop)]
pub(crate) fn spawn_click_through(handle: tauri::AppHandle) {
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
pub fn wait_clipboard_change<F: FnMut() -> Option<String>>(
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
pub(crate) fn grab_selected_text() -> Option<String> {
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
