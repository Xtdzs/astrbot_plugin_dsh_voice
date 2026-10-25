"""CPU stem separation in an isolated child, before the NPU process loads."""

import argparse
import os
from pathlib import Path
import ssl
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    options = parser.parse_args()
    import torch
    import certifi

    # Use the verified CA bundle: this Conda build cannot parse one Windows store entry.
    urllib.request.install_opener(
        urllib.request.build_opener(
            urllib.request.HTTPSHandler(
                context=ssl.create_default_context(cafile=certifi.where())
            )
        )
    )
    if os.getenv("SEEDVC_ROOT"):
        torch.hub.set_dir(str(Path(os.environ["SEEDVC_ROOT"]).parent / "torch-cache"))

    torch.set_num_threads(1)
    torch.backends.cuda.enable_flash_sdp(False)
    torch.backends.cuda.enable_mem_efficient_sdp(False)
    from demucs.separate import main as separate

    separate(
        [
            "-n",
            "htdemucs",
            "--two-stems",
            "vocals",
            "-d",
            "cpu",
            "--segment",
            "7",
            "-o",
            options.output,
            options.source,
        ]
    )


if __name__ == "__main__":
    main()
