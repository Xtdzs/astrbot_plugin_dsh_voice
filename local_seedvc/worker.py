"""One resident model process; cancellation terminates only this owned worker."""

import asyncio
import json
import os
from pathlib import Path
import sys


class ResidentWorker:
    def __init__(self, root, device="NPU", steps=30):
        self.root = Path(root)
        self.device, self.steps = device, steps
        self.process = None
        self.lock = asyncio.Lock()

    @property
    def alive(self):
        return self.process is not None and self.process.returncode is None

    async def read_until(self, prefix):
        while True:
            line = await self.process.stdout.readline()
            if not line:
                raise RuntimeError("Resident worker exited; see last-worker.log")
            with (self.root / "last-worker.log").open("ab") as log:
                log.write(line)
            if line.startswith(prefix):
                return json.loads(line[len(prefix) :])

    async def terminate(self):
        if self.alive:
            self.process.kill()
            await self.process.wait()
        self.process = None

    async def start_unlocked(self):
        if self.alive:
            return
        settings = (
            {"creationflags": 0x08000000}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(Path(__file__).with_name("engine.py")),
            "--runtime",
            str(self.root),
            "--device",
            self.device,
            "--steps",
            str(self.steps),
            "--worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=1024 * 1024,
            **settings,
        )
        try:
            await asyncio.wait_for(self.read_until(b"VOICE_READY "), 900)
        except BaseException:
            await self.terminate()
            raise

    async def start(self):
        async with self.lock:
            await self.start_unlocked()

    async def convert(self, source, reference, output):
        async with self.lock:
            try:
                await self.start_unlocked()
                task = {
                    "source": str(source),
                    "reference": str(reference),
                    "output": str(output),
                }
                self.process.stdin.write((json.dumps(task) + "\n").encode())
                await self.process.stdin.drain()
                result = await self.read_until(b"VOICE_RESULT ")
                if not result.get("success"):
                    raise RuntimeError("Local conversion failed; see last-worker.log")
            except BaseException:
                await self.terminate()
                raise

    async def stop(self):
        async with self.lock:
            await self.terminate()
