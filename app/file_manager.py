"""File management utilities."""

import asyncio
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
    SAVE_TO_LOCAL,
    SAVE_TO_WEBDAV,
)

logger = logging.getLogger(__name__)


def sanitize_filename(name: str, max_length: int = 64) -> str:
    """
    Sanitize filename to keep original name as much as possible.
    Removes emoji and unsafe special symbols but keeps Chinese characters,
    Latin letters, numbers, and safe symbols (._-).
    """
    if not name:
        return ""

    # Replace spaces with underscores
    name = name.replace(" ", "_")

    # Remove emoji and other special Unicode characters while preserving Chinese, letters, numbers, and safe symbols
    # This regex keeps:
    # - Chinese characters (Unicode range \u4e00-\u9fff)
    # - Latin letters (A-Za-z)
    # - Numbers (0-9)
    # - Common safe symbols (._-)
    # - Other common punctuation that might be in filenames
    name = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9._\-]", "", name)

    # Clean up multiple underscores
    name = re.sub(r"_+", "_", name)
    name = name.strip("_.")

    if len(name) > max_length:
        parts = name.rsplit(".", 1)
        if len(parts) == 2 and len(parts[1]) <= 10:
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
    sanitized_title = sanitize_filename(title) if title else ""

    if sanitized_title:
        dir_name = f"{source_chat_id}_{sanitized_title}"
    else:
        dir_name = str(source_chat_id)

    archive_dir = FILES_PATH / source_type / dir_name

    if original_name:
        sanitized_name = sanitize_filename(original_name)
        filename = f"{file_unique_id}__{sanitized_name}"
    else:
        filename = f"{file_unique_id}.bin"

    full_path = archive_dir / filename

    return archive_dir, full_path


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
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.rename(final_path)
    logger.debug(f"Moved {temp_path} -> {final_path}")


def get_temp_path(final_path: Path) -> Path:
    """Get temporary path for downloading."""
    return final_path.with_suffix(final_path.suffix + ".part")


# ============ tdl Download ============


def build_message_url(
    source_username: Optional[str],
    source_chat_id: Optional[int],
    original_message_id: Optional[int],
) -> Optional[str]:
    """
    Build Telegram message URL for tdl.

    Returns:
        Message URL or None if cannot build
    """
    if not original_message_id:
        return None

    if source_username:
        return f"https://t.me/{source_username}/{original_message_id}"

    if source_chat_id:
        chat_id_str = str(source_chat_id)
        if chat_id_str.startswith("-100"):
            clean_id = chat_id_str[4:]
        elif chat_id_str.startswith("-"):
            clean_id = chat_id_str[1:]
        else:
            clean_id = chat_id_str
        return f"https://t.me/c/{clean_id}/{original_message_id}"

    return None


async def download_with_tdl(
    message_url: str,
    target_path: Path,
) -> Tuple[bool, Optional[str]]:
    """
    Download file using tdl.

    Args:
        message_url: Telegram message URL (e.g., https://t.me/channel/123)
        target_path: Final target path

    Returns:
        (success, error_message)
    """
    try:
        temp_dir = target_path.parent / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "tdl",
            "dl",
            "-u",
            message_url,
            "-d",
            str(temp_dir),
            "--continue",
            "--skip-same",
        ]

        logger.info(f"Running tdl: {' '.join(cmd)}")

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            stderr_msg = stderr.decode("utf-8", errors="ignore")
            stdout_msg = stdout.decode("utf-8", errors="ignore")
            error = stderr_msg or stdout_msg or "Unknown error"
            logger.error(f"tdl failed: {error}")
            return False, f"tdl exit {process.returncode}: {error.strip()}"

        # Find downloaded file
        downloaded_files = [f for f in temp_dir.glob("*") if f.is_file()]
        if not downloaded_files:
            return False, "No file found in tdl output"

        if len(downloaded_files) > 1:
            logger.warning(f"Multiple files found, using first: {downloaded_files}")

        # Move to target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        downloaded_files[0].rename(target_path)

        # Cleanup temp dir
        try:
            temp_dir.rmdir()
        except:
            pass

        logger.info(f"tdl download successful: {target_path}")
        return True, None

    except Exception as e:
        logger.error(f"tdl exception: {e}")
        return False, str(e)


# ============ WebDAV Upload ============


def get_webdav_path(local_path: Path) -> str:
    """
    Convert local path to WebDAV remote path.
    """
    try:
        relative = local_path.relative_to(FILES_PATH)
        return "/" + str(relative).replace("\\", "/")
    except ValueError:
        return "/" + local_path.name


async def upload_to_webdav(local_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Upload file to WebDAV server.
    """
    if not WEBDAV_ENABLED:
        return True, None

    remote_path = get_webdav_path(local_path)
    url = WEBDAV_URL.rstrip("/") + remote_path

    logger.info(f"Uploading to WebDAV: {local_path} -> {url}")

    try:
        auth = aiohttp.BasicAuth(WEBDAV_USERNAME, WEBDAV_PASSWORD)

        async with aiohttp.ClientSession(auth=auth) as session:
            # Create parent directories
            parent_path = "/".join(remote_path.split("/")[:-1])
            if parent_path:
                await _ensure_webdav_dirs(session, parent_path)

            # Upload file
            async with aiofiles.open(local_path, "rb") as f:
                content = await f.read()

            async with session.put(url, data=content) as resp:
                if resp.status in (200, 201, 204):
                    logger.info(f"WebDAV upload successful: {remote_path}")
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
                if resp.status not in (201, 405, 409):
                    logger.warning(f"MKCOL {current} returned {resp.status}")
        except Exception as e:
            logger.warning(f"MKCOL {current} failed: {e}")


async def save_file(
    local_path: Path, delete_local_after_webdav: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Save file according to configured storage mode.
    """
    errors = []

    # Upload to WebDAV if enabled
    if SAVE_TO_WEBDAV:
        webdav_success, webdav_error = await upload_to_webdav(local_path)
        if not webdav_success:
            errors.append(f"WebDAV: {webdav_error}")

    # Handle local storage
    if SAVE_TO_LOCAL:
        logger.info(f"File saved locally: {local_path}")
    elif SAVE_TO_WEBDAV and delete_local_after_webdav:
        if not errors:
            try:
                local_path.unlink()
                logger.info(f"Deleted local file after WebDAV upload: {local_path}")
            except Exception as e:
                logger.warning(f"Failed to delete local file: {e}")

    if errors:
        return False, "; ".join(errors)
    return True, None
