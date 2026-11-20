import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import { setAppLanguage } from "@/i18n";
import type { UIMessage } from "@/lib/types";

const requestMutation = vi.fn();

vi.mock("@/providers/ClientProvider", () => ({
  useOptionalClient: () => ({ client: { requestMutation } }),
}));

function assistantMessage(overrides: Partial<UIMessage> = {}): UIMessage {
  return {
    id: "m1",
    role: "assistant",
    content: "你好",
    createdAt: 1,
    ...overrides,
  };
}

describe("MessageBubble feedback", () => {
  beforeEach(async () => {
    requestMutation.mockReset();
    requestMutation.mockResolvedValue({ ok: true });
    await setAppLanguage("zh-CN");
  });

  it("sends a helpful score for an assistant message with trace id", async () => {
    render(
      <MessageBubble
        message={assistantMessage({ traceId: "a".repeat(32) })}
      />,
    );

    fireEvent.click(screen.getByLabelText("有帮助"));

    await waitFor(() =>
      expect(requestMutation).toHaveBeenCalledWith("feedback.submit", {
        trace_id: "a".repeat(32),
        helpful: true,
      }),
    );
  });

  it("sends an unhelpful score", async () => {
    render(
      <MessageBubble
        message={assistantMessage({ traceId: "b".repeat(32) })}
      />,
    );

    fireEvent.click(screen.getByLabelText("没帮助"));

    await waitFor(() =>
      expect(requestMutation).toHaveBeenCalledWith("feedback.submit", {
        trace_id: "b".repeat(32),
        helpful: false,
      }),
    );
  });

  it("shows a failure message when the gateway rejects the score", async () => {
    requestMutation.mockRejectedValue(new Error("boom"));
    render(
      <MessageBubble
        message={assistantMessage({ traceId: "c".repeat(32) })}
      />,
    );

    fireEvent.click(screen.getByLabelText("有帮助"));

    expect(await screen.findByText("反馈失败")).toBeTruthy();
  });

  it("adds the trace to the annotation queue", async () => {
    render(
      <MessageBubble
        message={assistantMessage({ traceId: "f".repeat(32) })}
      />,
    );

    fireEvent.click(screen.getByLabelText("加入人工标注"));

    await waitFor(() =>
      expect(requestMutation).toHaveBeenCalledWith("annotation.add", {
        trace_id: "f".repeat(32),
      }),
    );
  });

  it("hides feedback buttons without a trace id", () => {
    render(<MessageBubble message={assistantMessage()} />);

    expect(screen.queryByLabelText("有帮助")).toBeNull();
    expect(screen.queryByLabelText("没帮助")).toBeNull();
  });

  it("restores persisted feedback state", () => {
    render(
      <MessageBubble
        message={assistantMessage({
          traceId: "d".repeat(32),
          feedback: "up",
        })}
      />,
    );

    expect(screen.getByLabelText("有帮助").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByLabelText("没帮助").getAttribute("aria-pressed")).toBe("false");
  });
});
