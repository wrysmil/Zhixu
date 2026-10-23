import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import type { WebUIMutationTransport } from "@/lib/api";
import type { WritingChangedEvent } from "@/lib/types";
import { preloadMarkdownText } from "@/components/MarkdownText";

/**
 * 团队写入与作者本地稿的并发口径（A11/A12/A14）：
 * - 干净稿件：收到 writing_changed 后拉最新版、显示前后对比，可撤销；
 * - 有未保存修改：只提示，绝不覆盖作者本地稿；
 * - 撤销走 writing.undo_change，并以事件里的 revision 做 CAS。
 */

const ARTICLE_ID = "srv-1";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

function wireArticle(revision: number, body: string, digest: string) {
  return {
    id: ARTICLE_ID,
    title: "服务器上的文章",
    writing_type: "",
    template_id: "",
    status: "draft",
    summary: "",
    brief: {},
    body,
    outline: [],
    materials: [],
    tags: [],
    revision,
    digest,
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-02T00:00:00+00:00",
  };
}

const AUTHOR_BODY = "作者自己的正文";
const TEAM_BODY = "团队改过的正文，补充了资料出处。";
const REVERTED_BODY = "回到作者原来的写法。";

function changedEvent(overrides: Partial<WritingChangedEvent> = {}): WritingChangedEvent {
  return {
    event: "writing_changed",
    chat_id: "chat-1",
    article_id: ARTICLE_ID,
    revision: 4,
    digest: "digest-team",
    change_id: "chg-1",
    kind: "body",
    actor: "writing-author",
    actor_name: "写手",
    ...overrides,
  };
}

function setup(options: { saveFails?: boolean } = {}) {
  let serverRevision = 3;
  let serverBody = AUTHOR_BODY;
  let serverDigest = "digest-author";
  const handlers = new Set<(event: WritingChangedEvent) => void>();
  const requestMutation = vi.fn(async (action: string, payload: Record<string, unknown>) => {
    if (action === "article.save") {
      const base = Number(payload.base_revision);
      // 服务端按 base_revision 做 CAS：团队版本更新后，旧的本地基线要被判冲突。
      if (base < serverRevision) {
        throw Object.assign(
          new Error(`article_conflict current_revision=${serverRevision} article_id=${ARTICLE_ID}`),
          { status: 409 },
        );
      }
      if (options.saveFails) throw new Error("offline");
      serverRevision = base + 1;
      serverBody = String((payload as { snapshot?: { body?: string } }).snapshot?.body ?? serverBody);
      serverDigest = "digest-local";
      return {
        article_id: ARTICLE_ID,
        revision: serverRevision,
        digest: serverDigest,
        updated_at: "2026-02-02T00:00:00+00:00",
        no_change: false,
      };
    }
    if (action === "writing.undo_change") {
      serverRevision = Number(payload.base_revision) + 1;
      serverBody = REVERTED_BODY;
      serverDigest = "digest-reverted";
      return {
        article_id: ARTICLE_ID,
        change_id: String(payload.change_id),
        revision: serverRevision,
        digest: serverDigest,
        no_change: false,
      };
    }
    throw new Error(`unexpected action ${action}`);
  });
  const transport = {
    requestMutation,
    onWritingChanged: (handler: (event: WritingChangedEvent) => void) => {
      handlers.add(handler);
      return () => handlers.delete(handler);
    },
  } as unknown as WebUIMutationTransport;

  const fetchMock = vi.fn(async (input: unknown) => {
    const url = String(input);
    if (url.includes("/articles/get")) {
      return jsonResponse({
        article: wireArticle(serverRevision, serverBody, serverDigest),
      });
    }
    return jsonResponse({
      articles: [
        {
          id: ARTICLE_ID,
          title: "服务器上的文章",
          summary: "",
          status: "draft",
          writing_type: "",
          tags: [],
          revision: serverRevision,
          created_at: "2026-01-01T00:00:00+00:00",
          updated_at: "2026-01-02T00:00:00+00:00",
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
  });
  vi.stubGlobal("fetch", fetchMock);

  const emit = (event: WritingChangedEvent) => {
    for (const handler of handlers) handler(event);
  };
  const teamWrites = () => {
    serverRevision = 4;
    serverBody = TEAM_BODY;
    serverDigest = "digest-team";
  };

  return { transport, requestMutation, emit, teamWrites, server: () => serverBody };
}

async function openArticle(transport: WebUIMutationTransport) {
  const props: CreativeWorkspaceProps = {
    getToken: () => "token",
    transport,
    workspacePath: "/team-sync",
  };
  render(<CreativeWorkspace {...props} />);
  fireEvent.click(await screen.findByTestId("open-article-srv-1"));
  await waitFor(() => expect(screen.getByTestId("article-body-input")).toBeInTheDocument());
}

function payloadFor(
  requestMutation: ReturnType<typeof vi.fn>,
  action: string,
): Record<string, unknown> | undefined {
  const call = requestMutation.mock.calls.find(
    (entry: unknown[]) => entry[0] === action,
  ) as [string, Record<string, unknown>] | undefined;
  return call?.[1];
}

describe("创作团队写入同步", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("干净稿自动载入团队版本，给出对比与撤销", async () => {
    const env = setup();
    await openArticle(env.transport);

    env.teamWrites();
    env.emit(changedEvent());

    await waitFor(() => expect(screen.getByTestId("writing-remote-banner")).toBeInTheDocument());
    expect(screen.getByTestId("article-body-input")).toHaveValue(TEAM_BODY);
    expect(screen.getByText(/团队修改了正文（写手）/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看对比" }));
    const banner = screen.getByTestId("writing-remote-banner");
    expect(within(banner).getByText("修改前")).toBeInTheDocument();
    expect(within(banner).getAllByText(AUTHOR_BODY).length).toBeGreaterThan(0);
    expect(within(banner).getAllByText(TEAM_BODY).length).toBeGreaterThan(0);

    fireEvent.click(within(banner).getByRole("button", { name: /撤销这次修改/ }));
    await waitFor(() =>
      expect(payloadFor(env.requestMutation, "writing.undo_change")).toMatchObject({
        change_id: "chg-1",
        base_revision: 4,
      }),
    );
    await waitFor(() => expect(screen.getByTestId("article-body-input")).toHaveValue(REVERTED_BODY));
    expect(screen.queryByTestId("writing-remote-banner")).not.toBeInTheDocument();
  });

  it("本地有未保存修改时只提示，不覆盖作者稿", async () => {
    const env = setup();
    await openArticle(env.transport);

    fireEvent.change(screen.getByTestId("article-body-input"), {
      target: { value: "作者正在写的段落" },
    });
    env.teamWrites();
    env.emit(changedEvent());

    await waitFor(() => expect(screen.getByTestId("writing-remote-banner")).toBeInTheDocument());
    expect(screen.getByTestId("article-body-input")).toHaveValue("作者正在写的段落");
    expect(screen.getByText(/本地有未保存的修改，未覆盖本地稿/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "忽略" }));
    await waitFor(() => expect(screen.queryByTestId("writing-remote-banner")).not.toBeInTheDocument());
    expect(screen.getByTestId("article-body-input")).toHaveValue("作者正在写的段落");
  });

  it("本地稿基线已被团队版本超过时，保存被判冲突且不覆盖任何一方", async () => {
    const env = setup();
    await openArticle(env.transport);

    fireEvent.change(screen.getByTestId("article-body-input"), {
      target: { value: "作者正在写的段落" },
    });
    env.teamWrites();
    env.emit(changedEvent());
    await waitFor(() => expect(screen.getByTestId("writing-remote-banner")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /保存并载入团队版本/ }));
    await waitFor(() =>
      expect(payloadFor(env.requestMutation, "article.save")).toMatchObject({
        article_id: ARTICLE_ID,
        base_revision: 3,
        snapshot: { body: "作者正在写的段落" },
      }),
    );
    // CAS 失败：本地稿仍在编辑器里，团队版本也没有被静默写入。
    await waitFor(() => expect(screen.getByTestId("article-body-input")).toHaveValue("作者正在写的段落"));
    expect(screen.getByTestId("article-body-input")).toHaveValue("作者正在写的段落");
  });

  it("其他文章的写入事件不影响当前稿", async () => {
    const env = setup();
    await openArticle(env.transport);

    env.emit(changedEvent({ article_id: "other-article" }));
    await new Promise((resolve) => window.setTimeout(resolve, 220));

    expect(screen.queryByTestId("writing-remote-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("article-body-input")).toHaveValue(AUTHOR_BODY);
  });
});
