"""File management utilities."""

import re
import hashlib
import aiofiles
import aiohttp
from pathlib import Path
from typing import Optional, Tuple
import logging

from app.config import (
    FILES_PATH,
    WEBDAV_URL,
    WEBDAV_USERNAME,
    WEBDAV_PASSWORD,
    WEBDAV_ENABLED,
)

logger = logging.getLogger(__name__)


def sanitize_filename(name: str, max_length: int = 64) -> str:
    """
    Sanitize filename to only allow [A-Za-z0-9._-].
    - Replace spaces with _
    - Remove path separators and control characters
    - Compress consecutive underscores
    - Truncate to max_length
    """
    if not name:
        return ""

    # Replace spaces with underscore
    name = name.replace(" ", "_")

    # Keep only allowed characters
    name = re.sub(r"[^A-Za-z0-9._-]", "", name)

    # Compress consecutive underscores
    name = re.sub(r"_+", "_", name)

    # Remove leading/trailing underscores and dots
    name = name.strip("_.")

    # Truncate
    if len(name) > max_length:
        # Try to preserve extension
        parts = name.rsplit(".", 1)
        if len(parts) == 2 and len(parts[1]) <= 10:
            # Has extension
            ext = parts[1]
            base = parts[0][: max_length - len(ext) - 1]
            name = f"{base}.{ext}"
        else:
            name = name[:max_length]

    return name


def get_archive_path(
    source_type: str,
    source_chat_id: int,
    title: Optional[str],
    file_unique_id: str,
    original_name: Optional[str],
) -> Tuple[Path, Path]:
    """
    Generate archive directory and file path.

    Returns:
        (directory_path, full_file_path)
    """
    # Sanitize title
    sanitized_title = sanitize_filename(title) if title else ""

    # Build directory path
    if sanitized_title:
        dir_name = f"{source_chat_id}_{sanitized_title}"
    else:
        dir_name = str(source_chat_id)

    archive_dir = FILES_PATH / source_type / dir_name

    # Build filename
    if original_name:
        sanitized_name = sanitize_filename(original_name)
        filename = f"{file_unique_id}__{sanitized_name}"
    else:
        filename = f"{file_unique_id}.bin"

    full_path = archive_dir / filename

    return archive_dir, full_path


def guess_extension(mime_type: Optional[str]) -> str:
    """Guess file extension from MIME type."""
    if not mime_type:
        return ".bin"

    mime_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "video/mpeg": ".mpeg",
        "video/webm": ".webm",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "application/pdf": ".pdf",
        "application/zip": ".zip",
        "text/plain": ".txt",
    }

    return mime_map.get(mime_type, ".bin")


async def calculate_sha256(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()

    async with aiofiles.open(file_path, "rb") as f:
        while True:
            chunk = await f.read(8192)
            if not chunk:
                break
            sha256_hash.update(chunk)

    return sha256_hash.hexdigest()


async def verify_file(file_path: Path, expected_size: Optional[int] = None) -> bool:
    """
    Verify file exists and optionally check size.

    Returns:
        True if file is valid, False otherwise
    """
    if not file_path.exists():
        return False

    if expected_size is not None:
        actual_size = file_path.stat().st_size
        if actual_size != expected_size:
            logger.warning(
                f"Size mismatch: expected {expected_size}, got {actual_size}"
            )
            return False

    return True


async def atomic_write(temp_path: Path, final_path: Path):
    """
    Atomically move temp file to final location.
    Creates parent directory if needed.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.rename(final_path)
    logger.info(f"Atomically moved {temp_path} -> {final_path}")


def get_temp_path(final_path: Path) -> Path:
    """Get temporary path for downloading."""
    return final_path.with_suffix(final_path.suffix + ".part")


def get_webdav_path(local_path: Path) -> str:
    """
    Convert local path to WebDAV remote path.

    Local: /data/files/channel/-1001234/file.mp4
    WebDAV: /channel/-1001234/file.mp4
    """
    # Get relative path from FILES_PATH
    try:
        relative = local_path.relative_to(FILES_PATH)
        return "/" + str(relative).replace("\\", "/")
    except ValueError:
        # If not under FILES_PATH, use filename only
        return "/" + local_path.name


async def upload_to_webdav(local_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Upload file to WebDAV server.

    Args:
        local_path: Local file path

    Returns:
        (success, error_message)
    """
    if not WEBDAV_ENABLED:
        return True, None  # Skip if WebDAV not configured

    remote_path = get_webdav_path(local_path)
    url = WEBDAV_URL.rstrip("/") + remote_path

    logger.info(f"Uploading to WebDAV: {local_path} -> {url}")

    try:
        auth = aiohttp.BasicAuth(WEBDAV_USERNAME, WEBDAV_PASSWORD)

        async with aiohttp.ClientSession(auth=auth) as session:
            # Create parent directories (MKCOL)
            parent_path = "/".join(remote_path.split("/")[:-1])
            if parent_path:
                await _ensure_webdav_dirs(session, parent_path)

            # Upload file (PUT)
            async with aiofiles.open(local_path, "rb") as f:
                content = await f.read()

            async with session.put(url, data=content) as resp:
                if resp.status in (200, 201, 204):
                    logger.info(f"Successfully uploaded to WebDAV: {remote_path}")
                    return True, None
                else:
                    error = f"WebDAV upload failed: HTTP {resp.status}"
                    logger.error(error)
                    return False, error

    except Exception as e:
        error = f"WebDAV upload error: {e}"
        logger.error(error)
        return False, error


async def _ensure_webdav_dirs(session: aiohttp.ClientSession, path: str):
    """
    Ensure WebDAV directories exist (recursive MKCOL).

    Args:
        session: aiohttp session with auth
        path: Directory path like /channel/-1001234
    """
    parts = path.strip("/").split("/")
    current = ""

    for part in parts:
        if not part:
            continue
        current += "/" + part
        url = WEBDAV_URL.rstrip("/") + current

        try:
            async with session.request("MKCOL", url) as resp:
                # 201 = created, 405 = already exists, 409 = conflict (parent missing)
                if resp.status not in (201, 405, 409):
                    logger.warning(f"MKCOL {current} returned {resp.status}")
        except Exception as e:
            logger.warning(f"MKCOL {current} failed: {e}")
