"""Logon launcher: keep only this user's local voice service running."""

import argparse
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=17860)
    parser.add_argument("--device", choices=["NPU", "GPU", "CPU"], default="NPU")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--stop", action="store_true")
    options = parser.parse_args()
    root = options.runtime.resolve()
    root.mkdir(parents=True, exist_ok=True)
    stop_file = root / "stop-request"
    if options.stop:
        stop_file.touch()
        return
    lock = (root / "watchdog.lock").open("a+b")
    if os.name == "nt":
        import msvcrt

        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
    import httpx

    python = (
        root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    script = Path(__file__).with_name("server.py").resolve()
    child = None
    failures = 0
    log = (root / "watchdog.log").open("a", encoding="utf-8", buffering=1)

    def note(message):
        log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")

    def stop_owned_child():
        if child is not None and child.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x08000000,
                )
            else:
                child.terminate()
            child.wait(timeout=30)

    try:
        note("Watchdog started")
        with httpx.Client(timeout=5, trust_env=False) as client:
            while not stop_file.exists():
                healthy = False
                key_file = root / "api-key.txt"
                if key_file.is_file():
                    try:
                        response = client.get(
                            f"http://127.0.0.1:{options.port}/v1/health",
                            headers={
                                "Authorization": "Bearer "
                                + key_file.read_text("utf-8").strip()
                            },
                        )
                        healthy = (
                            response.is_success
                            and response.json().get("protocol") == "seedvc-cover-v1"
                        )
                    except (httpx.HTTPError, ValueError):
                        pass
                if healthy:
                    failures = 0
                else:
                    failures += 1
                    if child is None or child.poll() is not None:
                        note("Starting local service and warming resident models")
                        child = subprocess.Popen(
                            [
                                str(python),
                                "-u",
                                str(script),
                                "--runtime",
                                str(root),
                                "--device",
                                options.device,
                                "--steps",
                                str(options.steps),
                                "--port",
                                str(options.port),
                            ],
                            stdout=log,
                            stderr=log,
                            cwd=script.parents[1],
                            creationflags=0x08000000 if os.name == "nt" else 0,
                        )
                        failures = 0
                    elif failures >= 36:
                        note(
                            "Owned service is unresponsive; restarting its process tree"
                        )
                        stop_owned_child()
                        failures = 0
                time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_owned_child()
        stop_file.unlink(missing_ok=True)
        note("Watchdog stopped")
        log.close()
        lock.close()


if __name__ == "__main__":
    main()
