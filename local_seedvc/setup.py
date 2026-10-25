"""Prepare an isolated runtime without changing AstrBot's Python packages."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess

REVISION = "51383efd921027683c89e5348211d93ff12ac2a8"


def patch_source(root):
    path = root / "seed-vc" / "inference.py"
    source = path.read_text("utf-8")
    replacements = {
        "max_context_window = sr // hop_length * 30": "max_context_window = sr // hop_length * 10",
        "torch_dtype=torch.float16).to(device)": 'torch_dtype=torch.float16 if device.type == "cuda" else torch.float32).to(device)',
        "max_batch_size=1, max_seq_length=8192": "max_batch_size=2, max_seq_length=1024",
    }
    for old, new in replacements.items():
        if old not in source and new not in source:
            raise RuntimeError("Unexpected upstream source; cannot apply local patch")
        source = source.replace(old, new)
    path.write_text(source, "utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=Path(".runtime/local-seedvc"))
    parser.add_argument("--git-proxy", default="")
    options = parser.parse_args()
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install uv first: python -m pip install --user uv")
    root = options.runtime.resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, UV_LINK_MODE="copy")
    python = (
        root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )

    def run(*args):
        subprocess.run(list(map(str, args)), check=True, env=env)

    if not python.is_file():
        run(uv, "venv", root / ".venv", "--python", "3.10")
    run(
        uv,
        "pip",
        "install",
        "--python",
        python,
        "torch==2.4.0",
        "torchaudio==2.4.0",
        "torchvision==0.19.0",
        "--index-url",
        "https://download.pytorch.org/whl/cpu",
    )
    run(
        uv,
        "pip",
        "install",
        "--python",
        python,
        "-r",
        Path(__file__).with_name("requirements.txt"),
    )
    repo = root / "seed-vc"
    if not repo.exists():
        git = ["git"]
        if options.git_proxy:
            git += ["-c", "http.proxy=" + options.git_proxy]
        run(
            *git,
            "clone",
            "--depth",
            "1",
            "https://github.com/Plachtaa/seed-vc.git",
            repo,
        )
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != REVISION:
        raise SystemExit(
            "Seed-VC checkout does not match the reviewed revision; preserve it and use a new runtime directory"
        )
    patch_source(root)
    run(python, Path(__file__).with_name("download.py"), "--runtime", root)
    print("Ready. Start the local service with:")
    print(
        f'"{python}" "{Path(__file__).with_name("server.py")}" --runtime "{root}" --device NPU --port 17860'
    )


if __name__ == "__main__":
    main()
