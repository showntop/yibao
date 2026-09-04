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

from .log import log


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
  capability_plan TEXT NOT NULL DEFAULT '',
  blocked_reason TEXT NOT NULL DEFAULT '',
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
  checkpoint TEXT NOT NULL DEFAULT '{}',
  checkpoint_version INTEGER NOT NULL DEFAULT 0,
  checkpointed_at REAL,
  checkpoint_invocation_id TEXT,
  started_at REAL,
  completed_at REAL,
  updated_at REAL NOT NULL,
  UNIQUE(workflow_run_id, stage_id)
);
CREATE INDEX IF NOT EXISTS idx_stage_instances_run ON stage_instances(workflow_run_id, ordinal);

CREATE TABLE IF NOT EXISTS durable_executions (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  stage_id TEXT NOT NULL,
  invocation_id TEXT REFERENCES invocations(id) ON DELETE SET NULL,
  capability_id TEXT NOT NULL,
  provider_id TEXT NOT NULL DEFAULT '',
  provider_candidates TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  request TEXT NOT NULL,
  checkpoint TEXT NOT NULL DEFAULT '{}',
  checkpoint_version INTEGER NOT NULL DEFAULT 0,
  progress REAL NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT NOT NULL,
  cancel_mode TEXT NOT NULL DEFAULT 'checkpoint',
  resume_supported INTEGER NOT NULL DEFAULT 1,
  error TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  started_at REAL,
  updated_at REAL NOT NULL,
  completed_at REAL,
  UNIQUE(workspace_id, capability_id, idempotency_key),
  FOREIGN KEY(workflow_run_id, stage_id)
    REFERENCES stage_instances(workflow_run_id, stage_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_durable_executions_stage
  ON durable_executions(workflow_run_id, stage_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_durable_executions_status
  ON durable_executions(status, updated_at);

CREATE TABLE IF NOT EXISTS durable_attempts (
  id TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL REFERENCES durable_executions(id) ON DELETE CASCADE,
  attempt INTEGER NOT NULL,
  provider_id TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  started_at REAL NOT NULL,
  completed_at REAL,
  UNIQUE(execution_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_durable_attempts_execution
  ON durable_attempts(execution_id, attempt);

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

CREATE TABLE IF NOT EXISTS gates (
  id TEXT PRIMARY KEY,
  workflow_run_id TEXT,
  invocation_id TEXT,
  conversation_id TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '{}',
  risk INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  preview_ref TEXT,
  diff_ref TEXT,
  decided_by TEXT,
  decided_at REAL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gates_run ON gates(workflow_run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gates_conversation ON gates(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gates_status ON gates(status, created_at DESC);

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


def _checked_json(value: Any, label: str, limit: int = 64 * 1024) -> str:
    """可恢复协议不能截断：超限就拒绝，大内容必须转 Artifact/Blob。"""
    raw = _json(value)
    if len(raw.encode("utf-8")) > limit:
        raise ValueError(f"{label} 超过 {limit // 1024}KiB，大内容应存 Artifact/Blob")
    return raw


def _external_content_ref(obj_type: str, ref: str) -> str:
    return f"external://{obj_type}/{ref}"


_DURABLE_TERMINAL = {"completed", "failed", "cancelled"}
_DURABLE_ACTIVE = {"queued", "running", "resuming", "checkpointing", "cancel_requested", "interrupted"}

# 能力预检只管理「还没开工」的 run；running 及以后由 DAG/产物事实接管，plan 不再重算。
_CAPABILITY_OPEN_RUN_STATUSES = ("draft", "ready", "blocked")

# Typed artifact registry 的内核内置类型。梳理结论：内核自身不经 attach 产出任何领域
# 对象——现存合法用法（project.create 初始 objects、project.attach）挂的都是插件域
# 类型（zimeiti.topic、video.script、deck.* 等），由插件 work_outputs 声明经能力索引
# 注册。本集合是「核心公认类型」的显式扩展点，当前为空是有意的，勿把插件域类型烤进内核。
CORE_ARTIFACT_TYPES: frozenset[str] = frozenset()


# 领域流程留在 Workflow Pack，不进入 Work Graph schema。depends_on 和
# acceptance 都是数据；StageInstance 才是某次执行的流程真相。
BUILTIN_WORKFLOWS: tuple[dict, ...] = (
    {
        "id": "video.explainer",
        "version": "1.0.0",
        "domain": "video",
        "label": "视频创作",
        "matches": [r"视频", r"video", r"zimeiti", r"storyboard", r"timeline"],
        "stages": [
            {"id": "topic", "label": "选题", "depends_on": [], "acceptance": [{"artifact_patterns": [r"^zimeiti\.topic$", r"^topic\.", r"^brief\."]}]},
            {"id": "evidence", "label": "证据", "depends_on": ["topic"], "acceptance": [{"artifact_patterns": [r"^material", r"^evidence\.", r"^research\.", r"^claim"]}]},
            {"id": "script", "label": "脚本", "depends_on": ["evidence"], "acceptance": [{"artifact_patterns": [r"^video\.script$", r"^script\.", r"^article", r"^doc\."]}]},
            {"id": "storyboard", "label": "分镜", "depends_on": ["script"], "acceptance": [{"artifact_patterns": [r"storyboard", r"shot"]}]},
            {"id": "assets", "label": "素材", "depends_on": ["storyboard"], "acceptance": [{"artifact_patterns": [r"asset", r"image"]}]},
            {"id": "voice", "label": "配音", "depends_on": ["storyboard"], "acceptance": [{"artifact_patterns": [r"voice", r"audio", r"narration"]}]},
            {"id": "compose", "label": "合成", "depends_on": ["assets", "voice"], "acceptance": [{"artifact_patterns": [r"timeline", r"composition"]}]},
            {"id": "deliver", "label": "交付", "depends_on": ["compose"], "acceptance": [{"artifact_patterns": [r"^video\.render$", r"^render\.", r"^export\.", r"^published"]}]},
        ],
    },
    {
        "id": "deck.presentation",
        "version": "1.0.0",
        "domain": "deck",
        "label": "演示文稿",
        "matches": [r"ppt", r"演示", r"幻灯", r"presentation", r"slide", r"deck"],
        "stages": [
            {"id": "brief", "label": "需求", "depends_on": [], "acceptance": [{"artifact_patterns": [r"^deck\.brief$", r"^brief\."]}]},
            {"id": "claims", "label": "主张", "depends_on": ["brief"], "acceptance": [{"artifact_patterns": [r"^deck\.claim", r"^claim"]}]},
            {"id": "storyline", "label": "故事线", "depends_on": ["claims"], "acceptance": [{"artifact_patterns": [r"storyline", r"outline"]}]},
            {"id": "slides", "label": "页面", "depends_on": ["storyline"], "acceptance": [{"artifact_patterns": [r"slide", r"deck\.document"]}]},
            {"id": "visual", "label": "视觉", "depends_on": ["storyline"], "acceptance": [{"artifact_patterns": [r"^deck\.visual", r"^chart\.", r"^image\."]}]},
            {"id": "validate", "label": "校验", "depends_on": ["slides", "visual"], "acceptance": [{"artifact_patterns": [r"quality", r"validation", r"review"]}]},
            {"id": "export", "label": "导出", "depends_on": ["validate"], "acceptance": [{"artifact_patterns": [r"pptx", r"pdf", r"export", r"published"]}]},
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


def build_capability_index(tools: Any) -> dict[str, list[dict]]:
    """从已注册 tool 的 work_outputs 构建能力索引：artifact_type → providers。

    代码工具（类属性 work_outputs）与声明式工具（manifest [tool.work_output] /
    [[tool.work_outputs]]，经 normalize_work_output）是同一形态。kind="artifact" 与
    kind="evidence" 都算能力：evidence.capture 在 Work Graph 里同样 upsert 一个
    artifact_type 的 Artifact（见 _capture_evidence_locked），能满足 acceptance；
    edge/checkpoint 不产出独立验收产物，不计入。
    """
    buckets: dict[str, dict[str, dict]] = {}
    for tool in tools:
        tool_id = str(getattr(tool, "id", "") or "").strip()
        if not tool_id:
            continue
        plugin_id = tool_id.rsplit(".", 1)[0] if "." in tool_id else ""
        label = str(getattr(tool, "label", "") or "").strip() or tool_id
        for output in getattr(tool, "work_outputs", ()) or ():
            if not isinstance(output, dict) or str(output.get("kind") or "") not in ("artifact", "evidence"):
                continue
            artifact_type = str(output.get("artifact_type") or "").strip()
            if not artifact_type:
                continue
            bucket = buckets.setdefault(artifact_type, {})
            bucket[tool_id] = {
                "plugin_id": plugin_id, "tool_id": tool_id,
                "label": label, "artifact_type": artifact_type,
                "degraded": bool(getattr(tool, "degraded", False)),
            }
    return {artifact_type: list(bucket.values()) for artifact_type, bucket in buckets.items()}


def _preflight_policy(definition: dict) -> str:
    """能力预检策略：enforce=缺 provider 即 capability-blocked；info=只算 plan 不干预状态机。

    mission.general 的 acceptance 是 brief/draft/doc 这类通用对象模式，不面向领域
    交付物——没有 provider 是常态而非缺口，强制 blocked 会误伤所有通用项目。
    """
    policy = str(definition.get("capability_preflight") or "").strip()
    if policy in ("enforce", "info"):
        return policy
    return "info" if str(definition.get("id") or "") == "mission.general" else "enforce"


def _capability_blocked_reason(plan: dict) -> str:
    labels = [str(stage["label"]) for stage in plan.get("stages") or [] if stage.get("status") == "missing"]
    return "、".join(labels) + " 缺能力 provider" if labels else ""


class WorkGraphStore:
    """线程安全的 Work Graph metadata store。"""

    def __init__(self, db_path: str):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        # 能力索引（artifact_type → providers）：server 在插件加载完成后经
        # set_capability_providers 注入；注入前为空，plan 只反映「暂无已注册能力」。
        self._capability_index: dict[str, list[dict]] = {}
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(_SCHEMA)
            self._ensure_stage_checkpoint_columns_locked()
            self._ensure_workflow_capability_columns_locked()
            self._register_builtin_workflows_locked()
            for row in self._conn.execute("SELECT id FROM workspaces").fetchall():
                self._sync_workflow_locked(str(row["id"]))
            # 上次进程若在 tool 执行中退出，running 不得永远冒充仍在执行。
            self._conn.execute(
                "UPDATE invocations SET status='interrupted',completed_at=? WHERE status='running'",
                (time.time(),),
            )
            self._conn.execute(
                "UPDATE durable_attempts SET status='interrupted',completed_at=? "
                "WHERE status='running'",
                (time.time(),),
            )
            self._conn.execute(
                "UPDATE durable_executions SET status='interrupted',updated_at=? "
                "WHERE status IN ('running','resuming','checkpointing')",
                (time.time(),),
            )
            # 上次进程若死在确认等待中，pending Gate 不得永远冒充待决：
            # 标 expired（无人做过决策，decided_* 保持空），与 interrupted 恢复同一拍。
            self._conn.execute("UPDATE gates SET status='expired' WHERE status='pending'")
            self._conn.commit()
        self.drain_outbox()

    # ---------- definition / bootstrap ----------

    def _ensure_stage_checkpoint_columns_locked(self) -> None:
        """SQLite CREATE TABLE IF NOT EXISTS 不会给旧表补列，显式演进持久节点。"""
        existing = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(stage_instances)").fetchall()
        }
        additions = {
            "checkpoint": "TEXT NOT NULL DEFAULT '{}'",
            "checkpoint_version": "INTEGER NOT NULL DEFAULT 0",
            "checkpointed_at": "REAL",
            "checkpoint_invocation_id": "TEXT",
        }
        for column, ddl in additions.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE stage_instances ADD COLUMN {column} {ddl}")

    def _ensure_workflow_capability_columns_locked(self) -> None:
        """能力预检（§4.2）的持久列：capability_plan（JSON）+ blocked_reason。"""
        existing = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }
        additions = {
            "capability_plan": "TEXT NOT NULL DEFAULT ''",
            "blocked_reason": "TEXT NOT NULL DEFAULT ''",
        }
        for column, ddl in additions.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE workflow_runs ADD COLUMN {column} {ddl}")

    def _register_builtin_workflows_locked(self) -> None:
        now = time.time()
        for raw in BUILTIN_WORKFLOWS:
            definition = self.normalize_workflow_definition(raw, source_plugin="core")
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
        for ordinal, stage in enumerate(stages):
            if not isinstance(stage, dict):
                raise ValueError("WorkflowDefinition stage 必须是对象")
            stage_id = str(stage.get("id") or "").strip()
            stage_label = str(stage.get("label") or "").strip()
            if not stage_id or stage_id in seen or not stage_label:
                raise ValueError(f"WorkflowDefinition stage id/label 非法：{stage!r}")
            depends_on = stage.get("depends_on")
            if depends_on is None:
                # 旧的 artifact_patterns 是当前内置插件包的简写；入库后只保留显式 DAG。
                depends_on = [str(stages[ordinal - 1].get("id") or "")] if ordinal else []
            if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
                raise ValueError(f"stage {stage_id} depends_on 必须是字符串数组")
            depends_on = [item.strip() for item in depends_on]
            if any(not item for item in depends_on) or len(depends_on) != len(set(depends_on)):
                raise ValueError(f"stage {stage_id} depends_on 包含空值或重复项")

            acceptance = stage.get("acceptance")
            if acceptance is None:
                acceptance = [{"artifact_patterns": stage.get("artifact_patterns") or [], "min_count": 1}]
            if not isinstance(acceptance, list) or not acceptance:
                raise ValueError(f"stage {stage_id} acceptance 必须是非空数组")
            normalized_acceptance: list[dict] = []
            for rule in acceptance:
                if not isinstance(rule, dict):
                    raise ValueError(f"stage {stage_id} acceptance rule 必须是对象")
                patterns = rule.get("artifact_patterns") or []
                if not isinstance(patterns, list) or not patterns or not all(
                    isinstance(pattern, str) and pattern for pattern in patterns
                ):
                    raise ValueError(
                        f"stage {stage_id} acceptance.artifact_patterns 必须是非空字符串数组"
                    )
                min_count = rule.get("min_count", 1)
                if isinstance(min_count, bool) or not isinstance(min_count, int) or min_count < 1:
                    raise ValueError(f"stage {stage_id} acceptance.min_count 必须是正整数")
                for pattern in patterns:
                    re.compile(pattern, re.IGNORECASE)
                normalized_acceptance.append({
                    "artifact_patterns": patterns,
                    "min_count": min_count,
                })
            seen.add(stage_id)
            normalized_stages.append({
                "id": stage_id,
                "label": stage_label,
                "depends_on": depends_on,
                "acceptance": normalized_acceptance,
            })
        stage_ids = {stage["id"] for stage in normalized_stages}
        for stage in normalized_stages:
            unknown = set(stage["depends_on"]) - stage_ids
            if unknown:
                raise ValueError(f"stage {stage['id']} 依赖不存在的节点：{sorted(unknown)}")
            if stage["id"] in stage["depends_on"]:
                raise ValueError(f"stage {stage['id']} 不能依赖自己")

        # Kahn 拓扑校验：允许分叉/汇合，拒绝任何环。
        remaining = {stage["id"]: set(stage["depends_on"]) for stage in normalized_stages}
        resolved: set[str] = set()
        while remaining:
            ready = {stage_id for stage_id, deps in remaining.items() if deps <= resolved}
            if not ready:
                raise ValueError(f"WorkflowDefinition 存在循环依赖：{sorted(remaining)}")
            resolved.update(ready)
            for stage_id in ready:
                remaining.pop(stage_id)
        for pattern in matches:
            re.compile(pattern, re.IGNORECASE)
        # 能力预检策略（§4.2）：领域流程默认 enforce（缺 provider 即 blocked）；
        # mission.general 的 acceptance 面向通用对象而非领域交付物，默认 info
        # （plan 照算作信息，但不把「没有 provider」当缺口阻断通用项目）。
        preflight = str(value.get("capability_preflight") or "").strip()
        if not preflight:
            preflight = "info" if workflow_id == "mission.general" else "enforce"
        if preflight not in ("enforce", "info"):
            raise ValueError(f"WorkflowDefinition capability_preflight 非法：{preflight!r}")
        return {
            "id": workflow_id,
            "version": version,
            "domain": domain,
            "label": label,
            "matches": matches,
            "stages": normalized_stages,
            "capability_preflight": preflight,
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
            affected = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE definition_id=? AND definition_version=?",
                (normalized["id"], normalized["version"]),
            ).fetchall()
            for row in affected:
                # 定义同版本原位重写（acceptance 可能变了）→ 非终态 run 的 plan 跟着刷新
                if str(row["status"]) in _CAPABILITY_OPEN_RUN_STATUSES:
                    self._refresh_capability_plan_locked(row)
                self._sync_workflow_locked(str(row["workspace_id"]))
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

    # ---------- capability preflight（§4.2 状态机 / §5.1 Capability 合同） ----------

    def set_capability_providers(self, index: dict[str, list[dict]] | None) -> None:
        """注入/更新能力索引（artifact_type → providers）。

        server 在插件加载完成后调用（热重载/后装路径同一条）。索引变化后对全部
        非终态 run（draft/ready/blocked）重算 plan 并重放 sync：插件后装补齐能力，
        capability-blocked 的 run 能翻回 ready；已开工的 run（running 及以后）不动。
        """
        normalized: dict[str, list[dict]] = {}
        for artifact_type, providers in (index or {}).items():
            atype = str(artifact_type).strip()
            if not atype:
                continue
            bucket: dict[str, dict] = {}
            for provider in providers or []:
                if not isinstance(provider, dict):
                    continue
                tool_id = str(provider.get("tool_id") or "").strip()
                if not tool_id:
                    continue
                bucket[tool_id] = {
                    "plugin_id": str(provider.get("plugin_id") or ""),
                    "tool_id": tool_id,
                    "label": str(provider.get("label") or "").strip() or tool_id,
                    "artifact_type": atype,
                    "degraded": bool(provider.get("degraded", False)),
                }
            if bucket:
                normalized[atype] = list(bucket.values())
        with self._lock:
            self._capability_index = normalized
            rows = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE status IN ('draft','ready','blocked')",
            ).fetchall()
            workspace_ids: set[str] = set()
            for row in rows:
                self._refresh_capability_plan_locked(row)
                workspace_ids.add(str(row["workspace_id"]))
            for workspace_id in workspace_ids:
                self._sync_workflow_locked(workspace_id)
            self._conn.commit()

    def _providers_for_patterns(self, patterns: list[str]) -> list[dict]:
        """与 _sync_workflow_locked 同一匹配语义：rule 的任一 pattern 命中 provider
        声明的 artifact_type（re.search，IGNORECASE）即该 provider 可产出。"""
        providers: dict[str, dict] = {}
        for artifact_type, entries in self._capability_index.items():
            if any(re.search(pattern, artifact_type, re.IGNORECASE) for pattern in patterns):
                for entry in entries:
                    providers[entry["tool_id"]] = entry
        return list(providers.values())

    def _compute_capability_plan(self, definition: dict) -> dict:
        """逐 stage、逐 acceptance rule 解析可满足性：rule 有至少一个 provider 命中
        即可满足；stage 的全部 rule 可满足 = available，否则 missing。
        全 available 但 provider 全是降级实现（degraded，如占位视觉卡）→ 标 degraded：
        立项回执据此提示「这段走降级路径」（N7），不把占位冒充满血。"""
        stages: list[dict] = []
        missing: list[str] = []
        degraded: list[str] = []
        for stage in definition.get("stages") or []:
            providers: dict[str, dict] = {}
            rules_satisfied = True
            for rule in stage.get("acceptance") or []:
                matched = self._providers_for_patterns(rule.get("artifact_patterns") or [])
                if not matched:
                    rules_satisfied = False
                for entry in matched:
                    providers[entry["tool_id"]] = entry
            status = "available" if rules_satisfied else "missing"
            if status == "missing":
                missing.append(str(stage["id"]))
            # 该段的全部候选 provider 都是降级实现 → 本段降级（有一个满血即不算）
            is_degraded = bool(providers) and all(p.get("degraded") for p in providers.values())
            if status == "available" and is_degraded:
                degraded.append(str(stage["id"]))
            stages.append({
                "id": str(stage["id"]), "label": str(stage["label"]),
                "status": status, "degraded": is_degraded,
                "providers": list(providers.values()),
            })
        return {
            "stages": stages,
            "missing": missing,
            "degraded": degraded,
            "ready": not missing,
            "policy": _preflight_policy(definition),
            "computed_at": time.time(),
        }

    def _refresh_capability_plan_locked(self, run: sqlite3.Row) -> dict:
        """按当前能力索引重算 run 的 plan 并落库；状态不在这里改，由 sync 统一收口。"""
        definition_row = self._conn.execute(
            "SELECT definition FROM workflow_definitions WHERE id=? AND version=?",
            (str(run["definition_id"]), str(run["definition_version"])),
        ).fetchone()
        definition = _decode(definition_row["definition"] if definition_row else None, {})
        plan = self._compute_capability_plan(definition)
        self._conn.execute(
            "UPDATE workflow_runs SET capability_plan=? WHERE id=?",
            (_json(plan), str(run["id"])),
        )
        return plan

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
        # 立项即预检（§4.2 draft → preflighting → ready/blocked）：预检是同步计算，
        # preflighting 不落库；enforce 流程全 available → ready，有缺口 → blocked
        # （blocked_reason 给人话）；info 流程（mission.general）保持 draft。
        plan = self._compute_capability_plan(definition)
        if plan["policy"] == "enforce":
            status = "ready" if plan["ready"] else "blocked"
            blocked_reason = _capability_blocked_reason(plan)
        else:
            status, blocked_reason = "draft", ""
        self._conn.execute(
            "INSERT INTO workflow_runs(id,workspace_id,mission_id,definition_id,definition_version,status,"
            "current_stage_id,capability_plan,blocked_reason,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, workspace_id, mission_id, definition["id"], definition["version"],
                status, stages[0]["id"], _json(plan), blocked_reason, now, now,
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

    def _check_artifact_type_registered_locked(self, obj_type: str, created_by: str) -> None:
        """Typed artifact registry：未注册类型不得经 attach 进入项目/图谱。

        注册类型 = 插件 work_outputs 声明（build_capability_index 经
        set_capability_providers 注入的能力索引）+ CORE_ARTIFACT_TYPES。类型随插件
        加载自动注册：server 启动在插件加载后注入索引，运行时 attach 天然在注册之后。

        legacy projects.json 迁移（created_by=migration:*）里的旧自由类型不硬拒：
        打告警放行（grandfather）——历史数据不因注册表上线而丢，但迁移完成后的新
        attach 走正常校验。ref 只做非空校验（见 attach_external_artifact）；ref 是否
        可 resolve 到领域对象属于插件域（PluginDb/外部系统），内核不做存在性 resolve。
        """
        if obj_type in CORE_ARTIFACT_TYPES or obj_type in self._capability_index:
            return
        if created_by.startswith("migration:"):
            log(f"legacy 对象类型未注册（迁移放行）：{obj_type}")
            return
        raise ValueError(
            f"未注册的对象类型：{obj_type}。可挂载的类型由插件 work_outputs 声明注册"
            "（插件加载后生效）；请确认对应插件已加载、类型拼写与声明一致。"
        )

    def _attach_external_locked(self, workspace_id: str, obj_type: str, ref: str, created_by: str) -> str:
        self._check_artifact_type_registered_locked(obj_type, created_by)
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
            row = self._add_edge_locked(
                source_artifact_id, target_artifact_id, relation, label=label,
                metadata=metadata or {}, created_by=created_by, invocation_id=invocation_id,
            )
            self._conn.commit()
        return self._edge_dict(row)

    def _add_edge_locked(
        self, source_artifact_id: str, target_artifact_id: str, relation: str, *,
        label: str, metadata: dict, created_by: str, invocation_id: str | None,
    ) -> sqlite3.Row:
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
        now = time.time()
        self._conn.execute(
            "INSERT INTO artifact_edges(id,workspace_id,source_artifact_id,target_artifact_id,relation,"
            "label,metadata,created_by,invocation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(workspace_id,source_artifact_id,target_artifact_id,relation,label) "
            "DO UPDATE SET metadata=excluded.metadata,created_by=excluded.created_by,"
            "invocation_id=excluded.invocation_id",
            (
                _id("edge"), workspace_id, source_artifact_id, target_artifact_id, relation,
                label, _json(metadata), created_by, invocation_id, now,
            ),
        )
        return self._conn.execute(
            "SELECT * FROM artifact_edges WHERE workspace_id=? AND source_artifact_id=? "
            "AND target_artifact_id=? AND relation=? AND label=?",
            (workspace_id, source_artifact_id, target_artifact_id, relation, label),
        ).fetchone()

    def save_stage_checkpoint(
        self, workflow_run_id: str, stage_id: str, checkpoint: dict, *,
        expected_version: int | None = None, invocation_id: str | None = None,
    ) -> dict:
        """CAS 写入可恢复节点；旧 worker 不能覆盖新 worker 的进度。"""
        if not isinstance(checkpoint, dict):
            raise ValueError("Stage checkpoint 必须是对象")
        raw = _json(checkpoint)
        if len(raw.encode("utf-8")) > 64 * 1024:
            raise ValueError("Stage checkpoint 超过 64KiB，大内容应存 Artifact/Blob")
        with self._lock:
            row = self._conn.execute(
                "SELECT si.*,wr.workspace_id FROM stage_instances si "
                "JOIN workflow_runs wr ON wr.id=si.workflow_run_id "
                "WHERE si.workflow_run_id=? AND si.stage_id=?",
                (workflow_run_id, stage_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"StageInstance 不存在：{workflow_run_id}/{stage_id}")
            current_version = int(row["checkpoint_version"])
            if expected_version is not None and expected_version != current_version:
                raise ValueError(
                    f"Stage checkpoint 版本冲突：期望 {expected_version}，当前 {current_version}"
                )
            now = time.time()
            self._conn.execute(
                "UPDATE stage_instances SET checkpoint=?,checkpoint_version=?,checkpointed_at=?,"
                "checkpoint_invocation_id=?,updated_at=? WHERE workflow_run_id=? AND stage_id=?",
                (
                    raw, current_version + 1, now, invocation_id, now,
                    workflow_run_id, stage_id,
                ),
            )
            self._sync_workflow_locked(str(row["workspace_id"]))
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM stage_instances WHERE workflow_run_id=? AND stage_id=?",
                (workflow_run_id, stage_id),
            ).fetchone()
        return self._stage_dict(updated)

    # ---------- durable workflow execution ----------

    def create_durable_execution(
        self, *, workspace_id: str, stage_id: str, capability_id: str,
        provider_candidates: list[str], request: dict, idempotency_key: str,
        invocation_id: str | None = None, cancel_mode: str = "checkpoint",
        resume_supported: bool = True,
    ) -> dict:
        """创建一个可跨 Run/重启继续的执行；request 只允许安全引用与参数。"""
        capability_id = str(capability_id).strip()
        idempotency_key = str(idempotency_key).strip()
        candidates = list(dict.fromkeys(str(item).strip() for item in provider_candidates if str(item).strip()))
        if not capability_id or not idempotency_key or not candidates:
            raise ValueError("DurableExecution 缺少 capability/provider/idempotency_key")
        if not isinstance(request, dict):
            raise ValueError("DurableExecution request 必须是对象")
        if cancel_mode not in ("immediate", "checkpoint", "unsupported"):
            raise ValueError(f"非法 cancel_mode：{cancel_mode}")
        request_raw = _checked_json(request, "DurableExecution request")
        now = time.time()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM durable_executions WHERE workspace_id=? AND capability_id=? "
                "AND idempotency_key=?",
                (workspace_id, capability_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return self._durable_execution_dict_locked(existing)
            run = self._conn.execute(
                "SELECT id FROM workflow_runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"Workspace 无 WorkflowRun：{workspace_id}")
            stage = self._conn.execute(
                "SELECT checkpoint,checkpoint_version FROM stage_instances "
                "WHERE workflow_run_id=? AND stage_id=?",
                (run["id"], stage_id),
            ).fetchone()
            if stage is None:
                raise ValueError(f"StageInstance 不存在：{stage_id}")
            execution_id = _id("execution")
            if invocation_id:
                invocation = self._conn.execute(
                    "SELECT workspace_id FROM invocations WHERE id=?", (invocation_id,),
                ).fetchone()
                if invocation is None or str(invocation["workspace_id"] or "") != workspace_id:
                    raise ValueError("DurableExecution invocation 不存在或不属于当前 Workspace")
            else:
                invocation_id = _id("invocation")
                params_hash = hashlib.sha256(request_raw.encode("utf-8")).hexdigest()
                self._conn.execute(
                    "INSERT INTO invocations(id,action_id,workspace_id,conversation_id,surface,tool_id,"
                    "params_hash,status,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        invocation_id, execution_id, workspace_id, "", "workflow",
                        capability_id, params_hash, "running", now,
                    ),
                )
            self._conn.execute(
                "INSERT INTO durable_executions(id,workspace_id,workflow_run_id,stage_id,invocation_id,"
                "capability_id,provider_candidates,status,request,checkpoint,checkpoint_version,progress,"
                "attempt,idempotency_key,cancel_mode,resume_supported,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    execution_id, workspace_id, run["id"], stage_id, invocation_id,
                    capability_id, _json(candidates), "queued", request_raw,
                    str(stage["checkpoint"] or "{}"), int(stage["checkpoint_version"]), 0.0,
                    0, idempotency_key, cancel_mode, 1 if resume_supported else 0, now, now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
        return self._durable_execution_dict_locked(row)

    def claim_durable_execution(self, execution_id: str, provider_id: str) -> dict:
        provider_id = str(provider_id).strip()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"DurableExecution 不存在：{execution_id}")
            if str(row["status"]) not in ("queued", "interrupted"):
                raise ValueError(f"DurableExecution 不可 claim：{row['status']}")
            candidates = _decode(row["provider_candidates"], [])
            if provider_id not in candidates:
                raise ValueError(f"provider 不在候选集：{provider_id}")
            has_checkpoint = bool(_decode(row["checkpoint"], {}))
            if has_checkpoint and not bool(row["resume_supported"]):
                raise ValueError("DurableExecution 存在 checkpoint 但声明不支持 resume")
            attempt = int(row["attempt"]) + 1
            now = time.time()
            status = "resuming" if has_checkpoint else "running"
            self._conn.execute(
                "UPDATE durable_executions SET provider_id=?,status=?,attempt=?,"
                "started_at=COALESCE(started_at,?),updated_at=?,error='' WHERE id=?",
                (provider_id, status, attempt, now, now, execution_id),
            )
            self._conn.execute(
                "INSERT INTO durable_attempts(id,execution_id,attempt,provider_id,status,started_at) "
                "VALUES(?,?,?,?,?,?)",
                (_id("attempt"), execution_id, attempt, provider_id, "running", now),
            )
            if row["invocation_id"]:
                self._conn.execute(
                    "UPDATE invocations SET status='running',error='',completed_at=NULL WHERE id=?",
                    (row["invocation_id"],),
                )
            self._sync_workflow_locked(str(row["workspace_id"]))
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
        return self._durable_execution_dict_locked(updated)

    def checkpoint_durable_execution(
        self, execution_id: str, checkpoint: dict, *, progress: float,
        expected_version: int,
    ) -> dict:
        if not isinstance(checkpoint, dict):
            raise ValueError("DurableExecution checkpoint 必须是对象")
        checkpoint_raw = _checked_json(checkpoint, "DurableExecution checkpoint")
        progress = max(0.0, min(float(progress), 1.0))
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"DurableExecution 不存在：{execution_id}")
            if str(row["status"]) not in ("running", "resuming", "checkpointing", "cancel_requested"):
                raise ValueError(f"DurableExecution 不可 checkpoint：{row['status']}")
            current = int(row["checkpoint_version"])
            if current != int(expected_version):
                raise ValueError(f"DurableExecution checkpoint 版本冲突：期望 {expected_version}，当前 {current}")
            stage = self._conn.execute(
                "SELECT checkpoint_version FROM stage_instances WHERE workflow_run_id=? AND stage_id=?",
                (row["workflow_run_id"], row["stage_id"]),
            ).fetchone()
            if stage is None:
                raise ValueError("DurableExecution 对应 StageInstance 不存在")
            now = time.time()
            next_version = current + 1
            next_status = "cancel_requested" if str(row["status"]) == "cancel_requested" else "running"
            self._conn.execute(
                "UPDATE durable_executions SET checkpoint=?,checkpoint_version=?,progress=?,status=?,"
                "updated_at=? WHERE id=?",
                (checkpoint_raw, next_version, progress, next_status, now, execution_id),
            )
            self._conn.execute(
                "UPDATE stage_instances SET checkpoint=?,checkpoint_version=checkpoint_version+1,"
                "checkpointed_at=?,checkpoint_invocation_id=?,updated_at=? "
                "WHERE workflow_run_id=? AND stage_id=?",
                (
                    checkpoint_raw, now, row["invocation_id"], now,
                    row["workflow_run_id"], row["stage_id"],
                ),
            )
            self._sync_workflow_locked(str(row["workspace_id"]))
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
        return self._durable_execution_dict_locked(updated)

    def request_cancel_durable_execution(self, execution_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
            if row is None or str(row["status"]) in _DURABLE_TERMINAL:
                return False
            if str(row["cancel_mode"]) == "unsupported":
                return False
            now = time.time()
            if str(row["status"]) in ("queued", "interrupted"):
                status, completed_at = "cancelled", now
            else:
                status, completed_at = "cancel_requested", None
            self._conn.execute(
                "UPDATE durable_executions SET status=?,updated_at=?,completed_at=? WHERE id=?",
                (status, now, completed_at, execution_id),
            )
            if status == "cancelled" and row["invocation_id"]:
                self._conn.execute(
                    "UPDATE invocations SET status='cancelled',error='用户取消',completed_at=? WHERE id=?",
                    (now, row["invocation_id"]),
                )
            self._sync_workflow_locked(str(row["workspace_id"]))
            self._conn.commit()
            return True

    def durable_execution_cancel_requested(self, execution_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
        return bool(row and str(row["status"]) in ("cancel_requested", "cancelled"))

    def fail_durable_attempt(self, execution_id: str, error: str, *, retryable: bool) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"DurableExecution 不存在：{execution_id}")
            now = time.time()
            next_status = "interrupted" if retryable else "failed"
            self._conn.execute(
                "UPDATE durable_attempts SET status='failed',error=?,completed_at=? "
                "WHERE execution_id=? AND attempt=?",
                (str(error)[:2000], now, execution_id, int(row["attempt"])),
            )
            self._conn.execute(
                "UPDATE durable_executions SET status=?,error=?,updated_at=?,completed_at=? WHERE id=?",
                (
                    next_status, str(error)[:2000], now,
                    None if retryable else now, execution_id,
                ),
            )
            if not retryable and row["invocation_id"]:
                self._conn.execute(
                    "UPDATE invocations SET status='failed',error=?,completed_at=? WHERE id=?",
                    (str(error)[:2000], now, row["invocation_id"]),
                )
            self._sync_workflow_locked(str(row["workspace_id"]))
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
        return self._durable_execution_dict_locked(updated)

    def finish_durable_execution(
        self, execution_id: str, *, status: str, result: dict | None = None,
        error: str = "", work_events: list[dict] | None = None,
    ) -> dict:
        if status not in _DURABLE_TERMINAL:
            raise ValueError(f"非法 DurableExecution 终态：{status}")
        result_raw = _checked_json(result or {}, "DurableExecution result")
        invocation_id = ""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"DurableExecution 不存在：{execution_id}")
            if str(row["status"]) in _DURABLE_TERMINAL:
                return self._durable_execution_dict_locked(row)
            now = time.time()
            attempt_status = "completed" if status == "completed" else status
            self._conn.execute(
                "UPDATE durable_attempts SET status=?,error=?,completed_at=? "
                "WHERE execution_id=? AND attempt=?",
                (attempt_status, str(error)[:2000], now, execution_id, int(row["attempt"])),
            )
            self._conn.execute(
                "UPDATE durable_executions SET status=?,progress=?,result=?,error=?,updated_at=?,"
                "completed_at=? WHERE id=?",
                (
                    status, 1.0 if status == "completed" else float(row["progress"]),
                    result_raw, str(error)[:2000], now, now, execution_id,
                ),
            )
            invocation_id = str(row["invocation_id"] or "")
            if invocation_id:
                invocation_status = "succeeded" if status == "completed" else status
                self._conn.execute(
                    "UPDATE invocations SET status=?,safe_result=?,error=?,completed_at=? WHERE id=?",
                    (
                        invocation_status, _bounded_json(result or {}), str(error)[:2000],
                        now, invocation_id,
                    ),
                )
                if status == "completed":
                    self._append_invocation_events_locked(
                        invocation_id, str(row["workspace_id"]), work_events or [], now,
                    )
            self._sync_workflow_locked(str(row["workspace_id"]))
            self._conn.commit()
        if invocation_id and status == "completed" and work_events:
            self.drain_outbox(invocation_id=invocation_id)
        return self.durable_execution_view(execution_id) or {}

    def _append_invocation_events_locked(
        self, invocation_id: str, workspace_id: str, events: list[dict], now: float,
    ) -> None:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(event_seq),0) AS seq FROM outbox_events WHERE invocation_id=?",
            (invocation_id,),
        ).fetchone()
        start = int(row["seq"] if row else 0)
        for offset, event in enumerate(events, start=1):
            event_type = str(event.get("event_type") or "").strip()
            if not event_type:
                continue
            self._conn.execute(
                "INSERT INTO outbox_events(id,workspace_id,invocation_id,event_seq,event_type,payload,"
                "status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    _id("outbox"), workspace_id, invocation_id, start + offset,
                    event_type, _bounded_json(event.get("payload") or {}), "pending", now,
                ),
            )

    def durable_execution_view(self, execution_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_executions WHERE id=?", (execution_id,),
            ).fetchone()
            return self._durable_execution_dict_locked(row) if row else None

    def resumable_durable_executions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM durable_executions WHERE status IN "
                "('queued','interrupted','cancel_requested') ORDER BY created_at",
            ).fetchall()
            return [self._durable_execution_dict_locked(row) for row in rows]

    # ---------- invocation / evidence / outbox ----------

    # ---------- Gate（L3 审批持久化：每道阶段门的决策可审计） ----------

    def record_gate_pending(
        self, gate_id: str, *, tool_id: str, params: dict, risk: int,
        conversation_id: str = "", workspace_id: str | None = None,
    ) -> None:
        """confirmation_requested 时落 Gate(pending)。

        gate_id 即 Action.id（confirmation_id）。action 只存有界快照
        （tool_id + params，_bounded_json 截断防爆库）；invocation 此刻尚未存在
        （执行在批准之后），经 begin_invocation 按 action_id 回填 invocation_id。
        同一 action 重复登记不覆盖：第一道 pending 是审计起点。
        """
        now = time.time()
        with self._lock:
            run_id = None
            if workspace_id:
                row = self._conn.execute(
                    "SELECT id FROM workflow_runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
                    (workspace_id,),
                ).fetchone()
                run_id = str(row["id"]) if row else None
            self._conn.execute(
                "INSERT INTO gates(id,workflow_run_id,conversation_id,action,risk,status,created_at) "
                "VALUES(?,?,?,?,?,'pending',?) ON CONFLICT(id) DO NOTHING",
                (
                    str(gate_id), run_id, conversation_id,
                    _bounded_json({"tool_id": tool_id, "params": params or {}}),
                    int(risk), now,
                ),
            )
            self._conn.commit()

    def record_gate_decision(self, gate_id: str, approved: bool, *, decided_by: str = "user") -> None:
        """用户决策到达：pending → approved/denied + decided_at。

        只有 pending 可被裁决——approved/denied/expired 都是终态，后到的相反决策
        （或取消后迟到的兜底 verdict）不改写审计。
        """
        with self._lock:
            self._conn.execute(
                "UPDATE gates SET status=?,decided_by=?,decided_at=? WHERE id=? AND status='pending'",
                ("approved" if approved else "denied", decided_by, time.time(), str(gate_id)),
            )
            self._conn.commit()

    def expire_gates(self, gate_ids: list[str]) -> int:
        """run 中断/抢占留下的悬空 pending → expired（无人决策，≠ denied）。"""
        ids = [str(gate_id) for gate_id in gate_ids or [] if str(gate_id).strip()]
        if not ids:
            return 0
        with self._lock:
            expired = 0
            for gate_id in ids:
                cursor = self._conn.execute(
                    "UPDATE gates SET status='expired' WHERE id=? AND status='pending'",
                    (gate_id,),
                )
                expired += cursor.rowcount
            self._conn.commit()
        return expired

    def gate_view(self, gate_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM gates WHERE id=?", (str(gate_id),)).fetchone()
        return self._gate_dict(row) if row else None

    def list_gates(
        self, *, workflow_run_id: str | None = None, conversation_id: str | None = None,
        status: str | None = None, limit: int = 200,
    ) -> list[dict]:
        """审计读模型：按 run/会话/状态过滤，时间倒序（供后续 UI/审计用）。"""
        clauses, args = [], []
        if workflow_run_id is not None:
            clauses.append("workflow_run_id=?")
            args.append(workflow_run_id)
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            args.append(conversation_id)
        if status is not None:
            clauses.append("status=?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM gates {where} ORDER BY created_at DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
        return [self._gate_dict(row) for row in rows]

    @staticmethod
    def _gate_dict(row: sqlite3.Row) -> dict:
        return {
            "id": str(row["id"]),
            "workflow_run_id": str(row["workflow_run_id"] or ""),
            "invocation_id": str(row["invocation_id"] or ""),
            "conversation_id": str(row["conversation_id"]),
            "action": _decode(row["action"], {}),
            "risk": int(row["risk"]),
            "status": str(row["status"]),
            "preview_ref": row["preview_ref"],
            "diff_ref": row["diff_ref"],
            "decided_by": str(row["decided_by"] or ""),
            "decided_at": float(row["decided_at"]) if row["decided_at"] is not None else None,
            "created_at": float(row["created_at"]),
        }

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
            # Gate↔Invocation 关联：按印放行的 action 进入执行，审批记录挂上 Invocation
            # （gates.id == action_id；无审批直行的 action 无匹配行，静默跳过）。
            self._conn.execute(
                "UPDATE gates SET invocation_id=? WHERE id=? AND invocation_id IS NULL",
                (invocation_id, action_id),
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
        elif event_type == "artifact.edge.upsert":
            self._upsert_event_edge_locked(workspace_id, invocation_id, payload)
        elif event_type == "stage.checkpoint":
            self._checkpoint_event_stage_locked(workspace_id, invocation_id, payload)
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

    def _artifact_for_selector_locked(self, workspace_id: str, selector: dict) -> str:
        artifact_type = str(selector.get("artifact_type") or "").strip()
        external_ref = str(selector.get("ref") or "").strip()
        if not artifact_type or not external_ref:
            raise ValueError("Artifact selector 缺少 artifact_type/ref")
        row = self._conn.execute(
            "SELECT a.id FROM artifacts a JOIN workspace_artifacts wa ON wa.artifact_id=a.id "
            "WHERE a.workspace_id=? AND a.type=? AND a.external_ref=? AND wa.detached_at IS NULL",
            (workspace_id, artifact_type, external_ref),
        ).fetchone()
        if row is None:
            raise ValueError(f"Artifact selector 未命中：{artifact_type}/{external_ref}")
        return str(row["id"])

    def _upsert_event_edge_locked(
        self, workspace_id: str, invocation_id: str, payload: dict,
    ) -> str:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        relation = str(payload.get("relation") or "").strip()
        if not relation:
            raise ValueError("artifact.edge.upsert 缺少 relation")
        source_id = self._artifact_for_selector_locked(workspace_id, source)
        target_id = self._artifact_for_selector_locked(workspace_id, target)
        row = self._add_edge_locked(
            source_id, target_id, relation,
            label=str(payload.get("label") or "").strip(),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            created_by="invocation", invocation_id=invocation_id,
        )
        return str(row["id"])

    def _checkpoint_event_stage_locked(
        self, workspace_id: str, invocation_id: str, payload: dict,
    ) -> None:
        stage_id = str(payload.get("stage_id") or "").strip()
        checkpoint = payload.get("checkpoint")
        if not stage_id or not isinstance(checkpoint, dict):
            raise ValueError("stage.checkpoint 缺少 stage_id/checkpoint")
        raw = _json(checkpoint)
        if len(raw.encode("utf-8")) > 64 * 1024:
            raise ValueError("Stage checkpoint 超过 64KiB，大内容应存 Artifact/Blob")
        run = self._conn.execute(
            "SELECT id FROM workflow_runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if run is None:
            raise ValueError(f"Workspace 无 WorkflowRun：{workspace_id}")
        row = self._conn.execute(
            "SELECT checkpoint_version FROM stage_instances WHERE workflow_run_id=? AND stage_id=?",
            (run["id"], stage_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"StageInstance 不存在：{stage_id}")
        expected = payload.get("expected_version")
        current = int(row["checkpoint_version"])
        if expected is not None and int(expected) != current:
            raise ValueError(f"Stage checkpoint 版本冲突：期望 {expected}，当前 {current}")
        now = time.time()
        self._conn.execute(
            "UPDATE stage_instances SET checkpoint=?,checkpoint_version=?,checkpointed_at=?,"
            "checkpoint_invocation_id=?,updated_at=? WHERE workflow_run_id=? AND stage_id=?",
            (raw, current + 1, now, invocation_id, now, run["id"], stage_id),
        )

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
        instance_rows = self._conn.execute(
            "SELECT * FROM stage_instances WHERE workflow_run_id=?", (run["id"],),
        ).fetchall()
        instances = {str(row["stage_id"]): row for row in instance_rows}
        execution_rows = self._conn.execute(
            "SELECT * FROM durable_executions WHERE workflow_run_id=? ORDER BY created_at DESC",
            (run["id"],),
        ).fetchall()
        latest_executions: dict[str, sqlite3.Row] = {}
        for execution in execution_rows:
            latest_executions.setdefault(str(execution["stage_id"]), execution)
        outputs: dict[str, list[str]] = {}
        accepted: dict[str, bool] = {}
        for stage in stages:
            stage_id = str(stage["id"])
            rule_matches: list[list[str]] = []
            for rule in stage.get("acceptance") or []:
                patterns = rule.get("artifact_patterns") or []
                ids = [
                    str(artifact["id"]) for artifact in artifacts
                    if any(
                        re.search(pattern, str(artifact["type"]), re.IGNORECASE)
                        for pattern in patterns
                    )
                ]
                rule_matches.append(ids)
            outputs[stage_id] = list(dict.fromkeys(
                artifact_id for ids in rule_matches for artifact_id in ids
            ))
            accepted[stage_id] = bool(rule_matches) and all(
                len(ids) >= int(rule.get("min_count") or 1)
                for rule, ids in zip(stage.get("acceptance") or [], rule_matches)
            )

        # 状态只由 DAG 依赖、acceptance 和持久 checkpoint 决定。
        statuses: dict[str, str] = {}
        unresolved = {str(stage["id"]): stage for stage in stages}
        while unresolved:
            progressed = False
            for stage_id, stage in list(unresolved.items()):
                dependencies = [str(item) for item in stage.get("depends_on") or []]
                if any(dependency not in statuses for dependency in dependencies):
                    continue
                deps_completed = all(statuses[dependency] == "completed" for dependency in dependencies)
                instance = instances.get(stage_id)
                checkpoint = _decode(instance["checkpoint"] if instance else None, {})
                has_checkpoint = bool(checkpoint)
                execution = latest_executions.get(stage_id)
                execution_status = str(execution["status"]) if execution else ""
                if deps_completed and accepted.get(stage_id, False):
                    status = "completed"
                elif not deps_completed:
                    status = "blocked" if accepted.get(stage_id, False) or has_checkpoint or execution else "pending"
                elif execution_status in ("queued", "running", "resuming", "checkpointing", "cancel_requested"):
                    status = "running"
                elif execution_status == "interrupted":
                    status = "blocked"
                elif execution_status == "failed":
                    status = "failed"
                elif execution_status == "cancelled":
                    status = "ready"
                elif has_checkpoint:
                    status = "running"
                else:
                    status = "ready"
                statuses[stage_id] = status
                unresolved.pop(stage_id)
                progressed = True
            if not progressed:  # 正规化已拒绝环，这里只是防御损坏数据。
                raise ValueError("WorkflowDefinition DAG 无法拓扑求值")

        now = time.time()
        completed = bool(stages) and all(status == "completed" for status in statuses.values())
        active_stage_ids = [
            str(stage["id"]) for stage in stages
            if statuses.get(str(stage["id"])) in ("running", "ready")
        ]
        if completed:
            run_status = "completed"
        elif any(status == "failed" for status in statuses.values()):
            run_status = "failed"
        elif any(status == "running" for status in statuses.values()):
            run_status = "running"
        elif not active_stage_ids and any(status == "blocked" for status in statuses.values()):
            run_status = "blocked"
        elif artifacts or any(status == "completed" for status in statuses.values()):
            run_status = "running"
        else:
            run_status = "draft"
        # 能力预检整合（§4.2）：只有「还没开工」的 run（DAG 求值仍是 draft）才应用已存
        # capability plan——enforce 流程全 available → ready，有缺口 → capability-blocked
        # 并留人话原因。产物进场（running 及以后）或 info 策略（mission.general 这类
        # acceptance 面向通用对象的流程）不干预状态机，缺口只留在 plan 里作信息。
        plan = _decode(run["capability_plan"], None)
        if not isinstance(plan, dict):
            plan = self._refresh_capability_plan_locked(run)  # 旧库 run 无 plan：现算补齐
        blocked_reason = ""
        if run_status == "draft" and _preflight_policy(definition) == "enforce":
            blocked_reason = _capability_blocked_reason(plan)
            run_status = "blocked" if blocked_reason else "ready"
        for stage in stages:
            stage_id = str(stage["id"])
            status = statuses[stage_id]
            dependencies = [str(item) for item in stage.get("depends_on") or []]
            input_ids = list(dict.fromkeys(
                artifact_id for dependency in dependencies for artifact_id in outputs.get(dependency, [])
            ))
            self._conn.execute(
                "UPDATE stage_instances SET status=?,input_artifact_ids=?,output_artifact_ids=?,"
                "started_at=CASE WHEN ? IN ('running','blocked','completed') THEN COALESCE(started_at,?) "
                "ELSE started_at END,"
                "completed_at=CASE WHEN ?='completed' THEN COALESCE(completed_at,?) ELSE NULL END,updated_at=? "
                "WHERE workflow_run_id=? AND stage_id=?",
                (
                    status, _json(input_ids), _json(outputs.get(stage_id, [])),
                    status, now, status, now, now, run["id"], stage_id,
                ),
            )
        if completed:
            current_stage_id = str(stages[-1]["id"]) if stages else ""
        else:
            current_stage_id = next(
                (str(stage["id"]) for stage in stages if statuses[str(stage["id"])] == "running"),
                next(
                    (str(stage["id"]) for stage in stages if statuses[str(stage["id"])] == "ready"),
                    next(
                        (str(stage["id"]) for stage in stages if statuses[str(stage["id"])] == "failed"),
                        next(
                            (str(stage["id"]) for stage in stages if statuses[str(stage["id"])] == "blocked"),
                            str(stages[0]["id"]) if stages else "",
                        ),
                    ),
                ),
            )
        self._conn.execute(
            "UPDATE workflow_runs SET status=?,current_stage_id=?,blocked_reason=?,updated_at=?,"
            "completed_at=? WHERE id=?",
            (run_status, current_stage_id, blocked_reason, now, now if completed else None, run["id"]),
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
        execution_rows = self._conn.execute(
            "SELECT * FROM durable_executions WHERE workflow_run_id=? ORDER BY created_at DESC",
            (run["id"],),
        ).fetchall()
        latest_executions: dict[str, sqlite3.Row] = {}
        for execution in execution_rows:
            latest_executions.setdefault(str(execution["stage_id"]), execution)
        by_id = {str(row["stage_id"]): row for row in instance_rows}
        stages = []
        current_index = 0
        for idx, stage in enumerate(definition.get("stages") or []):
            row = by_id.get(str(stage["id"]))
            execution = latest_executions.get(str(stage["id"]))
            if str(stage["id"]) == str(run["current_stage_id"]):
                current_index = idx
            stages.append({
                "id": str(stage["id"]),
                "label": str(stage["label"]),
                "depends_on": list(stage.get("depends_on") or []),
                "status": str(row["status"] if row else "pending"),
                "input_artifact_ids": _decode(row["input_artifact_ids"] if row else None, []),
                "output_artifact_ids": _decode(row["output_artifact_ids"] if row else None, []),
                "checkpoint": _decode(row["checkpoint"] if row else None, {}),
                "checkpoint_version": int(row["checkpoint_version"] if row else 0),
                "checkpointed_at": (
                    float(row["checkpointed_at"]) if row and row["checkpointed_at"] is not None else None
                ),
                "execution": self._durable_execution_dict_locked(execution) if execution else None,
            })
        active_stage_ids = [
            stage["id"] for stage in stages if stage["status"] in ("ready", "running")
        ]
        return {
            "id": str(run["id"]),
            "definition_id": str(run["definition_id"]),
            "definition_version": str(run["definition_version"]),
            "domain": str(run["domain"]),
            "label": str(run["label"]),
            "status": str(run["status"]),
            "current_stage_id": str(run["current_stage_id"]),
            "current_stage_index": current_index,
            "active_stage_ids": active_stage_ids,
            "stages": stages,
            "capability_plan": _decode(run["capability_plan"], None),
            "blocked_reason": str(run["blocked_reason"] or ""),
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

    def list_artifact_views(self, workspace_id: str) -> list[dict]:
        """工作语境产物列表（产物浏览器数据源）：挂载中的 artifact + head 版本摘要。

        path 只在 head 内容落在真实文件时给出（external://、blob://、event:// 等
        内部指针不给），供宿主「在 Finder 显示 / 打开」动作使用。按 updated_at 倒序。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT a.* FROM artifacts a JOIN workspace_artifacts wa ON wa.artifact_id=a.id "
                "WHERE wa.workspace_id=? AND wa.detached_at IS NULL ORDER BY a.updated_at DESC",
                (workspace_id,),
            ).fetchall()
            views = []
            for row in rows:
                head = None
                if row["head_revision_id"]:
                    head = self._conn.execute(
                        "SELECT * FROM revisions WHERE id=?", (row["head_revision_id"],),
                    ).fetchone()
                revision_count = int(self._conn.execute(
                    "SELECT COUNT(*) AS n FROM revisions WHERE artifact_id=?", (row["id"],),
                ).fetchone()["n"])
                metadata = _decode(head["metadata"], {}) if head else {}
                content_ref = str(head["content_ref"]) if head else ""
                path = ""
                if content_ref and "://" not in content_ref and os.path.isabs(content_ref):
                    path = content_ref
                views.append({
                    "id": str(row["id"]),
                    "type": str(row["type"]),
                    "ref": str(row["external_ref"]),
                    "lifecycle": str(row["lifecycle"]),
                    "head_revision_id": str(row["head_revision_id"] or ""),
                    "revision_count": revision_count,
                    "version": metadata.get("version"),
                    "path": path,
                    "updated_at": float(row["updated_at"]),
                })
            return views

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

    @staticmethod
    def _stage_dict(row: sqlite3.Row) -> dict:
        return {
            "id": str(row["id"]),
            "workflow_run_id": str(row["workflow_run_id"]),
            "stage_id": str(row["stage_id"]),
            "ordinal": int(row["ordinal"]),
            "status": str(row["status"]),
            "input_artifact_ids": _decode(row["input_artifact_ids"], []),
            "output_artifact_ids": _decode(row["output_artifact_ids"], []),
            "checkpoint": _decode(row["checkpoint"], {}),
            "checkpoint_version": int(row["checkpoint_version"]),
            "checkpointed_at": (
                float(row["checkpointed_at"]) if row["checkpointed_at"] is not None else None
            ),
            "checkpoint_invocation_id": str(row["checkpoint_invocation_id"] or ""),
        }

    def _durable_execution_dict_locked(self, row: sqlite3.Row) -> dict:
        attempts = self._conn.execute(
            "SELECT * FROM durable_attempts WHERE execution_id=? ORDER BY attempt", (row["id"],),
        ).fetchall()
        return {
            "id": str(row["id"]),
            "workspace_id": str(row["workspace_id"]),
            "workflow_run_id": str(row["workflow_run_id"]),
            "stage_id": str(row["stage_id"]),
            "invocation_id": str(row["invocation_id"] or ""),
            "capability_id": str(row["capability_id"]),
            "provider_id": str(row["provider_id"]),
            "provider_candidates": _decode(row["provider_candidates"], []),
            "status": str(row["status"]),
            "request": _decode(row["request"], {}),
            "checkpoint": _decode(row["checkpoint"], {}),
            "checkpoint_version": int(row["checkpoint_version"]),
            "progress": float(row["progress"]),
            "attempt": int(row["attempt"]),
            "idempotency_key": str(row["idempotency_key"]),
            "cancel_mode": str(row["cancel_mode"]),
            "resume_supported": bool(row["resume_supported"]),
            "error": str(row["error"]),
            "result": _decode(row["result"], {}),
            "attempts": [
                {
                    "attempt": int(attempt["attempt"]),
                    "provider_id": str(attempt["provider_id"]),
                    "status": str(attempt["status"]),
                    "error": str(attempt["error"]),
                }
                for attempt in attempts
            ],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "completed_at": float(row["completed_at"]) if row["completed_at"] is not None else None,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
