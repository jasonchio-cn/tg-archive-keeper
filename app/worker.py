"""Worker process for downloading large files using tdl."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any

from app import config
from app import database as db
from app import file_manager as fm
from app import markdown_logger as md

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_PATH / "worker.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

WORKER_ID = f"worker-{os.getpid()}"


def calculate_backoff(attempts: int) -> int:
    """
    Calculate exponential backoff in seconds.
    Formula: min(2^attempts * 30, 21600) = min(30s, 1m, 2m, 4m, 8m, 16m, 32m, 64m, 6h)
    """
    backoff = min(2**attempts * 30, 21600)  # Max 6 hours
    return backoff


async def download_with_tdl(
    target_path: Path,
    source_info: Optional[Dict[str, Any]] = None,
    original_message_id: Optional[int] = None,
    source_chat_id: Optional[int] = None,
) -> tuple[bool, Optional[str]]:
    """
    Download file using tdl.

    Args:
        target_path: Final target path
        source_info: Source information containing username for public channels
        original_message_id: Original message ID (from forward_origin)
        source_chat_id: Source chat ID (from forward_source)

    Returns:
        (success, error_message)
    """
    try:
        # Create temp directory
        temp_dir = target_path.parent / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Build Telegram message URL
        message_url = None

        if original_message_id and source_chat_id:
            if source_info and source_info.get("username"):
                # Public channel with username: https://t.me/{username}/{message_id}
                message_url = (
                    f"https://t.me/{source_info['username']}/{original_message_id}"
                )
            else:
                # Private channel/group: use c/ format
                chat_id_str = str(source_chat_id)
                if chat_id_str.startswith("-100"):
                    # Private supergroup/channel: remove -100 prefix
                    clean_chat_id = chat_id_str[4:]  # Remove "-100"
                    message_url = (
                        f"https://t.me/c/{clean_chat_id}/{original_message_id}"
                    )
                elif chat_id_str.startswith("-"):
                    # Regular group
                    clean_chat_id = chat_id_str[1:]  # Remove "-"
                    message_url = (
                        f"https://t.me/c/{clean_chat_id}/{original_message_id}"
                    )
                else:
                    # Direct chat - this is tricky, but try c/ format
                    message_url = f"https://t.me/c/{chat_id_str}/{original_message_id}"
        else:
            # Fallback: this may not work for forwarded messages
            logger.warning("No original message info available, tdl download may fail")
            return False, "No original message info available for tdl download"

        logger.info(f"Built message URL: {message_url}")

        # tdl command using URL format
        # --continue: resume download without interaction
        # --group: handle media groups/albums
        cmd = [
            "tdl",
            "dl",
            "-u",
            message_url,
            "-d",
            str(temp_dir),
            "--continue",
            "--group",
        ]

        logger.info(f"Running tdl command: {' '.join(cmd)}")

        # Run tdl
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            stderr_msg = stderr.decode("utf-8", errors="ignore")
            stdout_msg = stdout.decode("utf-8", errors="ignore")
            error_msg = stderr_msg or stdout_msg or "(no output)"
            logger.error(f"tdl failed with code {process.returncode}")
            logger.error(f"tdl stderr: {stderr_msg}")
            logger.error(f"tdl stdout: {stdout_msg}")
            return False, f"tdl exit code {process.returncode}: {error_msg}"

        logger.info(f"tdl output: {stdout.decode('utf-8', errors='ignore')}")

        # Find downloaded file in temp directory
        # tdl may create subdirectories or use different naming
        # For MVP, assume tdl downloads to temp_dir directly
        downloaded_files = list(temp_dir.glob("*"))
        downloaded_files = [f for f in downloaded_files if f.is_file()]

        if not downloaded_files:
            return False, "No file found in tdl output directory"

        if len(downloaded_files) > 1:
            logger.warning(
                f"Multiple files found in tdl output, using first: {downloaded_files}"
            )

        temp_file = downloaded_files[0]

        # Move to final location
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file.rename(target_path)

        # Cleanup temp directory
        try:
            temp_dir.rmdir()
        except:
            pass

        logger.info(f"Successfully downloaded file to {target_path}")
        return True, None

    except Exception as e:
        logger.error(f"Error downloading with tdl: {e}", exc_info=True)
        return False, str(e)


async def process_job(job: dict):
    """Process a single job."""
    job_id = job["id"]
    file_id = job["file_id"]
    message_id = job["message_id"]
    attempts = job["attempts"]

    logger.info(f"Processing job {job_id} (attempt {attempts})")

    try:
        # Get file info
        file_record = await db.get_file_by_id(file_id)
        if not file_record:
            raise Exception(f"File {file_id} not found in database")

        file_unique_id = file_record["file_unique_id"]
        file_size = file_record["file_size"]

        # Check if already downloaded
        if file_record["status"] == "DOWNLOADED" and file_record["local_path"]:
            local_path = Path(file_record["local_path"])
            if await fm.verify_file(local_path, file_size):
                logger.info(
                    f"File {file_unique_id} already downloaded, marking job done"
                )
                await db.update_job_done(job_id)
                return

        # Get message info
        message = await db.get_message_by_id(message_id)
        if not message:
            raise Exception(f"Message {message_id} not found in database")

        received_at = message["received_at"]

        # Get source info
        source = None
        if message["source_id"]:
            source = await db.get_source_by_id(message["source_id"])

        if not source:
            source = {"source_type": "unknown", "source_chat_id": 0, "title": None}

        # Generate target path
        archive_dir, target_path = fm.get_archive_path(
            source_type=source["source_type"],
            source_chat_id=source["source_chat_id"],
            title=source["title"],
            file_unique_id=file_unique_id,
            original_name=file_record["original_name"],
        )

        logger.info(f"Downloading to {target_path}")

        # Download with tdl
        success, error = await download_with_tdl(
            target_path=target_path,
            source_info=source,
            original_message_id=message.get("original_message_id"),
            source_chat_id=source.get("source_chat_id") if source else None,
        )

        if success:
            # Verify file
            if not await fm.verify_file(target_path, file_size):
                raise Exception(f"File verification failed: size mismatch")

            # Calculate hash
            actual_size = target_path.stat().st_size
            sha256 = await fm.calculate_sha256(target_path)

            # Update database
            await db.update_file_downloaded(
                file_id=file_id,
                local_path=str(target_path),
                local_size=actual_size,
                sha256=sha256,
            )

            await db.update_job_done(job_id)

            # Write to markdown
            await md.append_job_complete(
                job_id=job_id,
                message_id=message_id,
                file_unique_id=file_unique_id,
                local_path=str(target_path),
                local_size=actual_size,
                sha256=sha256,
                received_at=received_at,
            )

            logger.info(f"Job {job_id} completed successfully")
        else:
            # Failed
            raise Exception(error)

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Job {job_id} failed: {error_msg}")

        # Decide: retry or fail permanently
        if attempts >= config.MAX_ATTEMPTS:
            # Permanent failure
            await db.update_job_failed(job_id, error_msg)
            await db.update_file_failed(file_id)

            # Write to markdown
            message = await db.get_message_by_id(message_id)
            file_record = await db.get_file_by_id(file_id)
            if message and file_record:
                await md.append_job_failed(
                    job_id=job_id,
                    message_id=message_id,
                    file_unique_id=file_record["file_unique_id"],
                    error=error_msg,
                    received_at=message["received_at"],
                )

            logger.error(f"Job {job_id} permanently failed after {attempts} attempts")
        else:
            # Retry with backoff
            backoff = calculate_backoff(attempts)
            await db.update_job_retry(job_id, error_msg, backoff)
            logger.info(f"Job {job_id} will retry in {backoff} seconds")


async def worker_loop():
    """Main worker loop."""
    logger.info(f"Worker {WORKER_ID} started")

    # Recover stale jobs on startup
    recovered = await db.recover_stale_jobs(config.STALE_JOB_MINUTES)
    if recovered > 0:
        logger.info(f"Recovered {recovered} stale jobs")

    while True:
        try:
            # Pick and lock a job
            job = await db.pick_and_lock_job(WORKER_ID)

            if job:
                # Process it
                await process_job(job)
            else:
                # No jobs available, sleep
                await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Error in worker loop: {e}", exc_info=True)
            await asyncio.sleep(5)


async def main():
    """Main entry point."""
    logger.info("Starting Telegram Archive Worker")

    # Initialize database
    await db.init_db()

    # Run worker loop
    await worker_loop()


if __name__ == "__main__":
    asyncio.run(main())
