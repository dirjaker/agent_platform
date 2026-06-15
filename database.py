"""SQLite 数据库管理 — 扩展支持 Apps、知识库、工作流等"""

import sqlite3
import json
from datetime import datetime, date
from pathlib import Path
from contextlib import contextmanager
from models import Message, Conversation


class AgentDatabase:
    """Agent 数据库"""

    def __init__(self, db_path: str = "data/agent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                -- ========== 原有表 ==========
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    model TEXT DEFAULT 'deepseek-chat',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_tokens INTEGER DEFAULT 0,
                    total_cost REAL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT REFERENCES conversations(id),
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_results TEXT,
                    metadata TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tool_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT,
                    tool_name TEXT,
                    arguments TEXT,
                    result TEXT,
                    success INTEGER,
                    execution_time REAL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_tool_logs_conv ON tool_logs(conversation_id);

                -- ========== 模型提供商 ==========
                CREATE TABLE IF NOT EXISTS model_providers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider_type TEXT NOT NULL,
                    api_key TEXT,
                    base_url TEXT,
                    models TEXT,
                    config TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- ========== 应用 ==========
                CREATE TABLE IF NOT EXISTS apps (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    app_type TEXT DEFAULT 'chat',
                    icon TEXT DEFAULT '🤖',
                    model_config TEXT,
                    system_prompt TEXT,
                    tool_ids TEXT,
                    dataset_ids TEXT,
                    workflow_config TEXT,
                    status TEXT DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- ========== 知识库数据集 ==========
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    embedding_model TEXT DEFAULT 'tfidf',
                    chunk_size INTEGER DEFAULT 500,
                    chunk_overlap INTEGER DEFAULT 50,
                    document_count INTEGER DEFAULT 0,
                    segment_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- ========== 文档 ==========
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    dataset_id TEXT REFERENCES datasets(id),
                    name TEXT NOT NULL,
                    file_type TEXT,
                    file_size INTEGER DEFAULT 0,
                    content TEXT,
                    segment_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    error_msg TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- ========== 文本片段(向量) ==========
                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY,
                    document_id TEXT REFERENCES documents(id),
                    dataset_id TEXT REFERENCES datasets(id),
                    content TEXT NOT NULL,
                    word_count INTEGER DEFAULT 0,
                    tokens INTEGER DEFAULT 0,
                    vector TEXT,
                    position INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- ========== 工具定义(DB 级) ==========
                CREATE TABLE IF NOT EXISTS tools (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    tool_type TEXT DEFAULT 'api',
                    api_config TEXT,
                    code TEXT,
                    parameters TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- ========== 应用-数据集关联 ==========
                CREATE TABLE IF NOT EXISTS app_datasets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id TEXT REFERENCES apps(id),
                    dataset_id TEXT REFERENCES datasets(id),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(app_id, dataset_id)
                );

                -- ========== 工作流运行记录 ==========
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    app_id TEXT REFERENCES apps(id),
                    conversation_id TEXT,
                    workflow_config TEXT,
                    inputs TEXT,
                    outputs TEXT,
                    status TEXT DEFAULT 'running',
                    error TEXT,
                    elapsed_time REAL DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME
                );

                CREATE INDEX IF NOT EXISTS idx_documents_dataset ON documents(dataset_id);
                CREATE INDEX IF NOT EXISTS idx_segments_document ON segments(document_id);
                CREATE INDEX IF NOT EXISTS idx_segments_dataset ON segments(dataset_id);
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_app ON workflow_runs(app_id);
                CREATE INDEX IF NOT EXISTS idx_app_datasets_app ON app_datasets(app_id);
            """)

    # ==================== 原有方法（保持不变）====================

    def create_conversation(self, conv: Conversation) -> str:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, model) VALUES (?, ?, ?)",
                (conv.id, conv.title, conv.model),
            )
        return conv.id

    def get_conversation(self, conv_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
            return dict(row) if row else None

    def list_conversations(self, limit: int = 50) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def save_message(self, conversation_id: str, message: Message):
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO messages (id, conversation_id, role, content, tool_calls, tool_results, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.id, conversation_id, message.role, message.content,
                    json.dumps(message.tool_call, ensure_ascii=False) if message.tool_call else None,
                    json.dumps(message.tool_result, ensure_ascii=False) if message.tool_result else None,
                    json.dumps(message.metadata, ensure_ascii=False) if message.metadata else None,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), conversation_id),
            )

    def get_messages(self, conversation_id: str, limit: int = 100) -> list[Message]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()

        messages = []
        for row in rows:
            messages.append(Message(
                id=row["id"],
                role=row["role"],
                content=row["content"] or "",
                tool_call=json.loads(row["tool_calls"]) if row["tool_calls"] else None,
                tool_result=json.loads(row["tool_results"]) if row["tool_results"] else None,
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            ))
        return messages

    def log_tool_call(self, conversation_id: str, tool_name: str, arguments: dict,
                      result: dict, success: bool, execution_time: float):
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO tool_logs (conversation_id, tool_name, arguments, result, success, execution_time)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (conversation_id, tool_name, json.dumps(arguments, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False), int(success), execution_time),
            )

    def get_stats(self) -> dict:
        with self._get_conn() as conn:
            conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            tool_count = conn.execute("SELECT COUNT(*) FROM tool_logs").fetchone()[0]
            tool_success = conn.execute("SELECT COUNT(*) FROM tool_logs WHERE success = 1").fetchone()[0]
            app_count = conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
            dataset_count = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        return {
            "conversations": conv_count,
            "messages": msg_count,
            "tool_calls": tool_count,
            "tool_success_rate": f"{tool_success/tool_count*100:.1f}%" if tool_count > 0 else "N/A",
            "apps": app_count,
            "datasets": dataset_count,
            "documents": doc_count,
        }

    # ==================== Apps CRUD ====================

    def create_app(self, app: dict) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO apps (id, name, description, app_type, icon, model_config,
                   system_prompt, tool_ids, dataset_ids, workflow_config, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    app["id"], app["name"], app.get("description", ""),
                    app.get("app_type", "chat"), app.get("icon", "🤖"),
                    json.dumps(app.get("model_config", {}), ensure_ascii=False),
                    app.get("system_prompt", ""),
                    json.dumps(app.get("tool_ids", []), ensure_ascii=False),
                    json.dumps(app.get("dataset_ids", []), ensure_ascii=False),
                    json.dumps(app.get("workflow_config", {}), ensure_ascii=False),
                    app.get("status", "active"),
                ),
            )
        return app["id"]

    def get_app(self, app_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
            if not row:
                return None
            app = dict(row)
            app["model_config"] = json.loads(app["model_config"]) if app["model_config"] else {}
            app["tool_ids"] = json.loads(app["tool_ids"]) if app["tool_ids"] else []
            app["dataset_ids"] = json.loads(app["dataset_ids"]) if app["dataset_ids"] else []
            app["workflow_config"] = json.loads(app["workflow_config"]) if app["workflow_config"] else {}
            return app

    def list_apps(self, limit: int = 100) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM apps ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            apps = []
            for r in rows:
                app = dict(r)
                app["model_config"] = json.loads(app["model_config"]) if app["model_config"] else {}
                app["tool_ids"] = json.loads(app["tool_ids"]) if app["tool_ids"] else []
                app["dataset_ids"] = json.loads(app["dataset_ids"]) if app["dataset_ids"] else []
                app["workflow_config"] = json.loads(app["workflow_config"]) if app["workflow_config"] else {}
                apps.append(app)
            return apps

    def update_app(self, app_id: str, updates: dict) -> bool:
        allowed = {"name", "description", "app_type", "icon", "model_config", "system_prompt",
                    "tool_ids", "dataset_ids", "workflow_config", "status"}
        fields = []
        values = []
        for k, v in updates.items():
            if k not in allowed:
                continue
            if k in ("model_config", "tool_ids", "dataset_ids", "workflow_config"):
                v = json.dumps(v, ensure_ascii=False)
            fields.append(f"{k} = ?")
            values.append(v)
        if not fields:
            return False
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(app_id)
        with self._get_conn() as conn:
            cur = conn.execute(f"UPDATE apps SET {', '.join(fields)} WHERE id = ?", values)
            return cur.rowcount > 0

    def delete_app(self, app_id: str) -> bool:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM app_datasets WHERE app_id = ?", (app_id,))
            cur = conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))
            return cur.rowcount > 0

    # ==================== Datasets CRUD ====================

    def create_dataset(self, ds: dict) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO datasets (id, name, description, embedding_model, chunk_size, chunk_overlap)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ds["id"], ds["name"], ds.get("description", ""),
                 ds.get("embedding_model", "tfidf"),
                 ds.get("chunk_size", 500), ds.get("chunk_overlap", 50)),
            )
        return ds["id"]

    def get_dataset(self, ds_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM datasets WHERE id = ?", (ds_id,)).fetchone()
            return dict(row) if row else None

    def list_datasets(self, limit: int = 100) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM datasets ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_dataset(self, ds_id: str, updates: dict) -> bool:
        allowed = {"name", "description", "embedding_model", "chunk_size", "chunk_overlap", "status"}
        fields = []
        values = []
        for k, v in updates.items():
            if k in allowed:
                fields.append(f"{k} = ?")
                values.append(v)
        if not fields:
            return False
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(ds_id)
        with self._get_conn() as conn:
            cur = conn.execute(f"UPDATE datasets SET {', '.join(fields)} WHERE id = ?", values)
            return cur.rowcount > 0

    def delete_dataset(self, ds_id: str) -> bool:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM segments WHERE dataset_id = ?", (ds_id,))
            conn.execute("DELETE FROM documents WHERE dataset_id = ?", (ds_id,))
            conn.execute("DELETE FROM app_datasets WHERE dataset_id = ?", (ds_id,))
            cur = conn.execute("DELETE FROM datasets WHERE id = ?", (ds_id,))
            return cur.rowcount > 0

    def update_dataset_counts(self, ds_id: str):
        with self._get_conn() as conn:
            doc_count = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE dataset_id = ?", (ds_id,)
            ).fetchone()[0]
            seg_count = conn.execute(
                "SELECT COUNT(*) FROM segments WHERE dataset_id = ?", (ds_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE datasets SET document_count = ?, segment_count = ?, updated_at = ? WHERE id = ?",
                (doc_count, seg_count, datetime.now().isoformat(), ds_id),
            )

    # ==================== Documents CRUD ====================

    def create_document(self, doc: dict) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO documents (id, dataset_id, name, file_type, file_size, content, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (doc["id"], doc["dataset_id"], doc["name"],
                 doc.get("file_type", ""), doc.get("file_size", 0),
                 doc.get("content", ""), doc.get("status", "pending")),
            )
        return doc["id"]

    def get_document(self, doc_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def list_documents(self, dataset_id: str, limit: int = 100) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE dataset_id = ? ORDER BY created_at DESC LIMIT ?",
                (dataset_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_document(self, doc_id: str, updates: dict) -> bool:
        allowed = {"name", "content", "segment_count", "status", "error_msg"}
        fields = []
        values = []
        for k, v in updates.items():
            if k in allowed:
                fields.append(f"{k} = ?")
                values.append(v)
        if not fields:
            return False
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(doc_id)
        with self._get_conn() as conn:
            cur = conn.execute(f"UPDATE documents SET {', '.join(fields)} WHERE id = ?", values)
            return cur.rowcount > 0

    def delete_document(self, doc_id: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT dataset_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
            conn.execute("DELETE FROM segments WHERE document_id = ?", (doc_id,))
            cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            if cur.rowcount > 0 and row:
                self.update_dataset_counts(row["dataset_id"])
            return cur.rowcount > 0

    # ==================== Segments CRUD ====================

    def create_segment(self, seg: dict) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO segments (id, document_id, dataset_id, content, word_count,
                   vector, position, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (seg["id"], seg["document_id"], seg["dataset_id"], seg["content"],
                 seg.get("word_count", 0), seg.get("vector", ""),
                 seg.get("position", 0), seg.get("status", "active")),
            )
        return seg["id"]

    def list_segments(self, dataset_id: str = None, document_id: str = None,
                      limit: int = 1000) -> list[dict]:
        with self._get_conn() as conn:
            if document_id:
                rows = conn.execute(
                    "SELECT * FROM segments WHERE document_id = ? AND status = 'active' ORDER BY position LIMIT ?",
                    (document_id, limit),
                ).fetchall()
            elif dataset_id:
                rows = conn.execute(
                    "SELECT * FROM segments WHERE dataset_id = ? AND status = 'active' ORDER BY position LIMIT ?",
                    (dataset_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM segments WHERE status = 'active' ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_segments_by_document(self, doc_id: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM segments WHERE document_id = ?", (doc_id,))

    # ==================== Tools CRUD (DB 级) ====================

    def create_tool_db(self, tool: dict) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO tools (id, name, description, tool_type, api_config, code, parameters, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (tool["id"], tool["name"], tool.get("description", ""),
                 tool.get("tool_type", "api"),
                 json.dumps(tool.get("api_config", {}), ensure_ascii=False),
                 tool.get("code", ""),
                 json.dumps(tool.get("parameters", []), ensure_ascii=False),
                 int(tool.get("enabled", True))),
            )
        return tool["id"]

    def get_tool_db(self, tool_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
            if not row:
                return None
            t = dict(row)
            t["api_config"] = json.loads(t["api_config"]) if t["api_config"] else {}
            t["parameters"] = json.loads(t["parameters"]) if t["parameters"] else []
            return t

    def list_tools_db(self, limit: int = 100) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tools ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            tools = []
            for r in rows:
                t = dict(r)
                t["api_config"] = json.loads(t["api_config"]) if t["api_config"] else {}
                t["parameters"] = json.loads(t["parameters"]) if t["parameters"] else []
                tools.append(t)
            return tools

    def update_tool_db(self, tool_id: str, updates: dict) -> bool:
        allowed = {"name", "description", "tool_type", "api_config", "code", "parameters", "enabled"}
        fields = []
        values = []
        for k, v in updates.items():
            if k not in allowed:
                continue
            if k in ("api_config", "parameters"):
                v = json.dumps(v, ensure_ascii=False)
            elif k == "enabled":
                v = int(v)
            fields.append(f"{k} = ?")
            values.append(v)
        if not fields:
            return False
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(tool_id)
        with self._get_conn() as conn:
            cur = conn.execute(f"UPDATE tools SET {', '.join(fields)} WHERE id = ?", values)
            return cur.rowcount > 0

    def delete_tool_db(self, tool_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
            return cur.rowcount > 0

    # ==================== Model Providers CRUD ====================

    def create_model_provider(self, provider: dict) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO model_providers (id, name, provider_type, api_key, base_url, models, config, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (provider["id"], provider["name"], provider["provider_type"],
                 provider.get("api_key", ""), provider.get("base_url", ""),
                 json.dumps(provider.get("models", []), ensure_ascii=False),
                 json.dumps(provider.get("config", {}), ensure_ascii=False),
                 int(provider.get("enabled", True))),
            )
        return provider["id"]

    def get_model_provider(self, provider_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_providers WHERE id = ?", (provider_id,)
            ).fetchone()
            if not row:
                return None
            p = dict(row)
            p["models"] = json.loads(p["models"]) if p["models"] else []
            p["config"] = json.loads(p["config"]) if p["config"] else {}
            return p

    def list_model_providers(self, limit: int = 50) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM model_providers ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            providers = []
            for r in rows:
                p = dict(r)
                p["models"] = json.loads(p["models"]) if p["models"] else []
                p["config"] = json.loads(p["config"]) if p["config"] else {}
                providers.append(p)
            return providers

    def update_model_provider(self, provider_id: str, updates: dict) -> bool:
        allowed = {"name", "provider_type", "api_key", "base_url", "models", "config", "enabled"}
        fields = []
        values = []
        for k, v in updates.items():
            if k not in allowed:
                continue
            if k in ("models", "config"):
                v = json.dumps(v, ensure_ascii=False)
            elif k == "enabled":
                v = int(v)
            fields.append(f"{k} = ?")
            values.append(v)
        if not fields:
            return False
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(provider_id)
        with self._get_conn() as conn:
            cur = conn.execute(
                f"UPDATE model_providers SET {', '.join(fields)} WHERE id = ?", values
            )
            return cur.rowcount > 0

    def delete_model_provider(self, provider_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM model_providers WHERE id = ?", (provider_id,))
            return cur.rowcount > 0

    # ==================== Workflow Runs ====================

    def create_workflow_run(self, run: dict) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO workflow_runs (id, app_id, conversation_id, workflow_config,
                   inputs, outputs, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run["id"], run.get("app_id"), run.get("conversation_id"),
                 json.dumps(run.get("workflow_config", {}), ensure_ascii=False),
                 json.dumps(run.get("inputs", {}), ensure_ascii=False),
                 json.dumps(run.get("outputs", {}), ensure_ascii=False),
                 run.get("status", "running")),
            )
        return run["id"]

    def update_workflow_run(self, run_id: str, updates: dict) -> bool:
        fields = []
        values = []
        for k, v in updates.items():
            if k in ("outputs", "workflow_config", "inputs"):
                v = json.dumps(v, ensure_ascii=False)
            fields.append(f"{k} = ?")
            values.append(v)
        if not fields:
            return False
        values.append(run_id)
        with self._get_conn() as conn:
            cur = conn.execute(f"UPDATE workflow_runs SET {', '.join(fields)} WHERE id = ?", values)
            return cur.rowcount > 0

    def get_workflow_run(self, run_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not row:
                return None
            r = dict(row)
            r["workflow_config"] = json.loads(r["workflow_config"]) if r["workflow_config"] else {}
            r["inputs"] = json.loads(r["inputs"]) if r["inputs"] else {}
            r["outputs"] = json.loads(r["outputs"]) if r["outputs"] else {}
            return r

    def list_workflow_runs(self, app_id: str = None, limit: int = 50) -> list[dict]:
        with self._get_conn() as conn:
            if app_id:
                rows = conn.execute(
                    "SELECT * FROM workflow_runs WHERE app_id = ? ORDER BY created_at DESC LIMIT ?",
                    (app_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            runs = []
            for r in rows:
                run = dict(r)
                run["workflow_config"] = json.loads(run["workflow_config"]) if run["workflow_config"] else {}
                run["inputs"] = json.loads(run["inputs"]) if run["inputs"] else {}
                run["outputs"] = json.loads(run["outputs"]) if run["outputs"] else {}
                runs.append(run)
            return runs

    # ==================== App-Dataset 关联 ====================

    def bind_app_dataset(self, app_id: str, dataset_id: str):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO app_datasets (app_id, dataset_id) VALUES (?, ?)",
                (app_id, dataset_id),
            )

    def unbind_app_dataset(self, app_id: str, dataset_id: str):
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM app_datasets WHERE app_id = ? AND dataset_id = ?",
                (app_id, dataset_id),
            )

    def get_app_datasets(self, app_id: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT d.* FROM datasets d
                   JOIN app_datasets ad ON d.id = ad.dataset_id
                   WHERE ad.app_id = ?""",
                (app_id,),
            ).fetchall()
            return [dict(r) for r in rows]
