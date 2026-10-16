// 设置 / 团队：从已有 Agent 档案组建团队。
//
// 沿用 AgentsView 的卡片、搜索与写操作壳子（`runWrite` 统一把失败落到 actionError），
// 不复制 Agent 的工具/技能编辑器 —— 团队页只编辑**成员引用与职责**。
// 归档只阻止新执行：历史运行仍可读，因此确认框里必须把这句话讲清楚。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Layers, Plus, Search } from "lucide-react";

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
import { Badge, SectionLabel, StatusBlock } from "@/components/settings/agents/shared";
import { TeamEditorDialog, type TeamDraft } from "@/components/settings/teams/TeamEditorDialog";
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
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <SectionLabel index="01">{t("settings.teams.title", "团队")}</SectionLabel>
        <div className="relative ml-auto min-w-[12rem] flex-1 sm:max-w-[18rem]">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("settings.teams.search", "搜索团队")}
            aria-label={t("settings.teams.search", "搜索团队")}
            className="pl-8"
          />
        </div>
        <Button type="button" onClick={() => setEditing({ team: null })}>
          <Plus className="mr-1.5 h-4 w-4" aria-hidden />
          {t("settings.teams.create", "创建团队")}
        </Button>
      </div>

      {/* 写错误只显示一处：编辑器开着就归对话框，避免同一句话在遮罩上下各刷一遍。 */}
      <StatusBlock status={status} error={loadError ?? (editing ? null : actionError)} />

      {savedNotice ? (
        <p role="status" className="text-[12px] text-emerald-700 dark:text-emerald-300">
          {t("settings.teams.saved", "已保存")}
        </p>
      ) : null}

      {!loading && teams.length === 0 && !loadError ? (
        <div className="flex flex-col items-start gap-2 rounded-panel border border-dashed border-border px-4 py-6 text-[13px] text-muted-foreground">
          <Layers className="h-5 w-5" aria-hidden />
          <span>{t("settings.teams.emptyHint", "先创建 Agent 档案，再组建团队")}</span>
        </div>
      ) : null}

      <ul className="grid gap-3 sm:grid-cols-2">
        {filtered.map((team) => (
          <li key={team.id} className="rounded-panel border border-border bg-card/40 p-3">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-semibold text-foreground/90">{team.name}</p>
                <p className="mt-0.5 text-[12px] text-muted-foreground">
                  {t("settings.teams.leaderLine", "Leader：{{name}}", {
                    name: nameById.get(team.leaderAgentId) ?? team.leaderAgentId,
                  })}
                </p>
                <p className="mt-1 line-clamp-2 text-[12px] text-foreground/75">
                  {team.members
                    .map((member) => nameById.get(member.agentId) ?? member.agentId)
                    .join(" / ")}
                </p>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {t("settings.teams.concurrency", "最多并行 {{count}} 项", {
                    count: team.limits.maxConcurrency,
                  })}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1">
                <Badge tone="muted">v{team.revision}</Badge>
                {team.archived ? <Badge tone="warn">{t("settings.teams.archived", "已归档")}</Badge> : null}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" onClick={() => setEditing({ team })}>
                {t("settings.teams.edit", "编辑")}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => handleClone(team)}>
                {t("settings.teams.duplicate", "复制")}
              </Button>
              {!team.archived ? (
                <Button type="button" size="sm" variant="outline" onClick={() => setPendingArchive(team)}>
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
