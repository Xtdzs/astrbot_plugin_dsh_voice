"""HTTP contract tests for the separate service, with GPU work substituted."""

import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from test_plugin import ROOT, wav_bytes

HAS_SERVER = bool(
    importlib.util.find_spec("fastapi") and importlib.util.find_spec("multipart")
)
if HAS_SERVER:
    spec = importlib.util.spec_from_file_location(
        "seedvc_cloud_test", ROOT / "cloud_seedvc" / "app.py"
    )
    server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)


@unittest.skipUnless(HAS_SERVER, "Separate cloud requirements are not installed")
class CloudServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.patches = [
            patch.object(server, "ROOT", Path(self.folder.name)),
            patch.object(server, "API_KEY", "fake-cloud-key"),
            patch.object(server, "GPU_READY", True),
            patch.object(server, "jobs", {}),
            patch.object(server, "worker", asyncio.Semaphore(1)),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="https://singing.example.org",
            headers={"Authorization": "Bearer fake-cloud-key"},
        )
        self.addAsyncCleanup(self.client.aclose)

    async def submit(self):
        return await self.client.post(
            "/v1/jobs",
            files={
                "song": ("song.mp3", wav_bytes()),
                "reference": ("sample.wav", wav_bytes()),
            },
        )

    async def test_auth_protects_jobs_health_and_audio(self):
        for path in ("/v1/health", "/v1/jobs/unknown", "/v1/jobs/unknown/audio"):
            response = await self.client.get(
                path, headers={"Authorization": "Bearer wrong"}
            )
            self.assertEqual(response.status_code, 401)
        with patch.object(server, "API_KEY", ""):
            self.assertEqual((await self.client.get("/v1/health")).status_code, 503)

    async def test_submit_poll_download_and_input_cleanup(self):
        async def render(job):
            (job.folder / "output.wav").write_bytes(wav_bytes())

        with patch.object(server, "render", side_effect=render):
            response = await self.submit()
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["id"]
            await server.jobs[job_id].task
        self.assertEqual(
            (await self.client.get("/v1/jobs/" + job_id)).json()["status"], "succeeded"
        )
        response = await self.client.get("/v1/jobs/" + job_id + "/audio")
        self.assertEqual(response.content, wav_bytes())
        self.assertEqual(
            [p.name for p in server.jobs[job_id].folder.iterdir()], ["output.wav"]
        )

    async def test_cancel_processing_job_and_refuse_incomplete_audio(self):
        started = asyncio.Event()

        async def render(job):
            started.set()
            await asyncio.Event().wait()

        with patch.object(server, "render", side_effect=render):
            response = await self.submit()
            job_id = response.json()["id"]
            await started.wait()
            self.assertEqual(
                (await self.client.get("/v1/jobs/" + job_id + "/audio")).status_code,
                409,
            )
            canceled = await self.client.delete("/v1/jobs/" + job_id)
        self.assertEqual(canceled.json()["status"], "canceled")
        self.assertFalse(server.jobs[job_id].folder.exists())

    async def test_worker_failure_is_terminal_and_hides_raw_details(self):
        with patch.object(
            server, "render", new=AsyncMock(side_effect=RuntimeError("private-key"))
        ):
            response = await self.submit()
            job_id = response.json()["id"]
            await server.jobs[job_id].task
        response = await self.client.get("/v1/jobs/" + job_id)
        self.assertEqual(response.json()["status"], "failed")
        self.assertNotIn("private-key", response.text)
        self.assertEqual(list(server.jobs[job_id].folder.iterdir()), [])

    async def test_gpu_unavailable_rejects_before_accepting_job(self):
        with patch.object(server, "GPU_READY", False):
            response = await self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(server.jobs, {})

    async def test_empty_upload_rejected_and_capacity_released(self):
        response = await self.client.post(
            "/v1/jobs",
            files={"song": ("song.mp3", b""), "reference": ("sample.wav", wav_bytes())},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(server.jobs, {})

    async def test_expired_output_removed_on_request(self):
        job = server.Job(
            "a" * 32,
            Path(self.folder.name) / ("a" * 32),
            status="succeeded",
            finished=1,
        )
        job.folder.mkdir()
        (job.folder / "output.wav").write_bytes(wav_bytes())
        server.jobs[job.id] = job
        with patch.object(server.time, "monotonic", return_value=4000):
            response = await self.client.get("/v1/jobs/" + job.id)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(job.folder.exists())

    async def test_render_keeps_pitch_timing_and_remixes_original_backing(self):
        job = server.Job("a" * 32, Path(self.folder.name))
        calls = []

        async def command(*args, **kwargs):
            args = list(map(str, args))
            calls.append(args)
            if "--f0-condition" in args:
                output = Path(args[args.index("--output") + 1])
                output.mkdir()
                (output / "converted.wav").write_bytes(wav_bytes())
            elif args[-1].endswith("song.wav.tmp.wav"):
                Path(args[-1]).write_bytes(wav_bytes())

        with (
            patch.object(server, "duration", new=AsyncMock(return_value=12)),
            patch.object(server, "command", side_effect=command),
        ):
            await server.render(job)
        inference = next(args for args in calls if "--f0-condition" in args)
        self.assertEqual(inference[inference.index("--f0-condition") + 1], "True")
        self.assertEqual(inference[inference.index("--auto-f0-adjust") + 1], "False")
        self.assertEqual(inference[inference.index("--semi-tone-shift") + 1], "0")
        self.assertTrue(any("no_vocals.wav" in value for value in calls[-1]))
        self.assertTrue((job.folder / "output.wav").exists())
