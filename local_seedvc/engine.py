"""Local Seed-VC runner; OpenVINO accelerates DiT, PyTorch handles audio helpers.

Run in the separate local environment, never import this from AstrBot.
The upstream GPL-3.0 source and weights are downloaded separately by setup.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace


def create_runtime(runtime, device="NPU", steps=30):
    root = Path(runtime).resolve()
    args = SimpleNamespace(device=device, steps=steps)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.chdir(root / "seed-vc")
    sys.path.insert(0, str(root / "seed-vc"))

    import numpy as np
    import openvino as ov
    import torch
    import yaml

    # PyTorch 2.6 was verified stable with eight threads on the tested Panther Lake.
    torch.set_num_threads(max(1, min(int(os.getenv("SEEDVC_CPU_THREADS", "8")), 16)))
    torch.set_num_interop_threads(1)
    torch.backends.cuda.enable_flash_sdp(False)
    torch.backends.cuda.enable_mem_efficient_sdp(False)
    torch.manual_seed(42)
    import inference
    import modules.diffusion_transformer as dit_module

    model_root = root / "models"
    config_path = model_root / "local-config.yml"
    config = yaml.safe_load(
        (model_root / "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml").read_text(
            "utf-8"
        )
    )
    config["model_params"]["speech_tokenizer"]["name"] = str(
        model_root / "openai--whisper-small"
    )
    config["model_params"]["vocoder"]["name"] = str(
        model_root / "nvidia--bigvgan_v2_44khz_128band_512x"
    )
    config_path.write_text(yaml.safe_dump(config), "utf-8")
    paths = {
        "funasr/campplus": model_root / "funasr--campplus" / "campplus_cn_common.bin",
        "lj1995/VoiceConversionWebUI": model_root
        / "lj1995--VoiceConversionWebUI"
        / "rmvpe.pt",
    }

    def local_download(repo_id, model_filename, config_filename=None):
        return str(paths[repo_id])

    inference.load_custom_model_from_hf = local_download
    report = {
        "device": args.device,
        "steps": args.steps,
        "dit_calls": 0,
        "dit_seconds": 0.0,
    }
    core = ov.Core()
    if args.device not in core.available_devices:
        raise RuntimeError(f"OpenVINO device unavailable: {args.device}")
    original_loader = inference.load_models
    cached_models = []

    class Estimator(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.model = model
            self.compiled = {}
            model.style_as_token = int(model.style_as_token)
            model.time_as_token = int(model.time_as_token)

        def forward(self, x, prompt, lengths, t, style, cond):
            actual = x.shape[-1]
            frames = max(128, 2 ** math.ceil(math.log2(actual)))
            if frames > 1024:
                raise RuntimeError(
                    "Local runner expects <=10 seconds of combined reference and source context"
                )
            batch = x.shape[0]
            padded = (
                torch.nn.functional.pad(x, (0, frames - actual)),
                torch.nn.functional.pad(prompt, (0, frames - actual)),
                lengths.to(torch.int64),
                t,
                style,
                torch.nn.functional.pad(cond, (0, 0, 0, frames - actual)),
            )
            key = (batch, frames)
            if key not in self.compiled:
                started = time.perf_counter()
                ir = model_root / f"dit-local-b{batch}-f{frames}.xml"
                if not ir.exists():
                    original_mask = dit_module.sequence_mask

                    def mask(lens, max_length=None):
                        return (
                            torch.arange(frames, device=lens.device)[None]
                            < lens[:, None]
                        )

                    class Fixed(torch.nn.Module):
                        def __init__(self, model):
                            super().__init__()
                            self.model = model

                        def forward(self, x, prompt, lengths, t, style, cond):
                            return self.model(x, prompt, lengths, t, style, cond)

                    dit_module.sequence_mask = mask
                    try:
                        graph = ov.convert_model(
                            Fixed(self.model).eval(), example_input=padded
                        )
                        graph.reshape(
                            {i: list(value.shape) for i, value in enumerate(padded)}
                        )
                        ov.save_model(graph, ir, compress_to_fp16=False)
                    finally:
                        dit_module.sequence_mask = original_mask
                compiled = core.compile_model(
                    ir, args.device, {"CACHE_DIR": str(root / "ov-cache")}
                )
                # Verify the actual audio features against stable PyTorch before use.
                expected = (
                    self.model(x, prompt, lengths, t, style, cond).detach().numpy()
                )
                value = compiled([v.detach().numpy() for v in padded])[0][..., :actual]
                relative = float(
                    np.linalg.norm(value - expected) / (np.linalg.norm(expected) + 1e-9)
                )
                if not np.isfinite(value).all() or relative > 0.03:
                    raise RuntimeError(
                        f"OpenVINO DiT accuracy check failed: {relative}"
                    )
                self.compiled[key] = compiled
                report.setdefault("compilation", []).append(
                    {
                        "frames": frames,
                        "seconds": time.perf_counter() - started,
                        "relative_l2_error": relative,
                    }
                )
                print(
                    json.dumps({"stage": "dit_ready", **report["compilation"][-1]}),
                    flush=True,
                )
            started = time.perf_counter()
            value = self.compiled[key]([v.detach().numpy() for v in padded])[0][
                ..., :actual
            ].copy()
            if not np.isfinite(value).all():
                raise RuntimeError("Nonfinite NPU/GPU output")
            report["dit_seconds"] += time.perf_counter() - started
            report["dit_calls"] += 1
            return torch.from_numpy(value)

    def loader(options):
        if cached_models:
            report["load_seconds"] = 0.0
            return cached_models[0]
        started = time.perf_counter()
        loaded = original_loader(options)
        loaded[0].cfm.estimator = Estimator(loaded[0].cfm.estimator)
        cached_models.append(loaded)
        report["load_seconds"] = time.perf_counter() - started
        print(
            json.dumps({"stage": "models_loaded", "seconds": report["load_seconds"]}),
            flush=True,
        )
        return loaded

    inference.load_models = loader
    options = SimpleNamespace(
        source="",
        target="",
        output="",
        diffusion_steps=args.steps,
        length_adjust=1.0,
        inference_cfg_rate=0.7,
        f0_condition=True,
        auto_f0_adjust=False,
        semi_tone_shift=0,
        fp16=False,
        checkpoint=str(
            model_root
            / "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"
        ),
        config=str(config_path),
    )
    loader(options)

    def convert(source, reference, output):
        source, reference, output = (
            Path(source).resolve(),
            Path(reference).resolve(),
            Path(output).resolve(),
        )
        options.source, options.target, options.output = (
            str(source),
            str(reference),
            str(output),
        )
        report.clear()
        report.update(
            device=args.device, steps=args.steps, dit_calls=0, dit_seconds=0.0
        )
        torch.manual_seed(42)
        started = time.perf_counter()
        inference.main(options)
        report["total_seconds"] = time.perf_counter() - started
        output.mkdir(parents=True, exist_ok=True)
        (output / "benchmark.json").write_text(json.dumps(report, indent=2), "utf-8")
        print(json.dumps(report), flush=True)
        return dict(report)

    return convert


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--device", choices=["NPU", "GPU", "CPU"], default="NPU")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    args.runtime = args.runtime.resolve()
    for name in ("source", "reference", "output"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    convert = create_runtime(args.runtime, args.device, args.steps)
    if not args.worker:
        if not all((args.source, args.reference, args.output)):
            parser.error("source/reference/output are required outside worker mode")
        convert(args.source, args.reference, args.output)
        return

    # Warm up real public example features before advertising readiness.
    import soundfile as sf

    root = args.runtime.resolve()
    warm = root / "warmup"
    warm.mkdir(exist_ok=True)
    demo = next((root / "seed-vc/examples/source").glob("TECHNOPOLIS*.wav"))
    info = sf.info(demo)
    data, sr = sf.read(demo, start=info.samplerate * 2, frames=info.samplerate * 3)
    sf.write(warm / "source.wav", data, sr)
    sample = root / "seed-vc/examples/reference/s1p1.wav"
    info = sf.info(sample)
    data, sr = sf.read(sample, frames=info.samplerate * 5)
    sf.write(warm / "reference.wav", data, sr)
    convert(warm / "source.wav", warm / "reference.wav", warm / "output")
    print("VOICE_READY " + json.dumps({"device": args.device}), flush=True)
    for line in sys.stdin:
        try:
            task = json.loads(line)
            convert(task["source"], task["reference"], task["output"])
            result = {"success": True}
        except Exception as error:
            import traceback

            traceback.print_exc()
            result = {"success": False, "error": type(error).__name__}
        print("VOICE_RESULT " + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
