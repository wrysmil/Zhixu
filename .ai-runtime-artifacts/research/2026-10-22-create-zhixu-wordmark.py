"""Export the two Chinese brand glyphs as portable SVG outlines."""
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen

font_path = Path(r"C:\Windows\Fonts\Noto Sans SC Bold (TrueType).otf")
font = TTFont(font_path)
glyphs = font.getGlyphSet()
cmap = font.getBestCmap()
paths = []
bounds = []
cursor = 0
for char in "知序":
    glyph = glyphs[cmap[ord(char)]]
    path_pen = SVGPathPen(glyphs)
    glyph.draw(TransformPen(path_pen, (1, 0, 0, -1, cursor, 0)))
    paths.append(path_pen.getCommands())
    bound_pen = BoundsPen(glyphs)
    glyph.draw(TransformPen(bound_pen, (1, 0, 0, -1, cursor, 0)))
    bounds.append(bound_pen.bounds)
    cursor += glyph.width + 12

left = min(b[0] for b in bounds) - 24
top = min(b[1] for b in bounds) - 24
right = max(b[2] for b in bounds) + 24
bottom = max(b[3] for b in bounds) + 24
width, height = right - left, bottom - top
svg = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    f'viewBox="{left:g} {top:g} {width:g} {height:g}" '
    f'width="{width:g}" height="{height:g}">\n'
    '  <title>知序</title>\n'
    '  <desc>Noto Sans SC Bold wordmark, converted to outlines.</desc>\n'
    + '\n'.join(f'  <path fill="#303030" d="{p}"/>' for p in paths)
    + '\n</svg>\n'
)
destination = Path(__file__).resolve().parents[2] / "webui/public/brand/zhixu-wordmark.svg"
destination.write_text(svg, encoding="utf-8")
print(f"{destination}: {width:g} x {height:g}, font=Noto Sans SC Bold")
