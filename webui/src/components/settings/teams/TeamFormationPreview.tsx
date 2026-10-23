import { ArrowDown, Check, Crown } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AgentProfile } from "@/lib/agents/types";
import type { TeamMember } from "@/lib/teams/types";
import { AgentAvatar, TeamProgress, TeamStructureIcon, teamIllustration } from "./TeamVisuals";

interface Props {
  name: string;
  description: string;
  leader: AgentProfile | undefined;
  members: TeamMember[];
  agentById: Map<string, AgentProfile>;
  busy: boolean;
  membersReady: boolean;
  onFocusLeader: () => void;
  onFocusMember: (agentId: string) => void;
}

export function TeamFormationPreview({ name, description, leader, members, agentById, busy, membersReady, onFocusLeader, onFocusMember }: Props) {
  const { t } = useTranslation();
  const completed = Number(Boolean(name.trim())) + Number(Boolean(leader)) + Number(membersReady);
  return (
    <aside className="team-formation" aria-label={t("settings.teams.formationLabel", "团队关系预览")}>
      <div className="team-formation-title"><TeamStructureIcon />{t("settings.teams.formationTitle", "你的团队正在成形")}</div>
      <h3 className="team-formation-name">{name.trim() || t("settings.teams.untitled", "一支新团队")}</h3>
      <p className="team-formation-target">{description.trim() || t("settings.teams.goalPrompt", "把一个目标，交给各有所长的 Agent。")}</p>
      <p className="team-formation-label">{t("settings.teams.receiveGoal", "接收你的目标")}</p>
      <div className="team-formation-connector" aria-hidden="true" />
      <button type="button" className="team-formation-person is-leader" disabled={busy} onClick={onFocusLeader} aria-label={t("settings.teams.focusLeader", "选择或更换队长")}>
        <AgentAvatar id={leader?.id || "leader"} name={leader?.name || "A"} />
        <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold">{leader?.name || t("settings.teams.chooseLeaderSeat", "等待一位队长")}</span><span className="team-helper flex items-center gap-1"><Crown className="h-3 w-3" aria-hidden="true" />{t("settings.teams.leaderJob", "分派任务，整理结果")}</span></span>
      </button>
      <div className="team-formation-connector" aria-hidden="true" />
      {members.length > 0 ? (
        <div className="team-formation-members">
          {members.map((member) => {
            const agent = agentById.get(member.agentId);
            const agentName = agent?.name ?? member.agentId;
            return (
              <button key={member.memberId} type="button" className="team-formation-person" disabled={busy} onClick={() => onFocusMember(member.agentId)} aria-label={t("settings.teams.focusMember", "编辑 {{name}} 的职责", { name: agentName })}>
                <AgentAvatar id={member.agentId} name={agentName} />
                <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold">{agentName}</span><span className="team-helper block truncate">{member.role.trim() || t("settings.teams.rolePlaceholder", "本团队职责")}</span></span>
              </button>
            );
          })}
        </div>
      ) : <p className="team-formation-empty">{t("settings.teams.emptySeats", "加入成员后，他们会出现在这里。至少需要 1 位成员。")}</p>}
      <div className="mt-3 flex items-center justify-center gap-1.5 text-[10px] text-muted-foreground"><ArrowDown className="h-3 w-3" aria-hidden="true" />{t("settings.teams.summaryFlow", "成员执行后，由队长汇总交付")}</div>
      <div className="team-formation-foot">
        {members.length === 0 ? <img className="team-formation-art" src={teamIllustration} alt="" width="1536" height="1024" /> : null}
        <div className="flex items-center justify-between gap-2 text-[11px]"><span className="flex items-center gap-1.5">{completed === 3 ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : null}{completed === 3 ? t("settings.teams.formationReady", "基本配置已就绪") : t("settings.teams.formationProgress", "团队准备进度")}</span><span>{completed}/3</span></div>
        <TeamProgress completed={completed} total={3} />
        <p className="team-helper">{t("settings.teams.previewHint", "点击队长或成员，可直接调整配置。")}</p>
      </div>
    </aside>
  );
}
