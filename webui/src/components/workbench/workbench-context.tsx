import { useWorkbenchText } from "./workbench-parts";
export { WorkbenchMemories } from "./workbench-memory";
export { WorkbenchCapabilities } from "./workbench-capabilities";

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
