import hashlib
import logging
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from aiohttp import web


class ArchiveStorageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {
            "BOT_TOKEN": "test",
            "DB_PATH": str(root / "app.db"),
            "FILES_PATH": str(root / "files"),
            "LOG_PATH": str(root),
            "NOTES_PATH": str(root),
            "STORAGE_MODE": "local",
        }))
        with patch("logging.FileHandler", return_value=logging.NullHandler()):
            from app import bot

        self.worker = bot
        self.database = root / "app.db"
        self.enterContext(patch.object(bot.db, "DB_PATH", self.database))
        self.enterContext(patch.object(bot.fm, "FILES_PATH", root / "files"))
        self.enterContext(patch.object(bot.md, "NOTES_PATH", root))
        self.notes = root / "2026-01.md"
        await bot.db.init_db()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("""INSERT INTO messages (tg_chat_id,tg_message_id,received_at,raw_json)
                          VALUES (1,1,'2026-01-01T00:00:00Z','{}')""")
        self.file_id = await bot.db.upsert_file("test-file", "telegram-id")
        self.job_id = await bot.db.insert_job(self.file_id, 1)
        self.content = b"archive storage regression"

        async def download(bot, file_info, target_path, source_info, original_message_id):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(self.content)
            return True, "bot_api", None, None

        self.enterContext(patch.object(bot, "download_file", download))

    async def run_job(self, statuses, keep_local):
        uploads = []
        self.wait = AsyncMock()
        async def handle(request):
            if request.method == "MKCOL":
                return web.Response(status=201)
            self.assertEqual(await request.read(), self.content)
            uploads.append(request.method)
            return web.Response(status=statuses[len(uploads) - 1])

        server = web.Application()
        server.router.add_route("*", "/{path:.*}", handle)
        runner = web.AppRunner(server)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        fm = self.worker.fm
        with patch.object(fm, "sleep", self.wait), patch.multiple(
            fm, WEBDAV_ENABLED=True, SAVE_TO_WEBDAV=True, SAVE_TO_LOCAL=keep_local,
            WEBDAV_URL=f"http://127.0.0.1:{runner.addresses[0][1]}",
            WEBDAV_USERNAME="test", WEBDAV_PASSWORD="test",
        ):
            await self.worker.process_download_job(None, self.job_id, self.file_id, 1)
        self.assertEqual(uploads, ["PUT"] * len(statuses))
        with closing(sqlite3.connect(self.database)) as db:
            job = db.execute("SELECT status, error FROM jobs WHERE id=?", (self.job_id,)).fetchone()
        record = await self.worker.db.get_file_by_id(self.file_id)
        return job, record

    async def check_failed_upload(self, keep_local):
        job, record = await self.run_job([503] * 4, keep_local)
        self.assertEqual(self.wait.await_args_list, [call(2), call(4), call(8)])
        self.assertEqual(job[0], "FAILED")
        self.assertIn("HTTP 503", job[1])
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(Path(record["local_path"]).read_bytes(), self.content)
        self.assertEqual(record["local_size"], len(self.content))
        self.assertEqual(record["sha256"], hashlib.sha256(self.content).hexdigest())
        notes = self.notes.read_text(encoding="utf-8")
        self.assertIn("STORAGE_FAILED", notes)
        self.assertIn("HTTP 503", notes)
        self.assertNotIn("### COMPLETE", notes)

    async def test_webdav_failure(self):
        await self.check_failed_upload(keep_local=False)

    async def test_local_and_webdav_failure(self):
        await self.check_failed_upload(keep_local=True)

    async def test_webdav_success(self):
        job, record = await self.run_job([201], keep_local=False)
        self.wait.assert_not_awaited()
        self.assertEqual(job, ("DONE", None))
        self.assertEqual(record["status"], "DOWNLOADED")
        self.assertFalse(Path(record["local_path"]).exists())
        self.assertIn("### COMPLETE", self.notes.read_text(encoding="utf-8"))

    async def test_success_on_second_retry(self):
        job, record = await self.run_job([503, 503, 201], keep_local=False)
        self.assertEqual(self.wait.await_args_list, [call(2), call(4)])
        self.assertEqual(job, ("DONE", None))
        self.assertFalse(Path(record["local_path"]).exists())
