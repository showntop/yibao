"""译宝 Work Graph：Workspace / Mission / Artifact / Revision / Edge / WorkflowRun。

这是 Agent OS 的权威工作元数据层。ProjectStore 只保留兼容 façade；新对象关系、
不可变版本与流程状态均落在本 SQLite WAL 库中。大内容仍由领域插件或后续 BlobStore
持有，本层保存稳定身份、外部引用、哈希、关系与可恢复运行状态。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  root_path TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  definition_of_done TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_missions_workspace ON missions(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  mission_id TEXT REFERENCES missions(id) ON DELETE SET NULL,
  type TEXT NOT NULL,
  schema_version TEXT NOT NULL DEFAULT '1.0',
  lifecycle TEXT NOT NULL DEFAULT 'draft',
  external_ref TEXT NOT NULL,
  head_revision_id TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(workspace_id, type, external_ref)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_workspace ON artifacts(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS revisions (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  parent_revision_ids TEXT NOT NULL DEFAULT '[]',
  content_ref TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL,
  invocation_id TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revisions_artifact ON revisions(artifact_id, created_at DESC);

CREATE TABLE IF NOT EXISTS workspace_artifacts (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  linked_at REAL NOT NULL,
  detached_at REAL,
  PRIMARY KEY(workspace_id, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_artifacts_active
  ON workspace_artifacts(workspace_id, detached_at, linked_at DESC);

CREATE TABLE IF NOT EXISTS artifact_edges (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source_artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  target_artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL,
  invocation_id TEXT,
  created_at REAL NOT NULL,
  UNIQUE(workspace_id, source_artifact_id, target_artifact_id, relation, label)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON artifact_edges(source_artifact_id, relation);
CREATE INDEX IF NOT EXISTS idx_edges_target ON artifact_edges(target_artifact_id, relation);

CREATE TABLE IF NOT EXISTS workflow_definitions (
  id TEXT NOT NULL,
  version TEXT NOT NULL,
  domain TEXT NOT NULL,
  label TEXT NOT NULL,
  definition TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(id, version)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  definition_id TEXT NOT NULL,
  definition_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  current_stage_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  completed_at REAL,
  UNIQUE(workspace_id, mission_id, definition_id, definition_version),
  FOREIGN KEY(definition_id, definition_version)
    REFERENCES workflow_definitions(id, version)
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workspace ON workflow_runs(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS stage_instances (
  id TEXT PRIMARY KEY,
  workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  stage_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  input_artifact_ids TEXT NOT NULL DEFAULT '[]',
  output_artifact_ids TEXT NOT NULL DEFAULT '[]',
  started_at REAL,
  completed_at REAL,
  updated_at REAL NOT NULL,
  UNIQUE(workflow_run_id, stage_id)
);
CREATE INDEX IF NOT EXISTS idx_stage_instances_run ON stage_instances(workflow_run_id, ordinal);

CREATE TABLE IF NOT EXISTS invocations (
  id TEXT PRIMARY KEY,
  action_id TEXT NOT NULL,
  workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
  conversation_id TEXT NOT NULL DEFAULT '',
  surface TEXT NOT NULL DEFAULT '',
  tool_id TEXT NOT NULL,
  params_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  safe_result TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  started_at REAL NOT NULL,
  completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_invocations_workspace ON invocations(workspace_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_invocations_conversation ON invocations(conversation_id, started_at DESC);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  artifact_id TEXT REFERENCES artifacts(id) ON DELETE SET NULL,
  invocation_id TEXT REFERENCES invocations(id) ON DELETE SET NULL,
  claim TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  source_title TEXT NOT NULL DEFAULT '',
  publisher TEXT NOT NULL DEFAULT '',
  locator TEXT NOT NULL DEFAULT '{}',
  excerpt_hash TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.5,
  freshness TEXT NOT NULL DEFAULT '{}',
  captured_at REAL NOT NULL,
  verified_at REAL,
  UNIQUE(invocation_id, artifact_id, source_uri, claim)
);
CREATE INDEX IF NOT EXISTS idx_evidence_workspace ON evidence(workspace_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS outbox_events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
  invocation_id TEXT NOT NULL REFERENCES invocations(id) ON DELETE CASCADE,
  event_seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  applied_at REAL,
  UNIQUE(invocation_id, event_seq)
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(status, created_at);

CREATE TABLE IF NOT EXISTS migrations (
  source_kind TEXT NOT NULL,
  source_id TEXT NOT NULL,
  migrated_at REAL NOT NULL,
  PRIMARY KEY(source_kind, source_id)
);
"""


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _bounded_json(value: Any, limit: int = 16_000) -> str:
    """持久日志只保留安全结果的有界投影，避免正文/二进制结果撑爆 metadata 库。"""
    raw = _json(value)
    if len(raw) <= limit:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return _json({"truncated": True, "sha256": digest, "preview": raw[: min(2_000, limit)]})


def _external_content_ref(obj_type: str, ref: str) -> str:
    return f"external://{obj_type}/{ref}"


# 领域流程留在 Workflow Pack，不进入 Work Graph schema。artifact_patterns 只负责
# 把迁移期外部对象投影到 pack 节点；StageInstance 才是 UI 的流程真相。
BUILTIN_WORKFLOWS: tuple[dict, ...] = (
    {
        "id": "video.explainer",
        "version": "1.0.0",
        "domain": "video",
        "label": "视频创作",
        "matches": [r"视频", r"video", r"zimeiti", r"storyboard", r"timeline"],
        "stages": [
            {"id": "topic", "label": "选题", "artifact_patterns": [r"topic", r"brief"]},
            {"id": "evidence", "label": "证据", "artifact_patterns": [r"material", r"evidence", r"claim", r"research"]},
            {"id": "script", "label": "脚本", "artifact_patterns": [r"script", r"article", r"doc"]},
            {"id": "storyboard", "label": "分镜", "artifact_patterns": [r"storyboard", r"shot"]},
            {"id": "assets", "label": "素材", "artifact_patterns": [r"asset", r"image", r"visual"]},
            {"id": "voice", "label": "配音", "artifact_patterns": [r"voice", r"audio", r"narration"]},
            {"id": "compose", "label": "合成", "artifact_patterns": [r"timeline", r"composition"]},
            {"id": "deliver", "label": "交付", "artifact_patterns": [r"render", r"export", r"published"]},
        ],
    },
    {
        "id": "deck.presentation",
        "version": "1.0.0",
        "domain": "deck",
        "label": "演示文稿",
        "matches": [r"ppt", r"演示", r"幻灯", r"presentation", r"slide", r"deck"],
        "stages": [
            {"id": "brief", "label": "需求", "artifact_patterns": [r"brief", r"requirement"]},
            {"id": "claims", "label": "主张", "artifact_patterns": [r"claim", r"evidence", r"research"]},
            {"id": "storyline", "label": "故事线", "artifact_patterns": [r"storyline", r"outline"]},
            {"id": "slides", "label": "页面", "artifact_patterns": [r"slide", r"deck\.document"]},
            {"id": "visual", "label": "视觉", "artifact_patterns": [r"chart", r"image", r"visual", r"asset"]},
            {"id": "validate", "label": "校验", "artifact_patterns": [r"quality", r"validation", r"review"]},
            {"id": "export", "label": "导出", "artifact_patterns": [r"pptx", r"pdf", r"export", r"published"]},
        ],
    },
    {
        "id": "code.change",
        "version": "1.0.0",
        "domain": "code",
        "label": "软件交付",
        "matches": [r"代码", r"开发", r"code", r"coding", r"repo", r"patch"],
        "stages": [
            {"id": "issue", "label": "问题", "artifact_patterns": [r"issue", r"question", r"brief"]},
            {"id": "plan", "label": "方案", "artifact_patterns": [r"plan", r"design"]},
            {"id": "change", "label": "改动", "artifact_patterns": [r"patch", r"commit", r"source"]},
            {"id": "verify", "label": "验证", "artifact_patterns": [r"test", r"lint", r"quality"]},
            {"id": "deliver", "label": "交付", "artifact_patterns": [r"build", r"release", r"published"]},
        ],
    },
    {
        "id": "data.analysis",
        "version": "1.0.0",
        "domain": "data",
        "label": "数据分析",
        "matches": [r"数据", r"data", r"dataset", r"query", r"chart", r"analysis"],
        "stages": [
            {"id": "question", "label": "问题", "artifact_patterns": [r"question", r"brief"]},
            {"id": "data", "label": "数据", "artifact_patterns": [r"dataset", r"snapshot"]},
            {"id": "quality", "label": "质量", "artifact_patterns": [r"quality", r"profile"]},
            {"id": "analysis", "label": "分析", "artifact_patterns": [r"query", r"notebook", r"analysis"]},
            {"id": "insight", "label": "洞察", "artifact_patterns": [r"chart", r"insight"]},
            {"id": "deliver", "label": "交付", "artifact_patterns": [r"report", r"export", r"published"]},
        ],
    },
    {
        "id": "mission.general",
        "version": "1.0.0",
        "domain": "general",
        "label": "通用任务",
        "matches": [],
        "stages": [
            {"id": "understand", "label": "理解", "artifact_patterns": [r"brief", r"question", r"note"]},
            {"id": "advance", "label": "推进", "artifact_patterns": [r"draft", r"doc", r"plan"]},
            {"id": "verify", "label": "核验", "artifact_patterns": [r"quality", r"review", r"test"]},
            {"id": "deliver", "label": "交付", "artifact_patterns": [r"export", r"published", r"deliver"]},
        ],
    },
)


class WorkGraphStore:
    """线程安全的 Work Graph metadata store。"""

    def __init__(self, db_path: str):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(_SCHEMA)
            self._register_builtin_workflows_locked()
            # 上次进程若在 tool 执行中退出，running 不得永远冒充仍在执行。
            self._conn.execute(
                "UPDATE invocations SET status='interrupted',completed_at=? WHERE status='running'",
                (time.time(),),
            )
            self._conn.commit()
        self.drain_outbox()

    # ---------- definition / bootstrap ----------

    def _register_builtin_workflows_locked(self) -> None:
        now = time.time()
        for definition in BUILTIN_WORKFLOWS:
            self._conn.execute(
                "INSERT INTO workflow_definitions(id,version,domain,label,definition,created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(id,version) DO UPDATE SET "
                "domain=excluded.domain,label=excluded.label,definition=excluded.definition",
                (
                    definition["id"], definition["version"], definition["domain"],
                    definition["label"], _json(definition), now,
                ),
            )

    @staticmethod
    def normalize_workflow_definition(definition: dict, *, source_plugin: str = "core") -> dict:
        """校验并规范 Workflow Pack；插件只能扩数据，不能扩内核 schema。"""
        value = dict(definition)
        workflow_id = str(value.get("id") or "").strip()
        version = str(value.get("version") or "").strip()
        domain = str(value.get("domain") or "").strip()
        label = str(value.get("label") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", workflow_id):
            raise ValueError(f"非法 WorkflowDefinition id：{workflow_id!r}")
        if not version or not domain or not label:
            raise ValueError("WorkflowDefinition version/domain/label 不能为空")
        matches = value.get("matches") or []
        stages = value.get("stages") or []
        if not isinstance(matches, list) or not all(isinstance(pattern, str) for pattern in matches):
            raise ValueError("WorkflowDefinition matches 必须是字符串数组")
        if not isinstance(stages, list) or not stages:
            raise ValueError("WorkflowDefinition stages 不能为空")
        normalized_stages: list[dict] = []
        seen: set[str] = set()
        for stage in stages:
            if not isinstance(stage, dict):
                raise ValueError("WorkflowDefinition stage 必须是对象")
            stage_id = str(stage.get("id") or "").strip()
            stage_label = str(stage.get("label") or "").strip()
            patterns = stage.get("artifact_patterns") or []
            if not stage_id or stage_id in seen or not stage_label:
                raise ValueError(f"WorkflowDefinition stage id/label 非法：{stage!r}")
            if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
                raise ValueError(f"stage {stage_id} artifact_patterns 必须是字符串数组")
            # 加载期编译一次，坏正则不能拖到运行期才炸。
            for pattern in patterns:
                re.compile(pattern, re.IGNORECASE)
            seen.add(stage_id)
            normalized_stages.append({
                "id": stage_id,
                "label": stage_label,
                "artifact_patterns": patterns,
            })
        for pattern in matches:
            re.compile(pattern, re.IGNORECASE)
        return {
            "id": workflow_id,
            "version": version,
            "domain": domain,
            "label": label,
            "matches": matches,
            "stages": normalized_stages,
            "source_plugin": source_plugin,
        }

    def register_workflow(self, definition: dict, *, source_plugin: str = "plugin") -> dict:
        normalized = self.normalize_workflow_definition(definition, source_plugin=source_plugin)
        with self._lock:
            existing = self._conn.execute(
                "SELECT definition FROM workflow_definitions WHERE id=? AND version=?",
                (normalized["id"], normalized["version"]),
            ).fetchone()
            existing_source = (
                str(_decode(existing["definition"], {}).get("source_plugin") or "core")
                if existing else ""
            )
            if source_plugin != "core" and existing_source == "core":
                raise ValueError(
                    f"插件 {source_plugin} 不能覆盖 core WorkflowDefinition "
                    f"{normalized['id']}@{normalized['version']}"
                )
            self._conn.execute(
                "INSERT INTO workflow_definitions(id,version,domain,label,definition,created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(id,version) DO UPDATE SET "
                "domain=excluded.domain,label=excluded.label,definition=excluded.definition",
                (
                    normalized["id"], normalized["version"], normalized["domain"],
                    normalized["label"], _json(normalized), time.time(),
                ),
            )
            self._conn.commit()
        return normalized

    def workflow_for(self, name: str, object_types: list[str] | None = None) -> dict:
        text = " ".join([name, *(object_types or [])]).lower()
        with self._lock:
            rows = self._conn.execute(
                "SELECT definition FROM workflow_definitions ORDER BY created_at DESC,version DESC",
            ).fetchall()
        definitions = [_decode(row["definition"], {}) for row in rows]
        for definition in definitions:
            if definition.get("id") == "mission.general":
                continue
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in definition["matches"]):
                return definition
        return next(
            (definition for definition in definitions if definition.get("id") == "mission.general"),
            BUILTIN_WORKFLOWS[-1],
        )

    def migrate_projects(self, projects: list[dict]) -> None:
        """幂等迁移 legacy projects.json；迁移完成后旧 objects 不再是写入目标。"""
        for project in projects:
            pid = str(project.get("id") or "")
            name = str(project.get("name") or "").strip()
            if not pid or not name:
                continue
            objects = [o for o in (project.get("objects") or []) if isinstance(o, dict)]
            with self._lock:
                try:
                    self._ensure_workspace_locked(
                        pid, name, str(project.get("dir") or ""),
                        float(project.get("created_at") or time.time()),
                        float(project.get("touched_at") or time.time()),
                        [str(o.get("type") or "") for o in objects], name,
                    )
                    done = self._conn.execute(
                        "SELECT 1 FROM migrations WHERE source_kind='project' AND source_id=?", (pid,),
                    ).fetchone()
                    if not done:
                        for obj in objects:
                            obj_type = str(obj.get("type") or "").strip()
                            ref = str(obj.get("ref") or "").strip()
                            if obj_type and ref:
                                self._attach_external_locked(pid, obj_type, ref, "migration:projects.json")
                        self._conn.execute(
                            "INSERT INTO migrations(source_kind,source_id,migrated_at) VALUES('project',?,?)",
                            (pid, time.time()),
                        )
                        self._sync_workflow_locked(pid)
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise

    def create_workspace(
        self, workspace_id: str, name: str, root_path: str, *,
        created_at: float | None = None, objects: list[dict] | None = None,
        mission_title: str | None = None,
    ) -> dict:
        now = created_at or time.time()
        object_types = [str(o.get("type") or "") for o in (objects or []) if isinstance(o, dict)]
        with self._lock:
            try:
                if self._conn.execute("SELECT 1 FROM workspaces WHERE id=? OR name=?", (workspace_id, name)).fetchone():
                    raise ValueError(f"工作语境已存在：{name}")
                self._ensure_workspace_locked(
                    workspace_id, name, root_path, now, now, object_types,
                    (mission_title or name).strip() or name,
                )
                for obj in objects or []:
                    if not isinstance(obj, dict):
                        continue
                    obj_type = str(obj.get("type") or "").strip()
                    ref = str(obj.get("ref") or "").strip()
                    if obj_type and ref:
                        self._attach_external_locked(workspace_id, obj_type, ref, "project.create")
                self._sync_workflow_locked(workspace_id)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.workspace_view(workspace_id) or {}

    def _ensure_workspace_locked(
        self, workspace_id: str, name: str, root_path: str,
        created_at: float, updated_at: float, object_types: list[str], mission_title: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO workspaces(id,name,root_path,created_at,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,root_path=excluded.root_path,"
            "updated_at=MAX(workspaces.updated_at,excluded.updated_at)",
            (workspace_id, name, root_path, created_at, updated_at),
        )
        mission = self._conn.execute(
            "SELECT id FROM missions WHERE workspace_id=? ORDER BY created_at LIMIT 1", (workspace_id,),
        ).fetchone()
        if mission is None:
            mission_id = _id("mission")
            self._conn.execute(
                "INSERT INTO missions(id,workspace_id,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (mission_id, workspace_id, mission_title, "active", created_at, updated_at),
            )
        else:
            mission_id = str(mission["id"])
        run = self._conn.execute(
            "SELECT 1 FROM workflow_runs WHERE workspace_id=? LIMIT 1", (workspace_id,),
        ).fetchone()
        if run is None:
            definition = self.workflow_for(name, object_types)
            self._create_run_locked(workspace_id, mission_id, definition, created_at)

    def _create_run_locked(self, workspace_id: str, mission_id: str, definition: dict, now: float) -> str:
        run_id = _id("wfrun")
        stages = definition["stages"]
        self._conn.execute(
            "INSERT INTO workflow_runs(id,workspace_id,mission_id,definition_id,definition_version,status,"
            "current_stage_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                run_id, workspace_id, mission_id, definition["id"], definition["version"],
                "draft", stages[0]["id"], now, now,
            ),
        )
        for ordinal, stage in enumerate(stages):
            self._conn.execute(
                "INSERT INTO stage_instances(id,workflow_run_id,stage_id,ordinal,status,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (_id("stage"), run_id, stage["id"], ordinal, "pending", now),
            )
        return run_id

    # ---------- artifact graph ----------

    def attach_external_artifact(
        self, workspace_id: str, obj_type: str, ref: str, *, created_by: str = "project.attach",
    ) -> dict | None:
        obj_type, ref = obj_type.strip(), ref.strip()
        if not obj_type or not ref:
            raise ValueError("对象 type 与 ref 不能为空")
        with self._lock:
            if self._conn.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)).fetchone() is None:
                return None
            artifact_id = self._attach_external_locked(workspace_id, obj_type, ref, created_by)
            self._sync_workflow_locked(workspace_id)
            self._conn.commit()
        return self.artifact_view(artifact_id)

    def _attach_external_locked(self, workspace_id: str, obj_type: str, ref: str, created_by: str) -> str:
        now = time.time()
        row = self._conn.execute(
            "SELECT id FROM artifacts WHERE workspace_id=? AND type=? AND external_ref=?",
            (workspace_id, obj_type, ref),
        ).fetchone()
        if row is None:
            mission = self._conn.execute(
                "SELECT id FROM missions WHERE workspace_id=? ORDER BY created_at LIMIT 1", (workspace_id,),
            ).fetchone()
            artifact_id = _id("artifact")
            self._conn.execute(
                "INSERT INTO artifacts(id,workspace_id,mission_id,type,external_ref,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (artifact_id, workspace_id, mission["id"] if mission else None, obj_type, ref, now, now),
            )
            self._create_revision_locked(
                artifact_id,
                _external_content_ref(obj_type, ref),
                {"external_ref": ref, "source": created_by},
                created_by,
                None,
                [],
                now,
            )
        else:
            artifact_id = str(row["id"])
            self._conn.execute(
                "UPDATE artifacts SET lifecycle='draft',updated_at=? WHERE id=?", (now, artifact_id),
            )
        self._conn.execute(
            "INSERT INTO workspace_artifacts(workspace_id,artifact_id,linked_at,detached_at) VALUES(?,?,?,NULL) "
            "ON CONFLICT(workspace_id,artifact_id) DO UPDATE SET linked_at=excluded.linked_at,detached_at=NULL",
            (workspace_id, artifact_id, now),
        )
        self._conn.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (now, workspace_id))
        return artifact_id

    def detach_external_artifact(self, workspace_id: str, obj_type: str, ref: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT a.id FROM artifacts a JOIN workspace_artifacts wa ON wa.artifact_id=a.id "
                "WHERE a.workspace_id=? AND a.type=? AND a.external_ref=? AND wa.detached_at IS NULL",
                (workspace_id, obj_type, ref),
            ).fetchone()
            if row is None:
                return self._conn.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)).fetchone() is not None
            now = time.time()
            self._conn.execute(
                "UPDATE workspace_artifacts SET detached_at=? WHERE workspace_id=? AND artifact_id=?",
                (now, workspace_id, row["id"]),
            )
            self._conn.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (now, workspace_id))
            self._sync_workflow_locked(workspace_id)
            self._conn.commit()
            return True

    def create_revision(
        self, artifact_id: str, content_ref: str, *, metadata: dict | None = None,
        created_by: str = "agent", invocation_id: str | None = None,
        parent_revision_ids: list[str] | None = None,
    ) -> dict:
        with self._lock:
            artifact = self._conn.execute(
                "SELECT workspace_id,head_revision_id FROM artifacts WHERE id=?", (artifact_id,),
            ).fetchone()
            if artifact is None:
                raise ValueError(f"Artifact 不存在：{artifact_id}")
            parents = parent_revision_ids
            if parents is None:
                parents = [str(artifact["head_revision_id"])] if artifact["head_revision_id"] else []
            revision_id = self._create_revision_locked(
                artifact_id, content_ref, metadata or {}, created_by, invocation_id, parents, time.time(),
            )
            self._conn.commit()
        return self.revision_view(revision_id) or {}

    def _create_revision_locked(
        self, artifact_id: str, content_ref: str, metadata: dict, created_by: str,
        invocation_id: str | None, parents: list[str], now: float,
    ) -> str:
        revision_id = _id("revision")
        payload = {"content_ref": content_ref, "metadata": metadata, "parents": parents}
        content_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        self._conn.execute(
            "INSERT INTO revisions(id,artifact_id,parent_revision_ids,content_ref,content_hash,metadata,"
            "created_by,invocation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                revision_id, artifact_id, _json(parents), content_ref, content_hash,
                _json(metadata), created_by, invocation_id, now,
            ),
        )
        self._conn.execute(
            "UPDATE artifacts SET head_revision_id=?,updated_at=? WHERE id=?",
            (revision_id, now, artifact_id),
        )
        return revision_id

    def add_edge(
        self, source_artifact_id: str, target_artifact_id: str, relation: str, *,
        label: str = "", metadata: dict | None = None, created_by: str = "agent",
        invocation_id: str | None = None,
    ) -> dict:
        relation = relation.strip()
        if not relation:
            raise ValueError("ArtifactEdge relation 不能为空")
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,workspace_id FROM artifacts WHERE id IN (?,?)",
                (source_artifact_id, target_artifact_id),
            ).fetchall()
            by_id = {str(row["id"]): str(row["workspace_id"]) for row in rows}
            if source_artifact_id not in by_id or target_artifact_id not in by_id:
                raise ValueError("ArtifactEdge 两端对象必须存在")
            if by_id[source_artifact_id] != by_id[target_artifact_id]:
                raise ValueError("跨 Workspace 关系需要独立授权，不能直接建边")
            workspace_id = by_id[source_artifact_id]
            edge_id = _id("edge")
            now = time.time()
            self._conn.execute(
                "INSERT INTO artifact_edges(id,workspace_id,source_artifact_id,target_artifact_id,relation,"
                "label,metadata,created_by,invocation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id,source_artifact_id,target_artifact_id,relation,label) "
                "DO UPDATE SET metadata=excluded.metadata,invocation_id=excluded.invocation_id",
                (
                    edge_id, workspace_id, source_artifact_id, target_artifact_id, relation,
                    label, _json(metadata or {}), created_by, invocation_id, now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM artifact_edges WHERE workspace_id=? AND source_artifact_id=? "
                "AND target_artifact_id=? AND relation=? AND label=?",
                (workspace_id, source_artifact_id, target_artifact_id, relation, label),
            ).fetchone()
            self._conn.commit()
        return self._edge_dict(row)

    # ---------- invocation / evidence / outbox ----------

    def begin_invocation(
        self, *, action_id: str, workspace_id: str | None, conversation_id: str,
        surface: str, tool_id: str, params: dict,
    ) -> str:
        """tool 开始前即时落盘；params 只存哈希，敏感原文不进入 Work Graph。"""
        invocation_id = _id("invocation")
        try:
            canonical = _json(params)
        except (TypeError, ValueError):
            canonical = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
        params_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._lock:
            self._conn.execute(
                "INSERT INTO invocations(id,action_id,workspace_id,conversation_id,surface,tool_id,"
                "params_hash,status,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    invocation_id, action_id, workspace_id or None, conversation_id,
                    surface, tool_id, params_hash, "running", time.time(),
                ),
            )
            self._conn.commit()
        return invocation_id

    def complete_invocation(
        self, invocation_id: str, *, success: bool, safe_result: dict,
        error: str = "", work_events: list[dict] | None = None,
    ) -> None:
        """完成 Invocation，并在同一提交中写入待消费 outbox 事件。"""
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT workspace_id FROM invocations WHERE id=?", (invocation_id,),
            ).fetchone()
            if row is None:
                return
            workspace_id = str(row["workspace_id"] or "")
            self._conn.execute(
                "UPDATE invocations SET status=?,safe_result=?,error=?,completed_at=? WHERE id=?",
                (
                    "succeeded" if success else "failed",
                    _bounded_json(safe_result), error, now, invocation_id,
                ),
            )
            if success:
                for seq, event in enumerate(work_events or [], start=1):
                    event_type = str(event.get("event_type") or "").strip()
                    if not event_type:
                        continue
                    status = "pending" if workspace_id else "blocked"
                    last_error = "Session 未绑定 Workspace，事件未投影" if not workspace_id else ""
                    self._conn.execute(
                        "INSERT OR IGNORE INTO outbox_events(id,workspace_id,invocation_id,event_seq,"
                        "event_type,payload,status,last_error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            _id("outbox"), workspace_id or None, invocation_id, seq,
                            event_type, _bounded_json(event.get("payload") or {}),
                            status, last_error, now,
                        ),
                    )
            self._conn.commit()
        self.drain_outbox(invocation_id=invocation_id)

    def ingest_plugin_events(self, invocation_id: str, events: list[dict]) -> dict[str, str]:
        """接收 PluginDb transactional outbox；稳定 event id 构成 Host inbox 幂等键。"""
        if not events:
            return {}
        with self._lock:
            invocation = self._conn.execute(
                "SELECT workspace_id,status FROM invocations WHERE id=?", (invocation_id,),
            ).fetchone()
            if invocation is None:
                raise ValueError(f"Invocation 不存在：{invocation_id}")
            workspace_id = str(invocation["workspace_id"] or "")
            # PluginDb 的事件只会和业务写入同事务提交。若进程在 plugin commit 后、
            # Host complete 前崩溃，事件本身就是成功证据，可把 interrupted 恢复为 succeeded。
            if str(invocation["status"]) in ("running", "interrupted"):
                self._conn.execute(
                    "UPDATE invocations SET status='succeeded',error='',completed_at=COALESCE(completed_at,?) "
                    "WHERE id=?",
                    (time.time(), invocation_id),
                )
            for event in events:
                event_id = str(event.get("id") or "").strip()
                event_type = str(event.get("event_type") or "").strip()
                event_seq = int(event.get("event_seq") or 0)
                if not event_id.startswith("pluginoutbox_") or not event_type or event_seq <= 0:
                    raise ValueError("PluginDb outbox event id/type/seq 非法")
                payload = _bounded_json(event.get("payload") or {})
                existing = self._conn.execute(
                    "SELECT invocation_id,event_seq,event_type,payload FROM outbox_events WHERE id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["invocation_id"]) != invocation_id
                        or int(existing["event_seq"]) != event_seq
                        or str(existing["event_type"]) != event_type
                        or str(existing["payload"]) != payload
                    ):
                        raise ValueError(f"PluginDb outbox event id 冲突：{event_id}")
                    continue
                status = "pending" if workspace_id else "blocked"
                last_error = "Session 未绑定 Workspace，事件未投影" if not workspace_id else ""
                self._conn.execute(
                    "INSERT INTO outbox_events(id,workspace_id,invocation_id,event_seq,event_type,payload,"
                    "status,last_error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        event_id, workspace_id or None, invocation_id, event_seq, event_type,
                        payload, status, last_error, time.time(),
                    ),
                )
            self._conn.commit()
        self.drain_outbox(invocation_id=invocation_id)
        ids = [str(event["id"]) for event in events]
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id,status FROM outbox_events WHERE id IN ({placeholders})", ids,
            ).fetchall()
        return {str(row["id"]): str(row["status"]) for row in rows}

    def drain_outbox(self, *, invocation_id: str | None = None) -> int:
        """幂等消费 Host outbox；崩溃后重启会继续 pending/failed 事件。"""
        applied = 0
        with self._lock:
            sql = (
                "SELECT * FROM outbox_events WHERE status IN ('pending','failed') AND attempts < 5"
            )
            args: tuple = ()
            if invocation_id:
                sql += " AND invocation_id=?"
                args = (invocation_id,)
            sql += " ORDER BY created_at,event_seq"
            rows = self._conn.execute(sql, args).fetchall()
            for row in rows:
                try:
                    self._apply_outbox_event_locked(row)
                    self._conn.execute(
                        "UPDATE outbox_events SET status='applied',attempts=attempts+1,last_error='',"
                        "applied_at=? WHERE id=?",
                        (time.time(), row["id"]),
                    )
                    self._conn.commit()
                    applied += 1
                except Exception as exc:
                    self._conn.rollback()
                    self._conn.execute(
                        "UPDATE outbox_events SET status='failed',attempts=attempts+1,last_error=? WHERE id=?",
                        (str(exc)[:500], row["id"]),
                    )
                    self._conn.commit()
        return applied

    def _apply_outbox_event_locked(self, row: sqlite3.Row) -> None:
        workspace_id = str(row["workspace_id"] or "")
        if not workspace_id:
            raise ValueError("outbox 事件缺少 Workspace")
        if self._conn.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)).fetchone() is None:
            raise ValueError(f"Workspace 不存在：{workspace_id}")
        payload = _decode(row["payload"], {})
        event_type = str(row["event_type"])
        invocation_id = str(row["invocation_id"])
        if event_type == "artifact.upsert":
            self._upsert_event_artifact_locked(workspace_id, invocation_id, payload)
        elif event_type == "evidence.capture":
            self._capture_evidence_locked(workspace_id, invocation_id, payload)
        else:
            raise ValueError(f"未知 Work Graph 事件：{event_type}")
        self._sync_workflow_locked(workspace_id)

    def _upsert_event_artifact_locked(
        self, workspace_id: str, invocation_id: str, payload: dict,
    ) -> str:
        artifact_type = str(payload.get("artifact_type") or "").strip()
        external_ref = str(payload.get("ref") or "").strip()
        if not artifact_type or not external_ref:
            raise ValueError("artifact.upsert 缺少 artifact_type/ref")
        content_ref = str(payload.get("content_ref") or "").strip()
        if not content_ref:
            content_ref = f"event://{invocation_id}/{artifact_type}/{external_ref}"
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        lifecycle = str(payload.get("lifecycle") or "draft").strip()
        now = time.time()
        row = self._conn.execute(
            "SELECT id,head_revision_id FROM artifacts WHERE workspace_id=? AND type=? AND external_ref=?",
            (workspace_id, artifact_type, external_ref),
        ).fetchone()
        if row is None:
            mission = self._conn.execute(
                "SELECT id FROM missions WHERE workspace_id=? ORDER BY created_at LIMIT 1", (workspace_id,),
            ).fetchone()
            artifact_id = _id("artifact")
            self._conn.execute(
                "INSERT INTO artifacts(id,workspace_id,mission_id,type,lifecycle,external_ref,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    artifact_id, workspace_id, mission["id"] if mission else None,
                    artifact_type, lifecycle, external_ref, now, now,
                ),
            )
            head_revision_id = ""
        else:
            artifact_id = str(row["id"])
            head_revision_id = str(row["head_revision_id"] or "")
            self._conn.execute(
                "UPDATE artifacts SET lifecycle=?,updated_at=? WHERE id=?",
                (lifecycle, now, artifact_id),
            )
        current_ref = ""
        if head_revision_id:
            revision = self._conn.execute(
                "SELECT content_ref FROM revisions WHERE id=?", (head_revision_id,),
            ).fetchone()
            current_ref = str(revision["content_ref"] if revision else "")
        if current_ref != content_ref:
            parents = [head_revision_id] if head_revision_id else []
            self._create_revision_locked(
                artifact_id, content_ref, metadata, "invocation", invocation_id, parents, now,
            )
        self._conn.execute(
            "INSERT INTO workspace_artifacts(workspace_id,artifact_id,linked_at,detached_at) VALUES(?,?,?,NULL) "
            "ON CONFLICT(workspace_id,artifact_id) DO UPDATE SET detached_at=NULL,linked_at=excluded.linked_at",
            (workspace_id, artifact_id, now),
        )
        self._conn.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (now, workspace_id))
        return artifact_id

    def _capture_evidence_locked(
        self, workspace_id: str, invocation_id: str, payload: dict,
    ) -> str:
        source_uri = str(payload.get("source_uri") or "").strip()
        claim = str(payload.get("claim") or "").strip()
        external_ref = str(payload.get("ref") or source_uri or "").strip()
        if not external_ref or not claim:
            raise ValueError("evidence.capture 缺少 ref/source_uri 或 claim")
        artifact_payload = {
            "artifact_type": str(payload.get("artifact_type") or "research.evidence"),
            "ref": external_ref,
            "content_ref": source_uri or f"evidence://{external_ref}",
            "metadata": payload.get("metadata") or {},
            "lifecycle": str(payload.get("lifecycle") or "draft"),
        }
        artifact_id = self._upsert_event_artifact_locked(workspace_id, invocation_id, artifact_payload)
        evidence_id = _id("evidence")
        self._conn.execute(
            "INSERT OR IGNORE INTO evidence(id,workspace_id,artifact_id,invocation_id,claim,source_uri,"
            "source_title,publisher,locator,excerpt_hash,confidence,freshness,captured_at,verified_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id, workspace_id, artifact_id, invocation_id, claim,
                source_uri or f"local://{external_ref}", str(payload.get("source_title") or ""),
                str(payload.get("publisher") or ""), _json(payload.get("locator") or {}),
                str(payload.get("excerpt_hash") or ""), float(payload.get("confidence") or 0.5),
                _json(payload.get("freshness") or {}), time.time(),
                float(payload["verified_at"]) if payload.get("verified_at") is not None else None,
            ),
        )
        row = self._conn.execute(
            "SELECT id FROM evidence WHERE invocation_id=? AND artifact_id=? AND source_uri=? AND claim=?",
            (invocation_id, artifact_id, source_uri or f"local://{external_ref}", claim),
        ).fetchone()
        return str(row["id"] if row else evidence_id)

    def invocation_view(self, invocation_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM invocations WHERE id=?", (invocation_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "action_id": str(row["action_id"]),
            "workspace_id": str(row["workspace_id"] or ""),
            "conversation_id": str(row["conversation_id"]),
            "surface": str(row["surface"]),
            "tool_id": str(row["tool_id"]),
            "params_hash": str(row["params_hash"]),
            "status": str(row["status"]),
            "safe_result": _decode(row["safe_result"], {}),
            "error": str(row["error"]),
        }

    def invocation_views(
        self, *, workspace_id: str | None = None, conversation_id: str | None = None,
    ) -> list[dict]:
        with self._lock:
            sql = "SELECT id FROM invocations"
            args: tuple = ()
            if workspace_id is not None:
                sql += " WHERE workspace_id=?"
                args = (workspace_id,)
            elif conversation_id is not None:
                sql += " WHERE conversation_id=?"
                args = (conversation_id,)
            sql += " ORDER BY started_at"
            ids = [str(row["id"]) for row in self._conn.execute(sql, args).fetchall()]
        return [view for invocation_id in ids if (view := self.invocation_view(invocation_id)) is not None]

    def outbox_views(self, invocation_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox_events WHERE invocation_id=? ORDER BY event_seq", (invocation_id,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]), "event_seq": int(row["event_seq"]),
                "event_type": str(row["event_type"]), "status": str(row["status"]),
                "attempts": int(row["attempts"]), "last_error": str(row["last_error"]),
                "payload": _decode(row["payload"], {}),
            }
            for row in rows
        ]

    def evidence_views(self, workspace_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM evidence WHERE workspace_id=? ORDER BY captured_at", (workspace_id,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]), "artifact_id": str(row["artifact_id"] or ""),
                "invocation_id": str(row["invocation_id"] or ""), "claim": str(row["claim"]),
                "source_uri": str(row["source_uri"]), "confidence": float(row["confidence"]),
            }
            for row in rows
        ]

    def blob_refs(self) -> set[str]:
        """BlobStore GC 的保守 live set：Revision 与未清除 outbox payload 都算引用。"""
        refs: set[str] = set()

        def collect(value: Any) -> None:
            if isinstance(value, str) and value.startswith("blob://sha256/"):
                refs.add(value)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        with self._lock:
            for row in self._conn.execute(
                "SELECT content_ref FROM revisions WHERE content_ref LIKE 'blob://sha256/%'",
            ).fetchall():
                refs.add(str(row["content_ref"]))
            for row in self._conn.execute("SELECT payload FROM outbox_events").fetchall():
                collect(_decode(row["payload"], {}))
        return refs

    # ---------- workflow projection ----------

    def _sync_workflow_locked(self, workspace_id: str) -> None:
        run = self._conn.execute(
            "SELECT * FROM workflow_runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if run is None:
            return
        definition_row = self._conn.execute(
            "SELECT definition FROM workflow_definitions WHERE id=? AND version=?",
            (run["definition_id"], run["definition_version"]),
        ).fetchone()
        definition = _decode(definition_row["definition"] if definition_row else None, {})
        stages = definition.get("stages") or []
        artifacts = self._conn.execute(
            "SELECT a.id,a.type FROM artifacts a JOIN workspace_artifacts wa ON wa.artifact_id=a.id "
            "WHERE wa.workspace_id=? AND wa.detached_at IS NULL",
            (workspace_id,),
        ).fetchall()
        highest = 0
        matched_any = False
        outputs: dict[int, list[str]] = {}
        for idx, stage in enumerate(stages):
            patterns = stage.get("artifact_patterns") or []
            ids = [
                str(a["id"]) for a in artifacts
                if any(re.search(pattern, str(a["type"]), re.IGNORECASE) for pattern in patterns)
            ]
            if ids:
                outputs[idx] = ids
                highest = max(highest, idx)
                matched_any = True
        now = time.time()
        completed = bool(stages and matched_any and highest == len(stages) - 1)
        run_status = "completed" if completed else ("running" if artifacts else "draft")
        for idx, stage in enumerate(stages):
            if completed or idx < highest:
                status = "completed"
            elif idx == highest:
                status = "running" if artifacts else "pending"
            else:
                status = "pending"
            self._conn.execute(
                "UPDATE stage_instances SET status=?,output_artifact_ids=?,"
                "started_at=CASE WHEN ? IN ('running','completed') THEN COALESCE(started_at,?) ELSE NULL END,"
                "completed_at=CASE WHEN ?='completed' THEN COALESCE(completed_at,?) ELSE NULL END,updated_at=? "
                "WHERE workflow_run_id=? AND stage_id=?",
                (
                    status, _json(outputs.get(idx, [])), status, now, status, now, now,
                    run["id"], stage["id"],
                ),
            )
        current_stage_id = stages[highest]["id"] if stages else ""
        self._conn.execute(
            "UPDATE workflow_runs SET status=?,current_stage_id=?,updated_at=?,completed_at=? WHERE id=?",
            (run_status, current_stage_id, now, now if completed else None, run["id"]),
        )

    # ---------- read models ----------

    def list_workspace_views(self) -> list[dict]:
        with self._lock:
            ids = [str(row["id"]) for row in self._conn.execute(
                "SELECT id FROM workspaces ORDER BY updated_at DESC",
            ).fetchall()]
        return [view for workspace_id in ids if (view := self.workspace_view(workspace_id)) is not None]

    def workspace_view(self, workspace_id: str) -> dict | None:
        with self._lock:
            workspace = self._conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            if workspace is None:
                return None
            mission = self._conn.execute(
                "SELECT * FROM missions WHERE workspace_id=? ORDER BY created_at LIMIT 1", (workspace_id,),
            ).fetchone()
            artifact_rows = self._conn.execute(
                "SELECT a.* FROM artifacts a JOIN workspace_artifacts wa ON wa.artifact_id=a.id "
                "WHERE wa.workspace_id=? AND wa.detached_at IS NULL ORDER BY wa.linked_at",
                (workspace_id,),
            ).fetchall()
            run = self._workflow_view_locked(workspace_id)
            return {
                "id": str(workspace["id"]),
                "name": str(workspace["name"]),
                "created_at": float(workspace["created_at"]),
                "touched_at": float(workspace["updated_at"]),
                "dir": str(workspace["root_path"]),
                "objects": [
                    {
                        "type": str(row["type"]),
                        "ref": str(row["external_ref"]),
                        "artifact_id": str(row["id"]),
                        "revision_id": str(row["head_revision_id"] or ""),
                        "lifecycle": str(row["lifecycle"]),
                    }
                    for row in artifact_rows
                ],
                "mission": self._mission_dict(mission) if mission else None,
                "workflow_run": run,
            }

    def _workflow_view_locked(self, workspace_id: str) -> dict | None:
        run = self._conn.execute(
            "SELECT wr.*,wd.domain,wd.label,wd.definition FROM workflow_runs wr "
            "JOIN workflow_definitions wd ON wd.id=wr.definition_id AND wd.version=wr.definition_version "
            "WHERE wr.workspace_id=? ORDER BY wr.created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if run is None:
            return None
        definition = _decode(run["definition"], {})
        instance_rows = self._conn.execute(
            "SELECT * FROM stage_instances WHERE workflow_run_id=? ORDER BY ordinal", (run["id"],),
        ).fetchall()
        by_id = {str(row["stage_id"]): row for row in instance_rows}
        stages = []
        current_index = 0
        for idx, stage in enumerate(definition.get("stages") or []):
            row = by_id.get(str(stage["id"]))
            if str(stage["id"]) == str(run["current_stage_id"]):
                current_index = idx
            stages.append({
                "id": str(stage["id"]),
                "label": str(stage["label"]),
                "status": str(row["status"] if row else "pending"),
                "output_artifact_ids": _decode(row["output_artifact_ids"] if row else None, []),
            })
        return {
            "id": str(run["id"]),
            "definition_id": str(run["definition_id"]),
            "definition_version": str(run["definition_version"]),
            "domain": str(run["domain"]),
            "label": str(run["label"]),
            "status": str(run["status"]),
            "current_stage_id": str(run["current_stage_id"]),
            "current_stage_index": current_index,
            "stages": stages,
            "updated_at": float(run["updated_at"]),
        }

    def artifact_view(self, artifact_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if row is None:
                return None
            revisions = self._conn.execute(
                "SELECT * FROM revisions WHERE artifact_id=? ORDER BY created_at", (artifact_id,),
            ).fetchall()
            edges = self._conn.execute(
                "SELECT * FROM artifact_edges WHERE source_artifact_id=? OR target_artifact_id=? "
                "ORDER BY created_at",
                (artifact_id, artifact_id),
            ).fetchall()
            return {
                "id": str(row["id"]),
                "workspace_id": str(row["workspace_id"]),
                "mission_id": str(row["mission_id"] or ""),
                "type": str(row["type"]),
                "schema_version": str(row["schema_version"]),
                "lifecycle": str(row["lifecycle"]),
                "external_ref": str(row["external_ref"]),
                "head_revision_id": str(row["head_revision_id"] or ""),
                "revisions": [self._revision_dict(revision) for revision in revisions],
                "edges": [self._edge_dict(edge) for edge in edges],
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
            }

    def revision_view(self, revision_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
        return self._revision_dict(row) if row else None

    def touch_workspace(self, workspace_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (time.time(), workspace_id))
            self._conn.commit()

    @staticmethod
    def _mission_dict(row: sqlite3.Row) -> dict:
        return {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "status": str(row["status"]),
            "definition_of_done": _decode(row["definition_of_done"], {}),
        }

    @staticmethod
    def _revision_dict(row: sqlite3.Row) -> dict:
        return {
            "id": str(row["id"]),
            "artifact_id": str(row["artifact_id"]),
            "parent_revision_ids": _decode(row["parent_revision_ids"], []),
            "content_ref": str(row["content_ref"]),
            "content_hash": str(row["content_hash"]),
            "metadata": _decode(row["metadata"], {}),
            "created_by": str(row["created_by"]),
            "invocation_id": str(row["invocation_id"] or ""),
            "created_at": float(row["created_at"]),
        }

    @staticmethod
    def _edge_dict(row: sqlite3.Row) -> dict:
        return {
            "id": str(row["id"]),
            "workspace_id": str(row["workspace_id"]),
            "source_artifact_id": str(row["source_artifact_id"]),
            "target_artifact_id": str(row["target_artifact_id"]),
            "relation": str(row["relation"]),
            "label": str(row["label"]),
            "metadata": _decode(row["metadata"], {}),
            "created_by": str(row["created_by"]),
            "invocation_id": str(row["invocation_id"] or ""),
            "created_at": float(row["created_at"]),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
