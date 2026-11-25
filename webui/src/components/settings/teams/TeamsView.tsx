// 设置 / 团队：从已有 Agent 档案组建团队。
//
// 沿用 AgentsView 的卡片、搜索与写操作壳子（`runWrite` 统一把失败落到 actionError），
// 不复制 Agent 的工具/技能编辑器 —— 团队页只编辑**成员引用与职责**。
// 归档只阻止新执行：历史运行仍可读，因此确认框里必须把这句话讲清楚。
// 归档默认不进列表（对齐对话侧的「显示归档」惯例），并且可以取消归档；归档态下内容
// 冻结，所以卡片不给「编辑」——取消归档是唯一的回头路。
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Archive, ArchiveRestore, ArrowUpRight, Copy, Crown, Plus, Search } from "lucide-react";

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
import { TeamMark } from "./TeamVisuals";
import "./teams.css";
import { useClient } from "@/providers/ClientProvider";
import { listAgents } from "@/lib/agents/api";
import type { AgentProfile } from "@/lib/agents/types";
import { notifySaved } from "@/lib/save-feedback";
import { archiveTeam, cloneTeam, listTeams, saveTeam, teamErrorCode } from "@/lib/teams/api";
import type { TeamDefinition } from "@/lib/teams/types";

export function TeamsView() {
  const { t, i18n } = useTranslation();
  const zh = i18n.resolvedLanguage?.startsWith("zh");
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
  const [pendingUnarchive, setPendingUnarchive] = useState<TeamDefinition | null>(null);
  const [showArchived, setShowArchived] = useState(false);

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

  // 归档默认不占列表位；计数与搜索都基于这份可见集合，免得出现「计数 2、卡片 1」的错位。
  const archivedCount = useMemo(
    () => teams.reduce((total, team) => (team.archived ? total + 1 : total), 0),
    [teams],
  );
  const visible = useMemo(
    () => (showArchived ? teams : teams.filter((team) => !team.archived)),
    [showArchived, teams],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return visible;
    return visible.filter(
      (team) =>
        team.name.toLowerCase().includes(needle) ||
        team.description.toLowerCase().includes(needle) ||
        team.members.some((member) => (nameById.get(member.agentId) ?? "").toLowerCase().includes(needle)),
    );
  }, [nameById, query, visible]);

  const upsert = (team: TeamDefinition) => {
    setTeams((prev) => {
      const index = prev.findIndex((entry) => entry.id === team.id);
      if (index === -1) return [team, ...prev];
      const copy = prev.slice();
      copy[index] = team;
      return copy;
    });
  };

  /** 写操作统一壳：失败只落到 actionError，并保留编辑器里的草稿。 */
  const runWrite = async (work: () => Promise<TeamDefinition | null>) => {
    setBusy(true);
    setActionError(null);
    try {
      const saved = await work();
      if (saved) {
        upsert(saved);
        notifySaved();
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

  const handleUnarchive = (team: TeamDefinition) => {
    // 取消归档复用 save 通道：服务端只放行这一种对归档团队的写（archived 显式置回 false）。
    void runWrite(async () => {
      const payload = await saveTeam(client, {
        requestId: crypto.randomUUID(),
        team: { ...team, archived: false },
        expectedRevision: team.revision,
      });
      return payload.team;
    });
    setPendingUnarchive(null);
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
        <p className="teams-toolbar-title">{t("settings.teams.myTeams", "我的团队")}<span className="teams-count">{visible.length.toString().padStart(2, "0")}</span></p>
        <div className="assistant-toolbar-search">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("settings.teams.search", "搜索团队")}
            aria-label={t("settings.teams.search", "搜索团队")}
            className="h-9 pl-8 text-[13px]"
          />
        </div>
        {archivedCount > 0 ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy || loading}
            aria-pressed={showArchived}
            onClick={() => setShowArchived((prev) => !prev)}
          >
            <ArchiveRestore className="mr-1.5 h-4 w-4" aria-hidden />
            {showArchived
              ? t("settings.teams.hideArchived", "隐藏归档")
              : t("settings.teams.showArchived", "显示归档")}
            <span className="teams-count">{archivedCount.toString().padStart(2, "0")}</span>
          </Button>
        ) : null}
        <Button type="button" disabled={busy || loading || Boolean(loadError)} onClick={() => setEditing({ team: null })}>
          <Plus className="mr-1.5 h-4 w-4" aria-hidden />
          {t("settings.teams.create", "创建团队")}
        </Button>
      </div>

      {/* 写错误只显示一处：编辑器开着就归对话框，避免同一句话在遮罩上下各刷一遍。 */}
      <StatusBlock status={status} error={loadError ?? (editing ? null : actionError)} />

      {!loading && teams.length === 0 && !loadError ? (
        <div className="teams-empty">
          <h2 className="teams-empty-title">{zh ? "还没有团队" : "No teams yet"}</h2>
          <p className="teams-empty-note">{t("settings.teams.emptyHint", "先创建 Agent 档案，再组建团队")}</p>
        </div>
      ) : null}

      <ul className="team-card-list">
        {filtered.map((team) => (
          <li key={team.id} className="team-card">
            <div className="team-card-content">
              <div className="flex min-w-0 items-start gap-3">
                <TeamMark id={team.id} name={team.name} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <h2 className="min-w-0 break-words text-[15px] font-semibold">{team.name}</h2>
                    <Badge tone="muted">v{team.revision}</Badge>
                    {team.archived ? <Badge tone="warn">{t("settings.teams.archived", "已归档")}</Badge> : null}
                  </div>
                  {team.description ? <p className="mt-1 line-clamp-2 break-words text-xs leading-relaxed text-muted-foreground">{team.description}</p> : null}
                </div>
              </div>
              <div className="team-card-roster">
                <p className="flex items-baseline gap-1.5 text-xs font-medium text-foreground">
                  <Crown className="h-3 w-3 shrink-0 self-center text-muted-foreground" aria-hidden="true" />
                  <span className="min-w-0 break-words">
                    {t("settings.teams.leaderLine", "Leader：{{name}}", {
                      name: nameById.get(team.leaderAgentId) ?? team.leaderAgentId,
                    })}
                  </span>
                </p>
                {team.members.length > 0 ? (
                  <dl className="team-card-members">
                    <dt>{t("settings.teams.members", "成员")}</dt>
                    <dd>{team.members.map((member) => nameById.get(member.agentId) ?? member.agentId).join(" / ")}</dd>
                  </dl>
                ) : null}
                <p className="text-xs text-muted-foreground">{t("settings.teams.concurrency", "最多并行 {{count}} 项", { count: team.limits.maxConcurrency })}</p>
              </div>
            </div>
            <div className="team-card-actions">
              {/* 归档态内容冻结（服务端会拒绝保存），所以卡片只留复制与取消归档两条出路。 */}
              {!team.archived ? (
                <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setEditing({ team })}>
                  {t("settings.teams.edit", "编辑")}
                  <ArrowUpRight className="ml-1.5 h-3 w-3" aria-hidden="true" />
                </Button>
              ) : null}
              <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => handleClone(team)}><Copy className="mr-1.5 h-3 w-3" aria-hidden="true" />
                {t("settings.teams.duplicate", "复制")}
              </Button>
              {team.archived ? (
                <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setPendingUnarchive(team)}><ArchiveRestore className="mr-1.5 h-3 w-3" aria-hidden="true" />
                  {t("settings.teams.unarchive", "取消归档")}
                </Button>
              ) : (
                <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setPendingArchive(team)}><Archive className="mr-1.5 h-3 w-3" aria-hidden="true" />
                  {t("settings.teams.archive", "归档")}
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>

      {!loading && teams.length > 0 && visible.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">{t("settings.teams.allArchived", "所有团队都已归档，打开「显示归档」就能看到它们。")}</p>
      ) : null}

      {visible.length > 0 && filtered.length === 0 ? (
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

      <AlertDialog
        open={pendingUnarchive !== null}
        onOpenChange={(next) => {
          if (!next) setPendingUnarchive(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("settings.teams.unarchiveTitle", "取消归档这支团队？")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t(
                "settings.teams.unarchiveBody",
                "取消归档后，这支团队可以重新发起运行，并因此生成一个新修订。",
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("settings.teams.cancel", "取消")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => pendingUnarchive && handleUnarchive(pendingUnarchive)}>
              {t("settings.teams.unarchive", "取消归档")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
