"""Replace project brand assets with the new tanuki/cat logo.

Pipeline:
1. Detect the connected background by flood-filling from the four borders
   (peach color clusters around the cat).
2. Apply a tight soft-edge band so anti-aliased rims fade cleanly into any
   surface the logo is placed on.
3. Trim, pad to square, generate icon variants for webui + bundled webui dist
   + the desktop app icon.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

SOURCE = Path("/Users/mima0000/Documents/学习-001/do-project/zhixu/.logo-cache/zhixu-source.png")
WEBUI_BRAND = Path("/Users/mima0000/Documents/学习-001/do-project/zhixu/webui/public/brand")
DIST_BRAND = Path("/Users/mima0000/Documents/学习-001/do-project/zhixu/nanobot/web/dist/brand")
DESKTOP_ICON = Path("/Users/mima0000/Documents/学习-001/do-project/zhixu/desktop/assets/icon.png")

TARGET_DIRS = [WEBUI_BRAND, DIST_BRAND]

# Reference background color from the corners of the source image.
BG_COLOR = np.array([255, 239, 220], dtype=np.float32)
# Pixel colors within this Euclidean distance are treated as background.
BG_TOLERANCE = 15.0
# Pixels in this band fade from transparent to opaque over 12 distance units.
SOFT_BAND = 12.0


def remove_background(img: Image.Image) -> Image.Image:
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    dist = np.linalg.norm(rgb - BG_COLOR, axis=-1)

    bg_mask = dist < BG_TOLERANCE

    h, w = bg_mask.shape
    visited = np.zeros_like(bg_mask, dtype=bool)
    stack: list[tuple[int, int]] = []
    for x in range(w):
        for y in (0, h - 1):
            if bg_mask[y, x] and not visited[y, x]:
                visited[y, x] = True
                stack.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg_mask[y, x] and not visited[y, x]:
                visited[y, x] = True
                stack.append((y, x))
    while stack:
        y, x = stack.pop()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and bg_mask[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = True
                stack.append((ny, nx))

    alpha = np.full((h, w), 255, dtype=np.uint8)
    alpha[visited] = 0

    # Narrow soft band right at the cat's silhouette: pixels within
    # BG_TOLERANCE..BG_TOLERANCE+SOFT_BAND that are NOT already inside the
    # background region ramp linearly. The lightest "tail puff" particles
    # deliberately land outside this band so they cleanly disappear instead
    # of becoming ghostly gray blobs.
    inside_fg = ~visited
    band = inside_fg & (dist < BG_TOLERANCE + SOFT_BAND)
    ramp = np.clip((dist[band] - BG_TOLERANCE) / SOFT_BAND, 0.0, 1.0)
    alpha[band] = (ramp * 255).astype(np.uint8)

    rgba = np.dstack([rgb.astype(np.uint8), alpha])
    return Image.fromarray(rgba, mode="RGBA")


def trim_to_square(img: Image.Image, pad_ratio: float = 0.06) -> Image.Image:
    arr = np.asarray(img)
    alpha = arr[..., 3]
    ys, xs = np.where(alpha > 16)
    if ys.size == 0 or xs.size == 0:
        raise RuntimeError("transparent trim found no opaque pixels")
    top, bottom = int(ys.min()), int(ys.max())
    left, right = int(xs.min()), int(xs.max())
    content = img.crop((left, top, right + 1, bottom + 1))

    cw, ch = content.size
    side = max(cw, ch)
    pad = int(side * pad_ratio)
    canvas = Image.new("RGBA", (side + pad * 2, side + pad * 2), (0, 0, 0, 0))
    ox = (canvas.size[0] - cw) // 2
    oy = (canvas.size[1] - ch) // 2
    canvas.paste(content, (ox, oy), content)
    return canvas


def render_maskable(img: Image.Image, size: int) -> Image.Image:
    inner = int(round(size * 0.66))
    artwork = img.resize((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    off = (size - inner) // 2
    canvas.paste(artwork, (off, off), artwork)
    return canvas


def write(img: Image.Image, name: str, size: int | None = None, maskable: bool = False) -> None:
    out = img if size is None else (
        render_maskable(img, size) if maskable else img.resize((size, size), Image.LANCZOS)
    )
    for d in TARGET_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        out.save(d / name, format="PNG", optimize=True)
    print(f"  wrote {name} ({out.size[0]}x{out.size[1]})")


def main() -> None:
    raw = Image.open(SOURCE)
    print(f"source: {raw.size} {raw.mode}")
    cutout = remove_background(raw)
    print("background removed (flood fill + soft edge)")
    master = trim_to_square(cutout, pad_ratio=0.06)
    print(f"master trimmed: {master.size}")

    master_1024 = master.resize((1024, 1024), Image.LANCZOS)
    mark_512 = master.resize((512, 512), Image.LANCZOS)
    main_png = master.resize((1254, 1254), Image.LANCZOS)

    for d in TARGET_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        main_png.save(d / "zhixu-logo.png", format="PNG", optimize=True)
    print(f"  wrote zhixu-logo.png (1254x1254)")

    for d in TARGET_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        mark_512.save(d / "mira_mark.png", format="PNG", optimize=True)
    print("  wrote mira_mark.png (512x512)")

    for d in TARGET_DIRS:
        svg = d / "mira_mark.svg"
        if svg.exists():
            svg.unlink()
            print(f"  removed legacy {svg}")

    write(master_1024, "mira_favicon_32.png", 32)
    write(master_1024, "mira_apple_touch.png", 180)
    write(master_1024, "mira_icon_192.png", 192)
    write(master_1024, "mira_icon_512.png", 512)
    write(master_1024, "mira_icon_maskable.png", 512, maskable=True)

    DESKTOP_ICON.parent.mkdir(parents=True, exist_ok=True)
    master.resize((1024, 1024), Image.LANCZOS).save(DESKTOP_ICON, format="PNG", optimize=True)
    print(f"  wrote {DESKTOP_ICON}")


if __name__ == "__main__":
    main()
