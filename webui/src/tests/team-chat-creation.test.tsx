// 新聊天选团队 → 服务端确认绑定 → 客户端把它当真值（plan Task 9 / 契约 §4）。
//
// 三条口径：
// 1. 帧形状：`team_id`/`team_revision` 只在选了团队时出现，普通创建请求与旧版完全一致。
// 2. 绑定由服务端确认：客户端只读 `attached`/`session_updated` 带回来的 team_binding，
//    自己拼出来的值一律不算（伪造 LeaderSnapshot 没有入口）。
// 3. 暂存失效（TEAM_BINDING_EXPIRED）时创建请求直接失败，不会退化成普通聊天。

import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThreadHeader } from "@/components/thread/ThreadHeader";
import { useSessions } from "@/hooks/useSessions";
import * as api from "@/lib/api";
import { NanobotClient } from "@/lib/nanobot-client";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, listSessions: vi.fn() };
});

class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  url: string;
  readyState = FakeSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.();
  }

  fakeOpen() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  fakeMessage(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }
}

function makeClient(): NanobotClient {
  const client = new NanobotClient({
    url: "ws://test",
    reconnect: false,
    socketFactory: (url: string) => new FakeSocket(url) as unknown as WebSocket,
  });
  client.connect();
  lastSocket().fakeOpen();
  return client;
}

function lastSocket(): FakeSocket {
  const socket = FakeSocket.instances.at(-1);
  if (!socket) throw new Error("no socket created yet");
  return socket;
}

const BINDING = { teamId: "content-team", teamRevision: 3, teamName: "内容团队" };

beforeEach(() => {
  FakeSocket.instances = [];
  vi.mocked(api.listSessions).mockReset();
});

describe("newChat 的团队参数", () => {
  it("选择团队时帧里带 team_id 与固定修订", async () => {
    const client = makeClient();
    const promise = client.newChat(1_000, null, { teamId: "content-team", teamRevision: 2 });
    expect(lastSocket().sent).toContain(
      JSON.stringify({ type: "new_chat", team_id: "content-team", team_revision: 2 }),
    );
    lastSocket().fakeMessage({ event: "attached", chat_id: "fresh-id" });
    await expect(promise).resolves.toBe("fresh-id");
  });

  it("没选团队时帧形状与普通创建完全一致", async () => {
    const client = makeClient();
    const promise = client.newChat(1_000);
    expect(lastSocket().sent).toContain(JSON.stringify({ type: "new_chat" }));
    lastSocket().fakeMessage({ event: "attached", chat_id: "fresh-id" });
    await expect(promise).resolves.toBe("fresh-id");
  });

  it("省略修订时交服务端解析最新值", async () => {
    const client = makeClient();
    const promise = client.newChat(1_000, null, { teamId: "content-team" });
    expect(lastSocket().sent).toContain(JSON.stringify({ type: "new_chat", team_id: "content-team" }));
    lastSocket().fakeMessage({ event: "attached", chat_id: "fresh-id", team_binding: BINDING });
    await expect(promise).resolves.toBe("fresh-id");
  });

  it("attached 回来的服务端绑定是唯一真值", () => {
    const client = makeClient();
    void client.newChat(1_000, null, { teamId: "content-team" });
    expect(client.teamBindingFor("fresh-id")).toBeNull();
    lastSocket().fakeMessage({ event: "attached", chat_id: "fresh-id", team_binding: BINDING });
    expect(client.teamBindingFor("fresh-id")).toEqual(BINDING);

    lastSocket().fakeMessage({
      event: "session_updated",
      chat_id: "fresh-id",
      scope: "metadata",
      team_binding: { ...BINDING, teamRevision: 5 },
    });
    expect(client.teamBindingFor("fresh-id")?.teamRevision).toBe(5);
  });

  it("普通聊天的 attached 不带绑定也不清空已有绑定", () => {
    const client = makeClient();
    void client.newChat(1_000, null, { teamId: "content-team" });
    lastSocket().fakeMessage({ event: "attached", chat_id: "fresh-id", team_binding: BINDING });
    lastSocket().fakeMessage({ event: "attached", chat_id: "other-id" });
    expect(client.teamBindingFor("fresh-id")).toEqual(BINDING);
    expect(client.teamBindingFor("other-id")).toBeNull();
  });

  it("暂存失效时创建请求失败，而不是建成一个普通聊天", async () => {
    const client = makeClient();
    const promise = client.newChat(1_000, null, { teamId: "content-team" });
    lastSocket().fakeMessage({
      event: "error",
      detail: "TEAM_BINDING_EXPIRED chat_id=fresh-id",
      chat_id: "fresh-id",
    });
    await expect(promise).rejects.toThrow(/TEAM_BINDING_EXPIRED/);
  });
});

describe("useSessions.createChat 的团队透传", () => {
  function wrap(client: NanobotClient) {
    return function Wrapper({ children }: { children: ReactNode }) {
      return <ClientProvider client={client} token="tok">{children}</ClientProvider>;
    };
  }

  it("乐观行带的是服务端确认的绑定", async () => {
    vi.mocked(api.listSessions).mockResolvedValue([]);
    const client = makeClient();
    const { result } = renderHook(() => useSessions(), { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.loading).toBe(false));

    let chatId = "";
    await act(async () => {
      const attached = result.current.createChat(null, null, { teamId: "content-team" });
      lastSocket().fakeMessage({ event: "attached", chat_id: "chat-1", team_binding: BINDING });
      chatId = await attached;
      await Promise.resolve();
    });

    expect(chatId).toBe("chat-1");
    expect(result.current.sessions[0].teamBinding).toEqual(BINDING);
  });

  it("普通创建的乐观行不带团队", async () => {
    vi.mocked(api.listSessions).mockResolvedValue([]);
    const client = makeClient();
    const { result } = renderHook(() => useSessions(), { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      const attached = result.current.createChat();
      lastSocket().fakeMessage({ event: "attached", chat_id: "chat-2" });
      await attached;
    });

    expect(result.current.sessions[0].teamBinding ?? null).toBeNull();
  });

  it("准入失败时不插入乐观行", async () => {
    vi.mocked(api.listSessions).mockResolvedValue([]);
    const client = makeClient();
    const { result } = renderHook(() => useSessions(), { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.loading).toBe(false));

    let rejected = false;
    await act(async () => {
      const attached = result.current
        .createChat(null, null, { teamId: "content-team" })
        .catch(() => {
          rejected = true;
          return "";
        });
      lastSocket().fakeMessage({ event: "error", detail: "TEAM_NOT_FOUND content-team" });
      await attached;
    });

    expect(rejected).toBe(true);
    expect(result.current.sessions).toEqual([]);
  });
});

describe("会话页头的团队绑定", () => {
  function header(teamInfo: typeof BINDING | null) {
    return (
      <ThreadHeader
        title="Weekly digest"
        onToggleSidebar={vi.fn()}
        theme="light"
        onToggleTheme={vi.fn()}
        teamInfo={teamInfo}
      />
    );
  }

  it("刷新后页头仍然显示绑定的团队与修订", () => {
    render(header(BINDING));
    expect(screen.getByTestId("thread-header-team")).toHaveTextContent("Team: 内容团队");
    expect(screen.getByTestId("thread-header-team")).toHaveAttribute(
      "title",
      "Bound to 内容团队 (revision 3)",
    );
  });

  it("普通聊天的页头不带团队", () => {
    render(header(null));
    expect(screen.queryByTestId("thread-header-team")).not.toBeInTheDocument();
  });
});
