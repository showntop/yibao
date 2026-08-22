//! SessionDb 集成测试（由 src/session_db.rs 内联测试迁出）。
//! 与实现分离：源码瘦身、测试独立编译。

use app_lib::session_db::{MAX_MESSAGES_PER_CONV, SessionDb};
use serde_json::json;

fn db() -> SessionDb {
    SessionDb::open_memory().unwrap()
}

#[test]
fn create_and_list_conversations() {
    let d = db();
    d.create_conversation("c1", "会话一", 100).unwrap();
    d.create_conversation("c2", "会话二", 200).unwrap();
    let list = d.list_conversations().unwrap();
    assert_eq!(list.len(), 2);
    assert_eq!(list[0].id, "c2"); // updated_at 倒序
    assert_eq!(list[0].title, "会话二");
}

#[test]
fn append_assigns_monotonic_seq_and_touches_meta() {
    let d = db();
    d.create_conversation("c1", "", 100).unwrap();
    let m1 = d.append_message("c1", "m1", "user", json!({"text":"你好"}), 1000, false).unwrap();
    let m2 = d.append_message("c1", "m2", "ai", json!({"text":"在"}), 2000, false).unwrap();
    assert_eq!(m1.seq, 1);
    assert_eq!(m2.seq, 2);
    let list = d.list_conversations().unwrap();
    assert_eq!(list[0].message_count, 2);
    assert_eq!(list[0].preview, "在"); // 最后一条文本进预览
    assert_eq!(list[0].updated_at, 2000);
}

#[test]
fn get_messages_orders_by_seq() {
    let d = db();
    d.create_conversation("c1", "", 100).unwrap();
    for i in 0..3 {
        d.append_message("c1", &format!("m{i}"), "ai", json!({"text":i}), i as i64, false).unwrap();
    }
    let msgs = d.get_messages("c1", 100).unwrap();
    assert_eq!(msgs.len(), 3);
    assert_eq!(msgs[0].payload["text"], 0);
    assert_eq!(msgs[2].payload["text"], 2);
}

#[test]
fn update_message_payload_rewrites() {
    let d = db();
    d.create_conversation("c1", "", 100).unwrap();
    d.append_message("c1", "m1", "ai", json!({"text":"部分"}), 1000, false).unwrap();
    d.update_message_payload("c1", "m1", json!({"text":"完整"})).unwrap();
    let msgs = d.get_messages("c1", 10).unwrap();
    assert_eq!(msgs[0].payload["text"], "完整");
}

#[test]
fn truncate_removes_beyond_keep() {
    let d = db();
    d.create_conversation("c1", "", 100).unwrap();
    for i in 0..4 {
        d.append_message("c1", &format!("m{i}"), "ai", json!({"text":i}), i as i64, false).unwrap();
    }
    d.truncate_messages("c1", 2).unwrap();
    let msgs = d.get_messages("c1", 100).unwrap();
    assert_eq!(msgs.len(), 2);
    assert_eq!(d.list_conversations().unwrap()[0].message_count, 2);
}

#[test]
fn upsert_panel_link_creates_then_updates() {
    let d = db();
    d.create_conversation("c1", "", 100).unwrap();
    let first = d.upsert_panel_link("c1", "⇢ 正在和「A」协作", "x1", 1000).unwrap();
    assert_eq!(first.payload["panelLink"], true);
    // 第二次：应更新同一条而非新增（查重）
    let second = d.upsert_panel_link("c1", "⇢ 正在和「B」协作", "x2", 2000).unwrap();
    assert_eq!(first.id, second.id, "应原地更新同一条 panelLink");
    let msgs = d.get_messages("c1", 10).unwrap();
    let links: Vec<_> = msgs.iter().filter(|m| m.payload["panelLink"] == true).collect();
    assert_eq!(links.len(), 1);
    assert_eq!(links[0].payload["text"], "⇢ 正在和「B」协作");
}

#[test]
fn delete_conversation_cascades_messages() {
    let d = db();
    d.create_conversation("c1", "", 100).unwrap();
    d.append_message("c1", "m1", "user", json!({"text":"x"}), 1000, false).unwrap();
    d.delete_conversation("c1").unwrap();
    assert_eq!(d.get_messages("c1", 100).unwrap().len(), 0);
    assert_eq!(d.list_conversations().unwrap().len(), 0);
}

#[test]
fn trim_caps_at_max() {
    let d = db();
    d.create_conversation("c1", "", 100).unwrap();
    for i in 0..(MAX_MESSAGES_PER_CONV + 5) {
        d.append_message("c1", &format!("m{i}"), "ai", json!({"text":i}), i, false).unwrap();
    }
    let count: i64 = d.list_conversations().unwrap()[0].message_count;
    assert_eq!(count, MAX_MESSAGES_PER_CONV);
    let msgs = d.get_messages("c1", 1000).unwrap();
    assert_eq!(msgs.len() as i64, MAX_MESSAGES_PER_CONV);
    // 最老的 5 条被裁：首条 seq 应为 6
    assert_eq!(msgs[0].seq, 6);
}

#[test]
fn active_conversation_pointer_roundtrip() {
    let d = db();
    assert_eq!(d.get_active_conversation().unwrap(), None);
    d.set_active_conversation("c1").unwrap();
    assert_eq!(d.get_active_conversation().unwrap().as_deref(), Some("c1"));
    d.set_active_conversation("c2").unwrap();
    assert_eq!(d.get_active_conversation().unwrap().as_deref(), Some("c2"));
}

#[test]
fn clear_all_wipes_everything() {
    let d = db();
    d.create_conversation("c1", "", 100).unwrap();
    d.append_message("c1", "m1", "user", json!({"text":"x"}), 1000, false).unwrap();
    d.set_active_conversation("c1").unwrap();
    d.clear_all().unwrap();
    assert_eq!(d.list_conversations().unwrap().len(), 0);
    assert_eq!(d.get_active_conversation().unwrap(), None);
}

#[test]
fn messages_are_isolated_per_conversation() {
    // 回复串会话 bug 的存储层保障：不同会话的消息互不可见
    let d = db();
    d.create_conversation("a", "会话A", 100).unwrap();
    d.create_conversation("b", "会话B", 100).unwrap();
    d.append_message("a", "m1", "ai", json!({"text":"A的回复"}), 1000, false).unwrap();
    d.append_message("b", "m2", "ai", json!({"text":"B的回复"}), 1000, false).unwrap();
    let a = d.get_messages("a", 100).unwrap();
    let b = d.get_messages("b", 100).unwrap();
    assert_eq!(a.len(), 1);
    assert_eq!(b.len(), 1);
    assert_eq!(a[0].payload["text"], "A的回复");
    assert_eq!(b[0].payload["text"], "B的回复");
}
