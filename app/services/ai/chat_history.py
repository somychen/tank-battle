"""
聊天记录管理 — 基于 SQLite 的会话持久化

表结构:
  sessions: id, title, model_provider, model_id, document_ref,
            message_count, compressed, original_message_count,
            created_at, updated_at
  messages: id, session_id, role, content, created_at
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

# ---- Token 估算 ----

# CJK 字符范围 (含扩展区)
_CJK_RE = re.compile(
    r'[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df'
    r'\uf900-\ufaff\U0002f800-\U0002fa1f]'
)


def _count_cjk(text: str) -> int:
    return len(_CJK_RE.findall(text))


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数（CJK 约 1.5 token/字，英文约 0.25 token/字）"""
    total = len(text)
    cjk = _count_cjk(text)
    other = total - cjk
    return int(cjk * 1.5 + other * 0.25)


# ---- ChatHistoryManager ----

class ChatHistoryManager:
    """SQLite 聊天记录管理器"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '新对话',
                    model_provider TEXT DEFAULT '',
                    model_id TEXT DEFAULT '',
                    document_ref TEXT DEFAULT '',
                    message_count INTEGER DEFAULT 0,
                    compressed INTEGER DEFAULT 0,
                    original_message_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, id);
            """)
            conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ---- Session CRUD ----

    def list_sessions(self) -> list[dict]:
        """返回所有会话列表，按更新时间倒序"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[dict]:
        """获取单个会话及其所有消息"""
        with self._connect() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not session:
                return None
            result = dict(session)
            msgs = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,)
            ).fetchall()
            result["messages"] = [dict(m) for m in msgs]
            return result

    def create_session(
        self,
        title: str = "新对话",
        model_provider: str = "",
        model_id: str = "",
        document_ref: str = "",
    ) -> dict:
        """创建新会话，返回会话数据"""
        session_id = f"chat_{self._now().replace(':', '').replace('-', '').replace('T', '_')[:15]}_{uuid.uuid4().hex[:6]}"
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sessions (id, title, model_provider, model_id, document_ref,
                   message_count, compressed, original_message_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?)""",
                (session_id, title, model_provider, model_id, document_ref, now, now),
            )
            conn.commit()
        return self.get_session(session_id)

    def update_session(
        self,
        session_id: str,
        messages: list[dict],
        title: Optional[str] = None,
        model_provider: Optional[str] = None,
        model_id: Optional[str] = None,
        document_ref: Optional[str] = None,
        compressed: Optional[bool] = None,
        original_message_count: Optional[int] = None,
    ) -> Optional[dict]:
        """更新会话的消息列表和元数据"""
        now = self._now()
        with self._connect() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not session:
                return None

            # 删除旧消息，写入新消息
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            for i, msg in enumerate(messages):
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (session_id, msg.get("role", "user"), msg.get("content", ""), now),
                )

            # 更新 sessions 表字段
            fields = {
                "title": title if title is not None else session["title"],
                "model_provider": model_provider if model_provider is not None else session["model_provider"],
                "model_id": model_id if model_id is not None else session["model_id"],
                "document_ref": document_ref if document_ref is not None else session["document_ref"],
                "message_count": len(messages),
                "compressed": 1 if compressed else (session["compressed"] or 0),
                "original_message_count": original_message_count if original_message_count is not None else session["original_message_count"],
                "updated_at": now,
            }
            conn.execute(
                """UPDATE sessions SET title=?, model_provider=?, model_id=?, document_ref=?,
                   message_count=?, compressed=?, original_message_count=?, updated_at=?
                   WHERE id=?""",
                (
                    fields["title"], fields["model_provider"], fields["model_id"],
                    fields["document_ref"], fields["message_count"], fields["compressed"],
                    fields["original_message_count"], fields["updated_at"], session_id,
                ),
            )
            conn.commit()

        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        """删除会话（CASCADE 自动删消息）"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return cur.rowcount > 0

    # ---- Token 估算 ----

    @staticmethod
    def estimate_messages_tokens(messages: list[dict]) -> int:
        """估算消息列表的总 token"""
        total = 0
        for m in messages:
            total += estimate_tokens(m.get("content", ""))
        return total


# ---- 单例 ----

_manager: Optional[ChatHistoryManager] = None


def get_chat_history_manager() -> ChatHistoryManager:
    global _manager
    if _manager is None:
        from app.config import settings  # 延迟导入避免循环引用
        db_path = os.path.join(settings.data_dir, "chats.db")
        _manager = ChatHistoryManager(db_path)
    return _manager
