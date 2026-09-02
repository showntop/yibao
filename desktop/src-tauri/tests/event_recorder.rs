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
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","tool_id":"web_search","label":"联网搜索"}}));
    r.record(&db, "c1", &json!({"kind":"action_result","action":{"id":"a1","tool_id":"web_search","label":"联网搜索"},"result":{"success":true,"data":{"human":"ok"}}}));
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a2","tool_id":"extract_url","label":"读网页"}}));
    r.record(&db, "c1", &json!({"kind":"action_result","action":{"id":"a2","tool_id":"extract_url","label":"读网页"},"result":{"success":true,"data":{"human":"ok"}}}));
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
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","tool_id":"sys_info","label":"查系统"}}));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1);
    assert_eq!(msgs[0].payload["proc"]["done"], false);
    assert_eq!(msgs[0].payload["proc"]["label"], "查系统");
    // 结果回来原地收尾
    r.record(&db, "c1", &json!({"kind":"action_result","action":{"id":"a1","tool_id":"sys_info","label":"查系统"},"result":{"success":true,"data":{"human":"ok"}}}));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1, "proc 收尾是原地更新，不新增");
    assert_eq!(msgs[0].payload["proc"]["done"], true);
    assert_eq!(msgs[0].payload["proc"]["ok"], true);
}

#[test]
fn proc_skip_use_plugin() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","tool_id":"use_plugin","label":"展开插件"}}));
    assert_eq!(db.get_messages("c1", 10).unwrap().len(), 0, "use_plugin 不插过程行");
}

#[test]
fn refs_attach_to_next_ai_message() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","tool_id":"sys_info","label":"查系统"}}));
    r.record(&db, "c1", &json!({"kind":"action_result","action":{"id":"a1","tool_id":"sys_info","label":"查系统"},"result":{"success":true,"data":{"human":"macOS"}}}));
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
fn action_result_capability_gap_persists_card() {
    // 能力边界卡落库（对齐前端 GapProjection）：刷新/切会话重拉后卡片恢复
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({
        "kind":"action_result",
        "action":{"id":"a1","tool_id":"project_create","label":"建项目"},
        "result":{"success":true,"data":{"capability":{
            "enforced":true,
            "available_stages":["S0 选题","S1 调研"],
            "missing_stages":["S2 脚本","S3 成片"],
            "degradation":"可先出选题清单"
        }}}
    }));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1);
    assert_eq!(msgs[0].role, "sys");
    assert_eq!(msgs[0].payload["text"], "能力边界 · 可做到S1 调研", "text 为回退标题");
    let gap = &msgs[0].payload["gap"];
    assert_eq!(gap["through"], "S1 调研");
    assert_eq!(gap["available"], json!(["S0 选题", "S1 调研"]));
    assert_eq!(gap["missing"], json!(["S2 脚本", "S3 成片"]));
    assert_eq!(gap["note"], "可先出选题清单");
}

#[test]
fn gap_note_falls_back_to_blocked_reason_and_empty_available_title() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({
        "kind":"action_result",
        "action":{"id":"a1","tool_id":"project_create","label":"建项目"},
        "result":{"success":true,"data":{"capability":{
            "enforced":true,
            "missing_stages":["S0 选题"],
            "blocked_reason":" 未接入视频流水线 "
        }}}
    }));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1);
    assert_eq!(msgs[0].payload["text"], "能力边界", "available 为空时标题无后缀");
    assert_eq!(msgs[0].payload["gap"]["through"], "");
    assert_eq!(
        msgs[0].payload["gap"]["note"], "未接入视频流水线",
        "degradation 缺省回退 blocked_reason（trim 后）"
    );
}

#[test]
fn no_gap_card_when_not_enforced_or_no_missing_or_failed() {
    let (db, mut r) = setup();
    for (i, result) in [
        json!({"success":true,"data":{"capability":{"enforced":false,"missing_stages":["S2"]}}}),
        json!({"success":true,"data":{"capability":{"enforced":true,"missing_stages":[]}}}),
        json!({"success":false,"data":{"capability":{"enforced":true,"missing_stages":["S2"]}}}),
        json!({"success":true,"data":{"capability":"not-an-object"}}),
        json!({"success":true,"data":{}}),
    ]
    .iter()
    .enumerate()
    {
        r.record(&db, "c1", &json!({
            "kind":"action_result",
            "action":{"id":format!("a{i}"),"tool_id":"project_create","label":"建项目"},
            "result":result
        }));
    }
    assert_eq!(db.get_messages("c1", 10).unwrap().len(), 0, "不满足出卡条件不落消息");
}

#[test]
fn gap_card_coexists_with_proc_closeout() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"action_proposed","action":{"id":"a1","tool_id":"project_create","label":"建项目"}}));
    r.record(&db, "c1", &json!({
        "kind":"action_result",
        "action":{"id":"a1","tool_id":"project_create","label":"建项目"},
        "result":{"success":true,"data":{"capability":{
            "enforced":true,
            "available_stages":["S0 选题"],
            "missing_stages":["S1 调研"],
            "degradation":"先出选题"
        }}}
    }));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 2, "proc 行原地收尾 + gap 卡各一条");
    assert_eq!(msgs[0].payload["proc"]["done"], true);
    assert_eq!(msgs[0].payload["proc"]["ok"], true);
    assert_eq!(msgs[1].payload["gap"]["through"], "S0 选题");
    assert_eq!(msgs[1].payload["text"], "能力边界 · 可做到S0 选题");
}

#[test]
fn stale_epoch_events_are_dropped() {
    // 被抢占旧 run 的迟到事件不落库（典型：旧 run 的 final_reply_chunk，防重拉时旧回复复活）
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"新 run 回复","run_epoch":2,"seq":5}));
    r.record(&db, "c1", &json!({"kind":"final_reply_chunk","text":"旧 run 迟到的半截","run_epoch":1,"seq":9}));
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"旧 run 迟到的完整回复","run_epoch":1,"seq":10}));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1, "更旧 epoch 的事件一律不落库");
    assert_eq!(msgs[0].payload["text"], "新 run 回复");
}

#[test]
fn equal_and_higher_epoch_pass() {
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"epoch2 第一句","run_epoch":2,"seq":1}));
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"epoch2 同代事件","run_epoch":2,"seq":2}));
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"epoch3 新一代","run_epoch":3,"seq":1}));
    assert_eq!(texts(&db), vec!["epoch2 第一句", "epoch2 同代事件", "epoch3 新一代"]);
}

#[test]
fn events_without_epoch_always_pass() {
    // 缺 run_epoch（notice/reminder 等非 run 事件、旧 sidecar）照常放行，缺省字段稳健
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"新 run","run_epoch":7,"seq":1}));
    r.record(&db, "c1", &json!({"kind":"notice","text":"无代数通知"}));
    r.record(&db, "c1", &json!({"kind":"reminder","text":"无代数提醒"}));
    assert_eq!(db.get_messages("c1", 10).unwrap().len(), 3);
}

#[test]
fn epoch_ledger_is_per_conversation() {
    let (db, mut r) = setup();
    db.create_conversation("c2", "", 0).unwrap();
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"c1 的 epoch5","run_epoch":5,"seq":1}));
    // c2 还没见过任何 epoch：epoch 1 是它的首个带代数事件，放行
    r.record(&db, "c2", &json!({"kind":"final_reply","text":"c2 的 epoch1","run_epoch":1,"seq":1}));
    assert_eq!(texts(&db), vec!["c1 的 epoch5"]);
    assert_eq!(db.get_messages("c2", 10).unwrap()[0].payload["text"], "c2 的 epoch1");
}

#[test]
fn reset_run_clears_epoch_ledger() {
    // sidecar 重启后 run_epoch 从 0 重编号：账本必须清零（对齐前端 resetRunEpochs），
    // 否则新 brain 的事件全被误判成旧账
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"旧 brain 的 epoch5","run_epoch":5,"seq":1}));
    r.reset_run();
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"新 brain 的 epoch1","run_epoch":1,"seq":1}));
    assert_eq!(texts(&db), vec!["旧 brain 的 epoch5", "新 brain 的 epoch1"]);
}

#[test]
fn stale_epoch_also_drops_gap_card() {
    // 闸门在 dispatch 之前：旧 run 的 action_result（含能力边界卡）同样不落库
    let (db, mut r) = setup();
    r.record(&db, "c1", &json!({"kind":"final_reply","text":"新 run","run_epoch":2,"seq":1}));
    r.record(&db, "c1", &json!({
        "kind":"action_result",
        "action":{"id":"a1","tool_id":"project_create","label":"建项目"},
        "result":{"success":true,"data":{"capability":{"enforced":true,"available_stages":["S0"],"missing_stages":["S2"]}}},
        "run_epoch":1,"seq":8
    }));
    assert_eq!(db.get_messages("c1", 10).unwrap().len(), 1, "旧 epoch 的 gap 卡不落库");
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
fn reminder_task_logs_do_not_enter_conversation() {
    let (db, mut r) = setup();
    r.record(
        &db,
        "c1",
        &json!({
            "kind":"reminder",
            "text":"✅ 沙箱脚本完成：print('hi')\nnpm install OK",
            "task":{"id":"abc","status":"done","label":"沙箱脚本"}
        }),
    );
    r.record(
        &db,
        "c1",
        &json!({"kind":"reminder","type":"watch_command","text":"命令跑完了","status":"completed"}),
    );
    r.record(&db, "c1", &json!({"kind":"reminder","text":"该开战会了"}));
    let msgs = db.get_messages("c1", 10).unwrap();
    assert_eq!(msgs.len(), 1, "任务收尾不落对话，只留真正的用户提醒");
    assert_eq!(msgs[0].payload["text"], "该开战会了");
    assert_eq!(msgs[0].payload["icon"], "clock");
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
