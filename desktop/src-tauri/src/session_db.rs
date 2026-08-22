//! 会话持久化层（SessionDb）：conversation 域的唯一权威存储。
//!
//! 架构定位：sidecar 事件流与 run_input 的用户消息统一在此落库（Rust 主进程是
//! 唯一写者），webview 只读渲染、启动时经 command 拉取恢复——从架构上消灭多窗双写。
//! 仅承载 conversation 域；surface/window 域单窗自有，留前端 IndexedDB 不动。

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;
use std::sync::Mutex;

/// 单会话消息截尾上限（超出裁最老，防无限增长）
pub const MAX_MESSAGES_PER_CONV: i64 = 500;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationMeta {
    pub id: String,
    pub title: String,
    pub preview: String,
    pub created_at: i64,
    pub updated_at: i64,
    pub message_count: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Message {
    pub id: String,
    pub conversation_id: String,
    pub seq: i64,
    pub role: String,
    /// 领域载荷（text/panelLink/halted/icon/refs/proc），JSON 字符串
    pub payload: Value,
    pub ts: i64,
    pub ephemeral: bool,
}

pub struct SessionDb {
    conn: Mutex<Connection>,
}

impl SessionDb {
    pub fn open(path: &Path) -> Result<Self, String> {
        let conn = Connection::open(path).map_err(|e| format!("打开会话库失败：{e}"))?;
        let db = Self { conn: Mutex::new(conn) };
        db.migrate()?;
        Ok(db)
    }

    /// 内存库（单测/集成测试用）
    pub fn open_memory() -> Result<Self, String> {
        let conn = Connection::open_in_memory().map_err(|e| e.to_string())?;
        let db = Self { conn: Mutex::new(conn) };
        db.migrate()?;
        Ok(db)
    }

    fn migrate(&self) -> Result<(), String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS conversations(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                preview TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS messages(
                id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                payload TEXT NOT NULL,
                ts INTEGER NOT NULL,
                ephemeral INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(conversation_id, id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv_seq
                ON messages(conversation_id, seq);
            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );",
        )
        .map_err(|e| format!("会话库建表失败：{e}"))?;
        Ok(())
    }

    // ---- 会话元数据 ----

    pub fn create_conversation(&self, id: &str, title: &str, now: i64) -> Result<ConversationMeta, String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        conn.execute(
            "INSERT INTO conversations(id, title, preview, created_at, updated_at, message_count)
             VALUES(?1, ?2, '', ?3, ?3, 0)",
            params![id, title, now],
        )
        .map_err(|e| e.to_string())?;
        Ok(ConversationMeta {
            id: id.into(),
            title: title.into(),
            preview: String::new(),
            created_at: now,
            updated_at: now,
            message_count: 0,
        })
    }

    pub fn list_conversations(&self) -> Result<Vec<ConversationMeta>, String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        let mut stmt = conn
            .prepare("SELECT id, title, preview, created_at, updated_at, message_count
                      FROM conversations ORDER BY updated_at DESC, created_at DESC")
            .map_err(|e| e.to_string())?;
        let rows = stmt
            .query_map([], |r| {
                Ok(ConversationMeta {
                    id: r.get(0)?,
                    title: r.get(1)?,
                    preview: r.get(2)?,
                    created_at: r.get(3)?,
                    updated_at: r.get(4)?,
                    message_count: r.get(5)?,
                })
            })
            .map_err(|e| e.to_string())?;
        let mut out = Vec::new();
        for m in rows {
            out.push(m.map_err(|e| e.to_string())?);
        }
        Ok(out)
    }

    fn touch_conversation(
        conn: &Connection,
        id: &str,
        preview: Option<&str>,
        now: i64,
    ) -> Result<(), String> {
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=?1",
                params![id],
                |r| r.get(0),
            )
            .map_err(|e| e.to_string())?;
        match preview {
            Some(p) => conn
                .execute(
                    "UPDATE conversations SET preview=?1, updated_at=?2, message_count=?3 WHERE id=?4",
                    params![p, now, count, id],
                )
                .map_err(|e| e.to_string())?,
            None => conn
                .execute(
                    "UPDATE conversations SET updated_at=?1, message_count=?2 WHERE id=?3",
                    params![now, count, id],
                )
                .map_err(|e| e.to_string())?,
        };
        Ok(())
    }

    pub fn update_conversation_title(&self, id: &str, title: &str, now: i64) -> Result<(), String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        conn.execute(
            "UPDATE conversations SET title=?1, updated_at=?2 WHERE id=?3",
            params![title, now, id],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    // ---- 消息 ----

    fn next_seq(conn: &Connection, conversation_id: &str) -> Result<i64, String> {
        conn.query_row(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id=?1",
            params![conversation_id],
            |r| r.get(0),
        )
        .map_err(|e| e.to_string())
    }

    pub fn append_message(
        &self,
        conversation_id: &str,
        id: &str,
        role: &str,
        payload: Value,
        ts: i64,
        ephemeral: bool,
    ) -> Result<Message, String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        let seq = Self::next_seq(&conn, conversation_id)?;
        let payload_str = payload.to_string();
        conn.execute(
            "INSERT INTO messages(id, conversation_id, seq, role, payload, ts, ephemeral)
             VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![id, conversation_id, seq, role, payload_str, ts, ephemeral as i64],
        )
        .map_err(|e| e.to_string())?;
        // 预览取 AI/user 文本；截尾；触碰元数据
        let preview = payload.get("text").and_then(|t| t.as_str()).map(|s| s.to_string());
        Self::trim_locked(&conn, conversation_id)?;
        Self::touch_conversation(&conn, conversation_id, preview.as_deref(), ts)?;
        Ok(Message {
            id: id.into(),
            conversation_id: conversation_id.into(),
            seq,
            role: role.into(),
            payload,
            ts,
            ephemeral,
        })
    }

    /// 更新既有消息的载荷（流式终态 / proc 收尾 / panelLink 文案更新）
    pub fn update_message_payload(
        &self,
        conversation_id: &str,
        id: &str,
        payload: Value,
    ) -> Result<(), String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        conn.execute(
            "UPDATE messages SET payload=?1 WHERE conversation_id=?2 AND id=?3",
            params![payload.to_string(), conversation_id, id],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    /// 截断到前 keep_count 条（重新生成/编辑重发：其后对话作废）
    pub fn truncate_messages(&self, conversation_id: &str, keep_count: i64) -> Result<(), String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        conn.execute(
            "DELETE FROM messages WHERE conversation_id=?1 AND seq > ?2",
            params![conversation_id, keep_count],
        )
        .map_err(|e| e.to_string())?;
        let now = now_ms();
        Self::touch_conversation(&conn, conversation_id, None, now)?;
        Ok(())
    }

    /// panelLink 查重：找最近一条 panelLink 消息，有则更新文案，无则新增。
    /// 返回最终消息（供调用方对齐 id）。
    pub fn upsert_panel_link(
        &self,
        conversation_id: &str,
        text: &str,
        id: &str,
        ts: i64,
    ) -> Result<Message, String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        let existing: Option<(String, i64)> = conn
            .query_row(
                "SELECT id, seq FROM messages
                 WHERE conversation_id=?1 AND json_extract(payload,'$.panelLink')=1
                 ORDER BY seq DESC LIMIT 1",
                params![conversation_id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .ok();
        if let Some((exist_id, seq)) = existing {
            let payload = serde_json::json!({ "text": text, "panelLink": true });
            conn.execute(
                "UPDATE messages SET payload=?1 WHERE conversation_id=?2 AND id=?3",
                params![payload.to_string(), conversation_id, exist_id],
            )
            .map_err(|e| e.to_string())?;
            return Ok(Message {
                id: exist_id,
                conversation_id: conversation_id.into(),
                seq,
                role: "ai".into(),
                payload,
                ts,
                ephemeral: false,
            });
        }
        drop(conn);
        let payload = serde_json::json!({ "text": text, "panelLink": true });
        self.append_message(conversation_id, id, "ai", payload, ts, false)
    }

    pub fn get_messages(&self, conversation_id: &str, limit: i64) -> Result<Vec<Message>, String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        let mut stmt = conn
            .prepare(
                "SELECT id, conversation_id, seq, role, payload, ts, ephemeral
                 FROM messages WHERE conversation_id=?1 ORDER BY seq ASC LIMIT ?2",
            )
            .map_err(|e| e.to_string())?;
        let rows = stmt
            .query_map(params![conversation_id, limit], |r| {
                let payload_str: String = r.get(4)?;
                Ok(Message {
                    id: r.get(0)?,
                    conversation_id: r.get(1)?,
                    seq: r.get(2)?,
                    role: r.get(3)?,
                    payload: serde_json::from_str(&payload_str).unwrap_or(Value::Null),
                    ts: r.get(5)?,
                    ephemeral: r.get::<_, i64>(6)? != 0,
                })
            })
            .map_err(|e| e.to_string())?;
        let mut out = Vec::new();
        for m in rows {
            out.push(m.map_err(|e| e.to_string())?);
        }
        Ok(out)
    }

    /// 截尾：超上限裁最老（含 ephemeral——敏感消息只活内存本就不落库，落库的都是非敏感）
    fn trim_locked(conn: &Connection, conversation_id: &str) -> Result<(), String> {
        conn.execute(
            "DELETE FROM messages WHERE conversation_id=?1 AND seq <=
             (SELECT COALESCE(MAX(seq), 0) - ?2 FROM messages WHERE conversation_id=?1)",
            params![conversation_id, MAX_MESSAGES_PER_CONV],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn delete_conversation(&self, id: &str) -> Result<(), String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        conn.execute("DELETE FROM messages WHERE conversation_id=?1", params![id])
            .map_err(|e| e.to_string())?;
        conn.execute("DELETE FROM conversations WHERE id=?1", params![id])
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn clear_all(&self) -> Result<(), String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        conn.execute_batch("DELETE FROM messages; DELETE FROM conversations; DELETE FROM meta;")
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    // ---- 活跃会话指针（M2：会话归属）----

    pub fn set_active_conversation(&self, id: &str) -> Result<(), String> {
        self.set_meta("active_conversation", id)
    }

    /// 活跃会话指针：大窗当前会话（小窗用固定 pet 指针，不共用）。
    pub fn get_active_conversation(&self) -> Result<Option<String>, String> {
        self.get_meta("active_conversation")
    }

    // ---- 小窗固定会话指针（方案 A：小窗永远用同一会话，不镜像活跃会话）----

    pub fn set_pet_conversation(&self, id: &str) -> Result<(), String> {
        self.set_meta("pet_conversation", id)
    }

    pub fn get_pet_conversation(&self) -> Result<Option<String>, String> {
        self.get_meta("pet_conversation")
    }

    fn get_meta(&self, key: &str) -> Result<Option<String>, String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        let v: Option<String> = conn
            .query_row("SELECT value FROM meta WHERE key=?1", params![key], |r| r.get(0))
            .ok();
        Ok(v)
    }

    fn set_meta(&self, key: &str, value: &str) -> Result<(), String> {
        let conn = self.conn.lock().map_err(|e| e.to_string())?;
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?1, ?2)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![key, value],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }
}

pub fn now_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}
