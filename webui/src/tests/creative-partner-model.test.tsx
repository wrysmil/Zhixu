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
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  WritingPartnerPanel,
  type WritingPartnerPanelProps,
} from "@/components/creative/WritingPartnerPanel";
import type { CollabFrameClient } from "@/components/creative/use-writing-collab";
import type { ModelPresetOption } from "@/components/thread/ModelPresetBadge";
import { createEmptyCollabSnapshot, type CollabApi } from "@/lib/writing-collab-api";

const ARTICLE_ID = "srv-1";
const CHAT_ID = `${ARTICLE_ID}:partner`;

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
