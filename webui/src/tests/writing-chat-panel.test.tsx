import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";

import { WritingChatPanel, type WritingChatSeed } from "@/components/creative/WritingChatPanel";
import { ClientProvider } from "@/providers/ClientProvider";
import { preloadMarkdownText } from "@/components/MarkdownText";
import type { ArticleSnapshotPayload } from "@/lib/writing-api";
import type { NanobotClient } from "@/lib/nanobot-client";
import type { WritingSessionInfo } from "@/lib/types";

/**
 * 右栏团队会话面板：会话身份来自服务端绑定，选择菜单只填输入不执行，
 * 发送时附带 writing 元数据（版本 + digest + 成员/任务/选区）。
 */

const SESSION: WritingSessionInfo = {
  article_id: "a-1",
  chat_id: "chat-writing-1",
  session_key: "websocket:chat-writing-1",
  team_id: "team-writing",
  team_revision: 4,
  team_name: "创作团队",
  revision: 7,
  digest: "sha256:current",
};

function snapshot(overrides: Partial<ArticleSnapshotPayload> = {}): ArticleSnapshotPayload {
  return {
    article_id: "a-1",
    title: "记忆系统",
    body: "正文内容",
    revision: 7,
    digest: "sha256:current",
    outline: [{ id: "o1", text: "先讲问题", order: 0 }],
    materials: [{ id: "m1", title: "观点", content: "内容", included: true, source: "" }],
    tags: [],
    ...overrides,
  } as ArticleSnapshotPayload;
}

type SentCall = { chatId: string; content: string; options?: Record<string, unknown> };

function makeClient(overrides: Record<string, unknown> = {}) {
  const sent: SentCall[] = [];
  const noop = () => () => {};
  const client = {
    sent,
    status: "open",
    defaultChatId: null,
    bindWritingSession: vi.fn(async () => SESSION),
    unbindWritingSession: vi.fn(),
    sendSystemCommand: vi.fn(async () => {}),
    sendMessage: vi.fn((
      chatId: string,
      content: string,
      _media?: unknown,
      options?: Record<string, unknown>,
    ) => {
      sent.push({ chatId, content, options });
    }),
    finishRunLocally: vi.fn(),
    getRunStartedAt: () => null,
    getRunTurnId: () => null,
    getRunGeneration: () => 0,
    hasUnsettledRun: () => false,
    getGoalState: () => undefined,
    teamBindingFor: () => null,
    onChat: noop,
    onError: noop,
    onStatus: noop,
    onRunStatus: noop,
    onSessionUpdate: noop,
    onRuntimeModelUpdate: noop,
    onTerminalEvent: noop,
    attach: vi.fn(),
    newChat: vi.fn(),
    forkChat: vi.fn(),
    connect: vi.fn(),
    close: vi.fn(),
    updateUrl: vi.fn(),
    requestMutation: vi.fn(async () => ({})),
    ...overrides,
  };
  return client as unknown as NanobotClient & { sent: SentCall[] };
}

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

function stubReads() {
  vi.stubGlobal("fetch", vi.fn(async (input: unknown) => {
    const url = String(input);
    if (url.includes("/webui-thread")) return jsonResponse({ messages: [] });
    if (url.includes("/teams/detail")) {
      return jsonResponse({
        snapshot: {
          leader: { id: "writing-editor", name: "创作团队主编" },
          members: [
            {
              member: { memberId: "writing-researcher", role: "资料检索与核实" },
              profile: { name: "资料研究员" },
            },
            {
              member: { memberId: "writing-author", role: "正文写作" },
              profile: { name: "写手" },
            },
          ],
        },
      });
    }
    return jsonResponse({});
  }));
}

type PanelProps = ComponentProps<typeof WritingChatPanel>;

function renderPanel(client: NanobotClient | null, props: Partial<PanelProps> = {}) {
  const panel = (
    <WritingChatPanel
      articleId="a-1"
      articleSnapshot={snapshot()}
      flushForWriting={async () => ({ revision: 7, digest: "sha256:current" })}
      modelPresets={[{
        name: "fast",
        model: "deepseek/deepseek-v4",
        provider: "deepseek",
        reasoningEffort: null,
      }]}
      modelPreset="fast"
      modelDetail="deepseek-v4"
      modelProvider="deepseek"
      modelProviderLabel="DeepSeek"
      modelPresetFromFallback={false}
      modelNeedsSetup={false}
      onManageModels={vi.fn()}
      onModelRefresh={async () => "fast"}
      {...props}
    />
  );
  if (!client) return render(panel);
  return render(
    <ClientProvider client={client} token="tok">
      {panel}
    </ClientProvider>,
  );
}

async function openPanel(props: Partial<PanelProps> = {}) {
  stubReads();
  const client = makeClient();
  renderPanel(client, props);
  await waitFor(() => expect(screen.getByTestId("writing-chat-panel")).toBeInTheDocument());
  await waitFor(() => expect(screen.getByTestId("writing-chat-input")).toBeInTheDocument());
  return { client, input: screen.getByTestId("writing-chat-input") as HTMLTextAreaElement };
}

describe("WritingChatPanel 服务端会话绑定", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("没有网关连接时如实提示，不伪造会话", () => {
    renderPanel(null);
    expect(screen.getByText("未连接网关")).toBeInTheDocument();
    expect(screen.queryByTestId("writing-chat-input")).not.toBeInTheDocument();
  });

  it("绑定失败时给出可重试的错误面板", async () => {
    stubReads();
    const client = makeClient({
      bindWritingSession: vi.fn(async () => {
        throw new Error("writing_session_unavailable");
      }),
    });
    renderPanel(client);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText("writing_session_unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("用服务端返回的 chat_id 挂载会话，并读取团队名册", async () => {
    stubReads();
    const client = makeClient();
    renderPanel(client);
    await waitFor(() => expect(screen.getByRole("heading", { name: "创作团队" })).toBeInTheDocument());

    expect(client.bindWritingSession).toHaveBeenCalledWith("a-1");
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    const popover = await screen.findByRole("dialog");
    expect(within(popover).getByText("资料研究员")).toBeInTheDocument();
    expect(within(popover).getByText("写手")).toBeInTheDocument();
  });
});

describe("WritingChatPanel 输入菜单", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
  });

  it("/ 只填入写作任务请求，不直接发送", async () => {
    const { client, input } = await openPanel();

    fireEvent.change(input, { target: { value: "/素" } });
    const menu = await screen.findByTestId("writing-slash-menu");
    fireEvent.click(within(menu).getByRole("option", { name: /查资料并整理素材/ }));

    expect(input.value).toBe("补充三个可靠的资料，并整理成文章素材。");
    expect(client.sendMessage).not.toHaveBeenCalled();
    expect(screen.getByTestId("writing-task-chip")).toBeInTheDocument();
    expect(screen.getByTestId("writing-member-chip")).toBeInTheDocument();
  });

  it("@ 点名成员后仍在同一会话，发送时带 member_id", async () => {
    const { client, input } = await openPanel();

    fireEvent.change(input, { target: { value: "@" } });
    const menu = await screen.findByTestId("writing-mention-menu");
    fireEvent.click(within(menu).getByRole("option", { name: /资料研究员/ }));

    expect(input.value).toBe("");
    expect(screen.getByTestId("writing-member-chip")).toHaveTextContent("资料研究员");

    fireEvent.change(input, { target: { value: "先查资料" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(client.sendMessage).toHaveBeenCalled());
    const call = client.sent[0];
    expect(call.chatId).toBe(SESSION.chat_id);
    expect(call.content).toBe("先查资料");
    expect(call.options?.writing).toEqual({
      article_id: "a-1",
      revision: 7,
      digest: "sha256:current",
      member_id: "writing-researcher",
    });
  });

  it("引用提纲时把提纲作为上下文前缀", async () => {
    const { client, input } = await openPanel();

    fireEvent.change(input, { target: { value: "@" } });
    const menu = await screen.findByTestId("writing-mention-menu");
    fireEvent.click(within(menu).getByRole("option", { name: "提纲" }));
    expect(screen.getByTestId("writing-context-chip")).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "按这个结构重写第一节" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(client.sendMessage).toHaveBeenCalled());
    expect(client.sent[0].content).toContain("【引用当前提纲】");
    expect(client.sent[0].content).toContain("1. 先讲问题");
  });
});

describe("WritingChatPanel 空态与发送前置条件", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
  });

  it("空态预设只填输入框，含占位符时拒绝发送", async () => {
    const { client, input } = await openPanel({
      articleSnapshot: snapshot({ title: "", body: "", outline: [], materials: [] }),
    });

    const preset = screen.getByTestId("writing-chat-preset-angles");
    fireEvent.click(preset);
    expect(client.sendMessage).not.toHaveBeenCalled();
    expect(input.value).toContain("【主题】");

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(client.sendMessage).not.toHaveBeenCalled();
  });

  it("文章还有未保存修改时不发送", async () => {
    const { client, input } = await openPanel({
      flushForWriting: async () => null,
    });

    fireEvent.change(input, { target: { value: "继续写" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(client.sendMessage).not.toHaveBeenCalled();
  });

  it("划词种子填进输入框并带上选区元数据", async () => {
    const seed: WritingChatSeed = {
      id: 1,
      text: "把这段改得更紧凑。",
      task: "condense",
      memberId: null,
      selection: { text: "正文内容", start: 0, end: 4 },
    };
    const onSeedConsumed = vi.fn();
    const { client, input } = await openPanel({ seed, onSeedConsumed });

    expect(input.value).toBe("把这段改得更紧凑。");
    expect(onSeedConsumed).toHaveBeenCalled();
    expect(screen.getByTestId("writing-selection-chip")).toBeInTheDocument();

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(client.sendMessage).toHaveBeenCalled());
    expect(client.sent[0].options?.writing).toMatchObject({
      task: "condense",
      selection: { text: "正文内容", start: 0, end: 4 },
    });
  });
});
