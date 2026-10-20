import { useTranslation } from "react-i18next";
import { ArrowUpRight, BookOpen, Cpu, Wrench } from "lucide-react";
import type { AgentProfile, ModelDescriptor, SkillDescriptor, ToolDescriptor } from "@/lib/agents/types";

export function AgentRailSummary({ profile, modelId, tools, skills, models, enabledToolIds, enabledSkillIds, globalModelLabel, onNavigate }: {
  profile?: AgentProfile;
  modelId: string | null;
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  enabledToolIds: Set<string>;
  enabledSkillIds: Set<string>;
  globalModelLabel?: string;
  onNavigate?: (tab: "identity" | "capabilities" | "more", capability?: "tools" | "skills") => void;
}) {
  const { t } = useTranslation();
  const enabledTools = tools.filter((tool) => !tool.blocked && enabledToolIds.has(tool.name));
  const enabledSkills = skills.filter((skill) => enabledSkillIds.has(skill.name));
  const model = models.find((item) => item.id === modelId);
  const modelLabel = modelId ? model?.label ?? t("settings.agents.workspace.unavailableModel", "原模型当前不可用") : globalModelLabel || t("settings.agents.workspace.globalModel", "跟随全局模型");
  return <div className="agent-editor-summary">
    <h4 className="agent-editor-summary-heading"><img src="/icons/agent-bot.svg" width="18" height="18" alt="" />{t("settings.agents.workspace.summary", "配置摘要")}</h4>
    <div className="agent-editor-illustration" aria-hidden="true"><img src="/illustrations/zhixu-toolbox.webp" width="164" height="109" alt="" /></div>
    {profile ? <div className="agent-editor-summary-identity"><p className="font-semibold text-foreground">{profile.name.trim() || t("settings.agents.workspace.unnamed", "尚未填写名称")}</p><p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{profile.description.trim() || t("settings.agents.workspace.noDescription", "补充一句话，说明它负责什么。")}</p></div> : null}
    <button type="button" className="agent-editor-summary-model" onClick={() => onNavigate?.("more")}><Cpu className="h-4 w-4 shrink-0" aria-hidden /><span className="min-w-0 flex-1"><span className="block text-[11px] text-muted-foreground">{modelId ? t("settings.agents.rail.model", "模型") : t("settings.agents.workspace.globalModel", "跟随全局模型")}</span><span className="mt-1 block break-words text-xs font-medium">{modelLabel}</span></span><ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden /></button>
    <div className="agent-editor-summary-capabilities">{(["tools", "skills"] as const).map((kind) => {
      const items = kind === "tools" ? enabledTools.map((item) => item.label) : enabledSkills.map((item) => item.name);
      const Icon = kind === "tools" ? Wrench : BookOpen;
      return <section key={kind}><button type="button" className="agent-editor-summary-link" onClick={() => onNavigate?.("capabilities", kind)}><Icon className="h-3.5 w-3.5" aria-hidden /><span>{t(`settings.agents.noun.${kind}`, kind === "tools" ? "工具" : "技能")}</span><strong className="ml-auto text-foreground">{items.length}</strong><ArrowUpRight className="h-3 w-3" aria-hidden /></button>{items.length ? <><ul className="agent-editor-summary-list">{items.slice(0, 3).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>{items.length > 3 ? <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.agents.workspace.remaining", "以及其他 {{count}} 项", { count: items.length - 3 })}</p> : null}</> : <p className="mt-2 text-xs text-muted-foreground">{kind === "tools" ? t("settings.agents.workspace.noTools", "未选择工具") : t("settings.agents.workspace.noSkills", "未添加技能")}</p>}</section>;
    })}</div>
  </div>;
}
