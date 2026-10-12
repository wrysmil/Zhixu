import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import type { WebUIMutationTransport } from "@/lib/api";
import { preloadMarkdownText } from "@/components/MarkdownText";
import { mergeImeValue } from "@/components/creative/use-article-save-flow";

const DEMO_TITLE = "从零构建 Agent 的记忆系统";

function renderPreview() {
  const props: CreativeWorkspaceProps = {
    getToken: () => "",
    transport: {
      requestMutation: async () => {
        throw new Error("preview must not use the injected transport");
      },
    } as unknown as WebUIMutationTransport,
    workspacePath: "/preview-ime",
    preview: true,
  };
  return render(<CreativeWorkspace {...props} />);
}

function openDemoBody(): HTMLTextAreaElement {
  renderPreview();
  fireEvent.click(screen.getByText(DEMO_TITLE));
  const body = screen.getByTestId("article-body-input");
  expect(body.tagName).toBe("TEXTAREA");
  expect(body.getAttribute("aria-label")).toBe("正文编辑");
  return body as HTMLTextAreaElement;
}

async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/**
 * 模拟输入法把组合串写进 DOM：走原生 value setter，绕开 React 的 value tracker，
 * 否则 `input` 事件会被 React 判定为「值没变」，用例就成了空断言。
 */
function typeImeText(el: HTMLTextAreaElement, next: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  if (!setter) throw new Error("HTMLTextAreaElement.prototype.value setter is missing");
  setter.call(el, next);
  el.setSelectionRange(next.length, next.length);
  fireEvent.input(el);
}

describe("mergeImeValue (A-T04 pure merge)", () => {
  it("takes the composed text when nothing else changed the body", () => {
    expect(mergeImeValue("原文", "原文", "原文拼音")).toBe("原文拼音");
  });

  it("keeps the store value when the editor text did not change", () => {
    expect(mergeImeValue("远端更新", "原文", "原文")).toBe("远端更新");
  });

  it("returns null when both sides changed so nothing is overwritten", () => {
    expect(mergeImeValue("远端更新", "原文", "原文拼音")).toBeNull();
  });
});

describe("ArticleEditor body input (A3.3)", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
  });

  it("does not commit anything while the IME is composing", async () => {
    const body = openDemoBody();
    const initial = body.value;
    expect(screen.getByTestId("save-phase-label").textContent).toContain("已保存");

    fireEvent.compositionStart(body);
    const composing = `${initial}拼音`;
    typeImeText(body, composing);
    await flushMicrotasks();

    // 组合期间不提交：保存阶段没被推进，DOM 值与光标都归输入法所有。
    expect(screen.getByTestId("save-phase-label").textContent).toContain("已保存");
    expect(body.value).toBe(composing);
    expect(body.selectionStart).toBe(composing.length);
    expect(body.selectionEnd).toBe(composing.length);

    fireEvent.compositionEnd(body, { data: "拼音" });
    await flushMicrotasks();

    expect(screen.getByTestId("save-phase-label").textContent).toContain("有未保存的修改");
    expect(body.value).toBe(composing);

    // 组合串最终落到工作副本：预览渲染的是 draft.body，出现即证明没有丢字。
    fireEvent.click(screen.getByRole("button", { name: "分屏" }));
    await waitFor(() =>
      expect(screen.getByTestId("article-preview-pane").textContent).toContain("拼音"),
    );
    expect(body.value).toBe(composing);
  });

  it("keeps pasted indentation and line breaks and survives mode switches", async () => {
    const body = openDemoBody();
    const pasted = "```python\n    await memory.consider(session_id)\n```";
    fireEvent.change(body, { target: { value: pasted } });
    expect(body.value).toBe(pasted);

    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    await waitFor(() =>
      expect(screen.getByTestId("article-preview-pane").textContent).toContain(
        "    await memory.consider(session_id)",
      ),
    );

    // A-03：切回编辑不能丢内容。
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    await waitFor(() =>
      expect(screen.getByTestId("article-body-input")).toHaveValue(pasted),
    );
  });

  it("renders edit and preview together in split mode", async () => {
    const body = openDemoBody();
    fireEvent.change(body, { target: { value: "## 分屏标题\n\n正文内容" } });

    fireEvent.click(screen.getByRole("button", { name: "分屏" }));
    expect(screen.getByTestId("article-body-input")).toHaveValue("## 分屏标题\n\n正文内容");
    await waitFor(() =>
      expect(screen.getByTestId("article-preview-pane").textContent).toContain("正文内容"),
    );
  });

  it("counts characters instead of words", async () => {
    const body = openDemoBody();
    fireEvent.change(body, { target: { value: "ab cde  f" } });

    const stats = screen.getByTestId("body-stats").textContent ?? "";
    // 去空白后是 6 个字符（countBodyChars），而不是任何「词」数口径。
    expect(stats).toContain("6");
    expect(stats).not.toContain("词");
  });

  it("fixes the body DOM id for the merge flow", () => {
    const body = openDemoBody();
    expect(body.id).toBe("article-body-input");
  });
});
