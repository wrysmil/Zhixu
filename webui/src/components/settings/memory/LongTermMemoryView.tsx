import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, RefreshCw, Save } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  fetchMemoryDoc,
  fetchSidebarState,
  fetchWorkspaces,
  saveMemoryDoc,
} from "@/lib/api";
import { notifySaveFailed, notifySaved } from "@/lib/save-feedback";
import { useClient } from "@/providers/ClientProvider";
import { memorySelectClass } from "./MemoryPresentation";

type Scope = "global" | "project";
type ProjectOption = { path: string; name: string };

function baseName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

function normalizePath(path: string): string {
  return path.trim().replace(/[\\/]+$/, "").toLowerCase();
}

export function LongTermMemoryView() {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { client, token } = useClient();
  // 默认落在当前项目的长期记忆：用户打开记忆页通常想看的是“这个项目记住了什么”，
  // 全局记忆是次要入口。
  const [scope, setScope] = useState<Scope>("project");
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [projectPath, setProjectPath] = useState("");
  const [content, setContent] = useState("");
  const [revision, setRevision] = useState("");
  const [limit, setLimit] = useState(3000);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const [workspaces, sidebar] = await Promise.allSettled([
        fetchWorkspaces(token),
        fetchSidebarState(token),
      ]);
      if (!alive) return;
      const currentPath =
        workspaces.status === "fulfilled"
          ? workspaces.value.default_scope.project_path
          : "";
      const currentName =
        workspaces.status === "fulfilled"
          ? workspaces.value.default_scope.project_name
          : undefined;
      const entries =
        sidebar.status === "fulfilled" ? sidebar.value.project_entries : [];
      const options: ProjectOption[] = [];
      const seen = new Set<string>();
      const add = (path: string, name?: string) => {
        const cleaned = path.trim();
        if (!cleaned || seen.has(normalizePath(cleaned))) return;
        seen.add(normalizePath(cleaned));
        options.push({ path: cleaned, name: name?.trim() || baseName(cleaned) });
      };
      add(currentPath, currentName || tx("settings.memory.currentProject", "Current project"));
      for (const entry of entries) add(entry.path, entry.name);
      setProjects(options);
      setProjectPath((previous) => previous || currentPath || options[0]?.path || "");
    })();
    return () => {
      alive = false;
    };
  }, [token, t]);

  const requestId = useRef(0);
  const load = useCallback(async () => {
    // 项目列表解析后 projectPath 会从空变成当前项目，触发第二次读取；
    // 只接受最后一次请求的结果，避免慢响应覆盖用户正在编辑的内容。
    const seq = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const doc = await fetchMemoryDoc(
        token,
        scope,
        scope === "project" && projectPath ? { workspacePath: projectPath } : {},
      );
      if (seq !== requestId.current) return;
      setContent(doc.content);
      setRevision(doc.revision);
      setLimit(doc.limit);
    } catch (reason) {
      if (seq !== requestId.current) return;
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (seq === requestId.current) setLoading(false);
    }
  }, [scope, projectPath, token]);

  useEffect(() => {
    void load();
  }, [load]);

  const activeProject = useMemo(
    () => projects.find((option) => option.path === projectPath),
    [projects, projectPath],
  );
  const overLimit = content.length > limit;
  const save = async () => {
    if (saving || overLimit) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await saveMemoryDoc(client, {
        scope,
        content,
        expectedRevision: revision || undefined,
        workspacePath: scope === "project" ? projectPath || undefined : undefined,
      });
      setContent(saved.content);
      setRevision(saved.revision);
      notifySaved();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      notifySaveFailed(message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-3" data-testid="long-term-memory-view">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl
            mode="tabs"
            ariaLabel={tx("settings.memory.scopeLabel", "Memory scope")}
            value={scope}
            options={[
              { value: "global", label: tx("settings.memory.scopeGlobal", "Global") },
              { value: "project", label: tx("settings.memory.scopeProject", "Project") },
            ]}
            onChange={(next) => setScope(next as Scope)}
            className="w-fit bg-primary/[0.06]"
            itemClassName="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          {scope === "project" && projects.length > 0 ? (
            <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
              <span>{tx("settings.memory.projectLabel", "Project")}</span>
              <select
                aria-label={tx("settings.memory.projectSelect", "Select project")}
                className={memorySelectClass}
                value={projectPath}
                onChange={(event) => setProjectPath(event.target.value)}
                disabled={loading || saving}
                title={activeProject?.path}
              >
                {projects.map((option) => (
                  <option key={option.path} value={option.path}>
                    {option.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <span
            className={
              "text-[12px] tabular-nums " +
              (overLimit ? "text-destructive" : "text-muted-foreground")
            }
          >
            {content.length} / {limit}
          </span>
          <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading || saving}>
            <RefreshCw className="mr-1.5 h-4 w-4" aria-hidden />
            {tx("settings.memory.refresh", "Refresh")}
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={loading || saving || overLimit}>
            {saving ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Save className="mr-1.5 h-4 w-4" aria-hidden />
            )}
            {tx("settings.memory.save", "Save")}
          </Button>
        </div>
      </div>

      <p className="text-[12px] leading-5 text-muted-foreground">
        {tx(
          "settings.memory.longTermHint",
          "Long-term rules and conventions are injected every turn. Put long details in sibling files and leave a pointer here.",
        )}
      </p>

      {error ? (
        <p role="alert" className="text-[12px] text-destructive">
          {error}
        </p>
      ) : null}
      {overLimit ? (
        <p role="alert" className="text-[12px] text-destructive">
          {tx(
            "settings.memory.overLimit",
            "Over the character limit. Split long details into sibling files before saving.",
          )}
        </p>
      ) : null}

      {loading ? (
        <div className="grid min-h-48 place-items-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-label={tx("common.loading", "Loading")} />
        </div>
      ) : (
        <textarea
          aria-label={tx("settings.memory.longTermEditor", "Long-term memory editor")}
          className="h-[clamp(18rem,48vh,34rem)] w-full resize-y rounded-lg border border-border/70 bg-background px-3 py-2 font-mono text-[12.5px] leading-6 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          spellCheck={false}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          placeholder={tx(
            "settings.memory.longTermPlaceholder",
            "## 项目约定\n- 构建用 bun",
          )}
        />
      )}
    </div>
  );
}
