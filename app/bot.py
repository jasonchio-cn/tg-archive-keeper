"""Telegram Bot process - Single service with integrated download."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

from telegram import Update, Message, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut

from app import config
from app import database as db
from app import file_manager as fm
from app import markdown_logger as md

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_PATH / "bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Suppress noisy httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)

# Reduce noisy PTB polling logs; we'll surface key info ourselves.
logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)

# Download semaphore for concurrency control
download_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)

# Track background tasks
background_tasks: set = set()


# ============ Error Handling ==========


_last_network_error_log_ts: float = 0.0


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global PTB error handler.

    Primary goal: make transient network errors less noisy while keeping
    unexpected exceptions visible.
    """

    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        # Throttle repetitive polling errors to avoid log spam.
        now = asyncio.get_running_loop().time()
        global _last_network_error_log_ts
        if now - _last_network_error_log_ts >= 60:
            _last_network_error_log_ts = now
            logger.warning("Telegram network error (will retry automatically): %s", err)
        return

    logger.error("Unhandled exception in PTB", exc_info=err)


# ============ Download Functions ============


async def download_with_bot_api(
    bot: Bot,
    file_id: str,
    target_path: Path,
) -> Tuple[bool, Optional[str]]:
    """Try to download file using Bot API."""
    try:
        file = await bot.get_file(file_id)
        temp_path = fm.get_temp_path(target_path)
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        await file.download_to_drive(custom_path=str(temp_path))
        await fm.atomic_write(temp_path, target_path)
        return True, None
    except TelegramError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


async def download_file(
    bot: Bot,
    file_info: Dict[str, Any],
    target_path: Path,
    source_info: Optional[Dict[str, Any]],
    original_message_id: Optional[int],
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Download file: try Bot API first, then tdl.

    Returns:
        (success, method, bot_api_error, tdl_error)
        method: "bot_api" | "tdl" | "failed"
    """
    file_id = file_info["file_id"]

    # 1. Try Bot API
    success, bot_error = await download_with_bot_api(bot, file_id, target_path)
    if success:
        return True, "bot_api", None, None

    logger.info(f"Bot API failed: {bot_error}, trying tdl...")

    # 2. Try tdl
    message_url = fm.build_message_url(
        source_username=source_info.get("username") if source_info else None,
        source_chat_id=source_info.get("source_chat_id") if source_info else 0,
        original_message_id=original_message_id,
    )

    if not message_url:
        return False, "failed", bot_error, "Cannot build message URL"

    success, tdl_error = await fm.download_with_tdl(message_url, target_path)
    if success:
        return True, "tdl", bot_error, None

    # 3. Both failed
    return False, "failed", bot_error, tdl_error


# ============ Background Download Task ============


async def process_download_job(
    bot: Bot,
    job_id: int,
    file_id: int,
    message_id: int,
):
    """Process a single download job in background."""
    async with download_semaphore:
        try:
            await db.update_job_running(job_id)

            # Get file and message info
            file_record = await db.get_file_by_id(file_id)
            message = await db.get_message_by_id(message_id)

            if not file_record or not message:
                await db.update_job_failed(job_id, "Record not found")
                return

            # Get source info
            source = None
            if message.get("source_id"):
                source = await db.get_source_by_id(message["source_id"])
            if not source:
                source = {"source_type": "unknown", "source_chat_id": 0}

            # Generate target path
            _, target_path = fm.get_archive_path(
                source_type=source["source_type"],
                source_chat_id=source["source_chat_id"],
                title=source.get("title"),
                file_unique_id=file_record["file_unique_id"],
                original_name=file_record["original_name"],
            )

            # Download
            file_info = {
                "file_id": file_record["last_seen_file_id"],
                "file_unique_id": file_record["file_unique_id"],
            }

            download_result = await download_file(
                bot=bot,
                file_info=file_info,
                target_path=target_path,
                source_info=source,
                original_message_id=message.get("original_message_id"),
            )

            success, method, bot_error, tdl_error = (
                download_result[0],
                download_result[1],
                download_result[2],
                download_result[3],
            )

            if success:
                # Calculate hash and save
                actual_size = target_path.stat().st_size
                sha256 = await fm.calculate_sha256(target_path)

                await fm.save_file(target_path)

                await db.update_file_downloaded(
                    file_id=file_id,
                    local_path=str(target_path),
                    local_size=actual_size,
                    sha256=sha256,
                )
                await db.update_job_done(job_id)

                logger.info(f"Job {job_id} completed via {method}")

                # Log to markdown
                await md.append_job_complete(
                    job_id=job_id,
                    message_id=message_id,
                    file_unique_id=file_record["file_unique_id"],
                    local_path=str(target_path),
                    local_size=actual_size,
                    method=method or "unknown",
                    received_at=message.get("received_at")
                    or (datetime.utcnow().isoformat() + "Z"),
                )
            else:
                # Record failure
                error_type = "BOTH_FAILED"
                if not bot_error:
                    error_type = "TDL_ONLY"
                elif not tdl_error:
                    error_type = "BOT_API_ONLY"

                await db.insert_download_failure(
                    file_id=file_id,
                    file_unique_id=file_record["file_unique_id"],
                    source_type=source["source_type"],
                    source_chat_id=source["source_chat_id"],
                    original_name=file_record["original_name"],
                    error_type=error_type,
                    bot_api_error=bot_error,
                    tdl_error=tdl_error,
                )

                await db.update_file_failed(file_id)
                await db.update_job_failed(
                    job_id, f"Bot API: {bot_error}; tdl: {tdl_error}"
                )

                logger.error(f"Job {job_id} failed: {error_type}")

                # Log to markdown
                await md.append_job_failed(
                    job_id=job_id,
                    message_id=message_id,
                    file_unique_id=file_record["file_unique_id"],
                    error_type=error_type,
                    bot_api_error=bot_error,
                    tdl_error=tdl_error,
                    received_at=message["received_at"],
                )

        except Exception as e:
            logger.error(f"Job {job_id} exception: {e}", exc_info=True)
            await db.update_job_failed(job_id, str(e))


def schedule_download(bot: Bot, job_id: int, file_id: int, message_id: int):
    """Schedule a download job as background task."""
    task = asyncio.create_task(process_download_job(bot, job_id, file_id, message_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


# ============ Message Handler ============


def parse_forward_source(
    message: Message,
) -> Tuple[str, int, Optional[str], Optional[str]]:
    """
    Parse forward source from message.

    Returns:
        (source_type, source_chat_id, title, username)
    """
    # Try forward_origin (new API)
    if hasattr(message, "forward_origin") and message.forward_origin:
        origin = message.forward_origin
        origin_type = origin.__class__.__name__

        if "Channel" in origin_type:
            # ForwardOriginChannel
            chat = getattr(origin, "chat", None)
            if chat:
                return "channel", chat.id, chat.title, getattr(chat, "username", None)
        elif "Chat" in origin_type:
            # ForwardOriginChat
            chat = getattr(origin, "sender_chat", None)
            if chat:
                chat_type = chat.type
                if chat_type == "channel":
                    return (
                        "channel",
                        chat.id,
                        chat.title,
                        getattr(chat, "username", None),
                    )
                elif chat_type == "supergroup":
                    return (
                        "supergroup",
                        chat.id,
                        chat.title,
                        getattr(chat, "username", None),
                    )
                elif chat_type == "group":
                    return "group", chat.id, chat.title, getattr(chat, "username", None)
                else:
                    return (
                        "unknown",
                        chat.id,
                        chat.title,
                        getattr(chat, "username", None),
                    )
        elif "User" in origin_type:
            # ForwardOriginUser
            user = getattr(origin, "sender_user", None)
            if user:
                return "user", user.id, user.full_name, getattr(user, "username", None)
        elif "HiddenUser" in origin_type:
            # ForwardOriginHiddenUser
            sender_name = getattr(origin, "sender_user_name", None)
            if sender_name:
                return "unknown", 0, sender_name, None

    # Try old API
    if hasattr(message, "forward_from_chat") and message.forward_from_chat:
        chat = message.forward_from_chat
        chat_type = chat.type
        if chat_type == "channel":
            return "channel", chat.id, chat.title, getattr(chat, "username", None)
        elif chat_type == "supergroup":
            return "supergroup", chat.id, chat.title, getattr(chat, "username", None)
        elif chat_type == "group":
            return "group", chat.id, chat.title, getattr(chat, "username", None)

    if hasattr(message, "forward_from") and message.forward_from:
        user = message.forward_from
        return "user", user.id, user.full_name, getattr(user, "username", None)

    if hasattr(message, "forward_sender_name") and message.forward_sender_name:
        return "unknown", 0, message.forward_sender_name, None

    # Not a forwarded message
    return "unknown", 0, None, None


def extract_file_info(message: Message) -> List[Dict[str, Any]]:
    """
    Extract file information from message.

    Returns list of dicts with keys:
        - kind: document|photo|video|audio|voice|animation|sticker
        - file_id: Telegram file_id
        - file_unique_id: Telegram file_unique_id
        - file_size: Size in bytes
        - mime_type: MIME type
        - original_name: Original filename
    """
    files = []

    # Document
    if message.document:
        doc = message.document
        files.append(
            {
                "kind": "document",
                "file_id": doc.file_id,
                "file_unique_id": doc.file_unique_id,
                "file_size": doc.file_size,
                "mime_type": doc.mime_type,
                "original_name": doc.file_name,
            }
        )

    # Photo (take largest)
    if message.photo:
        photo = message.photo[-1]  # Largest size
        files.append(
            {
                "kind": "photo",
                "file_id": photo.file_id,
                "file_unique_id": photo.file_unique_id,
                "file_size": photo.file_size,
                "mime_type": "image/jpeg",
                "original_name": f"{photo.file_unique_id}.jpg",
            }
        )

    # Video
    if message.video:
        video = message.video
        files.append(
            {
                "kind": "video",
                "file_id": video.file_id,
                "file_unique_id": video.file_unique_id,
                "file_size": video.file_size,
                "mime_type": video.mime_type,
                "original_name": video.file_name or f"{video.file_unique_id}.mp4",
            }
        )

    # Audio
    if message.audio:
        audio = message.audio
        files.append(
            {
                "kind": "audio",
                "file_id": audio.file_id,
                "file_unique_id": audio.file_unique_id,
                "file_size": audio.file_size,
                "mime_type": audio.mime_type,
                "original_name": audio.file_name or f"{audio.file_unique_id}.mp3",
            }
        )

    # Voice
    if message.voice:
        voice = message.voice
        files.append(
            {
                "kind": "voice",
                "file_id": voice.file_id,
                "file_unique_id": voice.file_unique_id,
                "file_size": voice.file_size,
                "mime_type": voice.mime_type,
                "original_name": f"{voice.file_unique_id}.ogg",
            }
        )

    # Animation (GIF)
    if message.animation:
        anim = message.animation
        files.append(
            {
                "kind": "animation",
                "file_id": anim.file_id,
                "file_unique_id": anim.file_unique_id,
                "file_size": anim.file_size,
                "mime_type": anim.mime_type,
                "original_name": anim.file_name or f"{anim.file_unique_id}.mp4",
            }
        )

    # Sticker
    if message.sticker:
        sticker = message.sticker
        files.append(
            {
                "kind": "sticker",
                "file_id": sticker.file_id,
                "file_unique_id": sticker.file_unique_id,
                "file_size": sticker.file_size,
                "mime_type": "image/webp",
                "original_name": f"{sticker.file_unique_id}.webp",
            }
        )

    return files


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming message."""
    message = update.message
    if not message:
        return

    logger.info(f"Received message {message.message_id} from chat {message.chat_id}")

    try:
        # Parse timestamps
        received_at = datetime.utcnow().isoformat() + "Z"
        forwarded_at = None
        if hasattr(message, "forward_date") and message.forward_date:
            forwarded_at = message.forward_date.isoformat() + "Z"

        # Parse source
        source_type, source_chat_id, source_title, source_username = (
            parse_forward_source(message)
        )

        # Upsert source
        source_id = None
        if source_chat_id != 0:
            source_id = await db.upsert_source(
                source_type=source_type,
                source_chat_id=source_chat_id,
                title=source_title,
                username=source_username,
            )

        # Get text
        text = message.text or message.caption

        # Extract original message ID from forward_origin
        original_message_id = None
        if hasattr(message, "forward_origin") and message.forward_origin:
            if hasattr(message.forward_origin, "message_id"):
                original_message_id = message.forward_origin.message_id
        elif hasattr(message, "forward_from_message_id"):
            # Old API
            original_message_id = message.forward_from_message_id

        # Insert message
        message_id = await db.insert_message(
            tg_chat_id=message.chat_id,
            tg_message_id=message.message_id,
            original_message_id=original_message_id,
            from_user_id=message.from_user.id if message.from_user else None,
            received_at=received_at,
            forwarded_at=forwarded_at,
            source_id=source_id,
            text=text,
            raw_json=json.dumps(message.to_dict()),
        )

        logger.info(f"Inserted message {message_id} into database")

        # Extract files
        file_infos = extract_file_info(message)

        # Process each file
        attachments = []
        for file_info in file_infos:
            file_unique_id = file_info["file_unique_id"]

            # Upsert file
            file_id = await db.upsert_file(
                file_unique_id=file_unique_id,
                last_seen_file_id=file_info["file_id"],
                file_size=file_info["file_size"],
                mime_type=file_info["mime_type"],
                original_name=file_info["original_name"],
            )

            # Insert message_file link
            await db.insert_message_file(
                message_id=message_id,
                file_id=file_id,
                tg_file_id=file_info["file_id"],
                tg_file_unique_id=file_unique_id,
                kind=file_info["kind"],
                caption=message.caption,
            )

            # Get file record
            file_record = await db.get_file_by_id(file_id)

            # Check if already downloaded
            if (
                file_record
                and file_record["status"] == "DOWNLOADED"
                and file_record["local_path"]
            ):
                local_path = Path(file_record["local_path"])
                if await fm.verify_file(local_path):
                    logger.info(f"File {file_unique_id} already downloaded, skipping")
                    attachments.append(
                        {
                            "kind": file_info["kind"],
                            "original_name": file_info["original_name"],
                            "file_size": file_info["file_size"],
                            "file_unique_id": file_unique_id,
                            "status": "DOWNLOADED",
                            "local_path": str(local_path),
                            "is_duplicate": True,
                        }
                    )
                    continue

            # Get source for path generation
            source = await db.get_source_by_id(source_id) if source_id else None
            if not source:
                source = {"source_type": "unknown", "source_chat_id": 0}

            # Generate target path
            _, target_path = fm.get_archive_path(
                source_type=source["source_type"],
                source_chat_id=source["source_chat_id"],
                title=source["title"],
                file_unique_id=file_unique_id,
                original_name=file_info["original_name"],
            )

            # Create job and schedule download
            job_id = await db.insert_job(file_id=file_id, message_id=message_id)

            if job_id:
                schedule_download(
                    bot=context.bot,
                    job_id=job_id,
                    file_id=file_id,
                    message_id=message_id,
                )
                logger.info(f"Scheduled download job {job_id}")
                attachments.append(
                    {
                        "kind": file_info["kind"],
                        "original_name": file_info["original_name"],
                        "file_size": file_info["file_size"],
                        "file_unique_id": file_unique_id,
                        "status": "QUEUED",
                        "job_id": job_id,
                    }
                )
            else:
                logger.info(f"Job already exists for file {file_unique_id}")
                attachments.append(
                    {
                        "kind": file_info["kind"],
                        "original_name": file_info["original_name"],
                        "file_size": file_info["file_size"],
                        "file_unique_id": file_unique_id,
                        "status": "QUEUED",
                        "job_id": None,
                    }
                )

        # Write to markdown
        await md.append_message_entry(
            message_id=message_id,
            tg_chat_id=message.chat_id,
            tg_message_id=message.message_id,
            received_at=received_at,
            forwarded_at=forwarded_at,
            source_type=source_type,
            source_chat_id=source_chat_id,
            source_title=source_title,
            text=text,
            attachments=attachments,
        )

        logger.info(f"Successfully processed message {message_id}")

    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)


# ============ Startup ============


async def post_init(application: Application):
    """Initialize on startup."""
    await db.init_db()

    # Resume pending jobs
    pending_jobs = await db.get_pending_jobs()
    for job in pending_jobs:
        schedule_download(
            bot=application.bot,
            job_id=job["id"],
            file_id=job["file_id"],
            message_id=job["message_id"],
        )
    if pending_jobs:
        logger.info(f"Resumed {len(pending_jobs)} pending jobs")


def main():
    """Main bot entry point."""
    logger.info("Starting Telegram Archive Keeper")

    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        # Increase tolerance for unstable networks.
        # Note: getUpdates long-poll `timeout` is configured in run_polling.
        .connect_timeout(config.BOT_CONNECT_TIMEOUT)
        .read_timeout(config.BOT_READ_TIMEOUT)
        .write_timeout(config.BOT_WRITE_TIMEOUT)
        .pool_timeout(config.BOT_POOL_TIMEOUT)
        .get_updates_connect_timeout(config.BOT_CONNECT_TIMEOUT)
        .get_updates_read_timeout(config.BOT_READ_TIMEOUT)
        .get_updates_write_timeout(config.BOT_WRITE_TIMEOUT)
        .get_updates_pool_timeout(config.BOT_POOL_TIMEOUT)
        .build()
    )

    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_message)
    )

    application.add_error_handler(on_error)

    logger.info("Bot is running...")

    # Long polling settings:
    # - poll_interval: sleep between requests, helps reduce churn
    # - timeout: server-side long poll duration (getUpdates timeout)
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        poll_interval=config.BOT_POLL_INTERVAL,
        timeout=config.BOT_GETUPDATES_TIMEOUT,
        bootstrap_retries=-1,
    )


if __name__ == "__main__":
    main()
