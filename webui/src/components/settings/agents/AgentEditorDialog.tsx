import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Bot, ChevronDown, CircleAlert, RotateCcw, SlidersHorizontal, Wrench, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { BasicsSection } from "@/components/settings/agents/parts/BasicsSection";
import {
  CapabilityPicker,
  type CapabilityItem,
} from "@/components/settings/agents/parts/CapabilityPicker";
import { ModelPicker } from "@/components/settings/agents/parts/ModelPicker";
import { PromptSection } from "@/components/settings/agents/parts/PromptSection";
import { AgentRailSummary } from "@/components/settings/agents/parts/AgentRailSummary";
import type { AgentProfile } from "@/lib/agents/types";
import type { AgentCatalog } from "@/lib/agents/catalog";
import { PROMPT_MAX_LENGTH, TOOL_CATEGORIES, resolveSelection } from "@/lib/agents/types";
import { SKILL_SOURCE_LABEL_KEY, TOOL_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";
import "./agent-editor.css";

type EditorTab = "identity" | "capabilities" | "more";
const EDITOR_TABS: EditorTab[] = ["identity", "capabilities", "more"];

export type { AgentCatalog };

export function AgentEditorDialog({
  open,
  agent,
  isNew,
  catalog,
  globalModelLabel,
  onOpenChange,
  onSave,
  onReset,
  saving = false,
}: {
  open: boolean;
  agent: AgentProfile | null;
  isNew: boolean;
  catalog: AgentCatalog;
  globalModelLabel: string;
  onOpenChange: (open: boolean) => void;
  onSave: (agent: AgentProfile) => void;
  onReset?: (agent: AgentProfile) => void;
  saving?: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<AgentProfile | null>(agent);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [activeTab, setActiveTab] = useState<EditorTab>("identity");
  const [capabilityTab, setCapabilityTab] = useState<"tools" | "skills">("tools");
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [validationFocus, setValidationFocus] = useState<"name" | "id" | "prompt" | null>(null);
  const editorId = useId();
  const tabRefs = useRef<Partial<Record<EditorTab, HTMLButtonElement | null>>>({});

  useEffect(() => {
    if (open) {
      setDraft(agent ? { ...agent } : null);
      setActiveTab("identity");
      setSummaryOpen(false);
    }
  }, [open, agent]);

  useEffect(() => {
    if (!validationFocus) return;
    const target = validationFocus === "prompt"
      ? document.getElementById(`${editorId}-identity-panel`)?.querySelector<HTMLTextAreaElement>("textarea[aria-invalid]")
      : document.getElementById(`agent-${validationFocus}`);
    if (target instanceof HTMLInputElement && target.disabled) tabRefs.current.more?.focus();
    else target?.focus();
    setValidationFocus(null);
  }, [activeTab, editorId, validationFocus]);

  const toolItems = useMemo<CapabilityItem[]>(
    () =>
      catalog.tools.map((tool) => ({
        id: tool.name,
        label: tool.label,
        description: tool.description,
        group: tool.category,
        groupLabel: tool.category,
        risk: tool.risk,
        locked: tool.locked,
        blocked: tool.blocked,
        blockedReason: tool.blockedReason,
      })),
    [catalog.tools],
  );

  const skillItems = useMemo<CapabilityItem[]>(
    () =>
      catalog.skills.map((skill) => ({
        id: skill.name,
        label: skill.name,
        description: skill.description,
        group: skill.source,
        groupLabel: skill.source,
        chips: skill.tags,
      })),
    [catalog.skills],
  );

  const patch = (changes: Partial<AgentProfile>) =>
    setDraft((prev) => (prev ? { ...prev, ...changes } : prev));

  const enabledToolIds = useMemo(
    () =>
      resolveSelection(
        draft?.tools ?? { mode: "all", entries: [] },
        // 只算子 Agent 实际能拿到的工具。core-only 工具勾了也会在运行时
        // 被丢弃（后端 tool_names 与 subagent scope 求交），算进来会让
        // 「已启用」这一栏与实际生效集不一致。
        catalog.tools.filter((tool) => !tool.blocked).map((tool) => tool.name),
        catalog.tools.filter((tool) => tool.locked && !tool.blocked).map((tool) => tool.name),
      ),
    [draft?.tools, catalog.tools],
  );

  const enabledSkillIds = useMemo(
    () =>
      resolveSelection(
        draft?.skills ?? { mode: "all", entries: [] },
        catalog.skills.map((skill) => skill.name),
      ),
    [draft?.skills, catalog.skills],
  );

  if (!draft) return null;

  const dirty = JSON.stringify(draft) !== JSON.stringify(agent);
  const validationError = !draft.name.trim()
    ? { field: "name" as const, message: t("settings.agents.workspace.nameRequired", "请先填写名称") }
    : !draft.id.trim()
      ? { field: "id" as const, message: t("settings.agents.workspace.idRequired", "智能体 ID 不能为空") }
      : draft.prompt.length > PROMPT_MAX_LENGTH
        ? { field: "prompt" as const, message: t("settings.agents.workspace.promptLimit", { count: PROMPT_MAX_LENGTH, defaultValue: "工作指令不能超过 {{count}} 字。" }) }
        : null;
  const canSave = !validationError;

  const requestClose = () => {
    if (dirty) setConfirmDiscard(true);
    else onOpenChange(false);
  };

  const guard = (event: Event) => {
    event.preventDefault();
    requestClose();
  };

  const navigateTab = (event: KeyboardEvent<HTMLButtonElement>, tab: EditorTab) => {
    let next: EditorTab | undefined;
    const index = EDITOR_TABS.indexOf(tab);
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = EDITOR_TABS[(index + 1) % EDITOR_TABS.length];
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = EDITOR_TABS[(index + EDITOR_TABS.length - 1) % EDITOR_TABS.length];
    if (event.key === "Home") next = EDITOR_TABS[0];
    if (event.key === "End") next = EDITOR_TABS[EDITOR_TABS.length - 1];
    if (next) {
      event.preventDefault();
      setActiveTab(next);
      tabRefs.current[next]?.focus();
    }
  };
  const tabLabels = {
    identity: t("settings.agents.workspace.identity", "职责与身份"),
    capabilities: t("settings.agents.workspace.capabilities", "工具与技能"),
    more: t("settings.agents.workspace.more", "模型与更多"),
  };
  const tabIcons = { identity: Bot, capabilities: Wrench, more: SlidersHorizontal };

  return (
    <>
      <Dialog open={open} onOpenChange={(next) => !next && requestClose()}>
        <DialogContent
          showCloseButton={false}
          onEscapeKeyDown={guard}
          onInteractOutside={guard}
          className="agent-editor flex w-full flex-col gap-0 overflow-hidden p-0"
        >
          <header className="agent-editor-header flex shrink-0 items-center gap-3">
            <span
              aria-hidden
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control text-[20px]"
              style={{ backgroundColor: `${draft.color}1a` }}
            >
              {draft.icon || <img src="/icons/agent-bot.svg" width="24" height="24" alt="" />}
            </span>
            <div className="min-w-0 flex-1">
              <DialogTitle className="truncate text-[19px] font-semibold tracking-tight">
                {isNew
                  ? t("settings.agents.editor.new", "新建 Agent")
                  : t("settings.agents.editor.edit", "编辑 · {{name}}", { name: draft.name })}
              </DialogTitle>
              <DialogDescription className="mt-1 text-[12px]">
                {t("settings.agents.workspace.subtitle", "先写清职责，再为它选择合适的能力。")}
              </DialogDescription>
            </div>
            {draft.type === "system" && onReset ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onReset(draft)}
                className="shrink-0 gap-1.5 text-[12px] text-muted-foreground"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                {t("settings.agents.editor.reset", "重置为默认")}
              </Button>
            ) : null}
            <button
              type="button"
              aria-label={t("common.close", "关闭")}
              onClick={requestClose}
              className="shrink-0 rounded-sm p-1 text-muted-foreground opacity-70 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </header>

          <div className="agent-editor-body scrollbar-thin">
            <nav className="agent-editor-nav" role="tablist" aria-label={t("settings.agents.workspace.summary", "配置摘要")}>
              {EDITOR_TABS.map((tab) => {
                const Icon = tabIcons[tab];
                return <button key={tab} ref={(element) => { tabRefs.current[tab] = element; }} id={`${editorId}-${tab}-tab`} type="button" role="tab" aria-selected={activeTab === tab} aria-controls={`${editorId}-${tab}-panel`} tabIndex={activeTab === tab ? 0 : -1} onClick={() => setActiveTab(tab)} onKeyDown={(event) => navigateTab(event, tab)} className="agent-editor-nav-item">
                  <Icon className="h-4 w-4 shrink-0" aria-hidden />
                  <span>{tabLabels[tab]}</span>
                </button>;
              })}
            </nav>
            <main className="agent-editor-main">
              <section role="tabpanel" id={`${editorId}-${activeTab}-panel`} aria-labelledby={`${editorId}-${activeTab}-tab`} className="agent-editor-panel">
                <div className="agent-editor-section-heading">
                  <h3>{tabLabels[activeTab]}</h3>
                  <p>{activeTab === "identity" ? t("settings.agents.workspace.identityHint", "给它一个明确的角色，以及做事的方法。") : activeTab === "capabilities" ? t("settings.agents.workspace.capabilitiesHint", "按需选择可执行的操作和可参考的做事方法。") : t("settings.agents.workspace.moreHint", "选择模型，整理档案的分类与外观。")}</p>
                </div>
                {activeTab === "identity" ? <div className="space-y-6">
                  <BasicsSection profile={draft} categories={catalog.categories} isNew={isNew} onChange={patch} variant="identity" />
                  <div className="agent-editor-prompt space-y-2">
                    <h4 className="text-[13px] font-medium">{t("settings.agents.prompt.label", "工作指令")}</h4>
                    <PromptSection profile={draft} onChange={(prompt) => patch({ prompt })} />
                  </div>
                </div> : activeTab === "capabilities" ? <div className="space-y-4">
                  <div className="agent-editor-capability-tabs" role="tablist" aria-label={tabLabels.capabilities}>
                    {(["tools", "skills"] as const).map((tab) => <button type="button" role="tab" key={tab} id={`${editorId}-${tab}-tab`} aria-controls={`${editorId}-${tab}-panel`} aria-selected={capabilityTab === tab} tabIndex={capabilityTab === tab ? 0 : -1} onClick={() => setCapabilityTab(tab)} onKeyDown={(event) => {
                      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
                        event.preventDefault();
                        const next = event.key === "Home" ? "tools" : event.key === "End" ? "skills" : tab === "tools" ? "skills" : "tools";
                        setCapabilityTab(next);
                        document.getElementById(`${editorId}-${next}-tab`)?.focus();
                      }
                    }}>{t(`settings.agents.noun.${tab}`, tab === "tools" ? "工具" : "技能")}<span>{tab === "tools" ? enabledToolIds.size : enabledSkillIds.size}</span></button>)}
                  </div>
                  <div role="tabpanel" id={`${editorId}-${capabilityTab}-panel`} aria-labelledby={`${editorId}-${capabilityTab}-tab`} className="space-y-4">
                    <p className="text-xs leading-relaxed text-muted-foreground">{capabilityTab === "tools" ? t("settings.agents.workspace.toolsHint", "允许它执行的操作，比如读文件、搜索网页。") : t("settings.agents.workspace.skillsHint", "完成某类任务时可参考的做事方法。")}</p>
                    {capabilityTab === "tools" ? <CapabilityPicker key="tools" items={toolItems} selection={draft.tools} onChange={(tools) => patch({ tools })} modeLabel={t("settings.agents.tools.modeLabel", "搜索工具")} groupLabelFor={(id) => t(TOOL_CATEGORY_LABEL_KEY[id] ?? id, TOOL_CATEGORIES.find((category) => category.id === id)?.label ?? id)} /> : <CapabilityPicker key="skills" items={skillItems} selection={draft.skills} onChange={(skills) => patch({ skills })} modeLabel={t("settings.agents.skills.modeLabel", "搜索技能")} groupLabelFor={(id) => t(SKILL_SOURCE_LABEL_KEY[id as keyof typeof SKILL_SOURCE_LABEL_KEY], id)} />}
                  </div>
                  <div hidden role="tabpanel" id={`${editorId}-${capabilityTab === "tools" ? "skills" : "tools"}-panel`} aria-labelledby={`${editorId}-${capabilityTab === "tools" ? "skills" : "tools"}-tab`} />
                </div> : <div className="space-y-8">
                  <ModelPicker models={catalog.models} value={draft.modelId} onChange={(modelId) => patch({ modelId })} globalModelLabel={globalModelLabel} />
                  <div className="space-y-4 border-t border-border/60 pt-6">
                    <h4 className="text-sm font-medium">{t("settings.agents.workspace.profileDetails", "档案信息")}</h4>
                    <BasicsSection profile={draft} categories={catalog.categories} isNew={isNew} onChange={patch} variant="details" />
                  </div>
                </div>}
              </section>
              {EDITOR_TABS.filter((tab) => tab !== activeTab).map((tab) => <section key={tab} hidden role="tabpanel" id={`${editorId}-${tab}-panel`} aria-labelledby={`${editorId}-${tab}-tab`} />)}
            </main>
            <div className="agent-editor-mobile-summary"><button type="button" onClick={() => setSummaryOpen(!summaryOpen)} aria-expanded={summaryOpen} aria-controls={`${editorId}-summary`}><Bot className="h-4 w-4" aria-hidden />{t("settings.agents.workspace.summaryToggle", "查看配置摘要")}<ChevronDown className={cn("ml-auto h-4 w-4", summaryOpen && "rotate-180")} aria-hidden /></button></div>
            <aside id={`${editorId}-summary`} className={cn("agent-editor-rail", summaryOpen && "is-open")}>
              <AgentRailSummary profile={draft} modelId={draft.modelId} tools={catalog.tools} skills={catalog.skills} models={catalog.models} enabledToolIds={enabledToolIds} enabledSkillIds={enabledSkillIds} globalModelLabel={globalModelLabel} onNavigate={(tab, kind) => { setActiveTab(tab); if (kind) setCapabilityTab(kind); tabRefs.current[tab]?.focus(); }} />
            </aside>
          </div>

          <footer
            className={cn(
              "agent-editor-footer flex shrink-0 flex-wrap items-center gap-3 border-t border-border/60",
            )}
          >
            <div className="min-w-0 flex-1 basis-40">
              {validationError ? (
                <button
                  id={`${editorId}-validation`}
                  type="button"
                  className="flex items-center gap-1.5 text-left text-[12px] text-destructive underline-offset-4 hover:underline"
                  onClick={() => {
                    setActiveTab(validationError.field === "id" ? "more" : "identity");
                    setValidationFocus(validationError.field);
                  }}
                >
                  <CircleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  {validationError.message}
                </button>
              ) : dirty ? (
                <p className="flex items-center gap-1.5 text-[12px] text-amber-800 dark:text-amber-200">
                  <CircleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  {t("settings.agents.editor.unsaved", "有未保存的修改")}
                </p>
              ) : null}
            </div>
            <div className="ml-auto flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={requestClose}
                className="text-[13px]"
              >
                {t("common.cancel", "取消")}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={!canSave || saving}
                aria-describedby={validationError ? `${editorId}-validation` : undefined}
                onClick={() => onSave({ ...draft, updatedAt: new Date().toISOString() })}
                className="agent-editor-save text-[13px]"
              >
                {t("settings.agents.editor.save", "保存")}
              </Button>
            </div>
          </footer>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmDiscard} onOpenChange={setConfirmDiscard}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("settings.agents.editor.discardTitle", "放弃未保存的修改？")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("settings.agents.editor.discardBody", "关闭后本次编辑不会保存。")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel", "取消")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmDiscard(false);
                onOpenChange(false);
              }}
            >
              {t("settings.agents.editor.discard", "放弃修改")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
