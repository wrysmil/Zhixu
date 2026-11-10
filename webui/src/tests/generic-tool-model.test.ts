import { describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { redactActivityText } from "@/components/thread/activity/activity-text";
import {
  canGroupGenericToolRuns,
  describeGenericToolRun,
  parseGenericToolTrace,
  type GenericToolStatus,
} from "@/components/thread/activity/generic-tool-model";

function describeRun(line: string, status: GenericToolStatus = "done") {
  const trace = parseGenericToolTrace(line);
  expect(trace).not.toBeNull();
  return describeGenericToolRun([{ trace: trace!, status }]);
}

describe("generic tool activity semantics", () => {
  it.each([
    ['find_files({"glob":"*.tsx"})', "Found files", "*.tsx"],
    ['grep({"pattern":"dream_cursor"})', "Searched files", "“dream_cursor”"],
    ['list_dir({"path":"memory"})', "Listed files", "memory"],
    ['read_file({"path":"docs/guide.md"})', "Read file", "docs/guide.md"],
    ['memory_search({"query":"launch date"})', "Searched memory", "“launch date”"],
    ['knowledge_search({"query":"回滚流程"})', "Searched knowledge base", "“回滚流程”"],
    ['knowledge_read({"handle":"chunk://abc"})', "Read source", "chunk://abc"],
    ['generate_image({"prompt":"private launch art"})', "Generated image", ""],
    ['spawn({"label":"Research competitors","task":"private task"})', "Delegated task", "Research competitors"],
    ['message({"channel":"telegram","content":"private message"})', "Sent message", "telegram"],
    ['my({"action":"check","key":"context_window_tokens"})', "Checked agent settings", "context_window_tokens"],
    ['my({"action":"set","key":"model","value":"private-model"})', "Updated agent settings", "model"],
    ['cron({"action":"add","name":"Daily digest","message":"private prompt"})', "Scheduled automation", "Daily digest"],
    ['cron({"action":"remove","name":"Daily digest"})', "Removed automation", "Daily digest"],
    ['create_goal({"objective":"private objective","ui_summary":"Benchmark memory"})', "Started long task", "Benchmark memory"],
    ['update_goal({"action":"complete","recap":"private recap"})', "Updated long task", "complete"],
    ['exec_session({"session_id":"session-1234567890-secret","until_exit":true})', "Continued command", "session…ecret"],
    ['write_stdin({"session_id":"legacy-1234567890-secret","chars":"private input"})', "Continued command", "legacy-…ecret"],
    ['list_exec_sessions({})', "Checked running commands", ""],
    ['screenshot({"path":"artifacts/home.png"})', "Captured screenshot", ""],
    ['third_party_sync({"token":"secret","payload":"private payload"})', "Completed Third party sync", ""],
  ])("describes %s without exposing implementation syntax", (line, label, detail) => {
    const presentation = describeRun(line);
    expect(presentation.label).toBe(label);
    expect(presentation.detail).toBe(detail);
    expect(`${presentation.label} ${presentation.detail}`).not.toMatch(/[{}]|private|tool-results/);
  });

  it.each([
    ["running", "Generating image"],
    ["done", "Generated image"],
    ["error", "Could not generate image"],
  ] as const)("uses human status copy for %s tools", (status, label) => {
    expect(describeRun('generate_image({"prompt":"private"})', status).label).toBe(label);
  });

  it("uses Chinese copy for activity rows when the locale is Chinese", async () => {
    await i18n.changeLanguage("zh-CN");
    try {
      const search = parseGenericToolTrace('knowledge_search({"query":"回滚流程"})')!;
      expect(describeGenericToolRun([{ trace: search, status: "done" }])).toMatchObject({
        kicker: "检索知识库",
        label: "已检索知识库",
        detail: "“回滚流程”",
      });
      const read = parseGenericToolTrace('knowledge_read({"handle":"chunk://abc"})')!;
      expect(describeGenericToolRun([{ trace: read, status: "done" }])).toMatchObject({
        kicker: "扩读知识",
        label: "已扩读原文",
      });
      const second = parseGenericToolTrace('knowledge_search({"query":"备份策略"})')!;
      expect(describeGenericToolRun([
        { trace: search, status: "done" },
        { trace: second, status: "done" },
      ])).toMatchObject({ label: "已检索知识库", aside: "2 次知识调用" });
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("keeps knowledge search and knowledge read on separate activity rows", () => {
    const search = parseGenericToolTrace('knowledge_search({"query":"回滚流程"})')!;
    const read = parseGenericToolTrace('knowledge_read({"handle":"chunk://abc"})')!;

    expect(canGroupGenericToolRuns(
      { trace: search, status: "done" },
      { trace: read, status: "done" },
    )).toBe(false);
    expect(canGroupGenericToolRuns(
      { trace: search, status: "done" },
      { trace: parseGenericToolTrace('knowledge_search({"query":"备份策略"})')!, status: "done" },
    )).toBe(true);
  });

  it("keeps spawn call ids as opaque association keys without grouping by title", () => {
    const first = parseGenericToolTrace(
      'spawn({"label":"Same label","task":"private one"})',
    )!;
    const second = parseGenericToolTrace(
      'spawn({"label":"Same label","task":"private two"})',
    )!;

    expect(canGroupGenericToolRuns(
      { trace: first, status: "running", toolCallId: "call-1" },
      { trace: second, status: "running", toolCallId: "call-2" },
    )).toBe(true);
  });

  it("groups searches over collected sources without exposing absolute paths", () => {
    const first = parseGenericToolTrace(
      'grep({"pattern":"July","path":"/Users/test/.nanobot/tool-results/session/call_first.txt"})',
    )!;
    const second = parseGenericToolTrace(
      'grep({"pattern":"OpenAI","path":"/Users/test/.nanobot/tool-results/session/call_second.txt"})',
    )!;
    const presentation = describeGenericToolRun([
      { trace: first, status: "done" },
      { trace: second, status: "done" },
    ]);

    expect(presentation).toMatchObject({ label: "Reviewed sources", detail: "", aside: "2 files" });
    expect(JSON.stringify(presentation)).not.toContain("/Users/test");
  });

  it("leaves specialized tools to their dedicated activity surfaces", () => {
    for (const line of [
      'web_search({"query":"nanobot"})',
      'web_fetch({"url":"https://example.com"})',
      'exec({"command":"date"})',
      'write_file({"path":"README.md"})',
      'edit_file({"path":"README.md"})',
      'apply_patch({"patch":"private"})',
      'run_cli_app({"name":"github"})',
      'mcp_browser_click({"text":"private"})',
    ]) {
      expect(parseGenericToolTrace(line)).toBeNull();
    }
  });

  it.each([
    ["Authorization: Bearer top-secret-token", "Authorization: <redacted>"],
    ["API_KEY=sk-proj-1234567890abcdef", "API_KEY=<redacted>"],
    ["--token xoxb-1234567890-secret", "--token <redacted>"],
    ["https://user:password@example.com/file?access_token=signed-secret", "https://<redacted>@example.com/file?access_token=<redacted>"],
    ["github ghp_1234567890abcdefghijkl", "github <redacted>"],
    ["aws AKIA1234567890ABCDEF", "aws <redacted>"],
    ["telegram 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd", "telegram <redacted>"],
  ])("redacts activity text before rendering: %s", (input, expected) => {
    expect(redactActivityText(input)).toBe(expected);
  });
});
