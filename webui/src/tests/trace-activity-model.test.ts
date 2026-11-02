import { describe, expect, it } from "vitest";

import { describeTraceLine } from "@/components/thread/activity/trace-activity-model";
import type { GenericToolStatus } from "@/components/thread/activity/generic-tool-model";
import { setAppLanguage } from "@/i18n";

function describeTrace(line: string, status: GenericToolStatus = "done") {
  return describeTraceLine(line, status);
}

describe("trace activity semantics", () => {
  it("uses readable copy when a hosted search query is unavailable", () => {
    expect(describeTrace("web_search()").label).toBe("Searched the web");
  });

  it.each([
    ['web_search({"query":"nanobot latest release"})', "done", "Searched nanobot latest release", ""],
    ['web_fetch({"url":"https://example.com/docs?token=private"})', "done", "Read", "example.com/docs"],
    ['read_file({"path":"/Users/alice/project/README.md"})', "done", "Read", "~/project/README.md"],
    ['exec({"command":"date +%Y-%m-%d"})', "done", "Checked current time", ""],
    ['exec_command({"cmd":"API_KEY=secret npm test"})', "running", "Running tests", ""],
    ['write_file({"path":"/home/alice/project/output.txt"})', "done", "Wrote file", "~/project/output.txt"],
    ['apply_patch({"file_path":"src/app.tsx","patch":"private"})', "error", "Could not edit file", "src/app.tsx"],
    ['third_party_sync({"token":"secret","payload":"private"})', "done", "Completed Third party sync", ""],
    ["Finished collecting results", "done", "Completed step", "Finished collecting results"],
  ] as const)("describes %s as one safe activity line", (line, status, label, detail) => {
    const result = describeTrace(line, status);
    expect(result).toMatchObject({ label, detail });
    expect(`${result.label} ${result.detail}`).not.toMatch(/[{}]|private|\/Users\/alice|\/home\/alice/);
  });

  it.each([
    ["running", "Searching status test"],
    ["done", "Searched status test"],
    ["error", "Could not search status test"],
  ] as const)("uses status-aware search copy for %s", (status, label) => {
    expect(describeTrace('web_search({"query":"status test"})', status).label).toBe(label);
  });

  it.each([
    ["running", "Searching X · status test"],
    ["done", "Searched X · status test"],
    ["error", "Could not search X · status test"],
  ] as const)("identifies hosted X search activity for %s", (status, label) => {
    expect(describeTrace('x_search({"query":"status test"})', status).label).toBe(label);
  });

  it("never exposes URL credentials, query secrets, or private-network links", () => {
    const publicResult = describeTrace(
      'web_fetch({"url":"https://user:password@example.com/docs?api_key=secret#section"})',
    );
    expect(publicResult).toMatchObject({ detail: "example.com/docs", host: "example.com" });
    expect(JSON.stringify(publicResult)).not.toMatch(/password|api_key|secret/);

    const privateResult = describeTrace('web_fetch({"url":"http://127.0.0.1:8765/private"})');
    expect(privateResult.url).toBeUndefined();
    expect(privateResult.detail).not.toContain("127.0.0.1");
  });

  it("summarizes multi-line commands without exposing every script line", () => {
    const result = describeTrace(
      'exec({"command":"npm test\\necho second-secret-line\\necho third-line"})',
    );
    expect(result).toMatchObject({
      label: "Ran tests",
      detail: "",
    });
    expect(result.detail).not.toContain("second-secret-line");
  });

  it.each(["running", "done"] as const)("uses an explicit purpose for a %s command", (status) => {
    expect(describeTrace(
      'exec({"command":"cd /Users/alice/project && npm test","description":"验证重复工单修复"})',
      status,
    )).toMatchObject({ label: "验证重复工单修复", detail: "" });
  });

  it("keeps failures explicit when the model supplies a purpose", () => {
    expect(describeTrace(
      'exec({"command":"npm test","ui_summary":"Verify the ticket fix"})',
      "error",
    ).label).toBe("Failed: Verify the ticket fix");
  });

  it.each([
    ["cd /project && npm run build", "Built the app"],
    ["uv run --no-sync python -m pytest tests/core -q", "Ran tests"],
    ["node node_modules/vitest/vitest.mjs run", "Ran tests"],
    ["ruff check nanobot/", "Checked code style"],
    ["pnpm run typecheck", "Checked types"],
    ["rg -n ticket src", "Searched file contents"],
    ["git status --short", "Checked workspace changes"],
    ["ls -la src", "Listed files"],
  ])("describes the observable purpose of %s", (command, label) => {
    const trace = `exec(${JSON.stringify({ command })})`;
    expect(describeTrace(trace)).toMatchObject({ label, detail: "" });
  });

  it("does not infer an action from quoted text or later script lines", () => {
    expect(describeTrace('exec({"command":"echo \'npm test\'"})').label).toBe("Ran command");
    expect(describeTrace('exec({"command":"echo setup\\nnpm test"})').label).toBe("Ran command");
    expect(describeTrace('exec({"command":"cat << \'EOF\' | bash\\nnpm test\\nEOF"})').label)
      .toBe("Ran command");
  });

  it("redacts explicit descriptions before displaying them", () => {
    const result = describeTrace('exec({"command":"true","description":"Check TOKEN=private-token /Users/alice/project"})');
    expect(result.label).not.toMatch(/private-token|\/Users\/alice/);
    expect(result.label).toContain("Check");
  });

  it("localizes observable shell actions for Chinese without changing English", async () => {
    await setAppLanguage("zh-CN");
    expect(describeTrace('exec({"command":"npm test"})', "running").label).toBe("正在运行测试");
    expect(describeTrace('exec({"command":"npm run build"})').label).toBe("已构建应用");
    expect(describeTrace('exec({"command":"custom-task"})').label).toBe("已执行命令");
    await setAppLanguage("en");
    expect(describeTrace('exec({"command":"npm test"})').label).toBe("Ran tests");
  });
});
