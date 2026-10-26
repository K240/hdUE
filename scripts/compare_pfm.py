# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2"]
# ///
"""Compare a native UE reference and the Hydra AOV (linear HDR)."""
import argparse
import json
from pathlib import Path
import numpy as np


def read(path: Path) -> np.ndarray:
    with path.open("rb") as stream:
        if stream.readline().strip() != b"PF":
            raise ValueError("Expected RGB PFM")
        width, height = map(int, stream.readline().split())
        scale = float(stream.readline())
        return np.frombuffer(stream.read(), dtype="<f4" if scale < 0 else ">f4").reshape(height, width, 3) * abs(scale)


parser = argparse.ArgumentParser()
parser.add_argument("reference", type=Path)
parser.add_argument("hydra", type=Path)
parser.add_argument("--max-relative-rmse", type=float, default=0.01)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
reference, actual = read(args.reference), read(args.hydra)
if reference.shape != actual.shape or not np.isfinite(reference).all() or not np.isfinite(actual).all():
    raise ValueError("Image dimensions or finite-pixel check failed")
rmse = float(np.sqrt(np.mean((reference - actual) ** 2, dtype=np.float64)))
relative = rmse / max(float(np.sqrt(np.mean(reference ** 2, dtype=np.float64))), 1e-6)
report = {"rmse_rgb": rmse, "relative_rmse": relative, "maximum_relative_rmse": args.max_relative_rmse,
          "passed": relative <= args.max_relative_rmse}
text = json.dumps(report, indent=2)
print(text)
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text + "\n", encoding="utf-8")
raise SystemExit(0 if report["passed"] else 1)
