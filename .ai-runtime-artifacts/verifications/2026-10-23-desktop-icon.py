"""Compose the desktop app icon from the brand robot asset.

Source art is 600x450 with transparent padding; the icon needs a tightly
fitted square so the robot reads at taskbar sizes.
"""
from PIL import Image

SRC = "webui/public/brand/zhixu-robot.webp"
OUT = "desktop/assets/icon.png"
PREVIEW = ".ai-runtime-artifacts/verifications/2026-10-23-desktop-icon-preview.png"
SIZE = 1024
FILL = 0.92

robot = Image.open(SRC).convert("RGBA")
robot = robot.crop(robot.getchannel("A").getbbox())

scale = (SIZE * FILL) / max(robot.size)
resized = robot.resize(
    (round(robot.width * scale), round(robot.height * scale)), Image.LANCZOS
)

icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
icon.alpha_composite(
    resized,
    ((SIZE - resized.width) // 2, (SIZE - resized.height) // 2),
)
icon.save(OUT)

# Contact sheet: how the icon reads at real taskbar/titlebar sizes.
sizes = [16, 24, 32, 48]
sheet = Image.new("RGBA", (sum(sizes) + 40, 64), (32, 32, 32, 255))
x = 8
for size in sizes:
    small = icon.resize((size, size), Image.LANCZOS)
    sheet.alpha_composite(small, (x, (64 - size) // 2))
    x += size + 8
sheet.save(PREVIEW)

print(OUT, icon.size, "subject", resized.size)
print(PREVIEW)
