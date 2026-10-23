export type AgentType = "system" | "custom";

/** 未显式 @ 任何 Agent 时接管主回合的系统档案，与后端 catalog 保持一致。 */
export const DEFAULT_MAIN_AGENT_ID = "commander";

/**
 * How a capability list resolves to a concrete set of ids.
 * "all" ignores entries; "include" keeps only entries; "exclude" drops entries.
 */
export type SelectionMode = "all" | "include" | "exclude";

export interface AgentSelection {
  mode: SelectionMode;
  entries: string[];
}

export interface AgentProfile {
  id: string;
  name: string;
  description: string;
  type: AgentType;
  /** System presets start from the shipped default and flip to true once edited. */
  customized: boolean;
  categoryId: string | null;
  icon: string;
  /** Hex string, e.g. "#4A90D9". */
  color: string;
  prompt: string;
  /** null = follow the global model. */
  modelId: string | null;
  tools: AgentSelection;
  skills: AgentSelection;
  subAgents: AgentSelection;
  hidden: boolean;
  updatedAt: string;
}

export function isDefaultMainAgent(agent: Pick<AgentProfile, "id">): boolean {
  return agent.id === DEFAULT_MAIN_AGENT_ID;
}

export interface AgentCategory {
  id: string;
  name: string;
  color: string;
  order: number;
}
export type ToolRisk = "low" | "medium" | "high";

export interface ToolDescriptor {
  name: string;
  label: string;
  description: string;
  /** Tool category id, see TOOL_CATEGORIES. */
  category: string;
  risk: ToolRisk;
  scope: "core" | "subagent" | "plugin";
  /** Framework dependency — always on, cannot be unchecked. */
  locked?: boolean;
  /** Not usable by subagents — visible but not selectable in the profile editor. */
  blocked?: boolean;
  blockedReason?: string;
}

export interface ToolCategoryDescriptor {
  id: string;
  label: string;
}

export type SkillSource = "builtin" | "workspace" | "plugin";

export interface SkillDescriptor {
  name: string;
  description: string;
  source: SkillSource;
  tags: string[];
}

export type ModelHealth = "healthy" | "degraded" | "unavailable";

export interface ModelDescriptor {
  id: string;
  label: string;
  provider: string;
  contextWindow: number;
  health: ModelHealth;
  /** Shown when health is not "healthy". */
  statusNote?: string;
  vision: boolean;
  toolUse: boolean;
}

export interface PromptContext {
  name: string;
  description: string;
  skills: string[];
  tools: string[];
  model: string;
  date: string;
  userProfile: string;
  workspace: string;
}

export interface PromptVariable {
  token: string;
  label: string;
}

export const AGENT_CATEGORIES: AgentCategory[] = [
  { id: "general", name: "通用", color: "#4A90D9", order: 0 },
  { id: "coding", name: "编码", color: "#8E44AD", order: 1 },
  { id: "writing", name: "写作", color: "#E67E22", order: 2 },
  { id: "research", name: "研究", color: "#16A085", order: 3 },
  { id: "ops", name: "运维", color: "#C0392B", order: 4 },
  { id: "efficiency", name: "效率", color: "#2C3E50", order: 5 },
];

export const TOOL_CATEGORIES: ToolCategoryDescriptor[] = [
  { id: "filesystem", label: "文件系统" },
  { id: "execution", label: "执行与进程" },
  { id: "web", label: "网络与搜索" },
  { id: "memory", label: "记忆" },
  { id: "scheduling", label: "调度与定时" },
  { id: "session", label: "会话" },
  { id: "orchestration", label: "编排" },
  { id: "media", label: "媒体" },
];
export const PROMPT_VARIABLES: PromptVariable[] = [
  { token: "{{name}}", label: "Agent 名称" },
  { token: "{{description}}", label: "Agent 描述" },
  { token: "{{skills}}", label: "已启用技能列表" },
  { token: "{{tools}}", label: "已启用工具列表" },
  { token: "{{model}}", label: "当前模型" },
  { token: "{{date}}", label: "今天日期" },
  { token: "{{user_profile}}", label: "用户档案" },
  { token: "{{workspace}}", label: "工作区名称" },
];

export const PROMPT_MAX_LENGTH = 5000;

export const AGENT_COLOR_PRESETS: string[] = [
  "#4A90D9",
  "#8E44AD",
  "#E67E22",
  "#16A085",
  "#C0392B",
  "#2C3E50",
  "#D4AF37",
  "#27AE60",
];

export function categoryById(id: string | null | undefined): AgentCategory | null {
  if (!id) return null;
  return AGENT_CATEGORIES.find((item) => item.id === id) ?? null;
}

/** Locked ids are always present, whichever mode is active. */
export function resolveSelection(
  selection: AgentSelection,
  allIds: string[],
  lockedIds: string[] = [],
): Set<string> {
  const locked = new Set(lockedIds);
  if (selection.mode === "all") return new Set([...allIds, ...locked]);
  const result = new Set(locked);
  for (const id of allIds) {
    if (selection.mode === "include") {
      if (selection.entries.includes(id)) result.add(id);
    } else if (!selection.entries.includes(id)) {
      result.add(id);
    }
  }
  return result;
}

export function isSelectionDirty(selection: AgentSelection, allIds: string[]): boolean {
  if (selection.mode !== "all") return selection.entries.length > 0;
  return allIds.length === 0;
}

/** Ids reachable from `rootId` through sub-agent edges, excluding `rootId` itself. */
function descendantsOf(agents: AgentProfile[], rootId: string): Set<string> {
  const byId = new Map(agents.map((agent) => [agent.id, agent]));
  const allIds = agents.map((agent) => agent.id);
  const found = new Set<string>();
  const walk = (id: string) => {
    const node = byId.get(id);
    if (!node) return;
    for (const childId of resolveSelection(node.subAgents, allIds)) {
      if (childId === rootId || found.has(childId)) continue;
      found.add(childId);
      walk(childId);
    }
  };
  walk(rootId);
  return found;
}

/**
 * Sub-agent ids that must not be selectable from `editingId` because picking them
 * would close a cycle. Includes the agent itself.
 */
export function cycleBlockedIds(agents: AgentProfile[], editingId: string | null): Set<string> {
  if (!editingId) return new Set<string>();
  const blocked = new Set<string>([editingId]);
  for (const agent of agents) {
    if (agent.id === editingId) continue;
    if (descendantsOf(agents, agent.id).has(editingId)) blocked.add(agent.id);
  }
  return blocked;
}
