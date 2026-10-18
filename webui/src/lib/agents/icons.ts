import {
  BookOpen,
  Bot,
  Boxes,
  Brain,
  Bug,
  ChartLine,
  CircuitBoard,
  Code,
  Compass,
  Cpu,
  Crown,
  Database,
  Feather,
  Gauge,
  GitBranch,
  Globe,
  GraduationCap,
  Hammer,
  Layers,
  Microscope,
  Orbit,
  Palette,
  PenTool,
  Puzzle,
  Rocket,
  Satellite,
  Search,
  Shield,
  Sparkles,
  Sword,
  Target,
  Telescope,
  Terminal,
  WandSparkles,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";

/** 内置默认 Agent 图标，与 Agent 编辑器里的兜底保持一致。 */
export const DEFAULT_AGENT_ICON = "/icons/agent-bot.svg";

export const LUCIDE_ICON_PREFIX = "lucide:";

const LUCIDE_ICONS: Record<string, LucideIcon> = {
  "book-open": BookOpen,
  bot: Bot,
  boxes: Boxes,
  brain: Brain,
  bug: Bug,
  "chart-line": ChartLine,
  "circuit-board": CircuitBoard,
  code: Code,
  compass: Compass,
  cpu: Cpu,
  crown: Crown,
  database: Database,
  feather: Feather,
  gauge: Gauge,
  "git-branch": GitBranch,
  globe: Globe,
  "graduation-cap": GraduationCap,
  hammer: Hammer,
  layers: Layers,
  microscope: Microscope,
  orbit: Orbit,
  palette: Palette,
  "pen-tool": PenTool,
  puzzle: Puzzle,
  rocket: Rocket,
  satellite: Satellite,
  search: Search,
  shield: Shield,
  sparkles: Sparkles,
  sword: Sword,
  target: Target,
  telescope: Telescope,
  terminal: Terminal,
  "wand-sparkles": WandSparkles,
  workflow: Workflow,
  wrench: Wrench,
};

export interface LucideAgentIconGroup {
  id: string;
  label: string;
  items: string[];
}

/** 图标选择器用的 Lucide 分组；`commander` 默认的 compass 排在最前。 */
export const LUCIDE_AGENT_ICON_GROUPS: LucideAgentIconGroup[] = [
  {
    id: "command",
    label: "指挥与目标",
    items: ["compass", "crown", "target", "rocket", "shield", "sword", "orbit", "satellite"],
  },
  {
    id: "thinking",
    label: "思考与创作",
    items: ["brain", "sparkles", "wand-sparkles", "puzzle", "feather", "pen-tool", "palette", "book-open"],
  },
  {
    id: "engineering",
    label: "工程与工具",
    items: [
      "code",
      "terminal",
      "git-branch",
      "bug",
      "wrench",
      "hammer",
      "database",
      "workflow",
    ],
  },
  {
    id: "science",
    label: "探索与系统",
    items: [
      "search",
      "telescope",
      "microscope",
      "graduation-cap",
      "globe",
      "layers",
      "boxes",
      "circuit-board",
    ],
  },
  {
    id: "runtime",
    label: "运行与度量",
    items: ["bot", "cpu", "gauge", "chart-line"],
  },
];

export const LUCIDE_AGENT_ICON_NAMES = LUCIDE_AGENT_ICON_GROUPS.flatMap(
  (group) => group.items,
);

export function agentIconLucideName(icon: string | null | undefined): string | null {
  const value = (icon ?? "").trim();
  if (!value.toLowerCase().startsWith(LUCIDE_ICON_PREFIX)) return null;
  const name = value.slice(LUCIDE_ICON_PREFIX.length).toLowerCase();
  return name in LUCIDE_ICONS ? name : null;
}

export function agentIconLucideComponent(
  icon: string | null | undefined,
): LucideIcon | null {
  const name = agentIconLucideName(icon);
  return name ? LUCIDE_ICONS[name] : null;
}

export function lucideAgentIconToken(name: string): string {
  return `${LUCIDE_ICON_PREFIX}${name}`;
}

/** 任意非空字符串都可能是 emoji 或图片路径，只有空值才回退默认图标。 */
export function hasCustomAgentIcon(icon: string | null | undefined): boolean {
  return Boolean((icon ?? "").trim());
}
