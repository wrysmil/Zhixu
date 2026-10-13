import type { UIMessage, WorkspaceScopePayload } from "@/lib/types";
import {
  compactActivityPath,
  redactActivityText,
  safeActivityDetail,
} from "@/components/thread/activity/activity-text";
import { describeTraceLine } from "@/components/thread/activity/trace-activity-model";
import { webSearchRunsByTraceLine } from "@/components/thread/activity/web-search-model";

export interface WorkbenchOutput {
  path: string;
  display: string;
  added: number;
  deleted: number;
  status: "editing" | "done" | "error";
  operation?: string;
}

export interface WorkbenchSource {
  href: string;
  title: string;
  host: string;
}

export interface WorkbenchIntegration {
  name: string;
  count: number;
}

export interface SessionWorkbenchModel {
  updatedAtMs: number | null;
  summary: string;
  diffFiles: number;
  diffAdded: number;
  diffDeleted: number;
  projectName: string;
  projectPath: string;
  accessMode: "full" | "restricted" | null;
  mcpUsed: WorkbenchIntegration[];
  cliUsed: WorkbenchIntegration[];
  outputs: WorkbenchOutput[];
  sources: WorkbenchSource[];
}

const MAX_SUMMARY_LENGTH = 180;
const MAX_OUTPUTS = 24;
const MAX_SOURCES = 20;

export function deriveSessionWorkbench(
  messages: UIMessage[],
  workspaceScope: WorkspaceScopePayload | null | undefined,
): SessionWorkbenchModel {
  let updatedAtMs: number | null = null;
  let summary = "";
  const outputsByPath = new Map<string, WorkbenchOutput>();
  const sourcesByHref = new Map<string, WorkbenchSource>();
  const mcpCounts = new Map<string, number>();
  const cliCounts = new Map<string, number>();

  for (const message of messages) {
    if (Number.isFinite(message.createdAt)) {
      updatedAtMs = Math.max(updatedAtMs ?? 0, message.createdAt);
    }

    if (
      message.role === "assistant"
      && message.kind !== "trace"
      && !message.activityKind
      && !message.isStreaming
      && message.content.trim()
    ) {
      summary = plainSummary(message.content);
    }

    for (const edit of message.fileEdits ?? []) {
      const path = (edit.absolute_path || edit.path || "").trim();
      if (!path) continue;
      const key = path.replace(/\\/g, "/");
      const previous = outputsByPath.get(key);
      outputsByPath.set(key, {
        path,
        display: safeActivityDetail(compactActivityPath(path), 120),
        added: (previous?.added ?? 0) + Math.max(0, edit.added || 0),
        deleted: (previous?.deleted ?? 0) + Math.max(0, edit.deleted || 0),
        status: edit.status === "error" ? "error" : edit.status === "editing" ? "editing" : "done",
        operation: edit.operation,
      });
    }

    const lines = message.traces?.length
      ? message.traces
      : message.kind === "trace" && message.content.trim()
        ? [message.content]
        : [];
    for (const line of lines) {
      const name = traceName(line);
      if (!name) continue;
      if (name.startsWith("mcp_")) {
        mcpCounts.set(name, (mcpCounts.get(name) ?? 0) + 1);
      } else if (name === "run_cli_app" || name === "cli_anything_run") {
        const label = traceCliLabel(line) || name;
        cliCounts.set(label, (cliCounts.get(label) ?? 0) + 1);
      }
    }

    if (lines.length) {
      for (const run of webSearchRunsByTraceLine(message.toolEvents ?? []).values()) {
        for (const source of run.sources) {
          if (!sourcesByHref.has(source.href)) {
            sourcesByHref.set(source.href, {
              href: source.href,
              title: source.title || source.host,
              host: source.host,
            });
          }
        }
      }
      for (const line of lines) {
        const description = describeTraceLine(line, "done");
        if (description.url && !sourcesByHref.has(description.url)) {
          sourcesByHref.set(description.url, {
            href: description.url,
            title: description.detail || description.host || description.url,
            host: description.host ?? "",
          });
        }
      }
    }
  }

  const outputs = [...outputsByPath.values()].slice(-MAX_OUTPUTS).reverse();
  const sources = [...sourcesByHref.values()].slice(0, MAX_SOURCES);

  return {
    updatedAtMs,
    summary: summary ? redactActivityText(summary).slice(0, MAX_SUMMARY_LENGTH) : "",
    diffFiles: outputsByPath.size,
    diffAdded: [...outputsByPath.values()].reduce((total, item) => total + item.added, 0),
    diffDeleted: [...outputsByPath.values()].reduce((total, item) => total + item.deleted, 0),
    projectName: workspaceScope?.project_name?.trim() || "",
    projectPath: compactActivityPath(workspaceScope?.project_path ?? ""),
    accessMode: workspaceScope?.access_mode ?? null,
    mcpUsed: sortedIntegrations(mcpCounts),
    cliUsed: sortedIntegrations(cliCounts),
    outputs,
    sources,
  };
}

function plainSummary(content: string): string {
  return content
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\[[^\]]*\]\(([^)]*)\)/g, "$1")
    .replace(/[*_~]/g, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

function traceName(line: string): string {
  const match = /^([a-zA-Z0-9_.-]+)\(/.exec(line.trim());
  return (match?.[1] ?? "").toLowerCase().split(".").pop() ?? "";
}

function traceCliLabel(line: string): string {
  const match = /^[a-zA-Z0-9_.-]+\((.*)\)$/.exec(line.trim());
  if (!match) return "";
  try {
    const args = JSON.parse(match[1]) as { name?: unknown; app?: unknown };
    const value = args.name ?? args.app;
    return typeof value === "string" ? value.trim() : "";
  } catch {
    return "";
  }
}

function sortedIntegrations(counts: Map<string, number>): WorkbenchIntegration[] {
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}
