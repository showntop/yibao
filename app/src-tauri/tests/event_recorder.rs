//! EventRecorder 集成测试（由 src/event_recorder.rs 内联测试迁出）。
//! 与实现分离：源码瘦身、测试独立编译。

use app_lib::event_recorder::EventRecorder;
use app_lib::session_db::SessionDb;
use serde_json::json;

fn setup() -> (SessionDb, EventRecorder) {
    let db = SessionDb::open_memory().unwrap();
    db.create_conversation("c1", "", 0).unwrap();
    (db, EventRecorder::new())
}

fn texts(db: &SessionDb) -> Vec<String> {
    db.get_messages("c1", 100)
        .unwrap()
        .iter()
        .map(|m| m.payload["text"].as_str().unwrap_or("").to_string())
        .collect()
}

#[test]
fn final_reply_non_streaming_appends() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"完整回复"}));
    assert_eq!(texts(&db), vec!["完整回复"]);
}

#[test]
fn streaming_then_tools_then_reply_interleaves() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply_chunk","text":"先查一下。"}));
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","skill_id":"web_search","label":"联网搜索"}}));
    r.record(&db, "c1", &json!({"kind":"action_result","action":{"id":"a1","skill_id":"web_search","label":"联网搜索"},"result":{"success":true,"data":{"human":"ok"}}}));
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a2","skill_id":"extract_url","label":"读网页"}}));
    r.record(&db, "c1", &json!({"kind":"action_result","action":{"id":"a2","skill_id":"extract_url","label":"读网页"},"result":{"success":true,"data":{"human":"ok"}}}));
    r.record(&db, "c1", &json!({"kind":"final_reply_chunk","text":"结论是这样"}));
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"结论是这样"}));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 4, "正文 / 工具 / 工具 / 终复 按发生顺序各占一条");
    assert_eq!(msgs[0].role, "ai");
    assert_eq!(msgs[0].payload["text"], "先查一下。");
    assert_eq!(msgs[1].payload["proc"]["label"], "联网搜索");
    assert_eq!(msgs[2].payload["proc"]["label"], "读网页");
    assert_eq!(msgs[3].role, "ai");
    assert_eq!(msgs[3].payload["text"], "结论是这样");
}

#[test]
fn streaming_chunks_persist_single_final_message() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply_chunk","text":"你"}));
    r.record(&db, "c1", &json!({"kind":"final_reply_chunk","text":"好"}));
    // 流式中只建了一条消息（首片），内容还是部分
    assert_eq!(db.get_messages("c1", 100).unwrap().len(), 1);
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"你好，世界"}));
    let msgs = db.get_messages("c1", 100).unwrap();
    assert_eq!(msgs.len(), 1, "流式全程只一条消息");
    assert_eq!(msgs[0].payload["text"], "你好，世界");
}

#[test]
fn interrupted_stream_marks_halted() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply_chunk","text":"说了半"}));
    r.record(&db, "c1", &json!({"kind":"interrupted"}));
    let msgs = db.get_messages("c1", 100).unwrap();
    assert_eq!(msgs.len(), 1);
    assert_eq!(msgs[0].payload["text"], "说了半");
    assert_eq!(msgs[0].payload["halted"], true);
}

#[test]
fn interrupted_without_stream_appends_marker() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"interrupted"}));
    assert_eq!(texts(&db), vec!["已打断"]);
    assert_eq!(db.get_messages("c1", 10).unwrap()[0].payload["halted"], true);
}

#[test]
fn proc_lifecycle_done_and_ok() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","skill_id":"sys_info","label":"查系统"}}));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1);
    assert_eq!(msgs[0].payload["proc"]["done"], false);
    assert_eq!(msgs[0].payload["proc"]["label"], "查系统");
    // 结果回来原地收尾
    r.record(&db, "c1", &json!({"kind":"action_result","action":{"id":"a1","skill_id":"sys_info","label":"查系统"},"result":{"success":true,"data":{"human":"ok"}}}));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1, "proc 收尾是原地更新，不新增");
    assert_eq!(msgs[0].payload["proc"]["done"], true);
    assert_eq!(msgs[0].payload["proc"]["ok"], true);
}

#[test]
fn proc_skip_use_plugin() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","skill_id":"use_plugin","label":"展开插件"}}));
    assert_eq!(db.get_messages("c1", 10).unwrap().len(), 0, "use_plugin 不插过程行");
}

#[test]
fn refs_attach_to_next_ai_message() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","skill_id":"sys_info","label":"查系统"}}));
    r.record(&db, "c1", &json!({"kind":"action_result","action":{"id":"a1","skill_id":"sys_info","label":"查系统"},"result":{"success":true,"data":{"human":"macOS"}}}));
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"你是 macOS"}));
    let msgs = db.get_messages("c1", 10).unwrap();
    let ai = msgs.iter().find(|m| m.role == "ai").unwrap();
    let refs = ai.payload["refs"].as_array().unwrap();
    assert_eq!(refs.len(), 1);
    assert_eq!(refs[0]["label"], "查系统");
    assert_eq!(refs[0]["ok"], true);
    assert_eq!(refs[0]["detail"], "macOS");
}

#[test]
fn panel_upsert_dedupes() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"panel","payload":{"title":"选题看板","panel":"z:board"}}));
    r.record(&db, "c1", &json!({"kind":"panel","payload":{"title":"数据面板","panel":"z:data"}}));
    let msgs = db.get_messages("c1", 10).unwrap();
    let links: Vec<_> = msgs.iter().filter(|m| m.payload["panelLink"] == true).collect();
    assert_eq!(links.len(), 1);
    assert_eq!(links[0].payload["text"], "⇢ 正在和「数据面板」协作");
}

#[test]
fn empty_conv_id_skips() {
    let (db, mut r) = setup();
    r.record(&db, "", &json!({"kind":"final_reply","text":"x"}));
    assert_eq!(db.get_messages("c1", 10).unwrap().len(), 0);
}

#[test]
fn listening_done_with_text_appends_user() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"listening_done","text":"今天天气怎么样"}));
    r.record(&db, "c1", &json!({"kind":"listening_done","text":""}));
    r.record(&db, "c1", &json!({"kind":"listening_done"}));
    assert_eq!(texts(&db), vec!["今天天气怎么样"]);
    assert_eq!(db.get_messages("c1", 10).unwrap()[0].role, "user");
}

#[test]
fn flush_stream_as_interrupted_saves_partial() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply_chunk","text":"半截话"}));
    r.flush_stream_as_interrupted(&db, "c1");
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1);
    assert_eq!(msgs[0].payload["halted"], true);
    assert_eq!(msgs[0].payload["text"], "半截话");
}

#[test]
fn final_reply_metrics_persist_in_message_payload() {
    // 用量条（UsageBar）依赖 metrics 落库：重启后从 SQLite 拉回仍可见
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({
        "kind":"final_reply",
        "text":"你好",
        "payload":{"metrics":{"prompt_tokens":120,"completion_tokens":30,"cached_tokens":50,"total_tokens":150,"cost":0.0001,"elapsed_ms":17400,"model":"glm-4.6"}}
    }));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1);
    assert_eq!(msgs[0].payload["text"], "你好");
    assert_eq!(msgs[0].payload["metrics"]["total_tokens"], 150);
    assert_eq!(msgs[0].payload["metrics"]["model"], "glm-4.6");
}

#[test]
fn final_reply_metrics_persist_for_streamed_message() {
    // 流式场景：首片 chunk 建消息，终态 update 写 metrics
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply_chunk","text":"你"}));
    r.record(&db, "c1", &json!({
        "kind":"final_reply",
        "text":"你好",
        "payload":{"metrics":{"prompt_tokens":100,"completion_tokens":2,"cached_tokens":0,"total_tokens":102,"cost":null,"elapsed_ms":1000,"model":"glm-4.6"}}
    }));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1, "流式全程只一条消息");
    assert_eq!(msgs[0].payload["text"], "你好");
    assert_eq!(msgs[0].payload["metrics"]["total_tokens"], 102);
}
