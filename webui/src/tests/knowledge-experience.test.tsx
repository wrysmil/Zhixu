import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { KnowledgePreview } from "@/components/workspace/knowledge-shared/KnowledgePreview";
import { KnowledgeStatus } from "@/components/workspace/knowledge-shared/KnowledgeStatus";
import type { KnowledgeTone } from "@/components/workspace/knowledge-shared/experience-types";
import {
  KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription,
  KnowledgeDialog, KnowledgeDialogContent, KnowledgeDialogTitle, KnowledgeDialogDescription,
} from "@/components/workspace/knowledge-shared/KnowledgeSurfaces";

afterEach(cleanup);

// happy-dom 16.8.1 reports -1 for native buttons/inputs without a tabindex,
// unlike browsers. Restore their native tab stops so Radix's real FocusScope
// can discover them; do not mock FocusScope or its autofocus/trap behavior.
const nativeTabIndex = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "tabIndex")!.get!;
let tabIndexShim: ReturnType<typeof vi.spyOn>;
beforeAll(() => {
  tabIndexShim = vi.spyOn(HTMLElement.prototype, "tabIndex", "get").mockImplementation(function (this: HTMLElement) {
    if (!this.hasAttribute("tabindex") && this.matches("button, input:not([type=hidden])")) return 0;
    return nativeTabIndex.call(this);
  });
});
afterAll(() => tabIndexShim.mockRestore());

describe.each([
  ["Sheet", KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription],
  ["Dialog", KnowledgeDialog, KnowledgeDialogContent, KnowledgeDialogTitle, KnowledgeDialogDescription],
] as const)("Knowledge%s", (_name, Root, Content, Title, Description) => {
  function Fixture({ interactive = true, showCloseButton = true, ...props }: React.ComponentProps<typeof Content> & { interactive?: boolean }) {
    return (
      <Root>
        <DialogPrimitive.Trigger>打开详情</DialogPrimitive.Trigger>
        <Content showCloseButton={showCloseButton} {...props}>
          <Title>资料详情</Title>
          <Description>完整资料说明</Description>
          {interactive ? <input aria-label="资料名称" /> : <p>只读资料</p>}
        </Content>
      </Root>
    );
  }

  it("focuses content, traps Tab and restores trigger focus after Escape", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    const trigger = screen.getByRole("button", { name: "打开详情" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "资料详情" });
    expect(dialog).toHaveAccessibleDescription("完整资料说明");
    expect(screen.getByRole("textbox")).toHaveFocus();
    await user.tab({ shift: true });
    expect(within(dialog).getByRole("button")).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("textbox")).toHaveFocus();
    expect(document.body).toHaveAttribute("data-scroll-locked", "1");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
    expect(document.body).not.toHaveAttribute("data-scroll-locked");
  });

  it("provides a named close button for non-interactive content", async () => {
    const user = userEvent.setup();
    render(<Fixture interactive={false} />);
    await user.click(screen.getByRole("button", { name: "打开详情" }));
    const close = within(screen.getByRole("dialog")).getByRole("button");
    expect(close).toHaveAccessibleName(/close|关闭/i);
    expect(close).toHaveFocus();
    await user.click(close);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "打开详情" })).toHaveFocus();
  });

  it("keeps both animated nodes directly in the portal, with an independently scoped overlay", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(screen.getByRole("button", { name: "打开详情" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.parentElement).toBe(document.body);
    const overlay = document.querySelector(".knowledge-overlay");
    expect(overlay).toHaveClass("knowledge-experience-surface", "knowledge-motion");
    expect(overlay?.parentElement).toBe(document.body);
    expect(dialog).toHaveClass("knowledge-experience-surface", "knowledge-motion");
  });

  it("honors autofocus callbacks and supports hiding the built-in close button", async () => {
    const user = userEvent.setup();
    const onOpenAutoFocus = vi.fn();
    const onCloseAutoFocus = vi.fn();
    render(<Fixture showCloseButton={false} onOpenAutoFocus={onOpenAutoFocus} onCloseAutoFocus={onCloseAutoFocus} />);
    await user.click(screen.getByRole("button", { name: "打开详情" }));
    expect(onOpenAutoFocus).toHaveBeenCalledTimes(1);
    expect(within(screen.getByRole("dialog")).queryByRole("button")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(onCloseAutoFocus).toHaveBeenCalledTimes(1));
  });

  it("lets callers override entering and returning focus", async () => {
    const user = userEvent.setup();
    render(<>
      <button data-testid="focus-return">返回列表</button>
      <Fixture
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          screen.getByRole("dialog").focus();
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          screen.getByTestId("focus-return").focus();
        }}
      />
    </>);
    await user.click(screen.getByRole("button", { name: "打开详情" }));
    expect(screen.getByRole("dialog")).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByTestId("focus-return")).toHaveFocus());
  });

  it("respects a caller that prevents outside dismissal", async () => {
    const user = userEvent.setup();
    const onPointerDownOutside = vi.fn((event: Event) => event.preventDefault());
    render(<Fixture onPointerDownOutside={onPointerDownOutside} />);
    await user.click(screen.getByRole("button", { name: "打开详情" }));
    fireEvent.pointerDown(document.querySelector(".knowledge-overlay")!, { button: 0, pointerType: "mouse" });
    expect(onPointerDownOutside).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("dismisses on an outside pointer action and can reopen repeatedly", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    for (let cycle = 0; cycle < 3; cycle++) {
      await user.click(screen.getByRole("button", { name: "打开详情" }));
      expect(screen.getAllByRole("dialog")).toHaveLength(1);
      fireEvent.pointerDown(document.querySelector(".knowledge-overlay")!, { button: 0, pointerType: "mouse" });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    }
    expect(document.body).not.toHaveAttribute("data-scroll-locked");
  });

  it("follows the latest controlled state during rapid open-close-open updates", async () => {
    const onOpenChange = vi.fn();
    const view = (open: boolean) => (
      <Root open={open} onOpenChange={onOpenChange}>
        <Content><Title>快速切换</Title><Description>最新选择</Description><input aria-label="当前内容" /></Content>
      </Root>
    );
    const { rerender } = render(view(false));
    rerender(view(true));
    rerender(view(false));
    rerender(view(true));
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveFocus());
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    rerender(view(false));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body).not.toHaveAttribute("data-scroll-locked");
  });
});

describe("KnowledgeSheet sides", () => {
  it.each(["top", "right", "bottom", "left"] as const)("forwards %s and the content ref", (side) => {
    const ref = { current: null as HTMLDivElement | null };
    render(<KnowledgeSheet open>
      <KnowledgeSheetContent ref={ref} side={side}>
        <KnowledgeSheetTitle>侧边资料</KnowledgeSheetTitle>
        <KnowledgeSheetDescription>方向兼容</KnowledgeSheetDescription>
      </KnowledgeSheetContent>
    </KnowledgeSheet>);
    expect(ref.current).toBe(screen.getByRole("dialog"));
    expect(ref.current).toHaveAttribute("data-side", side);
  });
});

describe("KnowledgePreview", () => {
  it("opens a supplementary description on keyboard focus and dismisses with Escape", async () => {
    const user = userEvent.setup();
    render(
      <KnowledgePreview title="产品手册" summary="包含安装与维护步骤" meta="20 个文档">
        <button>查看产品手册</button>
      </KnowledgePreview>,
    );
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await user.tab();
    const trigger = screen.getByRole("button", { name: "查看产品手册" });
    expect(trigger).toHaveFocus();
    const tooltip = await screen.findByRole("tooltip");
    expect(within(tooltip).getByText("产品手册")).toBeInTheDocument();
    expect(tooltip).toHaveTextContent("包含安装与维护步骤");
    expect(tooltip).toHaveTextContent("20 个文档");
    expect(trigger).toHaveAccessibleDescription(/包含安装与维护步骤/);
    expect(within(tooltip).queryByRole("button")).not.toBeInTheDocument();
    expect(within(tooltip).queryByRole("link")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("opens on hover and stays open when the pointer enters the summary", async () => {
    const user = userEvent.setup();
    render(
      <KnowledgePreview title="手册" summary="补充摘要">
        <button>打开手册</button>
      </KnowledgePreview>,
    );
    const trigger = screen.getByRole("button", { name: "打开手册" });
    await user.hover(trigger);
    const tooltip = await screen.findByRole("tooltip");
    // Radix renders the accessible tooltip in a hidden sibling of the visual content.
    const surface = tooltip.closest("[data-state]")!;
    await user.unhover(trigger);
    await user.hover(surface);
    expect(screen.getByRole("tooltip")).toHaveTextContent("补充摘要");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
  });

  it("preserves the detail action and closes the preview on activation", async () => {
    const user = userEvent.setup();
    const openDetail = vi.fn();
    render(
      <KnowledgePreview title="手册">
        <button onClick={openDetail}>查看详情</button>
      </KnowledgePreview>,
    );
    await user.tab();
    await screen.findByRole("tooltip");
    await user.keyboard("{Enter}");
    expect(openDetail).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
  });

  it("supports text children and optional empty summary/meta without an extra button", async () => {
    const user = userEvent.setup();
    render(<KnowledgePreview title="完整名称">名称</KnowledgePreview>);
    await user.tab();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("完整名称");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    await user.tab();
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
  });
});

describe("KnowledgeStatus", () => {
  it("keeps the caller's visible stage label alongside a decorative busy indicator", () => {
    const { container, rerender } = render(
      <KnowledgeStatus label="正在读取文件" tone="pending" busy />,
    );
    const status = screen.getByRole("status", { name: "正在读取文件" });
    expect(status).toHaveTextContent("正在读取文件");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    rerender(<KnowledgeStatus label="已就绪" tone="success" />);
    expect(screen.getByRole("status", { name: "已就绪" })).toHaveTextContent("已就绪");
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
    expect(container.querySelector("svg")).not.toBeInTheDocument();
  });

  it.each<KnowledgeTone>(["neutral", "pending", "success", "error"])(
    "keeps %s readable as static text without busy animation",
    (tone) => {
      const { container } = render(<KnowledgeStatus label="由调用方提供的状态" tone={tone} />);
      expect(screen.getByRole("status", { name: "由调用方提供的状态" })).toHaveTextContent(
        "由调用方提供的状态",
      );
      expect(container.querySelector("svg")).not.toBeInTheDocument();
    },
  );

  it("renders an error message as text rather than HTML", () => {
    const label = '<img src=x onerror="alert(1)">';
    const { container } = render(<KnowledgeStatus label={label} tone="error" />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
    expect(container.querySelector("img")).not.toBeInTheDocument();
  });
});
