const fs = require("fs");
const file = "src/tests/knowledge-page.test.tsx";
let text = fs.readFileSync(file, "utf8");
const before = text;
text = text.replace(
  'const first = within(list).getByText("Product manual").closest("li")!;',
  'const first = (await within(list).findByText("Product manual")).closest("li")!;',
);
text = text.replace(
  'const second = within(list).getByText("Team notes").closest("li")!;',
  'const second = (await within(list).findByText("Team notes")).closest("li")!;',
);
fs.writeFileSync(file, text, "utf8");
console.log("changed:", before !== text);
