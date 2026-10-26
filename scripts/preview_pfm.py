# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2", "pillow>=11"]
# ///
"""Inspect a linear HDR render: uv run scripts/preview_pfm.py image.pfm"""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("image", type=Path)
args = parser.parse_args()
with args.image.open("rb") as stream:
    if stream.readline().strip() != b"PF":
        raise ValueError("Expected an RGB PFM image")
    width, height = map(int, stream.readline().split())
    scale = float(stream.readline())
    pixels = np.frombuffer(stream.read(), dtype="<f4" if scale < 0 else ">f4")
pixels = np.flipud(pixels.reshape(height, width, 3)) * abs(scale)
if not np.isfinite(pixels).all():
    raise ValueError("Non-finite pixels")
display = np.maximum(pixels, 0)
display = np.power(display / (1 + display), 1 / 2.2)
output = args.image.with_suffix(".png")
Image.fromarray(np.uint8(np.clip(display * 255 + 0.5, 0, 255))).save(output)
print(f"{output}: min={pixels.min():.6f}, max={pixels.max():.6f}, mean={pixels.mean():.6f}")
