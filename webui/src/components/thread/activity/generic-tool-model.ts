import { currentLocale } from "@/i18n";

import { compactActivityPath, redactActivityText } from "./activity-text";

export type GenericToolStatus = "running" | "done" | "error";
export type ToolFamily =
  | "content-search"
  | "file-search"
  | "list"
  | "read"
  | "memory"
  | "knowledge"
  | "generic";

interface ToolField {
  key:
    | "query"
    | "pattern"
    | "glob"
    | "path"
    | "file_path"
    | "url"
    | "action"
    | "key"
    | "label"
    | "name"
    | "channel"
    | "chat_id"
    | "session_id"
    | "handle"
    | "ui_summary";
  value: string;
}

export interface GenericToolTrace {
  name: string;
  family: ToolFamily;
  groupKey: string;
  fields: ToolField[];
  collectedSource: boolean;
}

export interface GenericToolRunItem {
  trace: GenericToolTrace;
  status: GenericToolStatus;
  error?: string;
  /** Opaque spawn tool-call id used only to join the public execution ledger. */
  toolCallId?: string;
}

export interface GenericToolPresentation {
  status: GenericToolStatus;
  /** Tool family badge ("读取文件", "终端命令"…). */
  kicker: string;
  label: string;
  detail: string;
  aside: string;
}

const CONTENT_SEARCH_TOOLS = new Set([
  "grep",
  "rg",
  "ripgrep",
  "search_code",
  "search_content",
  "search_files_content",
  "find_text",
]);
const FILE_SEARCH_TOOLS = new Set([
  "find",
  "find_file",
  "find_files",
  "glob",
  "search_files",
]);
const LIST_TOOLS = new Set(["list_dir", "list_directory", "list_files", "ls"]);
const READ_TOOLS = new Set(["read", "read_file", "read_text_file"]);
const MEMORY_TOOLS = new Set(["memory_search", "search_memory", "recall_memory"]);
const KNOWLEDGE_TOOLS = new Set(["knowledge_search", "knowledge_read"]);
const EXCLUDED_TOOL_PREFIXES = ["mcp_"];
const EXCLUDED_TOOLS = new Set([
  "apply_patch",
  "cli_anything_run",
  "edit_file",
  "exec",
  "exec_command",
  "execute_command",
  "run_cli_app",
  "run_command",
  "run_shell",
  "shell",
  "terminal",
  "web_fetch",
  "web_search",
  "x_search",
  "write_file",
]);

export function parseGenericToolTrace(line: string): GenericToolTrace | null {
  const call = parseCall(line);
  if (!call || isExcludedTool(call.name)) return null;
  const family = toolFamily(call.name);
  const fields = safeFields(call.args);
  const collectedSource = fields.some((field) => isCollectedSourcePath(field.value));
  return {
    name: call.name,
    family,
    groupKey: family === "generic" || family === "knowledge"
      ? `${family}:${call.name}`
      : `${family}:${collectedSource ? "collected" : "workspace"}`,
    fields,
    collectedSource,
  };
}

export function canGroupGenericToolRuns(previous: GenericToolRunItem, next: GenericToolRunItem): boolean {
  return previous.trace.groupKey === next.trace.groupKey;
}

function compactGenericToolPath(value: string): string {
  const normalized = redactActivityText(value).replace(/\\/g, "/");
  if (isCollectedSourcePath(normalized)) {
    return truncateMiddle(normalized.split("/").pop() || "collected source", 64);
  }
  return compactActivityPath(normalized);
}

export function describeGenericToolRun(items: GenericToolRunItem[]): GenericToolPresentation {
  const status = aggregateStatus(items);
  const family = items[0]?.trace.family ?? "generic";
  const name = items[0]?.trace.name ?? "tool";
  const collected = items.length > 0 && items.every((item) => item.trace.collectedSource);
  return {
    status,
    kicker: activityKicker(family, name),
    label: activityLabel(family, status, collected, name, items),
    detail: activityDetail(items, family, name),
    aside: activityAside(items, family),
  };
}

const GENERIC_KICKERS_ZH: Record<string, string> = {
  generate_image: "生成图片",
  spawn: "子 Agent",
  message: "消息",
  my: "设置",
  cron: "定时任务",
  create_goal: "长任务",
  update_goal: "长任务",
  exec_session: "终端",
  write_stdin: "终端",
  list_exec_sessions: "终端",
  screenshot: "截图",
  capture_screenshot: "截图",
};

function activityKicker(family: ToolFamily, name: string): string {
  const chinese = /^zh/i.test(currentLocale());
  switch (family) {
    case "content-search":
      return chinese ? "搜索内容" : "Search";
    case "file-search":
      return chinese ? "查找文件" : "Find files";
    case "list":
      return chinese ? "查看目录" : "List";
    case "read":
      return chinese ? "读取文件" : "Read";
    case "memory":
      return chinese ? "检索记忆" : "Memory";
    case "knowledge":
      if (name === "knowledge_read") return chinese ? "扩读知识" : "Knowledge read";
      return chinese ? "检索知识库" : "Knowledge search";
    default: {
      const localized = chinese ? GENERIC_KICKERS_ZH[name] : undefined;
      return localized ?? humanizeToolName(name);
    }
  }
}

function parseCall(line: string): { name: string; args: unknown } | null {
  const match = /^([a-zA-Z0-9_.-]+)\((.*)\)$/.exec(line.trim());
  if (!match) return null;
  const name = compactToolName(match[1]);
  let args: unknown;
  try {
    args = match[2].trim() ? JSON.parse(match[2]) : {};
  } catch {
    args = {};
  }
  return { name, args };
}

function compactToolName(name: string): string {
  return name.toLowerCase().split(".").pop() || name.toLowerCase();
}

function isExcludedTool(name: string): boolean {
  return EXCLUDED_TOOLS.has(name) || EXCLUDED_TOOL_PREFIXES.some((prefix) => name.startsWith(prefix));
}

function toolFamily(name: string): ToolFamily {
  if (CONTENT_SEARCH_TOOLS.has(name)) return "content-search";
  if (FILE_SEARCH_TOOLS.has(name)) return "file-search";
  if (LIST_TOOLS.has(name)) return "list";
  if (READ_TOOLS.has(name)) return "read";
  if (MEMORY_TOOLS.has(name)) return "memory";
  if (KNOWLEDGE_TOOLS.has(name)) return "knowledge";
  return "generic";
}

function safeFields(args: unknown): ToolField[] {
  if (!args || typeof args !== "object" || Array.isArray(args)) return [];
  const record = args as Record<string, unknown>;
  const fields: ToolField[] = [];
  for (const key of [
    "query",
    "pattern",
    "glob",
    "path",
    "file_path",
    "url",
    "action",
    "key",
    "label",
    "name",
    "channel",
    "chat_id",
    "session_id",
    "handle",
    "ui_summary",
  ] as const) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      fields.push({ key, value: value.trim() });
    }
  }
  return fields;
}

function aggregateStatus(items: GenericToolRunItem[]): GenericToolStatus {
  if (items.some((item) => item.status === "error")) return "error";
  if (items.some((item) => item.status === "running")) return "running";
  return "done";
}

function activityLabel(
  family: ToolFamily,
  status: GenericToolStatus,
  collected: boolean,
  name: string,
  items: GenericToolRunItem[],
): string {
  const chinese = /^zh/i.test(currentLocale());
  // 每项按 [中文, English] 成对书写，避免中英文两套分支漂移。
  const copy = (
    running: [string, string],
    done: [string, string],
    failed: [string, string],
  ): string => statusCopy(
    status,
    running[chinese ? 0 : 1],
    done[chinese ? 0 : 1],
    failed[chinese ? 0 : 1],
  );

  if (family === "content-search") {
    return collected
      ? copy(
        ["正在查阅来源", "Reviewing sources"],
        ["已查阅来源", "Reviewed sources"],
        ["无法查阅来源", "Could not review sources"],
      )
      : copy(
        ["正在搜索文件", "Searching files"],
        ["已搜索文件", "Searched files"],
        ["无法搜索文件", "Could not search files"],
      );
  }
  if (family === "file-search") {
    return copy(
      ["正在查找文件", "Finding files"],
      ["已查找文件", "Found files"],
      ["无法查找文件", "Could not find files"],
    );
  }
  if (family === "list") {
    return copy(
      ["正在查看目录", "Listing files"],
      ["已查看目录", "Listed files"],
      ["无法查看目录", "Could not list files"],
    );
  }
  if (family === "read") {
    return collected
      ? copy(
        ["正在阅读来源", "Reading source"],
        ["已阅读来源", "Read source"],
        ["无法阅读来源", "Could not read source"],
      )
      : copy(
        ["正在读取文件", "Reading file"],
        ["已读取文件", "Read file"],
        ["无法读取文件", "Could not read file"],
      );
  }
  if (family === "memory") {
    return copy(
      ["正在检索记忆", "Searching memory"],
      ["已检索记忆", "Searched memory"],
      ["无法检索记忆", "Could not search memory"],
    );
  }
  if (family === "knowledge") {
    if (name === "knowledge_read") {
      return copy(
        ["正在扩读原文", "Reading source"],
        ["已扩读原文", "Read source"],
        ["扩读原文失败", "Could not read source"],
      );
    }
    return copy(
      ["正在检索知识库", "Searching knowledge base"],
      ["已检索知识库", "Searched knowledge base"],
      ["检索知识库失败", "Could not search knowledge base"],
    );
  }

  const action = fieldValue(items[0]?.trace, "action").toLowerCase();
  switch (name) {
    case "generate_image":
      return copy(
        ["正在生成图片", "Generating image"],
        ["已生成图片", "Generated image"],
        ["生成图片失败", "Could not generate image"],
      );
    case "spawn":
      return copy(
        ["正在委派任务", "Delegating task"],
        ["已委派任务", "Delegated task"],
        ["委派任务失败", "Could not delegate task"],
      );
    case "message":
      return copy(
        ["正在发送消息", "Sending message"],
        ["已发送消息", "Sent message"],
        ["发送消息失败", "Could not send message"],
      );
    case "my":
      return action === "set" || action === "modify"
        ? copy(
          ["正在更新智能体设置", "Updating agent settings"],
          ["已更新智能体设置", "Updated agent settings"],
          ["更新智能体设置失败", "Could not update agent settings"],
        )
        : copy(
          ["正在检查智能体设置", "Checking agent settings"],
          ["已检查智能体设置", "Checked agent settings"],
          ["检查智能体设置失败", "Could not check agent settings"],
        );
    case "cron":
      if (action === "add") {
        return copy(
          ["正在创建定时任务", "Scheduling automation"],
          ["已创建定时任务", "Scheduled automation"],
          ["创建定时任务失败", "Could not schedule automation"],
        );
      }
      if (action === "remove") {
        return copy(
          ["正在删除定时任务", "Removing automation"],
          ["已删除定时任务", "Removed automation"],
          ["删除定时任务失败", "Could not remove automation"],
        );
      }
      return copy(
        ["正在检查定时任务", "Checking automations"],
        ["已检查定时任务", "Checked automations"],
        ["检查定时任务失败", "Could not check automations"],
      );
    case "create_goal":
      return copy(
        ["正在开始长任务", "Starting long task"],
        ["已开始长任务", "Started long task"],
        ["开始长任务失败", "Could not start long task"],
      );
    case "update_goal":
      return copy(
        ["正在更新长任务", "Updating long task"],
        ["已更新长任务", "Updated long task"],
        ["更新长任务失败", "Could not update long task"],
      );
    // TODO(0.3.2): Remove write_stdin display compatibility after 0.3.1.
    case "exec_session":
    case "write_stdin":
      return copy(
        ["正在继续命令", "Continuing command"],
        ["已继续命令", "Continued command"],
        ["继续命令失败", "Could not continue command"],
      );
    case "list_exec_sessions":
      return copy(
        ["正在检查运行中的命令", "Checking running commands"],
        ["已检查运行中的命令", "Checked running commands"],
        ["检查运行中的命令失败", "Could not check running commands"],
      );
    case "screenshot":
    case "capture_screenshot":
      return copy(
        ["正在截取屏幕", "Capturing screenshot"],
        ["已截取屏幕", "Captured screenshot"],
        ["截取屏幕失败", "Could not capture screenshot"],
      );
    default: {
      const humanName = humanizeToolName(name);
      return copy(
        [`正在运行 ${name}`, `Running ${humanName}`],
        [`已完成 ${name}`, `Completed ${humanName}`],
        [`${name} 执行失败`, `Could not complete ${humanName}`],
      );
    }
  }
}

function activityDetail(items: GenericToolRunItem[], family: ToolFamily, name: string): string {
  if (items.length !== 1) return "";
  const trace = items[0].trace;
  if (family === "content-search") {
    return quote(fieldValue(trace, "query") || fieldValue(trace, "pattern"));
  }
  if (family === "file-search") {
    return compactDetail(
      fieldValue(trace, "glob")
      || fieldValue(trace, "query")
      || fieldValue(trace, "pattern")
      || fieldValue(trace, "path"),
    );
  }
  if (family === "list" || family === "read") {
    return compactDetail(fieldValue(trace, "path") || fieldValue(trace, "file_path"));
  }
  if (family === "memory") return quote(fieldValue(trace, "query"));
  if (family === "knowledge") {
    if (name === "knowledge_read") return compactIdentifier(fieldValue(trace, "handle"));
    return quote(fieldValue(trace, "query"));
  }

  switch (name) {
    case "spawn":
      return safeText(fieldValue(trace, "label"));
    case "message":
      return safeText(fieldValue(trace, "channel"));
    case "my":
      return safeText(fieldValue(trace, "key"));
    case "cron":
      return safeText(fieldValue(trace, "name"));
    case "create_goal":
      return safeText(fieldValue(trace, "ui_summary"));
    case "update_goal":
      return safeText(fieldValue(trace, "action"));
    // TODO(0.3.2): Remove write_stdin display compatibility after 0.3.1.
    case "exec_session":
    case "write_stdin":
      return compactIdentifier(fieldValue(trace, "session_id"));
    case "screenshot":
    case "capture_screenshot":
      return "";
    default:
      return "";
  }
}

function activityAside(items: GenericToolRunItem[], family: ToolFamily): string {
  const chinese = /^zh/i.test(currentLocale());
  const pathCount = uniqueValues(items, ["path", "file_path"]).length;
  if (pathCount > 1) return chinese ? `${pathCount} 个文件` : `${pathCount} files`;
  if (items.length <= 1) return "";
  if (family === "content-search" || family === "file-search" || family === "memory") {
    return chinese ? `${items.length} 次检索` : `${items.length} searches`;
  }
  if (family === "knowledge") {
    return chinese ? `${items.length} 次知识调用` : `${items.length} knowledge calls`;
  }
  return chinese ? `${items.length} 个操作` : `${items.length} actions`;
}

function fieldValue(trace: GenericToolTrace | undefined, key: ToolField["key"]): string {
  return trace?.fields.find((field) => field.key === key)?.value ?? "";
}

function uniqueValues(items: GenericToolRunItem[], keys: ToolField["key"][]): string[] {
  const values = items.flatMap((item) => item.trace.fields)
    .filter((field) => keys.includes(field.key))
    .map((field) => field.value);
  return [...new Set(values)];
}

function statusCopy(status: GenericToolStatus, running: string, done: string, failed: string): string {
  return status === "running" ? running : status === "error" ? failed : done;
}

function compactDetail(value: string): string {
  return value ? truncateMiddle(compactGenericToolPath(value), 88) : "";
}

function safeText(value: string): string {
  return value ? truncateMiddle(redactActivityText(value).replace(/\s+/g, " ").trim(), 88) : "";
}

function quote(value: string): string {
  const safe = safeText(value);
  return safe ? `“${safe}”` : "";
}

function compactIdentifier(value: string): string {
  const safe = safeText(value);
  if (safe.length <= 16) return safe;
  return `${safe.slice(0, 7)}…${safe.slice(-5)}`;
}

function humanizeToolName(name: string): string {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  return words ? `${words[0].toUpperCase()}${words.slice(1)}` : "tool action";
}

function isCollectedSourcePath(value: string): boolean {
  const normalized = value.replace(/\\/g, "/");
  return normalized.includes("/.nanobot/tool-results/") || normalized.includes("/nanobot/tool-results/");
}

function truncateMiddle(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  const head = Math.ceil((maxLength - 1) * 0.62);
  const tail = Math.floor((maxLength - 1) * 0.38);
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}
