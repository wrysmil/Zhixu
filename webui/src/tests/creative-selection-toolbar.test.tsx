import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ArticleEditor } from "@/components/creative/ArticleEditor";
import { preloadMarkdownText } from "@/components/MarkdownText";
import type { WritingSelectionContext } from "@/lib/types";

/**
 * 划词工具条：只在编辑态出现，动作只把请求交给右栏（A05/A13），
 * 并且必须把浏览器 UTF-16 选区换算成服务端使用的 Python 码点区间。
 */

const BODY = "开头🎯结尾";

function renderEditor(overrides: Record<string, unknown> = {}) {
  const onSelectionAction = vi.fn();
  render(
    <ArticleEditor
      article={{ id: "a-1", title: "标题", body: BODY, revision: 4 } as never}
      draft={{
        title: "标题",
        writing_type: "",
        template_id: "",
        status: "draft",
        summary: "",
        brief: {},
        body: BODY,
        outline: [],
        materials: [],
        tags: [],
      } as never}
      dirty={false}
      saveState={{ phase: "saved", lastError: null } as never}
      setField={() => {}}
      onSaveNow={async () => {}}
      onAcknowledgeConflict={async () => {}}
      onFetchLatest={async () => ({}) as never}
      onSaveCopy={async () => {}}
      onBack={() => {}}
      onSelectionAction={onSelectionAction}
      {...overrides}
    />,
  );
  return onSelectionAction;
}

function selectRange(start: number, end: number) {
  const textarea = screen.getByTestId("article-body-input") as HTMLTextAreaElement;
  textarea.setSelectionRange(start, end);
  fireEvent.select(textarea);
  return textarea;
}

describe("ArticleEditor 划词工具条", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
  });

  it("选中文字后出现工具条，动作携带码点选区", () => {
    const onSelectionAction = renderEditor();

    expect(screen.queryByTestId("selection-toolbar")).not.toBeInTheDocument();

    selectRange(4, 6);
    expect(screen.getByTestId("selection-toolbar")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("selection-action-ask"));
    const [context, action] = onSelectionAction.mock.calls[0] as [WritingSelectionContext, string];
    expect(action).toBe("ask");
    expect(context.text).toBe("结尾");
    // 「🎯」在 UTF-16 里占 2 个单位、在码点里占 1 个：4..6 必须映射为 3..5。
    expect([context.start, context.end]).toEqual([3, 5]);
    expect(context.prefix).toBe("开头🎯");
  });

  it("改写/精简/扩写各自派发对应动作，「更多」里补低频动作", () => {
    const onSelectionAction = renderEditor();
    selectRange(0, 2);

    fireEvent.click(screen.getByTestId("selection-action-rewrite"));
    fireEvent.click(screen.getByTestId("selection-action-condense"));
    fireEvent.click(screen.getByTestId("selection-action-expand"));
    expect(onSelectionAction.mock.calls.map((call) => call[1])).toEqual([
      "rewrite",
      "condense",
      "expand",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    fireEvent.click(screen.getByRole("button", { name: "核实说法" }));
    expect(onSelectionAction.mock.calls[3][1]).toBe("verify");
  });

  it("Cmd/Ctrl+I 等价于问 AI；预览模式不提供写选区", () => {
    const onSelectionAction = renderEditor();
    const textarea = selectRange(0, 2);
    fireEvent.keyUp(textarea, { key: "i", metaKey: true });
    expect(onSelectionAction).toHaveBeenCalled();
    expect(onSelectionAction.mock.calls[0][1]).toBe("ask");

    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    expect(screen.queryByTestId("article-body-input")).not.toBeInTheDocument();
    expect(screen.queryByTestId("selection-toolbar")).not.toBeInTheDocument();
  });

  it("划词动作会自动打开收起的创作团队右栏", () => {
    renderEditor({ writingChat: <div data-testid="fake-chat">团队</div> });
    fireEvent.click(screen.getByTestId("partner-toggle"));
    expect(screen.queryByTestId("fake-chat")).not.toBeInTheDocument();

    selectRange(0, 2);
    fireEvent.click(screen.getByTestId("selection-action-ask"));

    expect(screen.getByTestId("creative-partner-column")).toBeInTheDocument();
    expect(screen.getByTestId("fake-chat")).toBeInTheDocument();
  });

  it("没有注入右栏回调时不显示工具条", () => {
    renderEditor({ onSelectionAction: undefined });
    selectRange(0, 2);
    expect(screen.queryByTestId("selection-toolbar")).not.toBeInTheDocument();
  });
});
