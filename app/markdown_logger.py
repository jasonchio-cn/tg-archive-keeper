"""Markdown logging utilities."""

import aiofiles
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import logging

from app.config import NOTES_PATH

logger = logging.getLogger(__name__)


def get_markdown_path(timestamp: str) -> Path:
    """
    Get markdown file path for a given timestamp.
    Format: /data/notes/YYYY-MM.md

    Args:
        timestamp: ISO format timestamp string
    """
    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    filename = dt.strftime("%Y-%m.md")
    return NOTES_PATH / filename


async def append_message_entry(
    message_id: int,
    tg_chat_id: int,
    tg_message_id: int,
    received_at: str,
    forwarded_at: Optional[str],
    source_type: str,
    source_chat_id: int,
    source_title: Optional[str],
    text: Optional[str],
    attachments: List[Dict[str, Any]],
):
    """
    Append a message entry to the markdown log.

    Args:
        message_id: Database message ID
        tg_chat_id: Telegram chat ID
        tg_message_id: Telegram message ID
        received_at: When bot received the message
        forwarded_at: Original message timestamp (if available)
        source_type: channel|group|supergroup|user|unknown
        source_chat_id: Source chat ID
        source_title: Source title/name
        text: Message text/caption
        attachments: List of attachment dicts with keys:
            - kind: document|photo|video|etc
            - original_name: Original filename
            - file_size: Size in bytes
            - file_unique_id: Telegram file unique ID
            - status: NEW|DOWNLOADED|QUEUED
            - local_path: Path if downloaded (optional)
            - job_id: Job ID if queued (optional)
    """
    md_path = get_markdown_path(received_at)

    # Build entry
    lines = []
    lines.append(
        f"\n## {received_at} msg:{message_id} tg:{tg_chat_id}/{tg_message_id}\n"
    )

    # Source info
    source_info = f"source: {source_type} {source_chat_id}"
    if source_title:
        source_info += f' "{source_title}"'
    lines.append(f"{source_info}\n")

    # Forwarded time
    if forwarded_at:
        lines.append(f"forwarded_at: {forwarded_at}\n")

    # Text
    if text:
        lines.append(f"text: {text}\n")

    # Attachments
    if attachments:
        lines.append("\nattachments:\n")
        for att in attachments:
            kind = att.get("kind", "unknown")
            name = att.get("original_name", "unnamed")
            size = att.get("file_size", 0)
            unique_id = att.get("file_unique_id", "")
            status = att.get("status", "NEW")

            att_line = f'- file: {kind} name="{name}" size={size} unique_id={unique_id}'

            if status == "DOWNLOADED" and att.get("local_path"):
                att_line += f" status=DOWNLOADED path={att['local_path']}"
            elif status == "QUEUED" and att.get("job_id"):
                att_line += f" status=QUEUED job:{att['job_id']}"
            elif att.get("is_duplicate") and att.get("local_path"):
                att_line += f" status=DUPLICATE path={att['local_path']}"
            else:
                att_line += f" status={status}"

            lines.append(att_line + "\n")

    lines.append("\n")

    # Append to file
    md_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(md_path, "a", encoding="utf-8") as f:
        await f.writelines(lines)

    logger.info(f"Appended message entry to {md_path}")


async def append_job_complete(
    job_id: int,
    message_id: int,
    file_unique_id: str,
    local_path: str,
    local_size: int,
    sha256: Optional[str],
    received_at: str,
):
    """
    Append a job completion entry to the markdown log.

    Args:
        job_id: Job ID
        message_id: Related message ID
        file_unique_id: File unique ID
        local_path: Downloaded file path
        local_size: File size
        sha256: SHA256 hash (optional)
        received_at: Original message timestamp (for determining which month file)
    """
    md_path = get_markdown_path(received_at)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")

    lines = []
    lines.append(
        f"\n### COMPLETE {now} job:{job_id} msg:{message_id} file:{file_unique_id}\n"
    )
    lines.append(f"- status=DOWNLOADED path={local_path} size={local_size}")

    if sha256:
        lines.append(f" sha256={sha256}")

    lines.append("\n\n")

    # Append to file
    md_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(md_path, "a", encoding="utf-8") as f:
        await f.writelines(lines)

    logger.info(f"Appended job completion to {md_path}")


async def append_job_failed(
    job_id: int, message_id: int, file_unique_id: str, error: str, received_at: str
):
    """
    Append a job failure entry to the markdown log.
    """
    md_path = get_markdown_path(received_at)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")

    lines = []
    lines.append(
        f"\n### FAILED {now} job:{job_id} msg:{message_id} file:{file_unique_id}\n"
    )
    lines.append(f"- error: {error}\n\n")

    # Append to file
    md_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(md_path, "a", encoding="utf-8") as f:
        await f.writelines(lines)

    logger.info(f"Appended job failure to {md_path}")
