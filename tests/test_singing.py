"""Cloud client and song resolution; no GPU, credentials or paid requests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from test_plugin import PACKAGE, PluginError, config, providers, wav_bytes

singing = __import__(PACKAGE + ".singing", fromlist=["*"])
JOB_ID = "a" * 32


class SingingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cfg = config()
        self.seen = []

    def client(self, handler=None):
        def transport(request):
            self.seen.append(request)
            if handler:
                result = handler(request)
                if result is not None:
                    return result
            if request.url.path.endswith("/health"):
                return httpx.Response(
                    200, json={"protocol": "seedvc-cover-v1", "ready": True}
                )
            if request.url.path.endswith("t2a_v2"):
                return httpx.Response(200, json={"data": {"audio": wav_bytes().hex()}})
            if request.url.path == "/v1/jobs" and request.method == "POST":
                self.assertIn(b'name="song"', request.content)
                self.assertIn(b'name="reference"', request.content)
                return httpx.Response(202, json={"id": JOB_ID, "status": "queued"})
            if request.method == "DELETE":
                return httpx.Response(200, json={"id": JOB_ID, "status": "canceled"})
            return httpx.Response(200, json={"id": JOB_ID, "status": "succeeded"})

        cloud = providers.CloudAudio(
            self.cfg, httpx.AsyncClient(transport=httpx.MockTransport(transport))
        )
        self.addAsyncCleanup(cloud.close)
        cloud.download = AsyncMock(
            side_effect=lambda audio, path: (
                path.write_bytes(audio.content or wav_bytes()),
                path,
            )[1]
        )
        return cloud, singing.SeedSinger(cloud, Path(self.folder.name))

    async def test_full_cover_submits_audio_polls_and_returns_scoped_url(self):
        cloud, singer = self.client()
        with patch.object(singing.asyncio, "sleep", new=AsyncMock()):
            audio = await singer.cover("https://audio.example.org/song.mp3")
        self.assertEqual(
            audio.url, "https://singing.example.org/v1/jobs/" + JOB_ID + "/audio"
        )
        self.assertEqual(len(self.seen), 4)
        for request in self.seen:
            expected = (
                "fake-minimax-secret"
                if request.url.host == "api.minimax.cn"
                else "fake-singing-secret"
            )
            self.assertEqual(request.headers["authorization"], "Bearer " + expected)
        self.assertEqual(cloud.download.await_count, 2)

    async def test_unready_service_fails_before_paid_sample(self):
        cloud, singer = self.client(
            lambda request: httpx.Response(
                200, json={"protocol": "seedvc-cover-v1", "ready": False}
            )
        )
        with self.assertRaisesRegex(PluginError, "尚未就绪"):
            await singer.cover("https://audio.example.org/song.mp3")
        self.assertEqual(len(self.seen), 1)
        cloud.download.assert_not_awaited()

    async def test_missing_service_config_avoids_all_network(self):
        self.cfg["singing"]["base_url"] = ""
        _, singer = self.client()
        with self.assertRaisesRegex(PluginError, "base_url"):
            await singer.cover("https://audio.example.org/song.mp3")
        self.assertEqual(self.seen, [])

    async def test_reference_reused_and_refreshed_after_voice_change(self):
        _, singer = self.client()
        first = await singer.reference()
        self.assertEqual(first, await singer.reference())
        self.cfg["minimax"]["voice_id"] = "another-voice"
        second = await singer.reference()
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())
        self.assertEqual(len(self.seen), 2)

    async def test_concurrent_reference_requests_only_generate_once(self):
        _, singer = self.client()
        one, two = await singing.asyncio.gather(singer.reference(), singer.reference())
        self.assertEqual(one, two)
        self.assertEqual(len(self.seen), 1)

    async def test_custom_reference_skips_tts_and_does_not_need_minimax_key(self):
        self.cfg["minimax"]["api_key"] = ""
        self.cfg["singing"]["reference_url"] = "https://audio.example.org/character.wav"
        cloud, singer = self.client()
        await singer.reference()
        self.assertEqual(self.seen, [])
        self.assertEqual(
            cloud.download.await_args.args[0].url, self.cfg["singing"]["reference_url"]
        )

    async def test_poll_error_cancels_job_without_echoing_provider_secrets(self):
        def fail_poll(request):
            if request.method == "GET" and request.url.path.endswith(JOB_ID):
                return httpx.Response(503, text="secret-payload")

        _, singer = self.client(fail_poll)
        with patch.object(singing.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(PluginError) as error:
                await singer.cover("https://audio.example.org/song.mp3")
        self.assertNotIn("secret-payload", str(error.exception))
        self.assertEqual(self.seen[-1].method, "DELETE")

    async def test_timeout_attempts_cloud_cancellation(self):
        _, singer = self.client()
        with patch.object(
            singing.asyncio, "sleep", new=AsyncMock(side_effect=TimeoutError)
        ):
            with self.assertRaisesRegex(PluginError, "已尝试取消"):
                await singer.cover("https://audio.example.org/song.mp3")
        self.assertEqual(self.seen[-1].method, "DELETE")

    async def test_terminal_failure_does_not_download_or_report_success(self):
        _, singer = self.client(
            lambda request: httpx.Response(202, json={"id": JOB_ID, "status": "failed"})
            if request.method == "POST" and request.url.path == "/v1/jobs"
            else None
        )
        with self.assertRaisesRegex(PluginError, "翻唱失败"):
            await singer.cover("https://audio.example.org/song.mp3")
        self.assertEqual(self.seen[-1].method, "DELETE")

    async def test_failed_reference_download_does_not_poison_cache(self):
        cloud, singer = self.client()
        cloud.download.side_effect = PluginError("下载失败")
        with self.assertRaises(PluginError):
            await singer.reference()
        self.assertEqual(list(singer.root.glob("*.audio")), [])
        self.assertEqual(list(singer.root.glob("*.part")), [])


class SongTests(unittest.TestCase):
    def test_library_resolves_exact_name_and_real_url(self):
        cfg = {"cover": {"songs": ["测试歌 | https://audio.example.org/song.mp3"]}}
        self.assertEqual(
            singing.resolve_song(cfg, "测试歌"), "https://audio.example.org/song.mp3"
        )
        self.assertEqual(
            singing.resolve_song(cfg, "https://audio.example.org/other.wav"),
            "https://audio.example.org/other.wav",
        )
        with self.assertRaisesRegex(PluginError, "歌单中没有"):
            singing.resolve_song(cfg, "相似但不同的歌")

    def test_bad_or_duplicate_library_items_fail_clearly(self):
        for entries in (
            ["只有歌名"],
            [" | https://audio.example.org/x"],
            ["A | https://audio.example.org/a", "a | https://audio.example.org/b"],
        ):
            with self.subTest(entries=entries), self.assertRaises(PluginError):
                singing.song_library({"cover": {"songs": entries}})
