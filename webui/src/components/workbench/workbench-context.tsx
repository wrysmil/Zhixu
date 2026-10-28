import { useTranslation } from "react-i18next";

import type { SessionWorkbenchSnapshot } from "./session-workbench-api";
import { WorkbenchBadge, WorkbenchEmpty, WorkbenchSection, useWorkbenchText } from "./workbench-parts";

export function WorkbenchMemories({ snapshot, onOpenFilePreview }: {
  snapshot: SessionWorkbenchSnapshot | null; onOpenFilePreview?: (path: string) => void;
}) {
  const text = useWorkbenchText();
  const { i18n } = useTranslation();
  const memories = snapshot?.memories ?? [];
  const formatTime = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString(i18n.language, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  };
  return <div className="px-4 py-3">
    <p className="mb-3 text-[11px] leading-5 text-muted-foreground">{text("区分任务中的上下文与已保存的长期记忆。", "Working context and saved long-term memories are shown separately.")}</p>
    {memories.length ? <ul className="space-y-3">{memories.map((memory) => <li key={memory.id} className="rounded-md border border-border/55 bg-background/60 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <WorkbenchBadge>{memory.kind === "working" ? text("工作记忆", "Working memory") : text("长期记忆", "Long-term memory")}</WorkbenchBadge>
        <span className="text-[10px] text-muted-foreground">{memory.persisted ? text("已持久化", "Saved") : text("尚未持久化", "Not persisted")}</span>
      </div>
      <p className="text-xs font-medium [overflow-wrap:anywhere]">{memory.title}</p>
      <details className="mt-2">
        <summary className="cursor-pointer text-[11px] text-muted-foreground">{text("查看内容", "View content")}</summary>
        <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">{memory.content}</p>
      </details>
      <p className="mt-2 text-[10px] text-muted-foreground [overflow-wrap:anywhere]">{text("来源", "Source")}: {memory.source === "session_scratchpad" ? text("本会话工作记忆", "Session scratchpad") : memory.source.startsWith("episode:") ? `${text("会话经历", "Session episode")} ${memory.source.slice(8)}` : memory.source.startsWith("session:") ? text("本会话抽取", "Extracted in this session") : memory.source}</p>
      {memory.kind === "long_term" && <span className="mt-1 block text-[10px] text-muted-foreground [overflow-wrap:anywhere]">{memory.is_global ? text("归属：全局记忆", "Ownership: global memory") : text(`归属：工作区 ${(memory.workspace_id ?? "").slice(0, 8)}`, `Ownership: workspace ${(memory.workspace_id ?? "").slice(0, 8)}`)}</span>}
      {memory.updated_at && <time dateTime={memory.updated_at} className="mt-1 block text-[10px] text-muted-foreground">{formatTime(memory.updated_at)}</time>}
      {memory.source.startsWith("/") && onOpenFilePreview && <button type="button" className="mt-2 text-[11px] text-primary hover:underline" onClick={() => onOpenFilePreview(memory.source)}>{text("打开来源文件", "Open source file")}</button>}
    </li>)}</ul> : <WorkbenchEmpty>{text("尚无与本会话关联的记忆。其他会话的长期记忆不会混入这里。", "No memories linked to this session. Memories from other sessions are excluded.")}</WorkbenchEmpty>}
  </div>;
}

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

export function WorkbenchLimitations({ limitations }: { limitations: string[] }) {
  const text = useWorkbenchText();
  const messages: Record<string, string> = {
    subagent_executions_unavailable: text("子 Agent 执行记录暂不可读取。", "Subagent execution history is unavailable."),
    memory_unavailable: text("记忆服务暂不可读取。", "Memory service is unavailable."),
    working_memory_unavailable: text("工作记忆暂不可读取。", "Working memory is unavailable."),
    linked_memory_unavailable: text("关联的长期记忆暂不可读取。", "Linked long-term memories are unavailable."),
    session_memories_unavailable: text("本会话的记忆关联暂不可读取。", "Session memory links are unavailable."),
    skills_unavailable: text("Skill 清单暂不可读取。", "The Skill list is unavailable."),
    skill_usage_tracks_successful_file_reads_only: text("Skill 使用状态依据成功读取技能文件的记录，不能代表技能已执行完成。", "Skill use is inferred from successful reads of skill files; it does not confirm completion."),
    tool_usage_limited_to_recent_500_messages: text("工具调用统计仅涵盖最近 500 条消息。", "Tool call counts cover the most recent 500 messages."),
    mcp_connection_state_unavailable: text("MCP 连接状态暂不可读取，调用记录仍会单独显示。", "MCP connection state is unavailable; call records are shown separately."),
    mcp_call_outcomes_not_tracked: text("MCP 调用结果尚未跟踪，成功与失败次数显示为未知。", "MCP outcomes are not tracked; success and failure counts are unknown."),
  };
  const descriptions = [...new Set(limitations.map((item) => messages[item] || text("部分状态暂不可读取，可稍后刷新。", "Some state is unavailable. Try refreshing later.")))];
  return <div className="border-b border-border/45 px-4 py-3"><details>
    <summary className="cursor-pointer text-[11px] text-muted-foreground">{text("数据来源与统计范围", "Data sources and tracking coverage")}</summary>
    <ul className="mt-2 space-y-2 text-[10px] leading-4 text-muted-foreground">{descriptions.map((item) => <li key={item} className="[overflow-wrap:anywhere]">{item}</li>)}</ul>
  </details></div>;
}
