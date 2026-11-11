const fs = require("fs");
const file = "src/tests/knowledge-graph-view.test.tsx";
let text = fs.readFileSync(file, "utf8");
const before = text;
text = text.replace(/name:\s*\/展开关联\//g, 'name: i18n.t("knowledge.experience.graphExpand")');
fs.writeFileSync(file, text, "utf8");
const left = fs.readFileSync(file, "utf8").split(/\r?\n/).filter((l) => /getBy|findBy|queryBy/.test(l) && /[\u4e00-\u9fa5]/.test(l));
console.log("changed:", before !== text, "| remaining zh lookups:", left.length);
