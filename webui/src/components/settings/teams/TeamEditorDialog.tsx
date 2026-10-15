// 团队编辑对话框：Leader + 成员 + 职责 + 并行上限。
//
// 客户端只挡「当场就能确定不合法」的四条（空名、无 Leader、无成员、成员重复/等于
// Leader）；**Leader 档案是否放行某个成员**由服务端按冻结档案判定，客户端用已有的
// `resolveSelection` 预检同一套语义，避免用户按下保存才知道越权。
// 冲突（TEAM_REVISION_CONFLICT）时保留本地草稿并提示重新加载，绝不覆盖他人编辑。
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge, FieldLabel, SectionLabel } from "@/components/settings/agents/shared";
import { resolveSelection, type AgentProfile } from "@/lib/agents/types";
import type { TeamDefinition, TeamMember } from "@/lib/teams/types";
import {
  TEAM_INSTRUCTIONS_MAX_LENGTH,
  TEAM_LIMIT_RANGES,
  TEAM_MEMBER_MAX,
  TEAM_MEMBER_ROLE_MAX_LENGTH,
  TEAM_NAME_MAX_LENGTH,
  defaultTeamLimits,
} from "@/lib/teams/types";

export interface TeamDraft {
  definition: TeamDefinition;
  expectedRevision: number | null;
}

interface Props {
  open: boolean;
  /** null 表示新建；已存在的团队带来服务端 revision，作为 expectedRevision。 */
  team: TeamDefinition | null;
  agents: AgentProfile[];
  busy: boolean;
  /** 服务端返回的错误前缀文案，显示在表单顶部而不是丢弃。 */
  serverError: string | null;
  onOpenChange: (open: boolean) => void;
  onSave: (draft: TeamDraft) => void;
}

interface Draft {
  id: string;
  name: string;
  description: string;
  instructions: string;
  leaderAgentId: string;
  members: TeamMember[];
  maxConcurrency: string;
  archived: boolean;
}

function emptyDraft(): Draft {
  return {
    id: "",
    name: "",
    description: "",
    instructions: "",
    leaderAgentId: "",
    members: [],
    maxConcurrency: String(TEAM_LIMIT_RANGES.maxConcurrency.default),
    archived: false,
  };
}

function draftFromTeam(team: TeamDefinition): Draft {
  return {
    id: team.id,
    name: team.name,
    description: team.description,
    instructions: team.instructions,
    leaderAgentId: team.leaderAgentId,
    members: team.members.map((member) => ({ ...member })),
    maxConcurrency: String(team.limits.maxConcurrency),
    archived: team.archived,
  };
}

function slugifyId(name: string): string {
  const base = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base || `team-${Date.now().toString(36)}`;
}

export function TeamEditorDialog({
  open,
  team,
  agents,
  busy,
  serverError,
  onOpenChange,
  onSave,
}: Props) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Draft>(() => (team ? draftFromTeam(team) : emptyDraft()));

  // 只在「打开」或「换了一支团队」时重建草稿。保存成功后列表会把刚落库的定义
  // 回填到 editing.team，但那仍是同一支团队：若按对象身份重建，用户在慢保存期间
  // 敲下去的字符会被基线覆盖掉。
  const teamId = team?.id ?? "";
  useEffect(() => {
    if (!open) return;
    setDraft(team ? draftFromTeam(team) : emptyDraft());
  }, [open, teamId]);

  const visibleAgents = useMemo(() => agents.filter((agent) => !agent.hidden), [agents]);
  const agentById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  const leader = draft.leaderAgentId ? agentById.get(draft.leaderAgentId) : undefined;

  const memberIds = useMemo(() => draft.members.map((member) => member.agentId), [draft.members]);

  const errors = useMemo(() => {
    const found: { field: string; message: string }[] = [];
    if (!draft.name.trim()) {
      found.push({ field: "name", message: t("settings.teams.errorName", "请填写团队名称") });
    }
    if (draft.name.length > TEAM_NAME_MAX_LENGTH) {
      found.push({
        field: "name",
        message: t("settings.teams.errorNameLength", "团队名称不能超过 {{max}} 字", {
          max: TEAM_NAME_MAX_LENGTH,
        }),
      });
    }
    if (!draft.leaderAgentId) {
      found.push({ field: "leader", message: t("settings.teams.errorLeader", "请选择 Leader") });
    }
    if (draft.members.length === 0) {
      found.push({ field: "members", message: t("settings.teams.errorMembers", "至少选择 1 个成员") });
    }
    if (draft.members.length > TEAM_MEMBER_MAX) {
      found.push({
        field: "members",
        message: t("settings.teams.errorMembersMax", "成员最多 {{max}} 个", { max: TEAM_MEMBER_MAX }),
      });
    }
    if (new Set(memberIds).size !== memberIds.length) {
      found.push({
        field: "members",
        message: t("settings.teams.errorDuplicate", "成员不能重复"),
      });
    }
    if (leader && memberIds.includes(leader.id)) {
      found.push({
        field: "members",
        message: t("settings.teams.errorLeaderIsMember", "Leader 不能同时作为成员"),
      });
    }
    if (draft.members.some((member) => !member.role.trim())) {
      found.push({
        field: "members",
        message: t("settings.teams.errorRole", "每位成员都要填写本团队职责"),
      });
    }
    // 并行上限超出区间会被服务端整条拒绝，这里按同一区间先挡住。
    const concurrency = Number(draft.maxConcurrency);
    if (
      !Number.isInteger(concurrency)
      || concurrency < TEAM_LIMIT_RANGES.maxConcurrency.min
      || concurrency > TEAM_LIMIT_RANGES.maxConcurrency.max
    ) {
      found.push({
        field: "concurrency",
        message: t("settings.teams.errorConcurrency", "最多并行需在 {{min}}–{{max}} 之间", {
          min: TEAM_LIMIT_RANGES.maxConcurrency.min,
          max: TEAM_LIMIT_RANGES.maxConcurrency.max,
        }),
      });
    }
    // 复用档案域的同一套 selection 语义：被 Leader 排除的成员不能保存。
    if (leader) {
      const allowed = resolveSelection(leader.subAgents, memberIds);
      const rejected = memberIds.filter((id) => !allowed.has(id));
      if (rejected.length > 0) {
        found.push({
          field: "members",
          message: t("settings.teams.errorLeaderExcludes", "Leader 档案不允许委派：{{ids}}", {
            ids: rejected.map((id) => agentById.get(id)?.name ?? id).join("、"),
          }),
        });
      }
    }
    return found;
  }, [agentById, draft, leader, memberIds, t]);

  const errorFor = (field: string) => errors.find((entry) => entry.field === field)?.message;
  const canSave = errors.length === 0 && !busy;

  const submit = () => {
    if (!canSave) return;
    const now = new Date().toISOString();
    const id = draft.id || slugifyId(draft.name);
    const definition: TeamDefinition = {
      id,
      // revision 由服务端推进：客户端提交的是「基于哪一版」，不是「变成哪一版」。
      revision: team ? team.revision + 1 : 1,
      name: draft.name.trim(),
      description: draft.description,
      instructions: draft.instructions,
      leaderAgentId: draft.leaderAgentId,
      members: draft.members.map((member) => ({ ...member })),
      limits: { ...defaultTeamLimits(), maxConcurrency: Number.parseInt(draft.maxConcurrency, 10) },
      archived: draft.archived,
      createdAt: team ? team.createdAt : now,
      updatedAt: now,
    };
    onSave({ definition, expectedRevision: team ? team.revision : null });
  };

  const toggleMember = (agent: AgentProfile) => {
    setDraft((prev) => {
      const exists = prev.members.some((member) => member.agentId === agent.id);
      if (exists) {
        return { ...prev, members: prev.members.filter((member) => member.agentId !== agent.id) };
      }
      return {
        ...prev,
        members: [
          ...prev.members,
          {
            memberId: `m-${slugifyId(agent.id)}`,
            agentId: agent.id,
            // 契约 §1 要求 role 至少 1 字：档案没填描述时用名字兜底，
            // 避免刚建好的档案一被选为成员就保存失败。
            role: (agent.description.trim() || agent.name).slice(0, TEAM_MEMBER_ROLE_MAX_LENGTH),
          },
        ],
      };
    });
  };

  const setRole = (agentId: string, role: string) => {
    setDraft((prev) => ({
      ...prev,
      members: prev.members.map((member) =>
        member.agentId === agentId ? { ...member, role } : member,
      ),
    }));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[min(42rem,92vw)]">
        <DialogHeader>
          <DialogTitle>
            {team ? t("settings.teams.editTitle", "编辑团队") : t("settings.teams.createTitle", "创建团队")}
          </DialogTitle>
          <DialogDescription className="mt-0.5 text-[12px]">
            {t("settings.teams.editorSubtitle", "从已有的 Agent 档案里选一位 Leader 和至多 8 位成员。")}
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
          {serverError ? (
            <div role="alert" className="rounded-panel border border-rose-300/70 bg-rose-50/70 px-3 py-2 text-[12px] text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200">
              {serverError}
            </div>
          ) : null}

          <div>
            <FieldLabel htmlFor="team-name" hint={`${draft.name.length}/${TEAM_NAME_MAX_LENGTH}`}>
              {t("settings.teams.name", "团队名称")}
            </FieldLabel>
            <Input
              id="team-name"
              value={draft.name}
              onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
              placeholder={t("settings.teams.namePlaceholder", "例如：内容团队")}
              aria-invalid={errorFor("name") ? true : undefined}
            />
            {errorFor("name") ? <p className="mt-1 text-[11px] text-rose-700">{errorFor("name")}</p> : null}
          </div>

          <div>
            <FieldLabel htmlFor="team-description">{t("settings.teams.description", "团队说明")}</FieldLabel>
            <Input
              id="team-description"
              value={draft.description}
              onChange={(event) => setDraft((prev) => ({ ...prev, description: event.target.value }))}
              placeholder={t("settings.teams.descriptionPlaceholder", "一句话说明这支团队做什么")}
            />
          </div>

          <div>
            <FieldLabel htmlFor="team-leader">{t("settings.teams.leader", "Leader")}</FieldLabel>
            <select
              id="team-leader"
              value={draft.leaderAgentId}
              onChange={(event) => setDraft((prev) => ({ ...prev, leaderAgentId: event.target.value }))}
              className="h-9 w-full rounded-md border border-border bg-background px-2 text-[13px] text-foreground outline-none"
              aria-invalid={errorFor("leader") ? true : undefined}
            >
              <option value="">{t("settings.teams.leaderPlaceholder", "选择一个 Agent 档案")}</option>
              {visibleAgents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
            {errorFor("leader") ? (
              <p className="mt-1 text-[11px] text-rose-700">{errorFor("leader")}</p>
            ) : null}
          </div>

          <div>
            <SectionLabel>
              {t("settings.teams.members", "成员")}
              <span className="ml-2 text-[11px] font-normal text-muted-foreground">
                {draft.members.length}/{TEAM_MEMBER_MAX}
              </span>
            </SectionLabel>
            <div className="mt-2 space-y-1.5">
              {visibleAgents.map((agent) => {
                const checked = memberIds.includes(agent.id);
                const member = draft.members.find((entry) => entry.agentId === agent.id);
                return (
                  <div key={agent.id} className="flex items-center gap-2">
                    <label className="flex min-w-0 flex-1 items-center gap-2 text-[13px]">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleMember(agent)}
                        aria-label={t("settings.teams.pickMember", "选择 {{name}} 为成员", {
                          name: agent.name,
                        })}
                      />
                      <span className="truncate">{agent.name}</span>
                      {agent.modelId ? <Badge tone="muted">{agent.modelId}</Badge> : null}
                    </label>
                    {checked && member ? (
                      <>
                        <Input
                          value={member.role}
                          maxLength={TEAM_MEMBER_ROLE_MAX_LENGTH}
                          onChange={(event) => setRole(agent.id, event.target.value)}
                          placeholder={t("settings.teams.rolePlaceholder", "本团队职责")}
                          aria-label={t("settings.teams.roleAria", "{{name}} 在本团队的职责", {
                            name: agent.name,
                          })}
                          className="h-8 w-[14rem]"
                        />
                        <button
                          type="button"
                          onClick={() => toggleMember(agent)}
                          aria-label={t("settings.teams.removeMember", "移除 {{name}}", {
                            name: agent.name,
                          })}
                          className="text-muted-foreground hover:text-foreground"
                        >
                          <X className="h-4 w-4" aria-hidden />
                        </button>
                      </>
                    ) : null}
                  </div>
                );
              })}
              {visibleAgents.length === 0 ? (
                <p className="text-[12px] text-muted-foreground">
                  {t("settings.teams.noAgents", "还没有可用的 Agent 档案")}
                </p>
              ) : null}
            </div>
            {errorFor("members") ? (
              <p className="mt-1 text-[11px] text-rose-700">{errorFor("members")}</p>
            ) : null}
          </div>

          <div>
            <FieldLabel htmlFor="team-instructions" hint={`${draft.instructions.length}/${TEAM_INSTRUCTIONS_MAX_LENGTH}`}>
              {t("settings.teams.instructions", "协作约定")}
            </FieldLabel>
            <textarea
              id="team-instructions"
              value={draft.instructions}
              onChange={(event) => setDraft((prev) => ({ ...prev, instructions: event.target.value }))}
              rows={4}
              placeholder={t("settings.teams.instructionsPlaceholder", "谁先做什么、交付标准是什么")}
              className="w-full rounded-md border border-border bg-background p-2 text-[13px] text-foreground outline-none"
            />
          </div>

          <div>
            <FieldLabel htmlFor="team-concurrency" hint={t("settings.teams.concurrencyHint", "同时运行的成员数上限", {
              max: TEAM_LIMIT_RANGES.maxConcurrency.max,
            })}>
              {t("settings.teams.maxConcurrency", "最多并行")}
            </FieldLabel>
            <input
              id="team-concurrency"
              type="number"
              min={TEAM_LIMIT_RANGES.maxConcurrency.min}
              max={TEAM_LIMIT_RANGES.maxConcurrency.max}
              value={draft.maxConcurrency}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, maxConcurrency: event.target.value }))
              }
              aria-invalid={errorFor("concurrency") ? true : undefined}
              className="h-9 w-24 rounded-md border border-border bg-background px-2 text-[13px]"
            />
            {errorFor("concurrency") ? (
              <p className="mt-1 text-[11px] text-rose-700">{errorFor("concurrency")}</p>
            ) : null}
          </div>

          {team ? (
            <p className="text-[11px] text-muted-foreground">
              {t("settings.teams.revisionNote", "当前修订 v{{revision}}；保存后生成 v{{next}}", {
                revision: team.revision,
                next: team.revision + 1,
              })}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t("settings.teams.cancel", "取消")}
          </Button>
          <Button type="button" onClick={submit} disabled={!canSave}>
            {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
            {t("settings.teams.save", "保存")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
