"""SQLite 数据库管理"""

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
            """)

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
            # 更新对话时间
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
        return {
            "conversations": conv_count,
            "messages": msg_count,
            "tool_calls": tool_count,
            "tool_success_rate": f"{tool_success/tool_count*100:.1f}%" if tool_count > 0 else "N/A",
        }
