const sharp = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp");
const path = require("node:path");

const file = process.argv[2];
(async () => {
  const { data, info } = await sharp(file).raw().toBuffer({ resolveWithObject: true });
  const at = (x, y) => {
    const i = (y * info.width + x) * info.channels;
    return `${data[i]},${data[i + 1]},${data[i + 2]}`;
  };
  const column = (x, from, to) => {
    const out = [];
    let previous = null;
    for (let y = from; y <= to; y += 1) {
      const value = at(x, y);
      if (value !== previous) {
        out.push({ y, rgb: value });
        previous = value;
      }
    }
    return out;
  };
  console.log(JSON.stringify({
    size: { w: info.width, h: info.height },
    topRight: column(1100, 40, 72),
    bottomRight: column(1100, 860, 895),
    leftEdgeRow: at(280, 400) + " | " + at(288, 400) + " | " + at(292, 400),
  }, null, 2));
})();
