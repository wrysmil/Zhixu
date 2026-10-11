import { type ReactNode, useState } from "react";
import {
  ArrowLeft,
  Blocks,
  Bot,
  Brain,
  Check,
  FilePlus2,
  FolderInput,
  Layers,
  ListChecks,
  MessageSquarePlus,
  Minimize2,
  Palette,
  Plug,
  Plus,
  Shield,
  Target,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { FeedbackPopover } from "@/components/thread/FeedbackPopover";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { AgentProfile } from "@/lib/agents/types";
import type { ModelPresetOption } from "@/components/thread/ModelPresetBadge";
import type {
  CliAppInfo,
  McpPresetInfo,
  SkillSummary,
  WorkspaceAccessMode,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Plus-menu row model (contract §4): every row is either a token insertion
 * into the composer, a direct action, or a second-level popup selection.
 */
type PlusRowMeta = {
  id: string;
  icon: ReactNode;
  title: string;
  description?: string;
  disabled?: boolean;
  statusDot?: boolean;
};

type PlusOption = {
  id: string;
  label: string;
  current?: boolean;
  disabled?: boolean;
};

type PlusRow =
  | (PlusRowMeta & { kind: "insert-token"; token: string })
  | (PlusRowMeta & { kind: "action"; run: () => void })
  | (PlusRowMeta & {
      kind: "popup-select";
      options: PlusOption[];
      onSelect: (id: string) => void;
    });

interface ComposerPlusMenuProps {
  isHero: boolean;
  /** Trigger disabled (composer not interactive). */
  disabled?: boolean;
  /** "Files and folders" disabled (attachment limit reached). */
  filesDisabled?: boolean;
  skills: SkillSummary[];
  plugins: CliAppInfo[];
  mcpPresets: McpPresetInfo[];
  agents: AgentProfile[];
  canWorkInProject: boolean;
  onPickFiles: () => void;
  onWorkInProject: () => void;
  onInsertToken: (token: string) => void;
  /** False in the hero composer (no active session) → compact/feedback disabled. */
  hasSession?: boolean;
  /** Runs a slash command (``/compact``, ``/model <id>``, ``/feedback <text>``). */
  onRunCommand?: (command: string) => void;
  /** Current workspace permission preset; null disables the permission row. */
  accessMode?: WorkspaceAccessMode | null;
  canUseFullAccess?: boolean;
  onSetAccessMode?: (mode: WorkspaceAccessMode) => void;
  /** Model presets for the model popup (``SettingsPayload.model_presets``). */
  modelPresets?: readonly ModelPresetOption[];
  currentModelPreset?: string | null;
  /** Shows a status dot on the goal row while a sustained goal is active. */
  goalActive?: boolean;
  /** Shows a status dot on the plan row while plan mode is active. */
  planActive?: boolean;
}

export function ComposerPlusMenu({
  isHero,
  disabled = false,
  filesDisabled = false,
  skills,
  plugins,
  mcpPresets,
  agents,
  canWorkInProject,
  onPickFiles,
  onWorkInProject,
  onInsertToken,
  hasSession = true,
  onRunCommand,
  accessMode = null,
  canUseFullAccess = true,
  onSetAccessMode,
  modelPresets = [],
  currentModelPreset = null,
  goalActive = false,
  planActive = false,
}: ComposerPlusMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [popupRowId, setPopupRowId] = useState<string | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  const closeMenu = () => {
    setOpen(false);
    setPopupRowId(null);
  };

  const run = (action: () => void) => {
    closeMenu();
    action();
  };

  const visibleSkills = skills.filter(
    (skill) => skill.enabled !== false && skill.available,
  );
  const installedPlugins = plugins.filter((app) => app.installed);
  const installedMcp = mcpPresets.filter(
    (preset) => preset.installed && preset.configured,
  );

  const addRows: PlusRow[] = [
    {
      kind: "action",
      id: "files",
      icon: <FilePlus2 className="h-4 w-4" />,
      title: t("thread.composer.menu.files"),
      description: t("thread.composer.menu.filesDesc"),
      disabled: filesDisabled,
      run: onPickFiles,
    },
    ...(canWorkInProject
      ? [
          {
            kind: "action" as const,
            id: "project",
            icon: <FolderInput className="h-4 w-4" />,
            title: t("thread.composer.menu.project"),
            description: t("thread.composer.menu.projectDesc"),
            run: onWorkInProject,
          },
        ]
      : []),
    {
      kind: "insert-token",
      id: "goal",
      icon: <Target className="h-4 w-4" />,
      title: t("thread.composer.menu.goal"),
      description: t("thread.composer.menu.goalDesc"),
      token: "/goal",
      statusDot: goalActive,
    },
    {
      kind: "insert-token",
      id: "plan",
      icon: <ListChecks className="h-4 w-4" />,
      title: t("thread.composer.menu.plan"),
      description: t("thread.composer.menu.planDesc"),
      token: "/plan",
      statusDot: planActive,
    },
    {
      kind: "insert-token",
      id: "draw",
      icon: <Palette className="h-4 w-4" />,
      title: t("thread.composer.menu.draw"),
      description: t("thread.composer.menu.drawDesc"),
      token: "$image-generation",
    },
  ];

  const commandRows: PlusRow[] = [
    {
      kind: "action",
      id: "compact",
      icon: <Minimize2 className="h-4 w-4" />,
      title: t("plusMenu.compact"),
      description: t("plusMenu.compactDesc"),
      disabled: !hasSession || !onRunCommand,
      run: () => onRunCommand?.("/compact"),
    },
    {
      kind: "popup-select",
      id: "permission",
      icon: <Shield className="h-4 w-4" />,
      title: t("plusMenu.permission"),
      description: t("plusMenu.permissionDesc"),
      disabled: accessMode == null || !onSetAccessMode,
      options: [
        {
          id: "restricted",
          label: t("thread.composer.workspace.default"),
          current: accessMode === "restricted",
        },
        {
          id: "full",
          label: t("thread.composer.workspace.full"),
          current: accessMode === "full",
          disabled: !canUseFullAccess,
        },
      ],
      onSelect: (id) => onSetAccessMode?.(id as WorkspaceAccessMode),
    },
    {
      kind: "popup-select",
      id: "model",
      icon: <Layers className="h-4 w-4" />,
      title: t("plusMenu.model"),
      description: t("plusMenu.modelDesc"),
      disabled: modelPresets.length === 0 || !onRunCommand,
      options: modelPresets.map((preset) => ({
        id: preset.name,
        label: preset.name,
        current: preset.name === currentModelPreset,
      })),
      onSelect: (id) => onRunCommand?.(`/model ${id}`),
    },
    {
      kind: "action",
      id: "feedback",
      icon: <MessageSquarePlus className="h-4 w-4" />,
      title: t("plusMenu.feedback"),
      description: t("plusMenu.feedbackDesc"),
      disabled: !hasSession || !onRunCommand,
      run: () => setFeedbackOpen(true),
    },
  ];

  const activePopupRow =
    popupRowId != null
      ? commandRows.find(
          (row): row is PlusRow & { kind: "popup-select" } =>
            row.id === popupRowId && row.kind === "popup-select",
        )
      : undefined;

  const renderRow = (row: PlusRow) => (
    <MenuItem
      key={row.id}
      icon={row.icon}
      title={row.title}
      description={row.description}
      disabled={row.disabled}
      statusDot={row.statusDot}
      onClick={() => {
        if (row.disabled) return;
        if (row.kind === "insert-token") {
          run(() => onInsertToken(row.token));
        } else if (row.kind === "action") {
          run(row.run);
        } else {
          setPopupRowId(row.id);
        }
      }}
    />
  );

  return (
    <>
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setPopupRowId(null);
        }}
      >
        <PopoverTrigger asChild>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={disabled}
            aria-label={t("thread.composer.menu.aria")}
            aria-haspopup="menu"
            aria-expanded={open}
            className={cn(
              "thread-composer-action touch-target rounded-full text-muted-foreground hover:text-foreground",
              isHero
                ? "h-8 w-8 border border-border/55 bg-card shadow-[0_2px_8px_rgba(15,23,42,0.05)] hover:bg-card"
                : "h-9 w-9 border border-border/55 bg-card shadow-[0_2px_8px_rgba(15,23,42,0.05)] hover:bg-card",
              open && "bg-card text-foreground",
            )}
          >
            <Plus className={cn(isHero ? "h-[18px] w-[18px]" : "h-4 w-4")} />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          side="top"
          align="start"
          sideOffset={8}
          className="w-[21rem] max-w-[calc(100vw-2rem)] rounded-prominent border-border/70 bg-popover p-1.5 shadow-[0_12px_36px_rgba(15,23,42,0.14)]"
        >
          {activePopupRow ? (
            <PopupSelectView
              title={activePopupRow.title}
              options={activePopupRow.options}
              onBack={() => setPopupRowId(null)}
              onSelect={(id) => run(() => activePopupRow.onSelect(id))}
            />
          ) : (
            <>
              <MenuSection label={t("plusMenu.groupAdd")}>
                {addRows.map(renderRow)}
              </MenuSection>
              <MenuSection label={t("plusMenu.groupCommands")}>
                {commandRows.map(renderRow)}
              </MenuSection>
              <MenuSection
                label={t("thread.composer.menu.skills")}
                emptyLabel={t("thread.composer.menu.noSkills")}
                empty={visibleSkills.length === 0}
              >
                {visibleSkills.map((skill) => (
                  <MenuItem
                    key={`skill:${skill.name}`}
                    icon={<Brain className="h-4 w-4" />}
                    title={skill.name}
                    description={skill.description || undefined}
                    onClick={() => run(() => onInsertToken(`$${skill.name}`))}
                  />
                ))}
              </MenuSection>
              <MenuSection
                label={t("thread.composer.menu.plugins")}
                emptyLabel={t("thread.composer.menu.noPlugins")}
                empty={installedPlugins.length === 0}
              >
                {installedPlugins.map((app) => (
                  <MenuItem
                    key={`plugin:${app.name}`}
                    icon={<Blocks className="h-4 w-4" />}
                    title={app.display_name || app.name}
                    description={app.description || undefined}
                    onClick={() => run(() => onInsertToken(`@${app.name}`))}
                  />
                ))}
              </MenuSection>
              <MenuSection
                label={t("thread.composer.menu.mcp")}
                emptyLabel={t("thread.composer.menu.noMcp")}
                empty={installedMcp.length === 0}
              >
                {installedMcp.map((preset) => (
                  <MenuItem
                    key={`mcp:${preset.name}`}
                    icon={<Plug className="h-4 w-4" />}
                    title={preset.display_name || preset.name}
                    description={preset.description || undefined}
                    onClick={() => run(() => onInsertToken(`@${preset.name}`))}
                  />
                ))}
              </MenuSection>
              <MenuSection
                label={t("thread.composer.menu.agents")}
                emptyLabel={t("thread.composer.menu.noAgents")}
                empty={agents.length === 0}
              >
                {agents.map((agent) => (
                  <MenuItem
                    key={`agent:${agent.id}`}
                    icon={<Bot className="h-4 w-4" />}
                    title={agent.name}
                    description={agent.description || undefined}
                    onClick={() =>
                      run(() =>
                        onInsertToken(
                          t("thread.composer.menu.agentInsert", {
                            name: agent.name,
                            defaultValue: "Run this with the agent \"{{name}}\": ",
                          }),
                        ),
                      )}
                  />
                ))}
              </MenuSection>
            </>
          )}
        </PopoverContent>
      </Popover>
      <FeedbackPopover
        open={feedbackOpen}
        onOpenChange={setFeedbackOpen}
        onSubmit={(text) => {
          setFeedbackOpen(false);
          onRunCommand?.("/feedback " + text.replace(/\n/g, " ⏎ "));
        }}
      />
    </>
  );
}

function PopupSelectView({
  title,
  options,
  onBack,
  onSelect,
}: {
  title: string;
  options: PlusOption[];
  onBack: () => void;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="pb-1">
      <div className="flex items-center gap-1 px-1 pb-1 pt-1">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("plusMenu.back")}
          onClick={onBack}
          className="h-7 w-7 rounded-full text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <span className="min-w-0 truncate text-[12.5px] font-semibold text-foreground">
          {title}
        </span>
      </div>
      <div className="space-y-0.5">
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            role="menuitemradio"
            aria-checked={option.current === true}
            disabled={option.disabled}
            onClick={() => onSelect(option.id)}
            className={cn(
              "flex w-full min-w-0 items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              option.disabled
                ? "cursor-not-allowed opacity-50"
                : "hover:bg-muted/70 dark:hover:bg-white/[0.06]",
            )}
          >
            <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-foreground">
              {option.label}
            </span>
            {option.current ? (
              <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            ) : null}
          </button>
        ))}
      </div>
    </div>
  );
}

function MenuSection({
  label,
  children,
  empty = false,
  emptyLabel,
}: {
  label: string;
  children: ReactNode;
  empty?: boolean;
  emptyLabel?: string;
}) {
  return (
    <div className="pb-1 last:pb-0">
      <div className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground/70">
        {label}
      </div>
      {empty ? (
        <div className="px-2 py-1 text-[12px] text-muted-foreground/65">
          {emptyLabel}
        </div>
      ) : (
        <div className="space-y-0.5">{children}</div>
      )}
    </div>
  );
}

function MenuItem({
  icon,
  title,
  description,
  disabled = false,
  statusDot = false,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  description?: string;
  disabled?: boolean;
  statusDot?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex w-full min-w-0 items-start gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        disabled
          ? "cursor-not-allowed opacity-50"
          : "hover:bg-muted/70 dark:hover:bg-white/[0.06]",
      )}
    >
      <span className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden>
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium text-foreground">
          {title}
          {statusDot ? (
            <span
              data-testid="status-dot"
              aria-hidden
              className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 align-middle"
            />
          ) : null}
        </span>
        {description ? (
          <span className="mt-0.5 line-clamp-2 block text-[11.5px] leading-snug text-muted-foreground">
            {description}
          </span>
        ) : null}
      </span>
    </button>
  );
}
