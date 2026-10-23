"""Regenerate the WebUI favicon / PWA icon set from the brand robot asset."""
from PIL import Image

SRC = "webui/public/brand/zhixu-robot.webp"
BRAND = "webui/public/brand"
DIST = "nanobot/web/dist/brand"

robot = Image.open(SRC).convert("RGBA")
robot = robot.crop(robot.getchannel("A").getbbox())


def compose(size: int, fill: float, background=None) -> Image.Image:
    scale = (size * fill) / max(robot.size)
    subject = robot.resize(
        (round(robot.width * scale), round(robot.height * scale)), Image.LANCZOS
    )
    canvas = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    canvas.alpha_composite(
        subject, ((size - subject.width) // 2, (size - subject.height) // 2)
    )
    return canvas


targets = [
    ("zhixu_logo.png", 512, 0.88, None),
    ("zhixu_favicon_32.png", 32, 0.98, None),
    ("zhixu_icon_192.png", 192, 0.92, None),
    ("zhixu_icon_512.png", 512, 0.92, None),
    ("zhixu_apple_touch.png", 180, 0.84, (255, 255, 255, 255)),
    ("zhixu_icon_maskable.png", 512, 0.7, (255, 255, 255, 255)),
]

import os

for name, size, fill, background in targets:
    image = compose(size, fill, background)
    image.save(os.path.join(BRAND, name))
    if os.path.isdir(DIST):
        image.save(os.path.join(DIST, name))
    print(name, image.size, image.mode, "bg", background)
