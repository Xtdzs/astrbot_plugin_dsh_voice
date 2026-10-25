"""Deploy separately on a GPU host. This file is never imported by AstrBot."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hmac
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ROOT = Path(os.getenv("COVER_JOB_DIR", "/tmp/seedvc-jobs")).resolve()
SEED_ROOT = Path(os.getenv("SEEDVC_ROOT", "/opt/seed-vc"))
MAX_BYTES = 40 * 1024 * 1024
API_KEY = os.getenv("COVER_API_KEY", "")
GPU_READY = False
jobs: dict[str, Job] = {}
worker = asyncio.Semaphore(1)
bearer = HTTPBearer(auto_error=False)


@dataclass
class Job:
    id: str
    folder: Path
    status: str = "uploading"
    created: float = field(default_factory=time.monotonic)
    finished: float = 0
    task: asyncio.Task | None = None
    stage: str = "queued"


async def authenticate(token: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not API_KEY:
        raise HTTPException(503, "Set COVER_API_KEY in cloud secrets first")
    if not token or not hmac.compare_digest(
        token.credentials.encode(), API_KEY.encode()
    ):
        raise HTTPException(401, "Invalid key")


@asynccontextmanager
async def lifespan(app):
    global GPU_READY
    import torch

    GPU_READY = torch.cuda.is_available() and (SEED_ROOT / "inference.py").is_file()
    ROOT.mkdir(parents=True, exist_ok=True)
    yield
    tasks = [j.task for j in jobs.values() if j.task and not j.task.done()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(lifespan=lifespan)


def prune():
    now = time.monotonic()
    for job_id, job in tuple(jobs.items()):
        if job.finished and now - job.finished > 3600:
            shutil.rmtree(job.folder, ignore_errors=True)
            del jobs[job_id]


def find_job(job_id):
    prune()
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found or expired")
    return job


async def save_upload(upload, target):
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Audio exceeds 40 MB")
                output.write(chunk)
        if not size:
            raise HTTPException(422, "Empty audio")
    finally:
        await upload.close()


async def command(*args, cwd=None):
    # A new process group lets cancellation stop Demucs and inference child processes.
    process = await asyncio.create_subprocess_exec(
        *map(str, args),
        cwd=cwd,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        # Non-ffprobe commands can print lengthy progress; drain continuously.
        output = bytearray()
        while chunk := await process.stdout.read(65536):
            if len(output) < 1024 * 1024:
                output.extend(chunk[: 1024 * 1024 - len(output)])
        await process.wait()
        if process.returncode:
            raise RuntimeError("Cloud media stage failed")
        return bytes(output)
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


async def duration(path):
    output = await command(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    )
    return float(json.loads(output)["format"]["duration"])


async def render(job):
    job.stage = "validating"
    folder = job.folder
    song, reference = folder / "song.audio", folder / "reference.audio"
    if (
        not 1 <= await duration(song) <= 600
        or not 1 <= await duration(reference) <= 600
    ):
        raise ValueError("Audio duration outside 1 to 600 seconds")
    reference_wav = folder / "reference.wav"
    await command(
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        reference,
        "-t",
        "25",
        "-ac",
        "1",
        "-ar",
        "24000",
        reference_wav,
    )
    # Normalize uploaded extensions so Demucs never mistakes a valid .audio file.
    source = folder / "song.wav"
    await command(
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        song,
        "-ar",
        "44100",
        "-ac",
        "2",
        source,
    )
    job.stage = "separating"
    await command(
        sys.executable,
        "-m",
        "demucs",
        "-n",
        "htdemucs",
        "--two-stems",
        "vocals",
        "-o",
        folder / "stems",
        source,
    )
    stems = folder / "stems" / "htdemucs" / "song"
    result_dir = folder / "converted"
    job.stage = "converting"
    await command(
        sys.executable,
        SEED_ROOT / "inference.py",
        "--source",
        stems / "vocals.wav",
        "--target",
        reference_wav,
        "--output",
        result_dir,
        "--diffusion-steps",
        "40",
        "--length-adjust",
        "1.0",
        "--inference-cfg-rate",
        "0.7",
        "--f0-condition",
        "True",
        "--auto-f0-adjust",
        "False",
        "--semi-tone-shift",
        "0",
        "--fp16",
        "True",
        cwd=SEED_ROOT,
    )
    converted = list(result_dir.glob("*.wav"))
    if len(converted) != 1:
        raise RuntimeError("No unique converted vocal")
    # Mono PCM WAV works as QQ voice without FFmpeg on the bot host. At 10min <29MB.
    job.stage = "mixing"
    await command(
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        converted[0],
        "-i",
        stems / "no_vocals.wav",
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95:level=false",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-c:a",
        "pcm_s16le",
        folder / "song.wav.tmp.wav",
    )
    (folder / "song.wav.tmp.wav").replace(folder / "output.wav")
    if not 44 < (folder / "output.wav").stat().st_size <= MAX_BYTES:
        raise ValueError("Output empty or too large")


async def run(job):
    async def execute():
        async with worker:
            job.status = "processing"
            await render(job)
            job.status = "succeeded"

    try:
        await asyncio.wait_for(execute(), 3600)
    except asyncio.CancelledError:
        job.status = "canceled"
        raise
    except Exception as error:
        # No raw URLs, keys, user filenames or input audio in status responses/logs.
        job.status = "failed"
        print(
            f"Cloud cover {job.id} failed at {job.stage}: {type(error).__name__}",
            flush=True,
        )
    finally:
        job.finished = time.monotonic()
        for path in tuple(job.folder.iterdir()):
            if job.status == "succeeded" and path.name == "output.wav":
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


@app.get("/")
async def home():
    return {"service": "Seed-VC cloud cover", "docs": "/docs"}


@app.get("/v1/health", dependencies=[Depends(authenticate)])
async def health():
    return {"protocol": "seedvc-cover-v1", "ready": GPU_READY}


@app.post("/v1/jobs", dependencies=[Depends(authenticate)], status_code=202)
async def submit(song: UploadFile, reference: UploadFile):
    prune()
    if not GPU_READY:
        raise HTTPException(503, "CUDA GPU or Seed-VC unavailable")
    if (
        sum(j.status in ("uploading", "queued", "processing") for j in jobs.values())
        >= 2
        or len(jobs) >= 32
    ):
        raise HTTPException(429, "Cloud capacity reached; wait before retrying")
    job_id = uuid.uuid4().hex
    job = Job(job_id, ROOT / job_id)
    jobs[job_id] = job  # Reserve capacity before the first await.
    job.folder.mkdir(parents=True)
    try:
        await save_upload(song, job.folder / "song.audio")
        await save_upload(reference, job.folder / "reference.audio")
        job.status = "queued"
        job.task = asyncio.create_task(run(job))
        return {"id": job.id, "status": job.status}
    except BaseException:
        shutil.rmtree(job.folder, ignore_errors=True)
        jobs.pop(job_id, None)
        raise
    finally:
        await song.close()
        await reference.close()


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
async def status(job_id: str):
    job = find_job(job_id)
    return {"id": job.id, "status": job.status}


@app.get("/v1/jobs/{job_id}/audio", dependencies=[Depends(authenticate)])
async def audio(job_id: str):
    job = find_job(job_id)
    if job.status != "succeeded":
        raise HTTPException(409, "Audio is not ready")
    return FileResponse(
        job.folder / "output.wav", media_type="audio/wav", filename="song.wav"
    )


@app.delete("/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
async def cancel(job_id: str):
    job = find_job(job_id)
    if job.task and not job.task.done():
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        # A queued task canceled before entering run() still needs cleanup.
        job.status = "canceled"
        job.finished = time.monotonic()
        shutil.rmtree(job.folder, ignore_errors=True)
    return {"id": job.id, "status": job.status}
