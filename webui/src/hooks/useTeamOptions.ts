import { useEffect, useState } from "react";

import type { TeamOption } from "@/lib/teams/types";
import { listTeams } from "@/lib/teams/api";

/** A chat creator only needs enough of the list to label a choice; 100 is well past that. */
const TEAM_OPTION_LIMIT = 100;

/**
 * Live team choices for the chat creator.
 *
 * Reads the same settings endpoint the management page uses, so a team created in one
 * place shows up in the other without a second source of truth. Archived teams stay out
 * of the picker (a binding to them would fail admission anyway), and a failed read
 * degrades to "no teams" instead of blocking chat creation.
 */
export function useTeamOptions(token: string): { options: TeamOption[]; loading: boolean } {
  const [options, setOptions] = useState<TeamOption[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    listTeams(token, { limit: TEAM_OPTION_LIMIT })
      .then((page) => {
        if (disposed) return;
        setOptions(
          page.teams
            .filter((team) => !team.archived)
            .map((team) => ({
              id: team.id,
              name: team.name,
              revision: team.revision,
              leaderAgentId: team.leaderAgentId,
              memberAgentIds: [
                ...new Set(
                  team.members
                    .map((member) => member.agentId)
                    .filter((agentId) => agentId && agentId !== team.leaderAgentId),
                ),
              ],
            })),
        );
      })
      .catch(() => {
        if (!disposed) setOptions([]);
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [token]);

  return { options, loading };
}
