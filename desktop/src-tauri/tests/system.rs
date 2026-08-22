//! system 域集成测试（由 src/lib.rs 内联 invoke_tests 迁出）。
//! 覆盖 wait_clipboard_change 的轮询/超时/立即返回三分支。

use app_lib::system::wait_clipboard_change;

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
