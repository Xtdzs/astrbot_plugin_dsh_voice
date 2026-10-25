"""Check real OpenVINO execution; hardware detection alone is insufficient."""

import argparse
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    import numpy as np
    import openvino as ov
    from openvino import opset13 as ops

    core = ov.Core()
    report = {
        "openvino": ov.__version__,
        "devices": core.available_devices,
        "tests": [],
    }
    rng = np.random.default_rng(0)
    x = ops.parameter([1, 16, 64], np.float32, name="features")
    weight = rng.normal(0, 0.1, (64, 32)).astype(np.float32)
    graph = ov.Model([ops.tanh(ops.matmul(x, ops.constant(weight), False, False))], [x])
    data = rng.normal(size=(1, 16, 64)).astype(np.float32)
    expected = np.tanh(data @ weight)
    for device in core.available_devices:
        started = time.perf_counter()
        try:
            compiled = core.compile_model(graph, device)
            output = compiled([data])[0]
            report["tests"].append(
                {
                    "device": device,
                    "name": core.get_property(device, "FULL_DEVICE_NAME"),
                    "success": bool(
                        np.allclose(output, expected, atol=0.02, rtol=0.02)
                    ),
                    "max_abs_error": float(np.max(np.abs(output - expected))),
                    "seconds": time.perf_counter() - started,
                }
            )
        except Exception as error:
            report["tests"].append(
                {"device": device, "success": False, "error": str(error)}
            )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if options.report:
        options.report.write_text(text, "utf-8")


if __name__ == "__main__":
    main()
