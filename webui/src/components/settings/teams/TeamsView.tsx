// 设置 / 团队：从已有 Agent 档案组建团队。
//
// 沿用 AgentsView 的卡片、搜索与写操作壳子（`runWrite` 统一把失败落到 actionError），
// 不复制 Agent 的工具/技能编辑器 —— 团队页只编辑**成员引用与职责**。
// 归档只阻止新执行：历史运行仍可读，因此确认框里必须把这句话讲清楚。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Archive, ArrowUpRight, Check, Copy, Crown, Plus, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { Badge, StatusBlock } from "@/components/settings/agents/shared";
import { TeamEditorDialog, type TeamDraft } from "@/components/settings/teams/TeamEditorDialog";
import { AgentAvatar, TeamMark, TeamStructureIcon, teamIllustration } from "./TeamVisuals";
import "./teams.css";
import { useClient } from "@/providers/ClientProvider";
import { listAgents } from "@/lib/agents/api";
import type { AgentProfile } from "@/lib/agents/types";
import { archiveTeam, cloneTeam, listTeams, saveTeam, teamErrorCode } from "@/lib/teams/api";
import type { TeamDefinition } from "@/lib/teams/types";

export function TeamsView() {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [teams, setTeams] = useState<TeamDefinition[]>([]);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<{ team: TeamDefinition | null } | null>(null);
  const [pendingArchive, setPendingArchive] = useState<TeamDefinition | null>(null);
  const [savedNotice, setSavedNotice] = useState(false);
  const savedNoticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const nameById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent.name])), [agents]);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const token = await getToken();
      const [teamPage, agentPage] = await Promise.all([
        listTeams(token, { limit: 100 }),
        listAgents(token),
      ]);
      setTeams(teamPage.teams);
      setAgents(agentPage.agents);
    } catch (reason: unknown) {
      setLoadError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(
    () => () => {
      if (savedNoticeTimer.current) clearTimeout(savedNoticeTimer.current);
    },
    [],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return teams;
    return teams.filter(
      (team) =>
        team.name.toLowerCase().includes(needle) ||
        team.description.toLowerCase().includes(needle) ||
        team.members.some((member) => (nameById.get(member.agentId) ?? "").toLowerCase().includes(needle)),
    );
  }, [nameById, query, teams]);

  const upsert = (team: TeamDefinition) => {
    setTeams((prev) => {
      const index = prev.findIndex((entry) => entry.id === team.id);
      if (index === -1) return [team, ...prev];
      const copy = prev.slice();
      copy[index] = team;
      return copy;
    });
  };

  const flashSaved = () => {
    setSavedNotice(true);
    if (savedNoticeTimer.current) clearTimeout(savedNoticeTimer.current);
    savedNoticeTimer.current = setTimeout(() => setSavedNotice(false), 3000);
  };

  /** 写操作统一壳：失败只落到 actionError，并保留编辑器里的草稿。 */
  const runWrite = async (work: () => Promise<TeamDefinition | null>) => {
    setBusy(true);
    setActionError(null);
    try {
      const saved = await work();
      if (saved) {
        upsert(saved);
        flashSaved();
      }
      return saved;
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      const code = teamErrorCode(message);
      setActionError(
        code === "TEAM_REVISION_CONFLICT"
          ? t("settings.teams.conflict", "团队已被其他改动更新，请重新加载后再保存（你的草稿已保留）")
          : message,
      );
      return null;
    } finally {
      setBusy(false);
    }
  };

  const handleSave = (draft: TeamDraft) => {
    void runWrite(async () => {
      const payload = await saveTeam(client, {
        requestId: crypto.randomUUID(),
        team: draft.definition,
        expectedRevision: draft.expectedRevision,
      });
      // 保存后留在编辑器：基线换成刚落库的那一份，修订提示随之更新。
      setEditing((prev) => (prev ? { team: payload.team } : prev));
      return payload.team;
    });
  };

  const handleArchive = (team: TeamDefinition) => {
    void runWrite(async () => {
      const result = await archiveTeam(client, team.id, team.revision, crypto.randomUUID());
      return { ...team, archived: true, revision: result.revision };
    });
    setPendingArchive(null);
  };

  const handleClone = (team: TeamDefinition) => {
    if (busy) return;
    void runWrite(async () => {
      const payload = await cloneTeam(
        client,
        team.id,
        `${team.name} ${t("settings.teams.copySuffix", "副本")}`,
        crypto.randomUUID(),
      );
      return payload.team;
    });
  };

  // StatusBlock 只借它的「加载中/出错」两种状态；empty 文案属于 Agent 域，
  // 团队页的空态自己画（要引导用户先建档案）。
  const status = loading ? "loading" : "ready";

  return (
    <div className="teams-surface space-y-4">
      <div className="teams-toolbar">
        <div className="teams-heading"><TeamStructureIcon className="teams-heading-icon" /><div><p className="teams-kicker">{t("settings.teams.workspaceEyebrow", "Agent teams")}</p><p className="teams-toolbar-title">{t("settings.teams.myTeams", "我的团队")}<span className="teams-count">{teams.length.toString().padStart(2, "0")}</span></p></div></div>
        <div className="relative min-w-[10rem] flex-1 sm:max-w-[16rem]">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("settings.teams.search", "搜索团队")}
            aria-label={t("settings.teams.search", "搜索团队")}
            className="pl-8"
          />
        </div>
        <Button type="button" disabled={busy || loading || Boolean(loadError)} onClick={() => setEditing({ team: null })}>
          <Plus className="mr-1.5 h-4 w-4" aria-hidden />
          {t("settings.teams.create", "创建团队")}
        </Button>
      </div>

      {/* 写错误只显示一处：编辑器开着就归对话框，避免同一句话在遮罩上下各刷一遍。 */}
      <StatusBlock status={status} error={loadError ?? (editing ? null : actionError)} />

      {savedNotice ? (
        <p role="status" className="flex items-center gap-1.5 text-[12px] text-emerald-700 dark:text-emerald-300"><Check className="h-3.5 w-3.5" aria-hidden="true" />
          {t("settings.teams.saved", "已保存")}
        </p>
      ) : null}

      {!loading && teams.length === 0 && !loadError ? (
        <div className="teams-empty">
          <div className="teams-empty-copy">
            <p className="teams-kicker">{t("settings.teams.emptyEyebrow", "Better, together")}</p>
            <h2 className="teams-empty-title">{t("settings.teams.emptyTitle", "让各有所长的 Agent，成为一支团队。")}</h2>
            <p className="teams-empty-description">{t("settings.teams.emptyDescription", "选一位队长协调任务，邀请成员发挥专长。从一个目标开始，一起完成更大的事。")}</p>
            <Button className="mt-6" type="button" disabled={busy} onClick={() => setEditing({ team: null })}>{t("settings.teams.buildFirst", "组建第一支团队")}<ArrowUpRight className="ml-2 h-4 w-4" aria-hidden="true" /></Button>
            <p className="teams-empty-note">{t("settings.teams.emptyHint", "先创建 Agent 档案，再组建团队")}</p>
          </div>
          <img className="teams-empty-art" src={teamIllustration} alt={t("settings.teams.illustrationAlt", "一位队长与研究、编码、检查 Agent 组成的小队")} width="1536" height="1024" />
        </div>
      ) : null}

      <ul className="team-card-list">
        {filtered.map((team) => (
          <li key={team.id} className="team-card">
            <div className="flex items-start gap-3">
              <TeamMark id={team.id} name={team.name} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[15px] font-semibold">{team.name}</p>
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{team.description || t("settings.teams.noDescription", "为共同的目标，各展所长。")}</p>
                <p className="mt-3 flex items-center gap-1.5 text-[11px] text-muted-foreground"><Crown className="h-3 w-3" aria-hidden="true" />
                  {t("settings.teams.leaderLine", "Leader：{{name}}", {
                    name: nameById.get(team.leaderAgentId) ?? team.leaderAgentId,
                  })}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1">
                <Badge tone="muted">v{team.revision}</Badge>
                {team.archived ? <Badge tone="warn">{t("settings.teams.archived", "已归档")}</Badge> : null}
              </div>
            </div>
            <div className="team-card-members">
              <div className="flex pr-2" aria-hidden="true">{team.members.slice(0, 5).map((member) => <AgentAvatar key={member.memberId} id={member.agentId} name={nameById.get(member.agentId) ?? member.agentId} />)}</div>
              <p className="min-w-0 flex-1 truncate pl-1 text-xs">{team.members.map((member) => nameById.get(member.agentId) ?? member.agentId).join(" / ")}</p>
              <span className="shrink-0 text-[10px] text-muted-foreground">{t("settings.teams.concurrency", "最多并行 {{count}} 项", { count: team.limits.maxConcurrency })}</span>
            </div>
            <div className="team-card-actions">
              <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setEditing({ team })}>
                {t("settings.teams.edit", "编辑")}
                <ArrowUpRight className="ml-1.5 h-3 w-3" aria-hidden="true" />
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => handleClone(team)}><Copy className="mr-1.5 h-3 w-3" aria-hidden="true" />
                {t("settings.teams.duplicate", "复制")}
              </Button>
              {!team.archived ? (
                <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setPendingArchive(team)}><Archive className="mr-1.5 h-3 w-3" aria-hidden="true" />
                  {t("settings.teams.archive", "归档")}
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>

      {teams.length > 0 && filtered.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">{t("settings.teams.noMatch", "没有匹配的团队")}</p>
      ) : null}

      <TeamEditorDialog
        open={editing !== null}
        team={editing?.team ?? null}
        agents={agents}
        busy={busy}
        serverError={actionError}
        onOpenChange={(next) => {
          if (!next) {
            setEditing(null);
            setActionError(null);
          }
        }}
        onSave={handleSave}
      />

      <AlertDialog
        open={pendingArchive !== null}
        onOpenChange={(next) => {
          if (!next) setPendingArchive(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("settings.teams.archiveTitle", "归档团队")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t(
                "settings.teams.archiveBody",
                "归档后不能再发起新的团队运行，已有聊天会继续使用它绑定的修订，历史运行仍可查看。",
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("settings.teams.cancel", "取消")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => pendingArchive && handleArchive(pendingArchive)}>
              {t("settings.teams.archive", "归档")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
