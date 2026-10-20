from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "public" / "assets" / "agents"
MODES = ("listening", "working", "result", "bug")
MOTION_POSES = (
    "idle",
    "listen-a",
    "listen-b",
    "work-a",
    "work-b",
    "wave-a",
    "wave-b",
    "scratch",
    "think",
)
POSES = MODES + MOTION_POSES
AGENTS = {
    "observability": "#1EA896",
    "ghost": "#8178D8",
    "blast": "#F2A65A",
    "resolution": "#FF715B",
}


def hex_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))


def recolor_body(image: Image.Image, target: str) -> Image.Image:
    target_h, target_s, _ = colorsys.rgb_to_hsv(*hex_rgb(target))
    result = image.convert("RGBA").copy()
    pixels = result.load()

    for y in range(result.height):
        for x in range(result.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0:
                continue
            hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
            # Select only the verdigris flocking. Eyes, highlights, coral stitches,
            # and transparent green-screen remnants remain untouched.
            if 0.43 <= hue <= 0.58 and saturation >= 0.28 and value <= 0.96:
                new_saturation = min(1.0, max(0.35, target_s * (0.78 + saturation * 0.22)))
                new_red, new_green, new_blue = colorsys.hsv_to_rgb(target_h, new_saturation, value)
                pixels[x, y] = (
                    round(new_red * 255),
                    round(new_green * 255),
                    round(new_blue * 255),
                    alpha,
                )
    return result


def square_and_center(image: Image.Image) -> Image.Image:
    bounds = image.getbbox()
    if bounds is None:
        raise ValueError("Agent asset has no visible pixels")
    cropped = image.crop(bounds)
    cropped.thumbnail((452, 452), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    offset = ((512 - cropped.width) // 2, (512 - cropped.height) // 2)
    canvas.alpha_composite(cropped, offset)
    return canvas


def main() -> None:
    originals = {
        mode: Image.open(ASSET_DIR / f"agent-{mode}.png").convert("RGBA")
        for mode in POSES
    }

    for agent, color in AGENTS.items():
        destination = ASSET_DIR / agent
        destination.mkdir(parents=True, exist_ok=True)
        for mode, original in originals.items():
            processed = square_and_center(recolor_body(original, color))
            processed.save(destination / f"agent-{mode}.png", optimize=True)

    for mode, original in originals.items():
        square_and_center(original).save(ASSET_DIR / f"agent-{mode}.png", optimize=True)


if __name__ == "__main__":
    main()
