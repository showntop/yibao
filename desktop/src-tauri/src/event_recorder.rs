//! 对话事件落库器（EventRecorder）：把 sidecar 的对话类 brain-event 转成消息记录落库。
//!
//! 定位：Rust 主进程是 conversation 域唯一写者。本模块在事件分发点（lib.rs 桥任务）
//! 独立做"原始事件 → 消息 payload"的领域转换（忠实复刻前端 bubbleToInput 逻辑）；
//! webview 的 onEvent 只做内存渲染、不写库——两路从同一事件流出发，逻辑一致则结果一致。
//!
//! 瞬态（流式缓冲 / proc 索引 / run 溯源 / 运行代数账本）随 sidecar 进程生命周期，重启时 reset。

use crate::session_db::{now_ms, SessionDb};
use serde_json::{json, Value};
use std::collections::HashMap;

/// 对话类事件落库的状态机：流式缓冲 + proc 索引 + run 溯源引用 + 运行代数闸。
#[derive(Default)]
pub struct EventRecorder {
    /// 流式缓冲：当前 run 的 AI 流式消息（chunk 只累积，final/interrupted 才落库）
    stream_msg_id: Option<String>,
    stream_text: String,
    stream_refs: Vec<Value>,
    stream_ts: i64,
    /// proc 过程行索引：action_id → (message_id, label)，结果回来原地收尾
    proc_ids: HashMap<String, (String, String)>,
    /// 本次 run 的溯源引用，挂到下一条 AI 消息（"参考了 ▾"）
    run_refs: Vec<Value>,
    /// 运行代数账本：conversation_id → 已见最大 run_epoch（P0 闸门。
    /// 被抢占旧 run 的迟到事件不落库，防刷新/切会话重拉时旧回复复活）。
    max_epoch: HashMap<String, i64>,
}

pub(crate) fn new_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

fn proc_label(a: &Value) -> String {
    a.get("label")
        .or_else(|| a.get("tool_id"))
        .and_then(|v| v.as_str())
        .unwrap_or("操作")
        .to_string()
}

/// use_plugin 不插过程行（成功有 notice 轻提示，重复；失败由 LLM 下一句转告）。
fn proc_skip(a: &Value) -> bool {
    a.get("tool_id").and_then(|v| v.as_str()) == Some("use_plugin")
}

fn truncate(s: &str, n: usize) -> String {
    if s.chars().count() > n {
        let t: String = s.chars().take(n).collect();
        format!("{t}…")
    } else {
        s.to_string()
    }
}

/// 能力边界卡投影（忠实复刻前端 capability-gap.ts 的 capabilityGapFromResult）：
/// 成功 + data.capability.enforced===true + missing_stages 非空 → 出卡素材；否则 None。
/// 返回字段与前端 MessagePayload.gap（GapProjection）对齐：through/available/missing/note。
fn gap_projection(result: &Value) -> Option<Value> {
    if result.get("success").and_then(|v| v.as_bool()) != Some(true) {
        return None;
    }
    let cap = result.get("data")?.get("capability")?.as_object()?;
    if cap.get("enforced").and_then(|v| v.as_bool()) != Some(true) {
        return None;
    }
    let strings_of = |key: &str| -> Vec<String> {
        cap.get(key)
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect())
            .unwrap_or_default()
    };
    let missing = strings_of("missing_stages");
    if missing.is_empty() {
        return None;
    }
    let available = strings_of("available_stages");
    let through = available.last().cloned().unwrap_or_default();
    // 降级建议优先，缺省回退 blocked_reason（同前端 degradation || blocked_reason）
    let degradation = cap.get("degradation").and_then(|v| v.as_str()).unwrap_or("").trim();
    let reason = cap.get("blocked_reason").and_then(|v| v.as_str()).unwrap_or("").trim();
    let note = if degradation.is_empty() { reason } else { degradation };
    Some(json!({ "through": through, "available": available, "missing": missing, "note": note }))
}

/// 卡标题（同前端 capabilityGapTitle）：也作气泡回退文本（不渲染卡的面退化为这一行）。
fn gap_title(gap: &Value) -> String {
    let through = gap.get("through").and_then(|v| v.as_str()).unwrap_or("");
    if through.is_empty() {
        "能力边界".to_string()
    } else {
        format!("能力边界 · 可做到{through}")
    }
}

impl EventRecorder {
    pub fn new() -> Self {
        Self::default()
    }

    /// sidecar 重启（hello 接管新进程）：清瞬态（流式缓冲若残留则按 interrupted 兜底落库由调用方先处理）。
    /// 代数账本一并清零——sidecar 重启后 run_epoch 从 0 重新编号，账本不清会把新 brain 的
    /// 事件全误判成旧账（对齐前端 resetRunEpochs）。
    pub fn reset_run(&mut self) {
        self.stream_msg_id = None;
        self.stream_text.clear();
        self.stream_refs.clear();
        self.stream_ts = 0;
        self.proc_ids.clear();
        self.run_refs.clear();
        self.max_epoch.clear();
    }

    /// 运行代数闸（对齐前端 run-epoch.ts 的 isStaleRunEvent）：事件带 run_epoch 时，
    /// 比本会话已见最大代数更旧 → 丢弃（被抢占旧 run 的迟到事件，典型 final_reply_chunk）；
    /// 更高 → 采纳并记账；相等放行。缺 run_epoch 字段（notice/reminder 等非 run 事件、
    /// 旧 sidecar）一律放行，缺省字段稳健。
    fn is_stale_epoch(&mut self, conv_id: &str, e: &Value) -> bool {
        let Some(epoch) = e.get("run_epoch").and_then(|v| v.as_i64()) else {
            return false;
        };
        match self.max_epoch.get(conv_id) {
            Some(&max) if epoch < max => true,
            _ => {
                self.max_epoch.insert(conv_id.to_string(), epoch);
                false
            }
        }
    }

    /// 处理一条 brain-event，按需落库到 conv_id 会话。conv_id 为空（活跃会话未建立）则跳过。
    pub fn record(&mut self, db: &SessionDb, conv_id: &str, e: &Value) {
        if conv_id.is_empty() {
            return;
        }
        // 运行代数闸（P0）：被抢占旧 run 的迟到事件不落库（对齐前端 run-epoch.ts 闸门语义）。
        if self.is_stale_epoch(conv_id, e) {
            return;
        }
        let kind = e.get("kind").and_then(|k| k.as_str()).unwrap_or("");
        match kind {
            "action_proposed" => self.on_action_proposed(db, conv_id, e),
            "action_result" => self.on_action_result(db, conv_id, e),
            "final_reply_chunk" => self.on_chunk(db, conv_id, e),
            "final_reply" => self.on_final_reply(db, conv_id, e),
            "interrupted" => self.on_interrupted(db, conv_id),
            "listening_done" => self.on_listening_done(db, conv_id, e),
            "notice" => self.append_simple(db, conv_id, "sys", e, None),
            "reminder" => self.on_reminder(db, conv_id, e),
            "error" => self.on_error(db, conv_id, e),
            // panel 事件不再落 panelLink 协作气泡（对话流已去除该机制，避免消息库里残留「⇢ 正在和 X 协作」）
            _ => {}
        }
    }

    fn append(&self, db: &SessionDb, conv_id: &str, role: &str, payload: Value, ts: i64) {
        let _ = db.append_message(conv_id, &new_id(), role, payload, ts, false);
    }

    fn on_listening_done(&self, db: &SessionDb, conv_id: &str, e: &Value) {
        // 语音识别用户句：打字走 run_input 在 Rust 落库，语音不走那条，必须在这里补上。
        // 空识别不落库（前端只做「没听清」提示，不当成用户消息）。
        let Some(text) = e.get("text").and_then(|t| t.as_str()).filter(|s| !s.is_empty()) else {
            return;
        };
        self.append(db, conv_id, "user", json!({ "text": text }), now_ms());
    }

    fn on_reminder(&self, db: &SessionDb, conv_id: &str, e: &Value) {
        // 沙箱/agent 收尾复用 reminder 写 Feed；落对话会变成蓝色闹钟胶囊并拆开当轮 run。
        let is_task = e.get("task").map(|t| t.is_object()).unwrap_or(false)
            || e.get("type").and_then(|t| t.as_str()) == Some("watch_command");
        if is_task {
            return;
        }
        self.append_simple(db, conv_id, "ai", e, Some("clock"));
    }

    fn append_simple(&self, db: &SessionDb, conv_id: &str, role: &str, e: &Value, icon: Option<&str>) {
        let text = e.get("text").and_then(|t| t.as_str()).unwrap_or("").to_string();
        let mut payload = json!({ "text": text });
        if let Some(ic) = icon {
            payload["icon"] = json!(ic);
        }
        self.append(db, conv_id, role, payload, now_ms());
    }

    /// 工具行要按发生顺序插在对话里：若当前还在流式，先把已吐出的正文封成一段，
    /// 后续 chunk 另起一条 AI 消息，避免终态把整段回复写回工具之前那一条。
    fn seal_stream(&mut self, db: &SessionDb, conv_id: &str) {
        let Some(msg_id) = self.stream_msg_id.take() else { return };
        let mut payload = json!({ "text": self.stream_text });
        if !self.stream_refs.is_empty() {
            payload["refs"] = json!(self.stream_refs);
        }
        let _ = db.update_message_payload(conv_id, &msg_id, payload);
        self.stream_text.clear();
        self.stream_refs.clear();
    }

    fn on_action_proposed(&mut self, db: &SessionDb, conv_id: &str, e: &Value) {
        let Some(action) = e.get("action") else { return };
        let Some(action_id) = action.get("id").and_then(|v| v.as_str()) else { return };
        if proc_skip(action) {
            return;
        }
        self.seal_stream(db, conv_id);
        let label = proc_label(action);
        // proc 过程行（sys 淡色小字，done=false 进行中）
        let msg_id = new_id();
        let _ = db.append_message(
            conv_id,
            &msg_id,
            "sys",
            json!({ "text": "", "proc": { "label": label, "done": false } }),
            now_ms(),
            false,
        );
        self.proc_ids.insert(action_id.to_string(), (msg_id, label.clone()));
        self.run_refs.push(json!({ "label": label, "detail": "调用工具中…", "ok": false }));
    }

    fn on_action_result(&mut self, db: &SessionDb, conv_id: &str, e: &Value) {
        let action = e.get("action").cloned().unwrap_or(Value::Null);
        let result = e.get("result").cloned().unwrap_or(Value::Null);
        let ok = result.get("success").and_then(|v| v.as_bool()).unwrap_or(true);
        // proc 过程行原地收尾（done=true + ok）
        if let Some(action_id) = action.get("id").and_then(|v| v.as_str()) {
            if let Some((msg_id, label)) = self.proc_ids.remove(action_id) {
                let _ = db.update_message_payload(
                    conv_id,
                    &msg_id,
                    json!({ "text": "", "proc": { "label": label, "done": true, "ok": ok } }),
                );
            }
        }
        // 溯源收尾：写回最近一条未完成引用
        let label = proc_label(&action);
        if let Some(r) = self.run_refs.iter_mut().rev().find(|r| {
            r.get("ok").and_then(|v| v.as_bool()) == Some(false)
                && r.get("label").and_then(|v| v.as_str()) == Some(label.as_str())
        }) {
            let detail = if ok {
                let human = result
                    .get("data")
                    .and_then(|d| d.get("human"))
                    .and_then(|h| h.as_str())
                    .unwrap_or("");
                if human.is_empty() { "已完成".to_string() } else { truncate(human, 60) }
            } else {
                let err = result.get("error").and_then(|v| v.as_str()).unwrap_or("失败");
                format!("失败：{}", truncate(err, 60))
            };
            *r = json!({ "label": label, "detail": detail, "ok": ok });
        }
        // 能力边界卡（对齐前端 useChatFlow 的 action_result 分支）：缺能力 visibly 落信息卡，
        // role=sys、text 为回退标题（纸面摊法/会话 preview 用），gap 投影随消息落库，
        // 刷新/切会话重拉后卡片恢复。
        if let Some(gap) = gap_projection(&result) {
            let title = gap_title(&gap);
            self.append(db, conv_id, "sys", json!({ "text": title, "gap": gap }), now_ms());
        }
    }

    fn on_chunk(&mut self, db: &SessionDb, conv_id: &str, e: &Value) {
        let text = e.get("text").and_then(|t| t.as_str()).unwrap_or("");
        if self.stream_msg_id.is_none() {
            // 首片：创建流式消息（含本次 run 溯源引用），后续 chunk 只累积不落库
            let msg_id = new_id();
            self.stream_msg_id = Some(msg_id.clone());
            self.stream_text = text.to_string();
            self.stream_ts = now_ms();
            self.stream_refs = std::mem::take(&mut self.run_refs);
            let mut payload = json!({ "text": self.stream_text });
            if !self.stream_refs.is_empty() {
                payload["refs"] = json!(self.stream_refs);
            }
            let _ = db.append_message(conv_id, &msg_id, "ai", payload, self.stream_ts, false);
        } else {
            self.stream_text.push_str(text);
        }
    }

    fn on_final_reply(&mut self, db: &SessionDb, conv_id: &str, e: &Value) {
        let full = e.get("text").and_then(|t| t.as_str()).unwrap_or("").to_string();
        // run 统计（token/费用/耗时）：sidecar 把 metrics 塞进 final_reply 的 payload 里，
        // 落库时透传→重启恢复后 UsageBar 仍可见
        let metrics = e.get("payload").and_then(|p| p.get("metrics")).cloned();
        if let Some(msg_id) = self.stream_msg_id.take() {
            // 流式终态落盘：更新已建消息
            let mut payload = json!({ "text": full });
            if !self.stream_refs.is_empty() {
                payload["refs"] = json!(self.stream_refs);
            }
            if let Some(m) = metrics {
                payload["metrics"] = m;
            }
            let _ = db.update_message_payload(conv_id, &msg_id, payload);
            self.stream_text.clear();
            self.stream_refs.clear();
        } else {
            // 非流式：直接落库完整消息
            let refs = std::mem::take(&mut self.run_refs);
            let mut payload = json!({ "text": full });
            if !refs.is_empty() {
                payload["refs"] = json!(refs);
            }
            if let Some(m) = metrics {
                payload["metrics"] = m;
            }
            self.append(db, conv_id, "ai", payload, now_ms());
        }
    }

    /// 拒绝/禁止执行等 error：对应过程行原地收尾（拒绝/打断没有 action_result，
    /// 不收尾重载后永远转圈），再落告警消息。run 溯源随 error 作废（同前端 error 分支）。
    fn on_error(&mut self, db: &SessionDb, conv_id: &str, e: &Value) {
        if let Some(action_id) = e
            .get("action")
            .and_then(|a| a.get("id"))
            .and_then(|v| v.as_str())
        {
            if let Some((msg_id, label)) = self.proc_ids.remove(action_id) {
                let _ = db.update_message_payload(
                    conv_id,
                    &msg_id,
                    json!({ "text": "", "proc": { "label": label, "done": true, "ok": false } }),
                );
            }
        }
        self.run_refs.clear();
        self.append_simple(db, conv_id, "ai", e, Some("alert"));
    }

    fn on_interrupted(&mut self, db: &SessionDb, conv_id: &str) {
        self.run_refs.clear();
        // 在途过程行全部收尾为失败：排队中/待确认的动作不会有 action_result，
        // 不收尾落库就是 done=false，重载后进度行永远转圈
        for (_action_id, (msg_id, label)) in self.proc_ids.drain() {
            let _ = db.update_message_payload(
                conv_id,
                &msg_id,
                json!({ "text": "", "proc": { "label": label, "done": true, "ok": false } }),
            );
        }
        if let Some(msg_id) = self.stream_msg_id.take() {
            // 半成品落库为 halted
            let mut payload = json!({ "text": self.stream_text, "halted": true });
            if !self.stream_refs.is_empty() {
                payload["refs"] = json!(self.stream_refs);
            }
            let _ = db.update_message_payload(conv_id, &msg_id, payload);
            self.stream_text.clear();
            self.stream_refs.clear();
        } else {
            self.append(db, conv_id, "ai", json!({ "text": "已打断", "halted": true }), now_ms());
        }
    }

    /// sidecar 掉线/重启：残留的流式缓冲按 interrupted 兜底落库（防"说了半句消失"）。
    pub fn flush_stream_as_interrupted(&mut self, db: &SessionDb, conv_id: &str) {
        if self.stream_msg_id.is_some() {
            self.on_interrupted(db, conv_id);
        }
        self.reset_run();
    }
}
