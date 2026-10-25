"""Loopback-only service using the shared job API and a separate local runtime."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import hashlib
import time
import os
from pathlib import Path
import secrets
import shutil
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--device", choices=["NPU", "GPU", "CPU"], default="NPU")
    parser.add_argument("--port", type=int, default=17860)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--vocals-only", action="store_true")
    options = parser.parse_args()
    root = options.runtime.resolve()
    root.mkdir(parents=True, exist_ok=True)
    key_file = root / "api-key.txt"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(32), "utf-8")
    os.environ["COVER_API_KEY"] = key_file.read_text("utf-8").strip()
    os.environ["COVER_JOB_DIR"] = str(root / "jobs")
    os.environ["SEEDVC_ROOT"] = str(root / "seed-vc")
    os.environ["TORCH_HOME"] = str(root / "torch-cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cloud_seedvc import app as api
    import imageio_ffmpeg
    import openvino as ov
    import soundfile as sf
    import uvicorn
    from local_seedvc.worker import ResidentWorker

    resident = ResidentWorker(root, options.device, options.steps)
    cache = root / "song-cache"
    cache.mkdir(exist_ok=True)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    python = sys.executable
    scripts = Path(__file__).resolve().parent

    async def command(*arguments):
        settings = (
            {"creationflags": 0x08000000}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        process = await asyncio.create_subprocess_exec(
            *map(str, arguments),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **settings,
        )
        try:
            with (root / "last-worker.log").open("ab") as log:
                while chunk := await process.stdout.read(65536):
                    log.write(chunk)
            await process.wait()
            if process.returncode:
                raise RuntimeError("Local worker failed; see last-worker.log")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def render(job):
        folder = job.folder
        identity = hashlib.sha256(
            f"local-v2:{options.device}:{options.steps}:{options.vocals_only}".encode()
        )
        for name in ("song.audio", "reference.audio"):
            identity.update((folder / name).stat().st_size.to_bytes(8, "big"))
            with (folder / name).open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    identity.update(chunk)
        cached = cache / (identity.hexdigest() + ".wav")
        if cached.is_file() and time.time() - cached.stat().st_mtime < 86400:
            shutil.copyfile(cached, folder / "output.wav")
            (root / "last-benchmark.json").write_text(
                json.dumps({"cached": True, "device": options.device}), "utf-8"
            )
            job.stage = "cached"
            return
        job.stage = "validating"
        song, sample = folder / "source.wav", folder / "reference.wav"
        await command(
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            folder / "song.audio",
            "-t",
            "601",
            "-ac",
            "2",
            "-ar",
            "44100",
            song,
        )
        if not 1 <= sf.info(song).duration <= 600:
            raise ValueError("Song must be between 1 and 600 seconds")
        await command(
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            folder / "reference.audio",
            "-t",
            "5",
            "-ac",
            "1",
            "-ar",
            "24000",
            sample,
        )
        if sf.info(sample).duration < 1:
            raise ValueError("Reference needs at least one second")
        backing = None
        if options.vocals_only:
            vocal = song
        else:
            job.stage = "separating"
            await command(
                python,
                scripts / "separate.py",
                "--source",
                song,
                "--output",
                folder / "stems",
            )
            stem_dir = folder / "stems" / "htdemucs" / "source"
            vocal, backing = stem_dir / "vocals.wav", stem_dir / "no_vocals.wav"
        job.stage = "converting"
        output_dir = folder / "converted"
        await resident.convert(vocal, sample, output_dir)
        files = list(output_dir.glob("*.wav"))
        if len(files) != 1:
            raise RuntimeError("No unique vocal output")
        report = output_dir / "benchmark.json"
        if report.is_file():
            shutil.copyfile(report, root / "last-benchmark.json")
        job.stage = "mixing"
        args = [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", files[0]]
        if backing:
            args += [
                "-i",
                backing,
                "-filter_complex",
                "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95:level=false",
            ]
        args += ["-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", folder / "output.wav"]
        await command(*args)
        shutil.copyfile(folder / "output.wav", cached)
        # Bound the local result cache to 1 GiB and 24 hours.
        entries = sorted(
            cache.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        size = 0
        for path in entries:
            size += path.stat().st_size
            if size > 1024**3 or time.time() - path.stat().st_mtime > 86400:
                path.unlink(missing_ok=True)

    @asynccontextmanager
    async def lifespan(app):
        api.ROOT.mkdir(parents=True, exist_ok=True)
        api.GPU_READY = False
        if options.device not in ov.Core().available_devices:
            raise RuntimeError("Requested OpenVINO device unavailable")

        async def supervise_worker():
            while True:
                if not resident.alive:
                    api.GPU_READY = False
                    try:
                        started = time.perf_counter()
                        await resident.start()
                        api.GPU_READY = True
                        print(
                            json.dumps(
                                {
                                    "stage": "ready",
                                    "warmup_seconds": round(
                                        time.perf_counter() - started, 3
                                    ),
                                }
                            ),
                            flush=True,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        print(
                            "Worker warmup failed: " + type(error).__name__, flush=True
                        )
                await asyncio.sleep(5)

        supervisor = asyncio.create_task(supervise_worker())
        print(
            json.dumps(
                {
                    "address": f"http://127.0.0.1:{options.port}",
                    "device": options.device,
                    "key_file": str(key_file),
                    "ready": api.GPU_READY,
                }
            ),
            flush=True,
        )
        yield
        tasks = [j.task for j in api.jobs.values() if j.task and not j.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)
        await resident.stop()

    api.render = render
    api.app.router.lifespan_context = lifespan
    uvicorn.run(
        api.app, host="127.0.0.1", port=options.port, workers=1, access_log=False
    )


if __name__ == "__main__":
    main()
