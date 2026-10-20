// 团队选择器与团队列表读数（plan Task 9 / 契约 §4）。
//
// 口径：选择只发生在「还没有消息」的聊天上；一旦服务端确认了绑定，这里就退化成
// 只读展示，客户端没有任何入口能改写已生效的绑定。
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TeamSelector, teamPickerVisibility } from "@/components/thread/TeamSelector";
import { useTeamOptions } from "@/hooks/useTeamOptions";
import type { AgentProfile } from "@/lib/agents/types";
import * as teamsApi from "@/lib/teams/api";
import type { TeamDefinition, TeamOption } from "@/lib/teams/types";

vi.mock("@/lib/teams/api", () => ({
  listTeams: vi.fn(),
}));

function agent(id: string, name: string, icon = "lucide:bot"): AgentProfile {
  return {
    id,
    name,
    description: "",
    type: "custom",
    customized: false,
    categoryId: null,
    icon,
    color: "#4A90D9",
    prompt: "",
    modelId: null,
    tools: { mode: "all", entries: [] },
    skills: { mode: "all", entries: [] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: "2026-10-18T00:00:00+00:00",
  };
}

function teamOption(
  id: string,
  name: string,
  revision: number,
  overrides: Partial<TeamOption> = {},
): TeamOption {
  return { id, name, revision, leaderAgentId: "leader", memberAgentIds: [], ...overrides };
}

function team(id: string, name: string, revision: number, archived = false): TeamDefinition {
  return {
    id,
    revision,
    name,
    description: "",
    instructions: "",
    leaderAgentId: "leader",
    members: [],
    limits: {},
    archived,
    createdAt: "2026-10-15T00:00:00+00:00",
    updatedAt: "2026-10-15T00:00:00+00:00",
  } as unknown as TeamDefinition;
}

beforeEach(() => {
  vi.mocked(teamsApi.listTeams).mockReset();
});

describe("TeamSelector", () => {
  it("已绑定团队时只做只读展示，不给可点的选择器", () => {
    render(
      <TeamSelector
        options={[teamOption("content-team", "内容团队", 3)]}
        value={null}
        valueName={null}
        bound={{ teamId: "content-team", teamRevision: 3, teamName: "内容团队" }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/内容团队/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Choose team" })).not.toBeInTheDocument();
  });

  it("打开列表并选中团队后回传固定修订", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TeamSelector
        options={[
          teamOption("content-team", "内容团队", 2),
          teamOption("code-team", "代码团队", 7),
        ]}
        value={null}
        valueName={null}
        bound={null}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Choose team" }));
    await user.click(await screen.findByRole("button", { name: /代码团队/ }));

    expect(onChange).toHaveBeenCalledWith({ teamId: "code-team", teamRevision: 7 }, "代码团队");
  });

  it("可以取消选择回到普通聊天", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TeamSelector
        options={[teamOption("content-team", "内容团队", 2)]}
        value={{ teamId: "content-team", teamRevision: 2 }}
        valueName="内容团队"
        bound={null}
        onChange={onChange}
      />,
    );

    expect(screen.getByRole("button", { name: /内容团队/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /内容团队/ }));
    await user.click(await screen.findByRole("button", { name: "No team" }));

    expect(onChange).toHaveBeenCalledWith(null, null);
  });

  it("没有可用团队时给出提示而不是空弹层", async () => {
    const user = userEvent.setup();
    render(
      <TeamSelector options={[]} value={null} valueName={null} bound={null} onChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Choose team" }));

    expect(await screen.findByText("No teams available yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "No team" })).not.toBeInTheDocument();
  });

  it("二级选择落在团队 Leader 上，并可改写为团队成员", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onAgentChange = vi.fn();
    const contentTeam = teamOption("content-team", "内容团队", 2, {
      leaderAgentId: "leader",
      memberAgentIds: ["writer"],
    });

    render(
      <TeamSelector
        options={[contentTeam]}
        value={null}
        valueName={null}
        bound={null}
        onChange={onChange}
        agents={[agent("leader", "队长"), agent("writer", "写手"), agent("other", "旁人")]}
        agentValue="leader"
        onAgentChange={onAgentChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Choose team" }));
    await user.click(await screen.findByRole("button", { name: /内容团队/ }));

    // 选中团队即绑定，二级默认落在该团队 Leader 上。
    expect(onChange).toHaveBeenCalledWith({ teamId: "content-team", teamRevision: 2 }, "内容团队");
    expect(onAgentChange).toHaveBeenLastCalledWith("leader");
    // 二级只列该团队的 Leader 与成员，不列无关档案。
    expect(await screen.findByRole("button", { name: /写手/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /旁人/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /写手/ }));

    expect(onAgentChange).toHaveBeenLastCalledWith("writer");
  });

  it("不使用团队时二级列出全部档案", async () => {
    const user = userEvent.setup();
    const onAgentChange = vi.fn();

    render(
      <TeamSelector
        options={[teamOption("content-team", "内容团队", 2)]}
        value={null}
        valueName={null}
        bound={null}
        onChange={vi.fn()}
        agents={[agent("leader", "队长"), agent("other", "旁人")]}
        agentValue="other"
        onAgentChange={onAgentChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Choose team" }));
    await user.click(await screen.findByRole("button", { name: "No team" }));
    await user.click(await screen.findByRole("button", { name: /队长/ }));

    expect(onAgentChange).toHaveBeenLastCalledWith("leader");
  });

  it("拿不到档案列表时二级退化，团队行仍是单击即选", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(
      <TeamSelector
        options={[teamOption("content-team", "内容团队", 2)]}
        value={null}
        valueName={null}
        bound={null}
        onChange={onChange}
        onAgentChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Choose team" }));
    await user.click(await screen.findByRole("button", { name: /内容团队/ }));

    expect(onChange).toHaveBeenCalledWith({ teamId: "content-team", teamRevision: 2 }, "内容团队");
  });

  it("已绑定与临时聊天各自决定选择器形态", () => {
    const bound = { teamId: "content-team", teamRevision: 3, teamName: "内容团队" };
    expect(
      teamPickerVisibility({ temporary: false, hasMessages: false, bound: null, canSelect: true }),
    ).toBe("select");
    // 服务端一旦确认绑定就只读（有消息的聊天不能换团队）。
    expect(
      teamPickerVisibility({ temporary: false, hasMessages: false, bound, canSelect: true }),
    ).toBe("bound");
    expect(
      teamPickerVisibility({ temporary: false, hasMessages: true, bound, canSelect: true }),
    ).toBe("bound");
    // 临时聊天不显示团队选择（契约 §4：临时聊天不显示团队选择）。
    expect(
      teamPickerVisibility({ temporary: true, hasMessages: false, bound: null, canSelect: true }),
    ).toBe("hidden");
    // 「下一条消息开临时聊天」的状态也要藏住选择器：那条发送根本不会带 team_id。
    expect(
      teamPickerVisibility({
        temporary: false,
        temporaryRequested: true,
        hasMessages: false,
        bound: null,
        canSelect: true,
      }),
    ).toBe("hidden");
    expect(
      teamPickerVisibility({ temporary: false, hasMessages: false, bound: null, canSelect: false }),
    ).toBe("hidden");
  });
});

describe("useTeamOptions", () => {
  it("只保留未归档团队", async () => {
    vi.mocked(teamsApi.listTeams).mockResolvedValue({
      teams: [team("live", "活团队", 1), team("old", "归档团队", 1, true)],
      nextCursor: null,
    });

    const { result } = renderHook(() => useTeamOptions("tok"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.options).toEqual([
      { id: "live", name: "活团队", revision: 1, leaderAgentId: "leader", memberAgentIds: [] },
    ]);
    expect(vi.mocked(teamsApi.listTeams)).toHaveBeenCalledWith("tok", { limit: 100 });
  });

  it("带上团队 Leader 与去重后的成员档案 id", async () => {
    const mixed = team("live", "活团队", 1);
    mixed.leaderAgentId = "leader";
    mixed.members = [
      { memberId: "m1", agentId: "writer", role: "写作" },
      { memberId: "m2", agentId: "leader", role: "兼任" },
      { memberId: "m3", agentId: "writer", role: "重复" },
    ];
    vi.mocked(teamsApi.listTeams).mockResolvedValue({ teams: [mixed], nextCursor: null });

    const { result } = renderHook(() => useTeamOptions("tok"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.options[0].leaderAgentId).toBe("leader");
    expect(result.current.options[0].memberAgentIds).toEqual(["writer"]);
  });

  it("读取失败时退化成空列表而不是抛错", async () => {
    vi.mocked(teamsApi.listTeams).mockRejectedValue(new Error("HTTP 503"));

    const { result } = renderHook(() => useTeamOptions("tok"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.options).toEqual([]);
  });
});
