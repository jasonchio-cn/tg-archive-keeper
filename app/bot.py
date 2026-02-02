"""Telegram Bot process."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

from telegram import Update, Message
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import aiofiles

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


async def download_small_file(
    bot_token: str, file_id: str, target_path: Path
) -> Tuple[bool, Optional[str]]:
    """
    Download file using Bot API.

    Returns:
        (success, error_message)
    """
    try:
        from telegram import Bot

        bot = Bot(token=bot_token)

        # Get file
        file = await bot.get_file(file_id)

        # Download to temp file
        temp_path = fm.get_temp_path(target_path)
        temp_path.parent.mkdir(parents=True, exist_ok=True)

        await file.download_to_drive(custom_path=str(temp_path))

        # Atomic move
        await fm.atomic_write(temp_path, target_path)

        return True, None

    except Exception as e:
        logger.error(f"Failed to download file {file_id}: {e}")
        return False, str(e)


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

        # Insert message
        message_id = await db.insert_message(
            tg_chat_id=message.chat_id,
            tg_message_id=message.message_id,
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
            file_id_tg = file_info["file_id"]
            file_size = file_info["file_size"]

            # Upsert file
            file_id = await db.upsert_file(
                file_unique_id=file_unique_id,
                last_seen_file_id=file_id_tg,
                file_size=file_size,
                mime_type=file_info["mime_type"],
                original_name=file_info["original_name"],
            )

            # Insert message_file link
            await db.insert_message_file(
                message_id=message_id,
                file_id=file_id,
                tg_file_id=file_id_tg,
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
                if await fm.verify_file(local_path, file_size):
                    logger.info(f"File {file_unique_id} already downloaded, skipping")
                    attachments.append(
                        {
                            "kind": file_info["kind"],
                            "original_name": file_info["original_name"],
                            "file_size": file_size,
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
                source = {"source_type": "unknown", "source_chat_id": 0, "title": None}

            # Generate target path
            archive_dir, target_path = fm.get_archive_path(
                source_type=source["source_type"],
                source_chat_id=source["source_chat_id"],
                title=source["title"],
                file_unique_id=file_unique_id,
                original_name=file_info["original_name"],
            )

            # Decide: download now or queue
            if file_size <= config.FILE_SIZE_THRESHOLD:
                # Download now
                logger.info(
                    f"Downloading small file {file_unique_id} ({file_size} bytes)"
                )
                success, error = await download_small_file(
                    bot_token=config.BOT_TOKEN,
                    file_id=file_id_tg,
                    target_path=target_path,
                )

                if success:
                    # Verify and calculate hash
                    actual_size = target_path.stat().st_size
                    sha256 = await fm.calculate_sha256(target_path)

                    # Update database
                    await db.update_file_downloaded(
                        file_id=file_id,
                        local_path=str(target_path),
                        local_size=actual_size,
                        sha256=sha256,
                    )

                    logger.info(f"Downloaded file to {target_path}")

                    attachments.append(
                        {
                            "kind": file_info["kind"],
                            "original_name": file_info["original_name"],
                            "file_size": file_size,
                            "file_unique_id": file_unique_id,
                            "status": "DOWNLOADED",
                            "local_path": str(target_path),
                        }
                    )
                else:
                    logger.error(f"Failed to download file: {error}")
                    attachments.append(
                        {
                            "kind": file_info["kind"],
                            "original_name": file_info["original_name"],
                            "file_size": file_size,
                            "file_unique_id": file_unique_id,
                            "status": "FAILED",
                        }
                    )
            else:
                # Queue for worker
                logger.info(f"Queueing large file {file_unique_id} ({file_size} bytes)")
                job_id = await db.insert_job(file_id=file_id, message_id=message_id)

                if job_id:
                    logger.info(f"Created job {job_id} for file {file_unique_id}")
                    attachments.append(
                        {
                            "kind": file_info["kind"],
                            "original_name": file_info["original_name"],
                            "file_size": file_size,
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
                            "file_size": file_size,
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


async def main():
    """Main bot loop."""
    logger.info("Starting Telegram Archive Bot")

    # Initialize database
    await db.init_db()

    # Create application
    application = Application.builder().token(config.BOT_TOKEN).build()

    # Add handlers
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_message)
    )

    # Start bot
    logger.info("Bot is running...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
