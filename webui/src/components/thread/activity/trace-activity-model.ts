import { currentLocale } from "@/i18n";

import type { GenericToolStatus } from "./generic-tool-model";
import { safeActivityDetail, summarizeShellCommand } from "./activity-text";
import { presentWebSearchAction } from "./web-search-model";
import { displayWebHost, formatCompactWebUrl, parseSafeActivityHttpUrl } from "./web-url";

export interface TraceDescription {
  kind: "search" | "tool" | "done" | "trace";
  label: string;
  detail: string;
  icon?: "clock";
  url?: string;
  host?: string;
}

export function describeTraceLine(
  line: string,
  status: GenericToolStatus,
  result?: unknown,
): TraceDescription {
  const trimmed = line.trim();
  const functionMatch = /^([a-zA-Z0-9_.-]+)\((.*)\)$/.exec(trimmed);
  const name = (functionMatch?.[1] ?? "").toLowerCase().split(".").pop() || "";
  const args = functionMatch?.[2] ?? "";
  const parsedUrl = traceUrlFromArgs(args, trimmed);
  const webDetail = parsedUrl ? formatCompactWebUrl(parsedUrl) : "";
  const plainWebReadTrace =
    !!parsedUrl && /\b(fetch(?:ing|ed)?|read(?:ing)?|opened?|opening)\b/i.test(trimmed);

  if (/search/i.test(name)) {
    const query = traceFieldFromArgs(args, ["query", "q", "text"]) || args;
    return {
      kind: "search",
      label: presentWebSearchAction(query, status, name === "x_search" ? "x" : "web"),
      detail: "",
    };
  }
  if (/fetch|read|open/i.test(name) || plainWebReadTrace) {
    const rawTarget = traceFieldFromArgs(args, ["path", "file_path", "url"]) || args || trimmed;
    const pageTitle = parsedUrl ? webPageTitle(result) : "";
    return {
      kind: "tool",
      label: pageTitle || statusCopy(status, "Reading", "Read", "Could not read"),
      detail: webDetail || (/^https?:\/\//i.test(rawTarget.trim())
        ? "Private address"
        : safeActivityDetail(rawTarget)),
      url: parsedUrl?.href,
      host: parsedUrl ? displayWebHost(parsedUrl.hostname) : undefined,
    };
  }
  if (isShellTraceName(name)) return describeShellTrace(args, trimmed, status);
  if (name === "write_file") {
    return describeFileMutationTrace(args, status, "Writing file", "Wrote file", "Could not write file");
  }
  if (name === "edit_file" || name === "apply_patch") {
    return describeFileMutationTrace(args, status, "Editing file", "Edited file", "Could not edit file");
  }
  if (name) {
    const action = humanizeTraceToolName(name);
    return {
      kind: "tool",
      label: statusCopy(
        status,
        `Running ${action}`,
        `Completed ${action}`,
        `Could not complete ${action}`,
      ),
      detail: "",
    };
  }
  if (/done|complete|success/i.test(trimmed)) {
    return { kind: "done", label: "Completed step", detail: safeActivityDetail(trimmed) };
  }
  return {
    kind: status === "done" ? "done" : "trace",
    label: statusCopy(status, "Working", "Completed step", "Step failed"),
    detail: safeActivityDetail(trimmed),
  };
}

function webPageTitle(result: unknown): string {
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const title = (result as Record<string, unknown>).title;
    if (typeof title === "string") return safeActivityDetail(title);
  }
  if (typeof result !== "string") return "";
  const heading = result.match(/^#\s+(.+)$/m)?.[1]?.trim();
  return heading ? safeActivityDetail(heading) : "";
}

function describeShellTrace(
  args: string,
  fallback: string,
  status: GenericToolStatus,
): TraceDescription {
  const command = shellCommandFromArgs(args) || fallback;
  const chinese = /^zh/i.test(currentLocale());
  const purpose = safeActivityDetail(traceFieldFromArgs(args, ["description", "ui_summary"]), 120);
  if (purpose) {
    return {
      kind: "tool",
      label: status === "error" ? `${chinese ? "失败：" : "Failed: "}${purpose}` : purpose,
      detail: "",
    };
  }
  if (/^(?:\/(?:usr\/)?bin\/)?date(?:\s|$)/i.test(command.trim())) {
    return {
      kind: "tool",
      label: statusCopy(
        status,
        chinese ? "正在查看当前时间" : "Checking current time",
        chinese ? "已查看当前时间" : "Checked current time",
        chinese ? "查看当前时间失败" : "Could not check current time",
      ),
      detail: "",
      icon: "clock",
    };
  }
  const action = shellAction(command);
  if (action) {
    const copy = SHELL_ACTION_COPY[action][chinese ? "zh" : "en"];
    return { kind: "tool", label: statusCopy(status, ...copy), detail: "" };
  }
  return {
    kind: "tool",
    label: statusCopy(
      status,
      chinese ? "正在执行命令" : "Running command",
      chinese ? "已执行命令" : "Ran command",
      chinese ? "命令执行失败" : "Command failed",
    ),
    detail: summarizeShellCommand(command),
  };
}

type ShellAction = "test" | "build" | "lint" | "typecheck" | "search" | "list" | "read" | "changes";
type StatusCopy = readonly [string, string, string];

const SHELL_ACTION_COPY: Record<ShellAction, { en: StatusCopy; zh: StatusCopy }> = {
  test: {
    en: ["Running tests", "Ran tests", "Tests failed"],
    zh: ["正在运行测试", "已运行测试", "测试失败"],
  },
  build: {
    en: ["Building the app", "Built the app", "Build failed"],
    zh: ["正在构建应用", "已构建应用", "构建失败"],
  },
  lint: {
    en: ["Checking code style", "Checked code style", "Code style check failed"],
    zh: ["正在检查代码规范", "已检查代码规范", "代码规范检查失败"],
  },
  typecheck: {
    en: ["Checking types", "Checked types", "Type check failed"],
    zh: ["正在检查类型", "已检查类型", "类型检查失败"],
  },
  search: {
    en: ["Searching file contents", "Searched file contents", "File search failed"],
    zh: ["正在搜索文件内容", "已搜索文件内容", "文件搜索失败"],
  },
  list: {
    en: ["Listing files", "Listed files", "Could not list files"],
    zh: ["正在查看文件列表", "已查看文件列表", "查看文件列表失败"],
  },
  read: {
    en: ["Reading files", "Read files", "Could not read files"],
    zh: ["正在读取文件", "已读取文件", "读取文件失败"],
  },
  changes: {
    en: ["Checking workspace changes", "Checked workspace changes", "Could not check changes"],
    zh: ["正在检查工作区改动", "已检查工作区改动", "检查工作区改动失败"],
  },
};

/** Infer only a known leading command, never words embedded in scripts or quoted text. */
function shellAction(command: string): ShellAction | undefined {
  const firstLine = command.split(/\r?\n/, 1)[0].trim();
  if (/<<|\|/.test(firstLine)) return undefined;
  const executable = firstLine
    .replace(/^(?:cd\s+(?:"[^"]*"|'[^']*'|[^&;\n]+)\s*&&\s*)+/, "")
    .replace(/^(?:[A-Za-z_]\w*=(?:"[^"]*"|'[^']*'|[^\s]+)\s+)+/, "")
    .replace(/^uv\s+run\s+(?:--[\w-]+\s+)*/, "");
  const packageScript = /^(?:npm|pnpm|bun|yarn)\s+(?:(?:run|exec|x)\s+)?([\w:-]+)/.exec(executable)?.[1];
  if (packageScript) {
    if (/^(?:test|vitest|pytest)(?::|$)/.test(packageScript)) return "test";
    if (/^build(?::|$)/.test(packageScript)) return "build";
    if (/^lint(?::|$)/.test(packageScript)) return "lint";
    if (/^(?:typecheck|type-check|tsc)(?::|$)/.test(packageScript)) return "typecheck";
  }
  if (/^(?:(?:\S*\/)?python[\d.]*\s+-m\s+)?(?:pytest|vitest)\b/.test(executable)) return "test";
  if (/^node\s+\S*\/(?:vitest\/vitest\.mjs|jest\/bin\/jest\.js)\b/.test(executable)) return "test";
  if (/^(?:ruff\s+check|eslint)\b/.test(executable)) return "lint";
  if (/^(?:tsc|basedpyright|pyright)\b/.test(executable)) return "typecheck";
  if (/^(?:rg|grep)\b/.test(executable)) return "search";
  if (/^(?:ls|find)\b/.test(executable)) return "list";
  if (/^(?:cat|head|tail)\b/.test(executable)) return "read";
  if (/^git\s+(?:status|diff)\b/.test(executable)) return "changes";
  return undefined;
}

function describeFileMutationTrace(
  args: string,
  status: GenericToolStatus,
  running: string,
  done: string,
  failed: string,
): TraceDescription {
  const path = traceFieldFromArgs(args, ["path", "file_path"]);
  return {
    kind: "tool",
    label: statusCopy(status, running, done, failed),
    detail: path ? safeActivityDetail(path) : "",
  };
}

function statusCopy(
  status: GenericToolStatus,
  running: string,
  done: string,
  failed: string,
): string {
  return status === "running" ? running : status === "error" ? failed : done;
}

function traceFieldFromArgs(args: string, keys: string[]): string {
  const compactArgs = args.trim();
  if (!compactArgs) return "";
  try {
    const parsed = JSON.parse(compactArgs) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return "";
    const record = parsed as Record<string, unknown>;
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  } catch {
    return "";
  }
  return "";
}

function isShellTraceName(name: string): boolean {
  return [
    "exec",
    "exec_command",
    "execute_command",
    "run_command",
    "run_shell",
    "shell",
    "terminal",
    "bash",
    "sh",
  ].includes(name.toLowerCase().split(".").pop() || name.toLowerCase());
}

function shellCommandFromArgs(args: string): string {
  const compactArgs = args.trim();
  if (!compactArgs) return "";
  try {
    const parsed = JSON.parse(compactArgs) as unknown;
    if (typeof parsed === "string") return parsed;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return "";
    const record = parsed as Record<string, unknown>;
    for (const key of ["command", "cmd", "script", "input"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value;
    }
  } catch {
    return compactArgs.replace(/^["']|["']$/g, "");
  }
  return "";
}

function humanizeTraceToolName(name: string): string {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  return words ? `${words[0].toUpperCase()}${words.slice(1)}` : "tool action";
}

function traceUrlFromArgs(args: string, fallback: string): URL | null {
  const candidates: string[] = [];
  const compactArgs = args.trim();
  if (compactArgs) {
    try {
      collectUrlCandidates(JSON.parse(compactArgs), candidates);
    } catch {
      candidates.push(compactArgs.replace(/^["']|["']$/g, ""));
    }
  }
  candidates.push(fallback);
  for (const candidate of candidates) {
    const url = parseSafeActivityHttpUrl(candidate);
    if (url) return url;
    const embedded = candidate.match(/https?:\/\/[^\s"'<>),]+/i)?.[0];
    if (embedded) {
      const embeddedUrl = parseSafeActivityHttpUrl(embedded);
      if (embeddedUrl) return embeddedUrl;
    }
  }
  return null;
}

function collectUrlCandidates(value: unknown, candidates: string[]) {
  if (typeof value === "string") {
    candidates.push(value);
    return;
  }
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    for (const item of value.slice(0, 6)) collectUrlCandidates(item, candidates);
    return;
  }
  const record = value as Record<string, unknown>;
  for (const key of ["url", "uri", "href", "link"]) {
    if (typeof record[key] === "string") candidates.push(record[key]);
  }
}
