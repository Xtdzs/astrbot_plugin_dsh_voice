"""Download public inference weights into this local runtime only."""

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    options = parser.parse_args()
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "90"
    from huggingface_hub import hf_hub_download, snapshot_download

    root = options.runtime.resolve() / "models"
    root.mkdir(parents=True, exist_ok=True)
    files = [
        "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml",
        "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth",
    ]
    for name in files:
        if not (root / name).is_file():
            hf_hub_download("Plachta/Seed-VC", name, local_dir=root)
    items = [
        ("funasr/campplus", ["campplus_cn_common.bin"]),
        ("lj1995/VoiceConversionWebUI", ["rmvpe.pt"]),
        (
            "openai/whisper-small",
            ["config.json", "preprocessor_config.json", "model.safetensors"],
        ),
        (
            "nvidia/bigvgan_v2_44khz_128band_512x",
            ["config.json", "bigvgan_generator.pt"],
        ),
    ]
    for repo, files in items:
        snapshot_download(
            repo_id=repo,
            allow_patterns=files,
            local_dir=root / repo.replace("/", "--"),
            max_workers=2,
        )
    print("Local weights ready. No account or paid cloud inference required.")


if __name__ == "__main__":
    main()
