import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from local_seedvc.worker import ResidentWorker


class FakeProcess:
    def __init__(self, reply=None):
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(b'loading models\nVOICE_READY {"device":"NPU"}\n')
        self.stdin = self
        self.reply = reply
        self.wait = AsyncMock()
        self.submitted = asyncio.Event()

    def write(self, data):
        self.submitted.set()
        if self.reply:
            self.stdout.feed_data(self.reply)

    async def drain(self):
        pass

    def kill(self):
        self.returncode = -9
        self.stdout.feed_eof()


class ResidentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.worker = ResidentWorker(Path(self.folder.name))
        self.addAsyncCleanup(self.worker.stop)

    async def test_canceled_conversion_kills_owned_process_before_next_song(self):
        stalled = FakeProcess()
        replacement = FakeProcess(b'VOICE_RESULT {"success":true}\n')
        with patch(
            "local_seedvc.worker.asyncio.create_subprocess_exec",
            AsyncMock(side_effect=[stalled, replacement]),
        ) as launch:
            job = asyncio.create_task(self.worker.convert("song", "voice", "output"))
            await stalled.submitted.wait()
            job.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await job
            self.assertEqual(stalled.returncode, -9)
            stalled.wait.assert_awaited_once()
            await self.worker.convert("next-song", "voice", "output")
            await self.worker.convert("another-song", "voice", "output")
            self.assertEqual(launch.await_count, 2)
            self.assertTrue(self.worker.alive)

    async def test_failed_warmup_does_not_leave_an_orphan_worker(self):
        failed = FakeProcess()
        failed.stdout = asyncio.StreamReader()
        failed.stdout.feed_eof()
        with patch(
            "local_seedvc.worker.asyncio.create_subprocess_exec",
            AsyncMock(return_value=failed),
        ):
            with self.assertRaisesRegex(RuntimeError, "exited"):
                await self.worker.start()
        self.assertFalse(self.worker.alive)
        self.assertEqual(failed.returncode, -9)
        failed.wait.assert_awaited_once()
