"""SQLite storage. One short-lived connection per operation (safe across threads)."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,          -- 'user' or 'assistant'
    content    TEXT NOT NULL,
    metadata   TEXT,                   -- JSON string, assistant messages only
    created_at TEXT NOT NULL           -- server time, ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    system_prompt TEXT,
    created_at    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()

    def ping(self) -> None:
        with self.connect() as conn:
            conn.execute("SELECT 1 FROM messages LIMIT 1").fetchall()

    def session_exists(self, session_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
            return row is not None

    def get_system_prompt(self, session_id: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return row["system_prompt"] if row else None

    def recent_history(self, session_id: str, limit: int) -> List[Dict[str, str]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM ("
                "  SELECT id, role, content FROM messages WHERE session_id = ? "
                "  ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (session_id, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def all_messages(self, session_id: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            item: Dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["role"] == "assistant" and r["metadata"]:
                item["metadata"] = json.loads(r["metadata"])
            out.append(item)
        return out

    def save_exchange(
        self,
        session_id: str,
        system_prompt: Optional[str],
        user_message: str,
        reply: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Atomically store the user turn + assistant turn (only called after success)."""
        now = _now()
        with self.connect() as conn:
            with conn:  # one transaction
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (session_id, system_prompt, created_at) VALUES (?, NULL, ?)",
                    (session_id, now),
                )
                if system_prompt is not None:
                    conn.execute(
                        "UPDATE sessions SET system_prompt = ? WHERE session_id = ?",
                        (system_prompt, session_id),
                    )
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'user', ?, NULL, ?)",
                    (session_id, user_message, now),
                )
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                    (session_id, reply, json.dumps(metadata), _now()),
                )
