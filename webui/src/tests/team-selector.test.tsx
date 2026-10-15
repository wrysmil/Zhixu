// 团队选择器与团队列表读数（plan Task 9 / 契约 §4）。
//
// 口径：选择只发生在「还没有消息」的聊天上；一旦服务端确认了绑定，这里就退化成
// 只读展示，客户端没有任何入口能改写已生效的绑定。
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TeamSelector, teamPickerVisibility } from "@/components/thread/TeamSelector";
import { useTeamOptions } from "@/hooks/useTeamOptions";
import * as teamsApi from "@/lib/teams/api";
import type { TeamDefinition } from "@/lib/teams/types";

vi.mock("@/lib/teams/api", () => ({
  listTeams: vi.fn(),
}));

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
        options={[team("content-team", "内容团队", 3)]}
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
        options={[team("content-team", "内容团队", 2), team("code-team", "代码团队", 7)]}
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
        options={[team("content-team", "内容团队", 2)]}
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

    expect(result.current.options).toEqual([{ id: "live", name: "活团队", revision: 1 }]);
    expect(vi.mocked(teamsApi.listTeams)).toHaveBeenCalledWith("tok", { limit: 100 });
  });

  it("读取失败时退化成空列表而不是抛错", async () => {
    vi.mocked(teamsApi.listTeams).mockRejectedValue(new Error("HTTP 503"));

    const { result } = renderHook(() => useTeamOptions("tok"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.options).toEqual([]);
  });
});
