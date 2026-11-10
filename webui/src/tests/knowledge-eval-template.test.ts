import { describe, expect, it } from "vitest";

import {
  buildDatasetTemplateCsv,
  parseDatasetTemplateCsv,
} from "@/components/workspace/knowledge-eval/dataset-template";

describe("dataset template", () => {
  it("builds a CSV whose header matches the parser", () => {
    const csv = buildDatasetTemplateCsv();
    const [header] = csv.split("\r\n");
    expect(header).toBe("query,expect_any");
  });

  it("round-trips the shipped template", () => {
    const parsed = parseDatasetTemplateCsv(buildDatasetTemplateCsv());
    expect(parsed.errors).toEqual([]);
    expect(parsed.cases).toHaveLength(3);
    expect(parsed.cases[0]).toEqual({
      query: "如何回滚部署",
      expectAny: ["回滚", "kubectl"],
    });
    expect(parsed.cases[2].expectAny).toEqual([]);
  });

  it("tolerates a BOM and quoted commas", () => {
    const parsed = parseDatasetTemplateCsv(
      '\uFEFFquery,expect_any\r\n"回滚时用 kubectl, 还是脚本",回滚\r\n',
    );
    expect(parsed.errors).toEqual([]);
    expect(parsed.cases[0]).toEqual({
      query: "回滚时用 kubectl, 还是脚本",
      expectAny: ["回滚"],
    });
  });

  it("reports rows without a query instead of silently dropping them", () => {
    const parsed = parseDatasetTemplateCsv("query,expect_any\r\n,回滚\r\n有查询,备份\r\n");
    expect(parsed.cases).toEqual([{ query: "有查询", expectAny: ["备份"] }]);
    expect(parsed.errors).toEqual(["ROW_2: 缺少 query"]);
  });

  it("rejects a template without the query column", () => {
    const parsed = parseDatasetTemplateCsv("question,expect_any\r\nq,回滚\r\n");
    expect(parsed.cases).toEqual([]);
    expect(parsed.errors[0]).toContain("HEADER");
  });

  it("rejects an empty template", () => {
    expect(parseDatasetTemplateCsv("   ").errors[0]).toContain("EMPTY");
  });
});
