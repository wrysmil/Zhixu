
import type { SessionWorkbenchSnapshot } from "./session-workbench-api";
import { WorkbenchEmpty, WorkbenchSection, useWorkbenchText } from "./workbench-parts";

export function WorkbenchCapabilities({ snapshot }: { snapshot: SessionWorkbenchSnapshot | null }) {
  const text = useWorkbenchText();
  const skillStatus = { available: text("可用 · 未记录使用", "Available · no recorded use"), always_loaded: text("常驻加载", "Always loaded"), used: text("本会话已使用", "Used in this session") };
  const mcpStatus: Record<string, string> = { connected: text("已连接", "Connected"), connecting: text("连接中", "Connecting"), failed: text("连接失败", "Connection failed"), observed: text("已记录调用 · 连接状态未知", "Calls observed · connection unknown"), disconnected: text("未连接", "Disconnected"), configured: text("已配置", "Configured"), unavailable: text("不可用", "Unavailable"), unknown: text("连接状态未知", "Connection unknown") };
  const skills = [...(snapshot?.skills ?? [])].sort((left, right) => {
    const priority = { used: 0, always_loaded: 1, available: 2 };
    return priority[left.status] - priority[right.status] || left.name.localeCompare(right.name);
  });
  const mcp = snapshot?.mcp ?? [];
  const usedSkills = skills.filter((skill) => skill.status === "used").length;
  const loadedSkills = skills.filter((skill) => skill.status === "always_loaded").length;
  const connectedMcp = mcp.filter((server) => ["connected", "connecting"].includes(server.status)).length;
  const calls = mcp.reduce((total, server) => total + server.calls, 0);
  const summaries = [
    { label: text("Skill 可用", "Skills available"), value: skills.length, detail: loadedSkills ? `${loadedSkills} ${text("常驻", "always loaded")}` : text("按需加载", "Load on demand") },
    { label: text("已记录使用", "Recorded use"), value: usedSkills, detail: text("本会话", "This session") },
    { label: text("MCP 连接", "MCP connected"), value: connectedMcp, detail: `${mcp.length} ${text("已配置", "configured")}` },
    { label: text("MCP 调用", "MCP calls"), value: calls, detail: text("最近 500 条消息", "Latest 500 messages") },
  ];
  return <>
    <div aria-label={text("能力概览", "Capability overview")} className="grid grid-cols-2 gap-2 border-b border-border/45 px-4 py-3">
      {summaries.map((summary) => <div key={summary.label} className="rounded-md border border-border/55 bg-background/70 px-2.5 py-2">
        <p className="text-[10px] text-muted-foreground">{summary.label}</p>
        <p className="mt-1 text-lg font-semibold tabular-nums leading-none">{summary.value}</p>
        <p className="mt-1 truncate text-[9px] text-muted-foreground">{summary.detail}</p>
      </div>)}
    </div>
    <WorkbenchSection title="Skill" count={skills.length} defaultOpen>
      {skills.length ? <ul className="space-y-2">{skills.map((skill) => <li key={`${skill.name}:${skill.source}`} className="rounded-md border border-border/45 bg-background/55 px-2.5 py-2">
        <div className="flex items-start gap-2">
          <span aria-hidden className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${skill.status === "used" ? "bg-emerald-500" : skill.status === "always_loaded" ? "bg-primary" : "bg-muted-foreground/45"}`} />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium [overflow-wrap:anywhere]">{skill.name}</p>
            <p className="mt-0.5 text-[10px] text-muted-foreground">{skillStatus[skill.status]}</p>
            <p className="mt-0.5 text-[9px] text-muted-foreground">{text("来源", "Source")}: {skill.source}</p>
          </div>
        </div>
      </li>)}</ul> : <WorkbenchEmpty>{text("当前没有可展示的 Skill 记录。", "No Skill records available.")}</WorkbenchEmpty>}
    </WorkbenchSection>
    <WorkbenchSection title="MCP" count={snapshot?.mcp.length ?? 0} defaultOpen>
      {mcp.length ? <ul className="space-y-2">{mcp.map((server) => <li key={server.name} className="rounded-md border border-border/45 bg-background/55 px-2.5 py-2">
        <p className="text-xs font-medium [overflow-wrap:anywhere]">{server.name}</p>
        <p className={`mt-1 text-[10px] ${server.status === "connected" ? "text-emerald-600 dark:text-emerald-400" : server.status === "failed" ? "text-destructive" : "text-muted-foreground"}`}>{mcpStatus[server.status] || server.status}</p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">{server.calls ? `${text("调用", "Calls")} ${server.calls}` : text("尚未调用", "No calls yet")}</p>
        {server.calls > 0 && <p className="mt-1 text-[10px] text-muted-foreground">
          {server.successes === null ? text("成功次数未知", "Success count unknown") : `${text("成功", "Succeeded")} ${server.successes}`} · {server.failures === null ? text("失败次数未知", "Failure count unknown") : `${text("失败", "Failed")} ${server.failures}`}
        </p>}
      </li>)}</ul> : <WorkbenchEmpty>{text("当前没有 MCP 连接或调用记录。", "No MCP connection or call records.")}</WorkbenchEmpty>}
    </WorkbenchSection>
  </>;
}

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { fetchWorkbenchSkills } from "./session-workbench-api";
import type { SkillOrigin } from "./workbench-live-types";

export function WorkbenchLiveCapabilities({ snapshot, token, sessionKey }: { snapshot: SessionWorkbenchSnapshot; token: string; sessionKey: string }) {
  const text = useWorkbenchText();
  const catalog = snapshot.skill_catalog!;
  const [groups, setGroups] = useState(catalog.groups);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => { setGroups(catalog.groups); }, [catalog.groups.workspace.revision, catalog.groups.global.revision, catalog.groups.plugin.revision, catalog.groups.builtin.revision]);
  useEffect(() => { const c = new AbortController(); controller.current = c; return () => c.abort(); }, [token, sessionKey, snapshot.scope?.project_id]);
  const more = async (group: SkillOrigin) => {
    const page = groups[group]; const cursor = page.next_cursor; const signal = controller.current?.signal; if (!cursor || !signal) return;
    setError("");
    try { const next = await fetchWorkbenchSkills(token, sessionKey, group, cursor, signal); if (!signal.aborted) setGroups(old => old[group].revision === page.revision && old[group].next_cursor === cursor ? ({ ...old, [group]: { ...next, items: [...old[group].items, ...next.items] } }) : old); }
    catch (reason) {
      if (signal.aborted) return;
      if (reason instanceof ApiError && reason.status === 409) {
        try { const first = await fetchWorkbenchSkills(token, sessionKey, group, null, signal); if (!signal.aborted) setGroups(old => old[group].revision === page.revision ? ({ ...old, [group]: first }) : old); }
        catch { if (!signal.aborted) setError(text("能力目录刷新失败", "Could not refresh skills")); }
      } else setError(text("加载更多失败", "Could not load more"));
    }
  };
  const availability = { available: text("可用", "Available"), disabled: text("已禁用", "Disabled"), requirements_missing: text("缺少依赖", "Requirements missing"), invalid: text("无效", "Invalid") };
  const observed = { available: text("未记录使用", "No recorded use"), provided: text("已提供", "Provided"), read: text("已读取", "Read"), unrecorded: text("未记录", "Not recorded") };
  const labels: Record<SkillOrigin, string> = { workspace: text("项目", "Project"), global: text("全局", "Global"), plugin: text("插件", "Plugin"), builtin: text("内置", "Built-in") };
  return <>
    <p className="px-4 py-3 text-xs">{text("可用技能", "Available skills")}: {catalog.available_total}</p>
    {(["workspace", "global", "plugin", "builtin"] as SkillOrigin[]).map(group => <WorkbenchSection key={group} title={labels[group]} count={groups[group].total} defaultOpen>
      <ul className="space-y-2">{groups[group].items.map(item => <li key={item.id} className="break-words text-xs"><p className="font-medium">{item.name}</p><p className="text-muted-foreground">{availability[item.availability]} · {observed[item.observed_state]}</p>{item.overridden_by && <p className="text-muted-foreground">{text("被覆盖", "Overridden by")}: {item.overridden_by}</p>}{item.requirement_labels.map(label => <p key={label} className="text-muted-foreground">{label}</p>)}</li>)}</ul>
      {groups[group].next_cursor && <button type="button" onClick={() => void more(group)} className="mt-2 text-xs underline">{text("加载更多", "Load more")}</button>}
    </WorkbenchSection>)}
    {error && <p role="alert" className="px-4 text-xs text-destructive">{error}</p>}
    <WorkbenchSection title="MCP" count={snapshot.mcp.length} defaultOpen>{snapshot.mcp.map(server => <p key={server.name} className="text-xs">{server.name} · {server.status} · {text("调用", "Calls")} {server.calls} · {server.successes === null ? text("成功次数未知", "Success count unknown") : `${text("成功", "Succeeded")} ${server.successes}`} · {server.failures === null ? text("失败次数未知", "Failure count unknown") : `${text("失败", "Failed")} ${server.failures}`}</p>)}</WorkbenchSection>
  </>;
}
