"""Database module for SQLite operations."""

import aiosqlite
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
import logging

from app.config import DB_PATH

logger = logging.getLogger(__name__)

# DDL Schema
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

-- 转发来源（按 forward 的原 chat/channel）
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type TEXT NOT NULL,           -- channel|group|supergroup|user|unknown
  source_chat_id INTEGER NOT NULL,      -- int64
  title TEXT,
  username TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(source_type, source_chat_id)
);

-- 每次你转发给 bot 的那条"消息记录"（即 bot 私聊里的一条 message）
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_chat_id INTEGER NOT NULL,          -- bot 收到消息所在 chat（通常是你的 private chat id）
  tg_message_id INTEGER NOT NULL,       -- bot chat 内 message_id（worker 用它配合 tdl 下载）
  from_user_id INTEGER,                 -- 转发者（你）
  received_at TEXT NOT NULL,            -- bot 接收时间（UTC/本地均可，保持一致）
  forwarded_at TEXT,                    -- 若可得，记录原消息时间
  source_id INTEGER REFERENCES sources(id),
  text TEXT,                            -- message.text 或 caption（建议都放这里）
  raw_json TEXT NOT NULL,               -- 便于追溯/兼容未来字段
  UNIQUE(tg_chat_id, tg_message_id)
);

-- 唯一文件实体（以 file_unique_id 去重）
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_unique_id TEXT NOT NULL,
  -- 最后一次看到的 file_id（可变，但用于 Bot API getFile）
  last_seen_file_id TEXT,
  file_size INTEGER,
  mime_type TEXT,
  original_name TEXT,
  -- 落地信息
  local_path TEXT,                      -- 绝对路径（/files/...）
  local_size INTEGER,
  sha256 TEXT,                          -- 可选：下载后算，提升完整性判断
  status TEXT NOT NULL DEFAULT 'NEW',   -- NEW|DOWNLOADED|FAILED
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(file_unique_id)
);

-- 消息与文件的引用关系（每条转发都要记录，即使文件已存在）
CREATE TABLE IF NOT EXISTS message_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id),
  tg_file_id TEXT,                      -- 当次消息里的 file_id（供排查）
  tg_file_unique_id TEXT,               -- 冗余一份便于查询
  kind TEXT NOT NULL,                   -- document|photo|video|audio|voice|animation|sticker|...
  caption TEXT,                         -- 可选：如果你想保留原 caption 分离
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(message_id, file_id, kind)
);

-- Job 队列（只给 >20MB 的文件）
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'QUEUED',  -- QUEUED|RUNNING|DONE|RETRY|FAILED
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  available_at TEXT NOT NULL DEFAULT (datetime('now')),
  locked_by TEXT,
  locked_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 避免同一个 file 在 QUEUED/RUNNING 状态出现多个 job（SQLite 支持部分索引）
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_one_active_per_file
ON jobs(file_id)
WHERE status IN ('QUEUED','RUNNING','RETRY');

CREATE INDEX IF NOT EXISTS idx_jobs_pick
ON jobs(status, available_at, created_at);

CREATE INDEX IF NOT EXISTS idx_message_files_file
ON message_files(file_id);

CREATE INDEX IF NOT EXISTS idx_messages_received_at
ON messages(received_at);
"""


async def init_db():
    """Initialize database with schema."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    logger.info(f"Database initialized at {DB_PATH}")


async def upsert_source(
    source_type: str,
    source_chat_id: int,
    title: Optional[str] = None,
    username: Optional[str] = None,
) -> int:
    """Insert or update a source, return source_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            """
            INSERT INTO sources (source_type, source_chat_id, title, username)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_type, source_chat_id) 
            DO UPDATE SET title=excluded.title, username=excluded.username
            RETURNING id
            """,
            (source_type, source_chat_id, title, username),
        )
        row = await cursor.fetchone()
        await db.commit()
        return row[0]


async def insert_message(
    tg_chat_id: int,
    tg_message_id: int,
    from_user_id: Optional[int],
    received_at: str,
    forwarded_at: Optional[str],
    source_id: Optional[int],
    text: Optional[str],
    raw_json: str,
) -> int:
    """Insert a message, return message_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            """
            INSERT INTO messages 
            (tg_chat_id, tg_message_id, from_user_id, received_at, forwarded_at, source_id, text, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tg_chat_id,
                tg_message_id,
                from_user_id,
                received_at,
                forwarded_at,
                source_id,
                text,
                raw_json,
            ),
        )
        message_id = cursor.lastrowid
        await db.commit()
        return message_id


async def upsert_file(
    file_unique_id: str,
    last_seen_file_id: str,
    file_size: Optional[int],
    mime_type: Optional[str],
    original_name: Optional[str],
) -> int:
    """Insert or update a file, return file_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            """
            INSERT INTO files (file_unique_id, last_seen_file_id, file_size, mime_type, original_name)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_unique_id) 
            DO UPDATE SET 
                last_seen_file_id=excluded.last_seen_file_id,
                file_size=COALESCE(excluded.file_size, file_size),
                mime_type=COALESCE(excluded.mime_type, mime_type),
                original_name=COALESCE(excluded.original_name, original_name),
                updated_at=datetime('now')
            RETURNING id
            """,
            (file_unique_id, last_seen_file_id, file_size, mime_type, original_name),
        )
        row = await cursor.fetchone()
        await db.commit()
        return row[0]


async def insert_message_file(
    message_id: int,
    file_id: int,
    tg_file_id: str,
    tg_file_unique_id: str,
    kind: str,
    caption: Optional[str] = None,
):
    """Link a message to a file."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            INSERT INTO message_files (message_id, file_id, tg_file_id, tg_file_unique_id, kind, caption)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, file_id, tg_file_id, tg_file_unique_id, kind, caption),
        )
        await db.commit()


async def update_file_downloaded(
    file_id: int, local_path: str, local_size: int, sha256: Optional[str] = None
):
    """Mark a file as downloaded."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            UPDATE files 
            SET local_path=?, local_size=?, sha256=?, status='DOWNLOADED', updated_at=datetime('now')
            WHERE id=?
            """,
            (local_path, local_size, sha256, file_id),
        )
        await db.commit()


async def update_file_failed(file_id: int):
    """Mark a file as failed."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            UPDATE files 
            SET status='FAILED', updated_at=datetime('now')
            WHERE id=?
            """,
            (file_id,),
        )
        await db.commit()


async def get_file_by_id(file_id: int) -> Optional[Dict[str, Any]]:
    """Get file by id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM files WHERE id=?", (file_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_file_by_unique_id(file_unique_id: str) -> Optional[Dict[str, Any]]:
    """Get file by unique_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM files WHERE file_unique_id=?", (file_unique_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def insert_job(file_id: int, message_id: int) -> Optional[int]:
    """
    Insert a job for a file. Returns job_id if inserted, None if already exists.
    The unique index prevents duplicate active jobs for the same file.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        try:
            cursor = await db.execute(
                """
                INSERT INTO jobs (file_id, message_id, status, available_at)
                VALUES (?, ?, 'QUEUED', datetime('now'))
                """,
                (file_id, message_id),
            )
            job_id = cursor.lastrowid
            await db.commit()
            return job_id
        except aiosqlite.IntegrityError:
            # Job already exists for this file
            return None


async def pick_and_lock_job(worker_id: str) -> Optional[Dict[str, Any]]:
    """
    Atomically pick and lock a job. Returns job dict or None.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row

        # Start immediate transaction
        await db.execute("BEGIN IMMEDIATE")

        try:
            # Pick oldest available job
            cursor = await db.execute(
                """
                SELECT id, file_id, message_id, attempts
                FROM jobs
                WHERE status IN ('QUEUED', 'RETRY') 
                  AND available_at <= datetime('now')
                ORDER BY created_at
                LIMIT 1
                """
            )
            row = await cursor.fetchone()

            if not row:
                await db.commit()
                return None

            job_id = row[0]

            # Lock it
            await db.execute(
                """
                UPDATE jobs
                SET status='RUNNING', 
                    locked_by=?, 
                    locked_at=datetime('now'),
                    attempts=attempts+1,
                    updated_at=datetime('now')
                WHERE id=? AND status IN ('QUEUED', 'RETRY')
                """,
                (worker_id, job_id),
            )

            if db.total_changes == 0:
                # Someone else got it
                await db.commit()
                return None

            await db.commit()
            return dict(row)

        except Exception as e:
            await db.rollback()
            raise


async def update_job_done(job_id: int):
    """Mark job as done."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            UPDATE jobs
            SET status='DONE', updated_at=datetime('now')
            WHERE id=?
            """,
            (job_id,),
        )
        await db.commit()


async def update_job_retry(job_id: int, error: str, backoff_seconds: int):
    """Mark job for retry with backoff."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            UPDATE jobs
            SET status='RETRY',
                last_error=?,
                available_at=datetime('now', '+' || ? || ' seconds'),
                updated_at=datetime('now')
            WHERE id=?
            """,
            (error, backoff_seconds, job_id),
        )
        await db.commit()


async def update_job_failed(job_id: int, error: str):
    """Mark job as permanently failed."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            UPDATE jobs
            SET status='FAILED', last_error=?, updated_at=datetime('now')
            WHERE id=?
            """,
            (error, job_id),
        )
        await db.commit()


async def recover_stale_jobs(stale_minutes: int) -> int:
    """
    Recover stale RUNNING jobs (locked too long).
    Returns count of recovered jobs.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            """
            UPDATE jobs
            SET status='RETRY',
                available_at=datetime('now'),
                updated_at=datetime('now')
            WHERE status='RUNNING'
              AND locked_at < datetime('now', '-' || ? || ' minutes')
            """,
            (stale_minutes,),
        )
        count = cursor.rowcount
        await db.commit()
        return count


async def get_message_by_id(message_id: int) -> Optional[Dict[str, Any]]:
    """Get message by id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM messages WHERE id=?", (message_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_source_by_id(source_id: int) -> Optional[Dict[str, Any]]:
    """Get source by id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM sources WHERE id=?", (source_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_message_files(message_id: int) -> List[Dict[str, Any]]:
    """Get all files for a message with file details."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT mf.*, f.*
            FROM message_files mf
            JOIN files f ON mf.file_id = f.id
            WHERE mf.message_id = ?
            """,
            (message_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
