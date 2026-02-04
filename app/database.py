"""Database module for SQLite operations."""

import aiosqlite
from typing import Optional, Dict, Any, List
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

-- 每次你转发给 bot 的那条"消息记录"
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_chat_id INTEGER NOT NULL,
  tg_message_id INTEGER NOT NULL,
  original_message_id INTEGER,
  from_user_id INTEGER,
  received_at TEXT NOT NULL,
  forwarded_at TEXT,
  source_id INTEGER REFERENCES sources(id),
  text TEXT,
  raw_json TEXT NOT NULL,
  UNIQUE(tg_chat_id, tg_message_id)
);

-- 唯一文件实体（以 file_unique_id 去重）
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_unique_id TEXT NOT NULL,
  last_seen_file_id TEXT,
  file_size INTEGER,
  mime_type TEXT,
  original_name TEXT,
  local_path TEXT,
  local_size INTEGER,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'NEW',   -- NEW|DOWNLOADED|FAILED
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(file_unique_id)
);

-- 消息与文件的引用关系
CREATE TABLE IF NOT EXISTS message_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id),
  tg_file_id TEXT,
  tg_file_unique_id TEXT,
  kind TEXT NOT NULL,
  caption TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(message_id, file_id, kind)
);

-- Job 队列（简化版，无重试）
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'QUEUED',  -- QUEUED|RUNNING|DONE|FAILED
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_one_active_per_file
ON jobs(file_id)
WHERE status IN ('QUEUED','RUNNING');

-- 下载失败统计表
CREATE TABLE IF NOT EXISTS download_failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL,
  file_unique_id TEXT NOT NULL,
  source_type TEXT,
  source_chat_id INTEGER,
  original_name TEXT,
  error_type TEXT NOT NULL,        -- BOT_API_ONLY|TDL_ONLY|BOTH_FAILED
  bot_api_error TEXT,
  tdl_error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_failures_error_type ON download_failures(error_type);
CREATE INDEX IF NOT EXISTS idx_failures_created_at ON download_failures(created_at);

CREATE INDEX IF NOT EXISTS idx_message_files_file ON message_files(file_id);
CREATE INDEX IF NOT EXISTS idx_messages_received_at ON messages(received_at);
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
    original_message_id: Optional[int],
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
            (tg_chat_id, tg_message_id, original_message_id, from_user_id, received_at, forwarded_at, source_id, text, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tg_chat_id,
                tg_message_id,
                original_message_id,
                from_user_id,
                received_at,
                forwarded_at,
                source_id,
                text,
                raw_json,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def insert_message_file(
    message_id: int,
    file_id: int,
    tg_file_id: str,
    tg_file_unique_id: str,
    kind: str,
    caption: Optional[str] = None,
):
    """Link a message to a file. Ignores if already exists."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            INSERT OR IGNORE INTO message_files (message_id, file_id, tg_file_id, tg_file_unique_id, kind, caption)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, file_id, tg_file_id, tg_file_unique_id, kind, caption),
        )
        await db.commit()


async def upsert_file(
    file_unique_id: str,
    last_seen_file_id: str,
    file_size: Optional[int] = None,
    mime_type: Optional[str] = None,
    original_name: Optional[str] = None,
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
                updated_at=datetime('now')
            RETURNING id
            """,
            (file_unique_id, last_seen_file_id, file_size, mime_type, original_name),
        )
        row = await cursor.fetchone()
        await db.commit()
        return row[0]


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
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        try:
            cursor = await db.execute(
                """
                INSERT INTO jobs (file_id, message_id, status)
                VALUES (?, ?, 'QUEUED')
                """,
                (file_id, message_id),
            )
            job_id = cursor.lastrowid
            await db.commit()
            return job_id
        except aiosqlite.IntegrityError:
            return None


async def get_pending_jobs() -> List[Dict[str, Any]]:
    """Get all QUEUED jobs."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, file_id, message_id
            FROM jobs
            WHERE status = 'QUEUED'
            ORDER BY created_at
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def update_job_running(job_id: int):
    """Mark job as running."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            UPDATE jobs
            SET status='RUNNING'
            WHERE id=?
            """,
            (job_id,),
        )
        await db.commit()


async def update_job_done(job_id: int):
    """Mark job as done."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            UPDATE jobs
            SET status='DONE', completed_at=datetime('now')
            WHERE id=?
            """,
            (job_id,),
        )
        await db.commit()


async def update_job_failed(job_id: int, error: str):
    """Mark job as failed."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            UPDATE jobs
            SET status='FAILED', error=?, completed_at=datetime('now')
            WHERE id=?
            """,
            (error, job_id),
        )
        await db.commit()


async def insert_download_failure(
    file_id: int,
    file_unique_id: str,
    source_type: Optional[str],
    source_chat_id: Optional[int],
    original_name: Optional[str],
    error_type: str,
    bot_api_error: Optional[str],
    tdl_error: Optional[str],
):
    """Record a download failure for statistics."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            INSERT INTO download_failures 
            (file_id, file_unique_id, source_type, source_chat_id, original_name, error_type, bot_api_error, tdl_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                file_unique_id,
                source_type,
                source_chat_id,
                original_name,
                error_type,
                bot_api_error,
                tdl_error,
            ),
        )
        await db.commit()


async def get_failure_stats(month: Optional[str] = None) -> Dict[str, int]:
    """
    Get download failure statistics.

    Args:
        month: Optional month filter in format 'YYYY-MM'

    Returns:
        Dict with error_type counts
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if month:
            cursor = await db.execute(
                """
                SELECT error_type, COUNT(*) as count
                FROM download_failures
                WHERE strftime('%Y-%m', created_at) = ?
                GROUP BY error_type
                """,
                (month,),
            )
        else:
            cursor = await db.execute(
                """
                SELECT error_type, COUNT(*) as count
                FROM download_failures
                GROUP BY error_type
                """
            )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}


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
