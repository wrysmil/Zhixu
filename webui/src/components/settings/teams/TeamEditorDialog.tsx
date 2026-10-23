// 团队编辑对话框：Leader + 成员 + 职责 + 并行上限。
//
// 客户端只挡「当场就能确定不合法」的四条（空名、无 Leader、无成员、成员重复/等于
// Leader）；**Leader 档案是否放行某个成员**由服务端按冻结档案判定，客户端用已有的
// `resolveSelection` 预检同一套语义，避免用户按下保存才知道越权。
// 冲突（TEAM_REVISION_CONFLICT）时保留本地草稿并提示重新加载，绝不覆盖他人编辑。
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Crown, Loader2, Search, X } from "lucide-react";

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
import { FieldLabel } from "@/components/settings/agents/shared";
import { AgentAvatar, CompletionStep, TeamMark } from "./TeamVisuals";
import { TeamFormationPreview } from "./TeamFormationPreview";
import "./teams.css";
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
  const [memberQuery, setMemberQuery] = useState("");
  const [leaderNotice, setLeaderNotice] = useState("");
  const roleRefs = useRef(new Map<string, HTMLInputElement>());
  const leaderRef = useRef<HTMLSelectElement>(null);
  const sessionId = useRef("");

  // 只在「打开」或「换了一支团队」时重建草稿。保存成功后列表会把刚落库的定义
  // 回填到 editing.team，但那仍是同一支团队：若按对象身份重建，用户在慢保存期间
  // 敲下去的字符会被基线覆盖掉。
  const teamId = team?.id ?? "";
  useEffect(() => {
    if (!open) return;
    setDraft(team ? draftFromTeam(team) : emptyDraft());
    setMemberQuery("");
    setLeaderNotice("");
    sessionId.current = team?.id || `team-${crypto.randomUUID().slice(0, 8)}`;
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
    if (draft.members.some((member) => member.role.length > TEAM_MEMBER_ROLE_MAX_LENGTH)) {
      found.push({ field: "members", message: t("settings.teams.errorRoleLength", "成员职责不能超过 {{max}} 字", { max: TEAM_MEMBER_ROLE_MAX_LENGTH }) });
    }
    if (draft.instructions.length > TEAM_INSTRUCTIONS_MAX_LENGTH) {
      found.push({ field: "instructions", message: t("settings.teams.errorInstructionsLength", "协作约定不能超过 {{max}} 字", { max: TEAM_INSTRUCTIONS_MAX_LENGTH }) });
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
    const id = draft.id || sessionId.current;
    const definition: TeamDefinition = {
      id,
      // revision 由服务端推进：客户端提交的是「基于哪一版」，不是「变成哪一版」。
      revision: team ? team.revision + 1 : 1,
      name: draft.name.trim(),
      description: draft.description,
      instructions: draft.instructions,
      leaderAgentId: draft.leaderAgentId,
      members: draft.members.map((member) => ({ ...member })),
      // 本编辑器只改 maxConcurrency；其余限额必须原样带回去，不能被默认值悄悄重置。
      limits: {
        ...(team ? team.limits : defaultTeamLimits()),
        maxConcurrency: Number.parseInt(draft.maxConcurrency, 10),
      },
      archived: draft.archived,
      createdAt: team ? team.createdAt : now,
      updatedAt: now,
    };
    onSave({ definition, expectedRevision: team ? team.revision : null });
  };

  const toggleMember = (agent: AgentProfile) => {
    if (busy || agent.id === draft.leaderAgentId) return;
    setDraft((prev) => {
      const exists = prev.members.some((member) => member.agentId === agent.id);
      if (exists) {
        return { ...prev, members: prev.members.filter((member) => member.agentId !== agent.id) };
      }
      if (prev.members.length >= TEAM_MEMBER_MAX) return prev;
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
    if (busy) return;
    setDraft((prev) => ({
      ...prev,
      members: prev.members.map((member) =>
        member.agentId === agentId ? { ...member, role } : member,
      ),
    }));
  };

  const chooseLeader = (nextId: string) => {
    if (busy) return;
    const wasMember = draft.members.some((member) => member.agentId === nextId);
    setDraft((prev) => ({ ...prev, leaderAgentId: nextId, members: prev.members.filter((member) => member.agentId !== nextId) }));
    setLeaderNotice(wasMember ? t("settings.teams.promotedNotice", "{{name}} 已成为队长，并从成员席位移出。", { name: agentById.get(nextId)?.name ?? nextId }) : "");
  };
  const candidates = visibleAgents.filter((agent) => agent.id !== draft.leaderAgentId && `${agent.name} ${agent.description}`.toLowerCase().includes(memberQuery.trim().toLowerCase()));
  const focusRole = (agentId: string) => {
    const input = roleRefs.current.get(agentId);
    input?.scrollIntoView?.({ behavior: "auto", block: "center" });
    input?.focus();
  };

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next); }}>
      <DialogContent className="team-editor" showCloseButton={!busy}>
        <DialogHeader className="team-editor-head">
          <div className="team-editor-heading">
            <TeamMark id={draft.id || sessionId.current} name={draft.name} />
            <div>
              <DialogTitle>{team ? t("settings.teams.editTitle", "编辑团队") : t("settings.teams.createTitle", "创建团队")}</DialogTitle>
              <DialogDescription className="mt-2 text-xs">{t("settings.teams.assemblySubtitle", "选好队长，邀请成员，让每位 Agent 都有明确分工。")}</DialogDescription>
            </div>
          </div>
          <div className="team-editor-steps" aria-label={t("settings.teams.setupSteps", "组队步骤")}>
            <CompletionStep index={1} complete={Boolean(draft.name.trim())}>{t("settings.teams.stepIdentity", "确定目标")}</CompletionStep>
            <CompletionStep index={2} complete={Boolean(leader)}>{t("settings.teams.stepLeader", "安排队长")}</CompletionStep>
            <CompletionStep index={3} complete={draft.members.length > 0 && !errorFor("members")}>{t("settings.teams.stepMembers", "成员就位")}</CompletionStep>
          </div>
        </DialogHeader>
        <div className="team-editor-body">
          <div className="team-editor-fields">
            {serverError ? <div role="alert" className="mb-4 rounded-lg border border-rose-300/70 bg-rose-50/70 px-3 py-2 text-xs text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200">{serverError}</div> : null}
            <fieldset disabled={busy}>
              <section className="team-section">
                <h3 className="team-section-heading"><span><span className="team-section-number">01</span>{t("settings.teams.identitySection", "这支团队做什么")}</span></h3>
                <FieldLabel htmlFor="team-name" hint={`${draft.name.length}/${TEAM_NAME_MAX_LENGTH}`}>{t("settings.teams.name", "团队名称")}</FieldLabel>
                <Input id="team-name" value={draft.name} maxLength={TEAM_NAME_MAX_LENGTH} onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))} placeholder={t("settings.teams.namePlaceholder", "例如：内容团队")} aria-invalid={errorFor("name") ? true : undefined} aria-describedby={errorFor("name") ? "team-name-error" : undefined} />
                {errorFor("name") ? <p id="team-name-error" className="team-field-error">{errorFor("name")}</p> : null}
                <div className="mt-3">
                  <FieldLabel htmlFor="team-description">{t("settings.teams.description", "团队说明")}</FieldLabel>
                  <Input id="team-description" value={draft.description} onChange={(event) => setDraft((prev) => ({ ...prev, description: event.target.value }))} placeholder={t("settings.teams.descriptionPlaceholder", "一句话说明这支团队做什么")} />
                </div>
              </section>
              <section className="team-section">
                <h3 className="team-section-heading"><span><span className="team-section-number">02</span>{t("settings.teams.leaderSection", "谁来带领团队")}</span><small className="flex items-center gap-1"><Crown className="h-3 w-3" aria-hidden="true" />{t("settings.teams.oneLeader", "1 位队长")}</small></h3>
                <div className="team-leader-seat">
                  <AgentAvatar id={leader?.id || "leader"} name={leader?.name || "A"} />
                  <div className="min-w-0 flex-1">
                    <FieldLabel htmlFor="team-leader">{t("settings.teams.leader", "Leader")}</FieldLabel>
                    <select ref={leaderRef} id="team-leader" value={draft.leaderAgentId} onChange={(event) => chooseLeader(event.target.value)} className="team-leader-select" aria-invalid={errorFor("leader") ? true : undefined} aria-describedby="team-leader-helper">
                      <option value="">{t("settings.teams.leaderPlaceholder", "选择一个 Agent 档案")}</option>
                      {draft.leaderAgentId && !visibleAgents.some((agent) => agent.id === draft.leaderAgentId) ? <option value={draft.leaderAgentId}>{leader?.name ?? draft.leaderAgentId}</option> : null}
                      {visibleAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
                    </select>
                  </div>
                </div>
                <p id="team-leader-helper" className="team-helper mt-2">{t("settings.teams.leaderExplanation", "队长接收你的目标，把任务分给成员，再整理最终结果。")}</p>
                {errorFor("leader") ? <p className="team-field-error">{errorFor("leader")}</p> : null}
                <p role="status" className="team-helper mt-1">{leaderNotice}</p>
              </section>
              <section className="team-section">
                <h3 className="team-section-heading"><span><span className="team-section-number">03</span>{t("settings.teams.membersSection", "让成员各展所长")}</span><small>{draft.members.length}/{TEAM_MEMBER_MAX} {t("settings.teams.members", "成员")}</small></h3>
                <div className="team-member-search"><Search aria-hidden="true" /><Input value={memberQuery} onChange={(event) => setMemberQuery(event.target.value)} placeholder={t("settings.teams.searchAgents", "搜索 Agent 名称或专长")} aria-label={t("settings.teams.searchAgents", "搜索 Agent 名称或专长")} /></div>
                <div className="team-candidate-list">
                  {candidates.map((agent) => {
                    const checked = memberIds.includes(agent.id);
                    const disallowed = leader && !resolveSelection(leader.subAgents, [agent.id]).has(agent.id);
                    return <label key={agent.id} className="team-candidate">
                      <input type="checkbox" checked={checked} disabled={!checked && draft.members.length >= TEAM_MEMBER_MAX} onChange={() => toggleMember(agent)} aria-label={t("settings.teams.pickMember", "选择 {{name}} 为成员", { name: agent.name })} />
                      <AgentAvatar id={agent.id} name={agent.name} />
                      <span className="min-w-0"><span className="block truncate font-medium">{agent.name}</span><span className="team-helper block truncate" title={disallowed ? t("settings.teams.permissionHint", "队长档案尚未允许委派给此成员") : agent.description}>{disallowed ? t("settings.teams.permissionHint", "队长档案尚未允许委派给此成员") : agent.description || t("settings.teams.agentProfile", "Agent 档案")}</span></span>
                    </label>;
                  })}
                </div>
                {candidates.length === 0 ? <p className="team-helper py-3">{visibleAgents.length === 0 ? t("settings.teams.noAgents", "还没有可用的 Agent 档案") : t("settings.teams.noAgentMatches", "没有匹配的 Agent，试试其他关键词。")}</p> : null}
                <div className="team-selected-list">
                  {draft.members.map((member) => {
                    const agent = agentById.get(member.agentId);
                    const name = agent?.name ?? member.agentId;
                    return <div key={member.memberId} className="team-member-config">
                      <div className="flex min-w-0 items-center gap-2.5"><AgentAvatar id={member.agentId} name={name} /><div className="min-w-0"><p className="truncate text-xs font-semibold">{name}</p><p className="team-helper">{t("settings.teams.memberResponsibility", "在这个团队负责")}</p></div><button type="button" disabled={busy} className="team-remove-member" onClick={() => {
                        if (agent) toggleMember(agent); else setDraft((prev) => ({ ...prev, members: prev.members.filter((entry) => entry.memberId !== member.memberId) }));
                      }} aria-label={t("settings.teams.removeMember", "移除 {{name}}", { name })}><X className="h-3.5 w-3.5" aria-hidden="true" /></button></div>
                      <Input ref={(node) => { if (node) roleRefs.current.set(member.agentId, node); else roleRefs.current.delete(member.agentId); }} value={member.role} maxLength={TEAM_MEMBER_ROLE_MAX_LENGTH} onChange={(event) => setRole(member.agentId, event.target.value)} placeholder={t("settings.teams.rolePlaceholder", "本团队职责")} aria-label={t("settings.teams.roleAria", "{{name}} 在本团队的职责", { name })} aria-invalid={!member.role.trim() || member.role.length > TEAM_MEMBER_ROLE_MAX_LENGTH ? true : undefined} />
                    </div>;
                  })}
                </div>
                {errorFor("members") ? <p className="team-field-error">{errorFor("members")}</p> : null}
                <p className="team-helper mt-2">{t("settings.teams.memberMinimum", "至少加入 1 位成员。职责只在本团队生效，不会改动 Agent 原始档案。")}</p>
              </section>
              <details className="team-advanced" open={errorFor("concurrency") || errorFor("instructions") ? true : undefined}>
                <summary>{t("settings.teams.advancedSettings", "协作约定与工作方式")}</summary>
                <div className="team-advanced-content">
                  <div><FieldLabel htmlFor="team-instructions" hint={`${draft.instructions.length}/${TEAM_INSTRUCTIONS_MAX_LENGTH}`}>{t("settings.teams.instructions", "协作约定")}</FieldLabel><textarea id="team-instructions" value={draft.instructions} maxLength={TEAM_INSTRUCTIONS_MAX_LENGTH} onChange={(event) => setDraft((prev) => ({ ...prev, instructions: event.target.value }))} rows={4} placeholder={t("settings.teams.instructionsPlaceholder", "谁先做什么、交付标准是什么")} className="w-full rounded-md border p-2 text-[13px] text-foreground" />{errorFor("instructions") ? <p className="team-field-error">{errorFor("instructions")}</p> : null}</div>
                  <div><FieldLabel htmlFor="team-concurrency" hint={t("settings.teams.concurrencyHint", "同时运行的成员数上限", { max: TEAM_LIMIT_RANGES.maxConcurrency.max })}>{t("settings.teams.maxConcurrency", "最多并行")}</FieldLabel><input id="team-concurrency" type="number" min={TEAM_LIMIT_RANGES.maxConcurrency.min} max={TEAM_LIMIT_RANGES.maxConcurrency.max} value={draft.maxConcurrency} onChange={(event) => setDraft((prev) => ({ ...prev, maxConcurrency: event.target.value }))} aria-invalid={errorFor("concurrency") ? true : undefined} className="h-9 w-24 rounded-md border px-2 text-[13px]" />{errorFor("concurrency") ? <p className="team-field-error">{errorFor("concurrency")}</p> : null}</div>
                </div>
              </details>
              {team ? <p className="team-revision-note">{t("settings.teams.revisionNote", "当前修订 v{{revision}}；保存后生成 v{{next}}", { revision: team.revision, next: team.revision + 1 })}</p> : null}
            </fieldset>
          </div>
          <TeamFormationPreview name={draft.name} description={draft.description} leader={leader} members={draft.members} agentById={agentById} busy={busy} membersReady={draft.members.length > 0 && !errorFor("members")} onFocusLeader={() => leaderRef.current?.focus()} onFocusMember={focusRole} />
        </div>
        <DialogFooter className="team-editor-footer">
          <p className="team-editor-footer-note" aria-live="polite">{leader ? t("settings.teams.rosterCount", "1 位队长 · {{count}} 位成员", { count: draft.members.length }) : t("settings.teams.rosterPrompt", "从一个目标开始，组成你的 Agent 团队。")}</p>
          <Button type="button" variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>{t("settings.teams.cancel", "取消")}</Button>
          <Button type="button" onClick={submit} disabled={!canSave}>{busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}{t("settings.teams.save", "保存")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
