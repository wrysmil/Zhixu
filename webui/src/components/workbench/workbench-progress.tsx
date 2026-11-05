import { useState } from "react";
import type { WorkbenchActivity } from "./workbench-live-types";
import { CheckCircle2, Circle, CircleDot, Bot } from "lucide-react";
import type { SessionWorkbenchSnapshot, WorkbenchTask } from "./session-workbench-api";
import { WorkbenchBadge, WorkbenchEmpty, WorkbenchSection, useWorkbenchText } from "./workbench-parts";

function TaskRow({ task }: { task: WorkbenchTask }) {
  const text = useWorkbenchText();
  const labels = {
    pending: text("待开始", "Pending"), in_progress: text("进行中", "In progress"),
    completed: text("已完成", "Completed"), blocked: text("受阻", "Blocked"), cancelled: text("已取消", "Cancelled"),
  };
  const Icon = task.status === "completed" ? CheckCircle2 : task.status === "in_progress" ? CircleDot : Circle;
  return <li className="flex gap-2 py-2">
    <Icon aria-hidden className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${task.status === "completed" ? "text-emerald-600 dark:text-emerald-400" : task.status === "in_progress" ? "text-primary" : "text-muted-foreground"}`} />
    <div className="min-w-0 flex-1">
      <div className="text-xs leading-5 [overflow-wrap:anywhere]">{task.title}</div>
      <p className="mt-0.5 text-[10px] text-muted-foreground">{labels[task.status]} · {task.source === "goal" ? text("持续目标", "Sustained goal") : ["workbench_plan", "plan"].includes(task.source) ? text("会话计划", "Session plan") : task.source}</p>
      {task.detail && <p className="mt-1 text-[11px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">{task.detail}</p>}
    </div>
  </li>;
}

export function WorkbenchProgress({ snapshot, activity, stale, onOpenExecution }: {
  snapshot: SessionWorkbenchSnapshot | null; activity?: WorkbenchActivity | null; stale?: boolean; onOpenExecution?: (invocationId: string) => void;
}) {
  const text = useWorkbenchText();
  const [expanded, setExpanded] = useState(false);
  const tasks = snapshot?.plan?.steps.length ? snapshot.plan.steps : snapshot?.tasks ?? [];
  const done = tasks.filter((task) => task.status === "completed").length;
  const active = tasks.filter((task) => task.status === "in_progress").length;
  const plan = snapshot?.plan;
  const phaseLabels: Record<string, string> = { processing: text("处理中", "Processing"), tool_running: text("工具运行中", "Tool running"), answering: text("正在回复", "Answering"), waiting_user: text("等待用户", "Waiting for user"), completed: text("已完成", "Completed"), failed: text("失败", "Failed"), interrupted: text("已中断", "Interrupted"), unknown: text("状态未知", "Unknown") };
  return <>
    {(activity || stale) && <div role="status" className="border-b border-border/45 px-4 py-3 text-xs"><p>{stale ? text("连接恢复中，状态可能已过期", "Reconnecting; status may be stale") : phaseLabels[activity?.phase ?? "unknown"]}</p>{!stale && activity?.current_action && <p className="mt-1 break-words text-muted-foreground">{activity.current_action}</p>}{!!activity?.events.length && <><ul className="mt-2 space-y-1">{activity.events.slice(-(expanded ? 20 : 5)).map(event => <li key={event.id} className="break-words text-muted-foreground">{event.label} · {({ running: text("运行中", "Running"), succeeded: text("成功", "Succeeded"), failed: text("失败", "Failed"), cancelled: text("已取消", "Cancelled") })[event.status]}</li>)}</ul>{activity.events.length > 5 && <button className="mt-2 underline" onClick={() => setExpanded(!expanded)}>{expanded ? text("收起", "Show less") : text("展开近期记录", "Show recent events")}</button>}</>}</div>}
    {snapshot?.goal && <div className="border-b border-border/45 px-4 py-3">
      <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
        {text("当前目标", "Current goal")}<WorkbenchBadge>{({ active: text("进行中", "Active"), completed: text("已完成", "Completed"), blocked: text("受阻", "Blocked"), cancelled: text("已取消", "Cancelled"), paused: text("已暂停", "Paused") } as Record<string, string>)[snapshot.goal.status] || snapshot.goal.status}</WorkbenchBadge>
      </div>
      <p className="text-xs leading-5 [overflow-wrap:anywhere]">{snapshot.goal.ui_summary || snapshot.goal.objective}</p>
    </div>}
    <WorkbenchSection title={text("当前计划", "Current plan")} count={tasks.length} defaultOpen>
      {plan?.explanation && <p className="mb-2 leading-5 text-muted-foreground">{plan.explanation}</p>}
      {tasks.length ? <>
        <div className="mb-2 flex items-baseline justify-between text-[11px] text-muted-foreground">
          <span>{text("已完成", "Completed")} <strong className="text-foreground">{done}/{tasks.length}</strong></span>
          <span>{active ? `${active} ${text("进行中", "in progress")}` : text("暂无运行任务", "No running tasks")}</span>
        </div>
        <div role="progressbar" aria-label={text("任务完成进度", "Task completion")} aria-valuenow={done} aria-valuemin={0} aria-valuemax={tasks.length} className="h-1 overflow-hidden rounded bg-muted">
          <div className="h-full bg-primary/75" style={{ width: `${done / tasks.length * 100}%` }} />
        </div>
        <ul className="mt-2 divide-y divide-border/35">{tasks.map((task) => <TaskRow key={task.id} task={task} />)}</ul>
      </> : <WorkbenchEmpty>{text("还没有记录任务。Agent 更新计划后会显示在这里。", "No recorded tasks. They appear when the agent updates its plan.")}</WorkbenchEmpty>}
    </WorkbenchSection>
    {!!snapshot?.agents.length && <WorkbenchSection title={text("子 Agent", "Subagents")} count={snapshot?.agents.length ?? 0} defaultOpen>
      {snapshot?.agents.length ? <ul className="space-y-2">{snapshot.agents.map((agent) => <li key={agent.id}>
        <button type="button" disabled={!onOpenExecution || !agent.invocation_id}
          onClick={() => onOpenExecution?.(agent.invocation_id)}
          className="flex w-full items-start gap-2 rounded border border-border/50 bg-background/60 p-2 text-left enabled:hover:bg-muted/60 disabled:cursor-default">
          <Bot aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1"><p className="text-xs [overflow-wrap:anywhere]">{agent.name}</p>
            <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-muted-foreground [overflow-wrap:anywhere]">{agent.detail}</p></div>
          <WorkbenchBadge>{({ running: text("运行中", "Running"), queued: text("排队中", "Queued"), succeeded: text("已完成", "Succeeded"), completed: text("已完成", "Completed"), failed: text("失败", "Failed"), cancelled: text("已取消", "Cancelled"), pending: text("等待中", "Pending") } as Record<string, string>)[agent.status] || agent.status}</WorkbenchBadge>
        </button>
      </li>)}</ul> : <WorkbenchEmpty>{text("当前会话没有子 Agent 执行记录。", "No subagent executions in this session.")}</WorkbenchEmpty>}
    </WorkbenchSection>}
  </>;
}
