// WU-02：创作搭档侧栏的模型 chip。
//
// Source: .ai-runtime-artifacts/specs/2026-10-17-writing-partner-model-selection-spec.md §4.3/§4.4/§6
//
// 覆盖口径：
// 1. chip 停在 header 标题行，显示当前 preset 与会话解析出的模型（模型名在 pill 的 title 里，
//    与聊天侧 ThreadShell 同一呈现口径）；
// 2. 会话还没建立时显示**全局默认** preset 并如实标注（不假装已选会话 preset）；
// 3. 切换走既有系统命令 `/model <preset>`，并按服务端读回值确认；失败 / 未生效都必须可见；
// 4. 没有可用 preset / 未配置时走 `ModelPresetBadge` 既有 needsSetup 行为，点进模型设置；
// 5. 只影响之后新建的批次（切换后的文案说明这一点）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import {
  WritingPartnerPanel,
  type WritingPartnerPanelProps,
} from "@/components/creative/WritingPartnerPanel";
import type { CollabFrameClient } from "@/components/creative/use-writing-collab";
import type { ModelPresetOption } from "@/components/thread/ModelPresetBadge";
import { preloadMarkdownText } from "@/components/MarkdownText";
import type { WebUIMutationTransport } from "@/lib/api";
import type { SettingsPayload } from "@/lib/types";
import { createEmptyCollabSnapshot, type CollabApi } from "@/lib/writing-collab-api";

const ARTICLE_ID = "srv-1";
const CHAT_ID = `${ARTICLE_ID}:partner`;
const SESSION_KEY = `websocket:${CHAT_ID}`;

const PRESET_OPTIONS: ModelPresetOption[] = [
  {
    name: "fast",
    model: "openai-codex/gpt-5.5",
    provider: "openai_codex",
    reasoningEffort: null,
  },
  {
    name: "slow",
    model: "deepseek/deepseek-v4-pro",
    provider: "deepseek",
    reasoningEffort: null,
  },
];

function fakeCollabApi(): CollabApi {
  const empty = createEmptyCollabSnapshot();
  const unused = async () => {
    throw new Error("collab api is not exercised in this test");
  };
  return {
    getSnapshot: async () => empty,
    getArtifact: unused,
    start: unused,
    reply: unused,
    confirm: unused,
    cancel: unused,
    retry: unused,
    apply: unused,
    undo: unused,
  } as unknown as CollabApi;
}

function modelClient(
  sendSystemCommand?: (chatId: string, command: string, timeoutMs?: number) => Promise<void>,
): CollabFrameClient {
  const client: CollabFrameClient = {
    requestMutation: (async () => ({})) as unknown as CollabFrameClient["requestMutation"],
  };
  if (sendSystemCommand) client.sendSystemCommand = sendSystemCommand;
  return client;
}

function renderPanel(overrides: Partial<WritingPartnerPanelProps> = {}) {
  return render(
    <WritingPartnerPanel
      articleId={ARTICLE_ID}
      articleTitle="记忆系统"
      articleBody="正文"
      articleRevision={3}
      snapshotDigest="digest-1"
      chatId={CHAT_ID}
      client={modelClient()}
      api={fakeCollabApi()}
      {...overrides}
    />,
  );
}

describe("WritingPartnerPanel 模型 chip（WU-02）", () => {
  it("chip 显示协作会话解析出的 preset 与模型", async () => {
    renderPanel({
      modelPresets: PRESET_OPTIONS,
      modelPreset: "fast",
      modelDetail: "openai-codex/gpt-5.5",
      modelProvider: "openai_codex",
      modelProviderLabel: "OpenAI Codex",
    });

    const chip = await screen.findByRole("button", { name: "fast" });
    expect(chip).toHaveTextContent("fast");
    // 与聊天侧同口径：模型名在 pill 的 title 上（preset · model · provider）。
    expect(screen.getByTitle("fast · openai-codex/gpt-5.5 · OpenAI Codex")).toBeInTheDocument();
  });

  it("会话还不存在时显示全局默认 preset，并如实标注来源", async () => {
    renderPanel({
      modelPresets: PRESET_OPTIONS,
      modelPreset: "default",
      modelDetail: "deepseek-v4-pro",
      modelProvider: "deepseek",
      modelProviderLabel: "DeepSeek",
      modelPresetFromFallback: true,
    });

    expect(await screen.findByRole("button", { name: "default" })).toBeInTheDocument();
    const hint = document.querySelector("[data-partner-model-fallback]");
    expect(hint).not.toBeNull();
    expect(hint).toHaveTextContent(/global default/i);
  });

  it("切换 preset 走既有 /model 系统命令，并按读回值确认", async () => {
    const sendSystemCommand = vi.fn(async () => {});
    const onModelRefresh = vi.fn(async () => "fast");
    renderPanel({
      modelPresets: PRESET_OPTIONS,
      modelPreset: "default",
      modelDetail: "deepseek-v4-pro",
      modelPresetFromFallback: true,
      client: modelClient(sendSystemCommand),
      onModelRefresh,
    });

    fireEvent.click(await screen.findByRole("button", { name: "default" }));
    fireEvent.click(await screen.findByRole("option", { name: "fast" }));

    await waitFor(() => expect(sendSystemCommand).toHaveBeenCalledWith(CHAT_ID, "/model fast"));
    expect(sendSystemCommand).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onModelRefresh).toHaveBeenCalled());
    // 读回值生效：chip 显示新 preset，并说明只影响之后的新批次。
    await waitFor(() => expect(screen.getByRole("button", { name: "fast" })).toBeInTheDocument());
    await waitFor(() =>
      expect(document.querySelector("[data-partner-model-notice]")).toHaveTextContent(
        /Switched to fast/i,
      ),
    );
    expect(document.querySelector("[data-partner-model-notice]")).toHaveTextContent(/new batches/i);
  });

  it("切换失败时给出可见反馈，并保留原 preset", async () => {
    const sendSystemCommand = vi.fn(async () => {
      throw new Error("command failed");
    });
    renderPanel({
      modelPresets: PRESET_OPTIONS,
      modelPreset: "default",
      modelDetail: "deepseek-v4-pro",
      client: modelClient(sendSystemCommand),
    });

    fireEvent.click(await screen.findByRole("button", { name: "default" }));
    fireEvent.click(await screen.findByRole("option", { name: "fast" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/couldn't switch/i),
    );
    // 不假装已切换：chip 仍是原值。
    expect(screen.getByRole("button", { name: "default" })).toBeInTheDocument();
  });

  it("服务端读回值没变时如实报「未生效」，不静默", async () => {
    const sendSystemCommand = vi.fn(async () => {});
    const onModelRefresh = vi.fn(async () => "default");
    renderPanel({
      modelPresets: PRESET_OPTIONS,
      modelPreset: "default",
      modelDetail: "deepseek-v4-pro",
      client: modelClient(sendSystemCommand),
      onModelRefresh,
    });

    fireEvent.click(await screen.findByRole("button", { name: "default" }));
    fireEvent.click(await screen.findByRole("option", { name: "fast" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/still using default/i),
    );
    expect(screen.getByRole("button", { name: "default" })).toBeInTheDocument();
  });

  it("连接层没有系统命令通道时给出可见错误（不静默吞掉）", async () => {
    renderPanel({
      modelPresets: PRESET_OPTIONS,
      modelPreset: "default",
      modelDetail: "deepseek-v4-pro",
      client: modelClient(),
    });

    fireEvent.click(await screen.findByRole("button", { name: "default" }));
    fireEvent.click(await screen.findByRole("option", { name: "fast" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/can't switch models/i),
    );
  });

  it("没有可用 preset 时走既有 needsSetup 行为跳模型设置", async () => {
    const onManageModels = vi.fn();
    renderPanel({
      modelPresets: [],
      modelNeedsSetup: true,
      onManageModels,
    });

    const chip = await screen.findByRole("button", { name: "Choose a model" });
    fireEvent.click(chip);

    expect(onManageModels).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// 集成层：CreativeWorkspace 把 settings.model_presets 与会话 preset 注入面板
// ---------------------------------------------------------------------------

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

function listBody(rows: Record<string, unknown>[] = []) {
  return { articles: rows, total: rows.length, limit: 50, offset: 0 };
}

function summaryRow(overrides: Record<string, unknown> = {}) {
  return {
    id: ARTICLE_ID,
    title: "文章标题",
    summary: "",
    status: "draft",
    writing_type: "",
    tags: [],
    revision: 3,
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-02T00:00:00+00:00",
    ...overrides,
  };
}

function wireArticle(overrides: Record<string, unknown> = {}) {
  return {
    id: ARTICLE_ID,
    title: "文章标题",
    writing_type: "",
    template_id: "",
    status: "draft",
    summary: "",
    brief: {},
    body: "正文",
    outline: [],
    materials: [],
    tags: [],
    revision: 3,
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-02T00:00:00+00:00",
    ...overrides,
  };
}

function presetRow(name: string, model: string, provider: string, isDefault = false) {
  return {
    name,
    label: name,
    active: isDefault,
    is_default: isDefault,
    model,
    provider,
    max_tokens: 4096,
    context_window_tokens: 65_536,
    temperature: 0.7,
    reasoning_effort: null,
  };
}

function partnerSettings(
  modelPresets: ReturnType<typeof presetRow>[],
  globalPreset: string | null = "default",
): SettingsPayload {
  return {
    agent: {
      model: "deepseek-v4-pro",
      provider: "deepseek",
      resolved_provider: "deepseek",
      has_api_key: true,
      model_preset: globalPreset,
      max_tokens: 4096,
      context_window_tokens: 65_536,
      temperature: 0.7,
      reasoning_effort: null,
      timezone: "UTC",
      tool_hint_max_length: 40,
    },
    model_presets: modelPresets,
    model_call_order: [],
    model_call_order_editable: false,
    providers: [
      { name: "deepseek", label: "DeepSeek", configured: true },
      { name: "openai_codex", label: "OpenAI Codex", configured: true },
    ],
  } as unknown as SettingsPayload;
}

/** `/api/sessions` 是 snake_case 线格式（api.ts `listSessions` 负责映射）。 */
function sessionRow(modelPreset: string | null): Record<string, unknown> {
  return { key: SESSION_KEY, model_preset: modelPreset };
}

function stubFetch(options: {
  settings: SettingsPayload;
  sessions: Record<string, unknown>[];
}): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: unknown) => {
    const url = String(input);
    if (url.includes("/api/settings")) return jsonResponse(options.settings);
    if (url.includes("/api/sessions")) return jsonResponse({ sessions: options.sessions });
    if (url.includes("/api/writing/collab")) {
      return jsonResponse({
        collaboration: null,
        artifacts: [],
        handoffs: [],
        suggestions: [],
      });
    }
    if (url.includes(ARTICLE_ID)) return jsonResponse({ article: wireArticle() });
    return jsonResponse(listBody([summaryRow()]));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock as unknown as ReturnType<typeof vi.fn>;
}

function workspaceProps(sendSystemCommand: ReturnType<typeof vi.fn>): CreativeWorkspaceProps {
  const transport = {
    requestMutation: vi.fn(async () => ({})),
    sendSystemCommand,
    onSessionUpdate: vi.fn(() => () => {}),
  } as unknown as WebUIMutationTransport;
  return { getToken: () => "t", transport, workspacePath: "/workspace" };
}

async function openArticle(): Promise<void> {
  fireEvent.click(await screen.findByText("文章标题"));
  await screen.findByTestId("creative-partner-column");
}

describe("CreativeWorkspace 注入协作会话模型（WU-02）", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("显示协作会话的 model_preset，切换发送 /model 系统命令", async () => {
    stubFetch({
      settings: partnerSettings([
        presetRow("default", "deepseek-v4-pro", "deepseek", true),
        presetRow("fast", "openai-codex/gpt-5.5", "openai_codex"),
        presetRow("slow", "deepseek/deepseek-v4-pro", "deepseek"),
      ]),
      sessions: [sessionRow("fast")],
    });
    const sendSystemCommand = vi.fn(async () => {});

    render(<CreativeWorkspace {...workspaceProps(sendSystemCommand)} />);
    await openArticle();

    const chip = await screen.findByRole("button", { name: "fast" });
    expect(screen.getByTitle(/gpt-5\.5/)).toBeInTheDocument();

    fireEvent.click(chip);
    fireEvent.click(await screen.findByRole("option", { name: "slow" }));

    await waitFor(() =>
      expect(sendSystemCommand).toHaveBeenCalledWith(CHAT_ID, "/model slow"),
    );
  });

  it("协作会话不存在时显示全局默认 preset，并标注还没为本篇选过模型", async () => {
    stubFetch({
      settings: partnerSettings([
        presetRow("default", "deepseek-v4-pro", "deepseek", true),
        presetRow("fast", "openai-codex/gpt-5.5", "openai_codex"),
      ]),
      sessions: [],
    });

    render(<CreativeWorkspace {...workspaceProps(vi.fn(async () => {}))} />);
    await openArticle();

    expect(await screen.findByRole("button", { name: "default" })).toBeInTheDocument();
    expect(document.querySelector("[data-partner-model-fallback]")).not.toBeNull();
  });

  it("没有可用 preset 时点击 chip 跳到模型设置（复用既有路由，不造新跳转）", async () => {
    stubFetch({ settings: partnerSettings([]), sessions: [] });

    render(<CreativeWorkspace {...workspaceProps(vi.fn(async () => {}))} />);
    await openArticle();

    fireEvent.click(await screen.findByRole("button", { name: "Choose a model" }));

    expect(window.location.hash).toBe("#/settings?section=models");
  });
});
