import type { ComponentProps } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ArticleList } from "@/components/creative/ArticleList";
import en from "@/i18n/locales/en/common.json";
import zhCN from "@/i18n/locales/zh-CN/common.json";

type Props = ComponentProps<typeof ArticleList>;

async function renderHome(overrides: Partial<Props> = {}, language = "zh-CN") {
  // 独立实例使用真实资源，避免改变其他用例使用的全局语言。
  // https://www.i18next.com/overview/api#createinstance
  const i18n = createInstance();
  await i18n.init({
    lng: language,
    fallbackLng: false,
    defaultNS: "common",
    resources: { en: { common: en }, "zh-CN": { common: zhCN } },
    interpolation: { escapeValue: false },
  });
  const props: Props = {
    articles: [], totalCount: 0, listState: "empty", preview: false,
    actionError: null, query: "", statusFilter: "all",
    onQueryChange: vi.fn(), onStatusFilterChange: vi.fn(), onOpen: vi.fn(),
    onCreate: vi.fn(async () => ({ error: null })),
    onDuplicate: vi.fn(), onDelete: vi.fn(), onRetry: vi.fn(),
    onDismissActionError: vi.fn(),
    ...overrides,
  };
  const view = render(<I18nextProvider i18n={i18n}><ArticleList {...props} /></I18nextProvider>);
  const title = () => screen.getByRole("textbox", { name: i18n.t("creative.topic_label") });
  return { ...view, props, i18n, title };
}

afterEach(cleanup);

describe("创作首页的标题表单", () => {
  it("明确显示文章标题标签，并使用单行输入框", async () => {
    await renderHome();
    expect(screen.getByText("文章标题", { selector: "label" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "文章标题" }).tagName).toBe("INPUT");
    expect(screen.getByRole("button", { name: "创建草稿" })).toBeEnabled();
    expect(screen.getByTestId("create-article-form")).toBeInTheDocument();
  });

  it("在标题输入框按 Enter 创建草稿", async () => {
    const { title, props } = await renderHome();
    const user = userEvent.setup();
    await user.type(title(), "新的理解{Enter}");
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith("新的理解", undefined));
  });

  it("中文输入法确认候选词时不提交，组合输入结束后可按 Enter 创建", async () => {
    const { title, props } = await renderHome();
    const input = title();
    fireEvent.change(input, { target: { value: "新的理解" } });
    fireEvent.compositionStart(input);
    expect(fireEvent.keyDown(input, { key: "Enter", code: "Enter", isComposing: true })).toBe(false);
    expect(props.onCreate).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    input.focus();
    await userEvent.setup().keyboard("{Enter}");
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledTimes(1));
  });

  it("Safari 候选词确认的 keyCode 229 不触发创建", async () => {
    const { title, props } = await renderHome();
    fireEvent.change(title(), { target: { value: "输入法候选词" } });
    expect(fireEvent.keyDown(title(), { key: "Enter", code: "Enter", keyCode: 229 })).toBe(false);
    expect(props.onCreate).not.toHaveBeenCalled();
  });

  it("选择模板只更新选择并聚焦标题，创建时才传递模板", async () => {
    const { title, props } = await renderHome();
    const template = screen.getByTestId("template-source-reading");
    fireEvent.click(template);
    expect(template).toHaveAttribute("aria-pressed", "true");
    expect(title()).toHaveFocus();
    expect(props.onCreate).not.toHaveBeenCalled();
    expect(screen.getByText("已选模板：源码阅读")).toBeVisible();
    fireEvent.change(title(), { target: { value: "调用链笔记" } });
    fireEvent.submit(title().closest("form")!);
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith("调用链笔记", "source-reading"));
  });

  it("同一事件批次的重复提交只创建一次，等待时显示创建状态", async () => {
    let resolveCreate!: (value: { error: null }) => void;
    const onCreate = vi.fn(() => new Promise<{ error: null }>((resolve) => { resolveCreate = resolve; }));
    const { title } = await renderHome({ onCreate });
    fireEvent.change(title(), { target: { value: "只创建一次" } });
    const form = title().closest("form")!;
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "创建中…" })).toBeDisabled();
    await act(async () => { resolveCreate({ error: null }); });
    expect(title()).toHaveValue("");
    expect(screen.getByRole("button", { name: "创建草稿" })).toBeEnabled();
  });

  it("创建失败后保留标题、模板并允许重试", async () => {
    const onCreate = vi.fn().mockResolvedValueOnce({ error: "连接中断，请重试" }).mockResolvedValueOnce({ error: null });
    const { title } = await renderHome({ onCreate });
    fireEvent.click(screen.getByTestId("template-learning-summary"));
    fireEvent.change(title(), { target: { value: "尚未完成的理解" } });
    fireEvent.submit(title().closest("form")!);
    expect(await screen.findByTestId("create-error")).toHaveTextContent("连接中断，请重试");
    expect(title()).toHaveValue("尚未完成的理解");
    expect(title()).toHaveFocus();
    expect(screen.getByTestId("template-learning-summary")).toHaveAttribute("aria-pressed", "true");
    fireEvent.submit(title().closest("form")!);
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(2));
    expect(onCreate).toHaveBeenLastCalledWith("尚未完成的理解", "learning-summary");
    await waitFor(() => expect(title()).toHaveValue(""));
    expect(screen.getByTestId("template-learning-summary")).toHaveAttribute("aria-pressed", "false");
  });

  it("请求抛出异常时保留标题并恢复创建按钮", async () => {
    const onCreate = vi.fn().mockRejectedValue(new Error("network unavailable"));
    const { title, i18n } = await renderHome({ onCreate });
    fireEvent.change(title(), { target: { value: "待重试的文章" } });
    fireEvent.submit(title().closest("form")!);
    expect(await screen.findByTestId("create-error")).toHaveTextContent(i18n.t("creative.createFailed"));
    expect(title()).toHaveValue("待重试的文章");
    expect(screen.getByRole("button", { name: "创建草稿" })).toBeEnabled();
  });

  it("空列表提供聚焦标题的创建入口", async () => {
    const { title, i18n } = await renderHome();
    fireEvent.click(screen.getByRole("button", { name: i18n.t("creative.empty_action") }));
    expect(title()).toHaveFocus();
  });

  it("筛选后无结果时可同时清空关键词和状态", async () => {
    const { props, i18n } = await renderHome({ query: "no result", statusFilter: "review" });
    fireEvent.click(screen.getByRole("button", { name: i18n.t("creative.clear_filters") }));
    expect(props.onQueryChange).toHaveBeenCalledWith("");
    expect(props.onStatusFilterChange).toHaveBeenCalledWith("all");
  });
});

describe("创作首页语言一致性", () => {
  it.each([
    ["zh-CN", "创作空间", "文章标题", "创建草稿"],
    ["en", "Creative space", "Article title", "Create draft"],
  ])("%s 首页含完整标题、提示和操作文案", async (language, heading, titleLabel, createLabel) => {
    const { container, i18n } = await renderHome({
      listState: "ready", totalCount: 1,
      articles: [{ id: "one", title: "A note", summary: "", status: "draft", writingType: "", tags: [], revision: 2, updatedAt: "2026-10-17" }],
    }, language);
    expect(screen.getByText(heading)).toBeVisible();
    expect(screen.getByRole("textbox", { name: titleLabel })).toBeVisible();
    expect(screen.getByRole("button", { name: createLabel })).toBeVisible();
    expect(screen.getByText(i18n.t("creative.input_hint"))).toBeVisible();
    expect(screen.getByText(i18n.t("creative.template_hint"))).toBeVisible();
    expect(screen.getByText(i18n.t("creative.no_summary"))).toBeVisible();
    expect(container.textContent).not.toContain("creative.");
    if (language === "en") expect(container.textContent).not.toMatch(/[\u4e00-\u9fff]/);
  });

  it("全部语言显式提供首页所需文案，不依赖其它语言或硬编码回退", () => {
    const modules = import.meta.glob<{ default: { creative: Record<string, unknown> } }>("../i18n/locales/*/common.json", { eager: true });
    const keys = [
      "title", "topic_label", "new_placeholder", "create_btn", "creating_btn", "search_placeholder",
      "status_filter", "empty_template", "selected_template", "title_hint", "library_hint", "empty_action",
      "clear_filters", "dismiss", "no_summary", "revision_label", "duplicate", "delete", "edit_article",
      "confirm_delete", "confirm_delete_body", "cancel", "confirm", "new_article", "template_hint", "input_hint",
      "loadFailed", "retry", "no_match", "empty", "createFailed", "title_required",
      "duplicateFailed", "deleteFailed", "note_storage", "confirm_delete_dirty",
    ];
    for (const [path, module] of Object.entries(modules)) {
      for (const key of keys) expect(module.default.creative[key], `${path}: creative.${key}`).toBeTypeOf("string");
    }
  });
});
