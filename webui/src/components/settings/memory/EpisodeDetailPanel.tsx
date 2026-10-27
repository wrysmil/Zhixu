import type { EpisodePayload } from "@/lib/types";
import { DetailFields, DetailProvenance, ImportanceBadge, MemoryTime, WorkspaceLabels, useMemoryCopy } from "./MemoryPresentation";

export function EpisodeDetailPanel({ episode, onClose }: { episode: EpisodePayload; onClose: () => void }) {
  const { text } = useMemoryCopy();
  const outcomes = { completed: text("已完成", "Completed"), partial: text("部分完成", "Partially completed"), failed: text("失败", "Failed"), ongoing: text("进行中", "In progress") };
  return <section className="mt-4 space-y-4 rounded-lg border border-border/55 bg-background/70 p-4" aria-label={text("对话记录详情", "Conversation memory details")}>
    <DetailFields fields={[
      [text("目标", "Goal"), episode.goal], [text("完整摘要", "Full summary"), episode.summary],
      [text("结果", "Outcome"), outcomes[episode.outcome] ?? episode.outcome],
      [text("来源", "Source"), <DetailProvenance record={episode} />],
      [text("工作区", "Workspace"), <div className="flex flex-wrap gap-2"><WorkspaceLabels workspaces={episode.workspaces} /></div>],
      [text("开始时间", "Started"), <MemoryTime value={episode.started_at} detailed />],
      [text("结束时间", "Ended"), <MemoryTime value={episode.ended_at} detailed />],
      [text("重要性", "Importance"), <ImportanceBadge value={episode.importance_score} />],
      [text("引用次数", "Reference count"), String(episode.access_count)],
      [text("会话 ID", "Session ID"), episode.session_id], [text("记录 ID", "Record ID"), episode.id],
      [text("实体", "Entities"), episode.entities.join(" · ")], [text("标签", "Tags"), episode.tags.join(" · ")],
      [text("使用工具", "Tools used"), episode.tools_used.join(" · ")],
      [text("关联记忆", "Linked memories"), episode.linked_memory_ids.join("\n")],
    ]} />
    <div className="border-t border-border/45 pt-3"><h4 className="text-[12px] font-medium text-muted-foreground">{text("工具调用链", "Tool call chain")}</h4>{episode.action_nodes.length === 0 ? <p className="mt-2 text-[12px] text-muted-foreground">{text("没有工具调用", "No tool calls")}</p> : <ol className="mt-2 space-y-2">{episode.action_nodes.map((node, index) => <li key={index} className="flex gap-2 text-[12px]"><span className="tabular-nums text-muted-foreground">{index + 1}</span><div className="min-w-0 flex-1"><span className="font-medium">{typeof node.tool === "string" ? node.tool : text("未记录工具名称", "Tool name not recorded")}</span><pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/40 p-2 text-muted-foreground">{JSON.stringify(node, null, 2)}</pre></div></li>)}</ol>}</div>
    {episode.metadata && Object.keys(episode.metadata).length > 0 && <details className="text-[12px]"><summary className="cursor-pointer text-muted-foreground">{text("原始元数据", "Original metadata")}</summary><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/40 p-2">{JSON.stringify(episode.metadata, null, 2)}</pre></details>}
    <div className="flex justify-end"><button type="button" onClick={onClose} className="rounded text-[12px] text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{text("收起详情", "Collapse details")}</button></div>
  </section>;
}
