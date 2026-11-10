/**
 * 数据集模板：CSV 生成与解析。
 *
 * 模板只暴露 UI 表单里真正支持的字段（查询 + 期望关键词），避免「下载了却填不进去」。
 * 期望关键词在单元格内用 `|` 分隔（逗号留给 CSV 本身，避免多一层转义负担）。
 */

export interface DatasetCaseDraft {
  query: string;
  expectAny: string[];
}

export interface TemplateParseResult {
  cases: DatasetCaseDraft[];
  errors: string[];
}

const TEMPLATE_ROWS: string[][] = [
  ["query", "expect_any"],
  ["如何回滚部署", "回滚|kubectl"],
  ["数据库多久备份一次", "备份"],
  ["向量检索支持哪些语言模型", ""],
];

function csvCell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** 模板 CSV 文本（不含 BOM；下载时再加，保证 Excel 打开中文不乱码）。 */
export function buildDatasetTemplateCsv(): string {
  return TEMPLATE_ROWS.map((row) => row.map(csvCell).join(",")).join("\r\n");
}

/** 下载模板文件。 */
export function downloadDatasetTemplate(filename: string): void {
  const blob = new Blob(["\uFEFF", buildDatasetTemplateCsv()], {
    type: "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** 解析一行 CSV（支持双引号包裹与转义引号）。 */
function splitCsvLine(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (quoted) {
      if (char === '"') {
        if (line[index + 1] === '"') {
          current += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        current += char;
      }
      continue;
    }
    if (char === '"') {
      quoted = true;
      continue;
    }
    if (char === ",") {
      cells.push(current);
      current = "";
      continue;
    }
    current += char;
  }
  cells.push(current);
  return cells.map((cell) => cell.trim());
}

/**
 * 解析模板 CSV。
 *
 * 首行必须是表头且包含 `query`；`expect_any` 可省略。缺 query 的行记为错误，
 * 不影响其它行——用户能一次看到所有需要修的行。
 */
export function parseDatasetTemplateCsv(text: string): TemplateParseResult {
  const errors: string[] = [];
  const cases: DatasetCaseDraft[] = [];
  const lines = text
    .replace(/^\uFEFF/, "")
    .split(/\r\n|\n|\r/)
    .filter((line) => line.trim().length > 0);
  if (lines.length === 0) {
    return { cases, errors: ["EMPTY: 模板是空的"] };
  }

  const header = splitCsvLine(lines[0]).map((cell) => cell.toLowerCase());
  const queryIndex = header.indexOf("query");
  const expectIndex = header.indexOf("expect_any");
  if (queryIndex < 0) {
    return { cases, errors: ["HEADER: 表头必须包含 query 列"] };
  }

  for (let lineIndex = 1; lineIndex < lines.length; lineIndex += 1) {
    const cells = splitCsvLine(lines[lineIndex]);
    const query = (cells[queryIndex] ?? "").trim();
    if (!query) {
      errors.push(`ROW_${lineIndex + 1}: 缺少 query`);
      continue;
    }
    const rawExpect = expectIndex >= 0 ? cells[expectIndex] ?? "" : "";
    const expectAny = rawExpect
      .split("|")
      .map((item) => item.trim())
      .filter(Boolean);
    cases.push({ query, expectAny });
  }
  return { cases, errors };
}
