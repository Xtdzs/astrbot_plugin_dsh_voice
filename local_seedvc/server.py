"""Loopback-only service using the shared job API and a separate local runtime."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--device", choices=["NPU", "GPU", "CPU"], default="NPU")
    parser.add_argument("--port", type=int, default=7860)
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
        await command(
            python,
            scripts / "engine.py",
            "--runtime",
            root,
            "--source",
            vocal,
            "--reference",
            sample,
            "--output",
            output_dir,
            "--device",
            options.device,
            "--steps",
            options.steps,
        )
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

    @asynccontextmanager
    async def lifespan(app):
        api.ROOT.mkdir(parents=True, exist_ok=True)
        api.GPU_READY = (
            options.device in ov.Core().available_devices
            and (root / "seed-vc/inference.py").is_file()
        )
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

    api.render = render
    api.app.router.lifespan_context = lifespan
    uvicorn.run(
        api.app, host="127.0.0.1", port=options.port, workers=1, access_log=False
    )


if __name__ == "__main__":
    main()
