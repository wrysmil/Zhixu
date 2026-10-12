import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { MaterialPanel } from "@/components/creative/MaterialPanel";
import { OutlinePanel } from "@/components/creative/OutlinePanel";
import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import type { WebUIMutationTransport } from "@/lib/api";
import type { Material, OutlineItem } from "@/components/creative/types";
import { preloadMarkdownText } from "@/components/MarkdownText";

const SEED_AT = "2026-01-05T09:00:00+00:00";

function material(id: string, order: number, overrides: Partial<Material> = {}): Material {
  return {
    id,
    type: "note",
    title: `素材 ${id}`,
    content: `内容 ${id}`,
    included: true,
    order,
    createdAt: SEED_AT,
    updatedAt: SEED_AT,
    ...overrides,
  };
}

function outlineItem(id: string, order: number, text = `章节 ${id}`): OutlineItem {
  return { id, text, order };
}

function lastArg<T>(spy: ReturnType<typeof vi.fn>): T {
  return spy.mock.calls.at(-1)?.[0] as T;
}

describe("MaterialPanel (A-04/A-05 stable ids + contract fields)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("adds a material with included defaulting to true and the full persisted field set", () => {
    const onChange = vi.fn();
    render(<MaterialPanel materials={[material("m1", 0)]} onChange={onChange} />);

    fireEvent.click(screen.getByTestId("material-add-open"));
    fireEvent.change(screen.getByTestId("material-title-input"), { target: { value: "新的观点" } });
    fireEvent.change(screen.getByTestId("material-content-input"), { target: { value: "待展开的要点" } });
    fireEvent.click(screen.getByTestId("material-form-submit"));

    const next = lastArg<Material[]>(onChange);
    expect(next).toHaveLength(2);
    const added = next[1];
    expect(added.included).toBe(true);
    expect(added.id).toBeTruthy();
    expect(added.order).toBe(1);
    expect(added.createdAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(added.updatedAt).toBe(added.createdAt);
    // 已有素材的 id 不漂移
    expect(next[0].id).toBe("m1");
  });

  it("keeps newlines and indentation for code materials and records the language", () => {
    const onChange = vi.fn();
    render(<MaterialPanel materials={[]} onChange={onChange} />);

    const raw = "  def check():\n      return 1\n";
    fireEvent.click(screen.getByTestId("material-add-open"));
    fireEvent.click(screen.getByTestId("material-type-code"));
    fireEvent.change(screen.getByTestId("material-title-input"), { target: { value: "示例代码" } });
    fireEvent.change(screen.getByTestId("material-content-input"), { target: { value: raw } });
    fireEvent.change(screen.getByTestId("material-language-input"), { target: { value: "python" } });
    fireEvent.click(screen.getByTestId("material-form-submit"));

    const added = lastArg<Material[]>(onChange)[0];
    expect(added.type).toBe("code");
    expect(added.content).toBe(raw);
    expect(added.language).toBe("python");
  });

  it("accepts only http(s) links and stores the url in the contract field", () => {
    const onChange = vi.fn();
    render(<MaterialPanel materials={[]} onChange={onChange} />);

    fireEvent.click(screen.getByTestId("material-add-open"));
    fireEvent.click(screen.getByTestId("material-type-link"));
    fireEvent.change(screen.getByTestId("material-title-input"), { target: { value: "文档" } });
    fireEvent.change(screen.getByTestId("material-url-input"), { target: { value: "javascript:alert(1)" } });
    fireEvent.click(screen.getByTestId("material-form-submit"));

    expect(screen.getByTestId("material-form-error")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("material-url-input"), { target: { value: "https://example.com/a" } });
    fireEvent.click(screen.getByTestId("material-form-submit"));

    const added = lastArg<Material[]>(onChange)[0];
    expect(added.type).toBe("link");
    expect(added.url).toBe("https://example.com/a");
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("asks before deleting and keeps the list untouched when cancelled", () => {
    const onChange = vi.fn();
    render(
      <MaterialPanel
        materials={[material("m1", 0), material("m2", 1)]}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByTestId("material-remove-m1"));
    expect(screen.getByTestId("material-delete-confirm")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("material-delete-cancel"));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByTestId("material-delete-confirm")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("material-remove-m1"));
    fireEvent.click(screen.getByTestId("material-delete-confirm-action"));

    const next = lastArg<Material[]>(onChange);
    expect(next.map((item) => item.id)).toEqual(["m2"]);
    expect(next[0].order).toBe(0);
  });

  it("edits a material in place: same id, createdAt and order, refreshed updatedAt", () => {
    const onChange = vi.fn();
    render(
      <MaterialPanel
        materials={[material("m1", 0), material("m2", 1, { title: "旧标题" })]}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByTestId("material-edit-m2"));
    const titleInput = screen.getByTestId("material-title-input") as HTMLInputElement;
    expect(titleInput.value).toBe("旧标题");

    fireEvent.change(titleInput, { target: { value: "新标题" } });
    fireEvent.click(screen.getByTestId("material-form-submit"));

    const next = lastArg<Material[]>(onChange);
    const edited = next.find((item) => item.id === "m2");
    expect(next).toHaveLength(2);
    expect(edited?.title).toBe("新标题");
    expect(edited?.createdAt).toBe(SEED_AT);
    expect(edited?.order).toBe(1);
    expect(edited?.updatedAt).not.toBe(SEED_AT);
    expect(next.find((item) => item.id === "m1")?.title).toBe("素材 m1");
  });

  it("renders by order and keeps ids stable after removal", () => {
    const onChange = vi.fn();
    render(
      <MaterialPanel
        materials={[
          material("m2", 1, { content: "第二" }),
          material("m1", 0, { content: "第一" }),
        ]}
        onChange={onChange}
      />,
    );

    const rows = Array.from(document.querySelectorAll("[data-material-id]"));
    expect(rows.map((row) => row.getAttribute("data-material-id"))).toEqual(["m1", "m2"]);

    fireEvent.click(screen.getByTestId("material-remove-m1"));
    fireEvent.click(screen.getByTestId("material-delete-confirm-action"));
    const next = lastArg<Material[]>(onChange);
    expect(next.map((item) => item.id)).toEqual(["m2"]);
    expect(next[0].order).toBe(0);
  });

  it("toggles the collaboration flag without touching other fields", () => {
    const onChange = vi.fn();
    render(<MaterialPanel materials={[material("m1", 0)]} onChange={onChange} />);

    fireEvent.click(screen.getByTestId("material-toggle-m1"));
    const next = lastArg<Material[]>(onChange);
    expect(next[0].included).toBe(false);
    expect(next[0].id).toBe("m1");
    expect(next[0].title).toBe("素材 m1");
  });
});

describe("OutlinePanel (A-04 stable ids, order-only moves)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders sorted by order even when the incoming array is scrambled", () => {
    render(
      <OutlinePanel
        outline={[
          outlineItem("o2", 1, "第二节"),
          outlineItem("o1", 0, "第一节"),
          outlineItem("o3", 2, "第三节"),
        ]}
        onChange={vi.fn()}
      />,
    );
    const rows = Array.from(document.querySelectorAll("[data-outline-id]"));
    expect(rows.map((row) => row.getAttribute("data-outline-id"))).toEqual(["o1", "o2", "o3"]);
  });

  it("moves an item by changing order only, ids never drift", () => {
    const onChange = vi.fn();
    const initial = [
      outlineItem("o1", 0),
      outlineItem("o2", 1),
      outlineItem("o3", 2),
    ];
    const view = render(<OutlinePanel outline={initial} onChange={onChange} />);

    fireEvent.click(screen.getByTestId("outline-up-o3"));
    const moved = lastArg<OutlineItem[]>(onChange);
    expect(moved.map((item) => item.id)).toEqual(["o1", "o3", "o2"]);
    expect(moved.map((item) => item.order)).toEqual([0, 1, 2]);

    view.rerender(<OutlinePanel outline={moved} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("outline-down-o1"));
    const back = lastArg<OutlineItem[]>(onChange);
    expect(back.map((item) => item.id)).toEqual(["o3", "o1", "o2"]);
    expect(back.map((item) => item.text)).toEqual(["章节 o3", "章节 o1", "章节 o2"]);
  });

  it("adds, edits and deletes while keeping every surviving id", () => {
    const onChange = vi.fn();
    const view = render(
      <OutlinePanel
        outline={[outlineItem("o1", 0), outlineItem("o2", 1)]}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByTestId("outline-add-open"));
    fireEvent.change(screen.getByTestId("outline-add-input"), { target: { value: "新增一节" } });
    fireEvent.click(screen.getByTestId("outline-add-submit"));
    const added = lastArg<OutlineItem[]>(onChange);
    expect(added).toHaveLength(3);
    expect(added[2].text).toBe("新增一节");
    expect(added[2].order).toBe(2);
    const addedId = added[2].id;
    expect(addedId).toBeTruthy();
    expect(added.map((item) => item.id).slice(0, 2)).toEqual(["o1", "o2"]);

    view.rerender(<OutlinePanel outline={added} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("outline-edit-o1"));
    fireEvent.change(screen.getByTestId("outline-edit-input"), { target: { value: "改过的第一节" } });
    fireEvent.click(screen.getByTestId("outline-edit-submit"));
    const edited = lastArg<OutlineItem[]>(onChange);
    expect(edited[0]).toEqual({ id: "o1", text: "改过的第一节", order: 0 });

    view.rerender(<OutlinePanel outline={edited} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("outline-remove-o2"));
    const removed = lastArg<OutlineItem[]>(onChange);
    // 删除后剩下的仍是原来的两条（按 id 认，不按下标认）。
    expect(removed.map((item) => item.id)).toEqual(["o1", addedId]);
    expect(removed.map((item) => item.order)).toEqual([0, 1]);
    expect(removed[1].text).toBe("新增一节");
  });
});

describe("panels wired into the article snapshot (A-08 rule 2)", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
  });

  function renderPreview() {
    const props: CreativeWorkspaceProps = {
      getToken: () => "",
      transport: {
        requestMutation: async () => {
          throw new Error("preview must not use the injected transport");
        },
      } as unknown as WebUIMutationTransport,
      workspacePath: "/preview-workspace",
      preview: true,
    };
    return render(<CreativeWorkspace {...props} />);
  }

  it("marks the snapshot dirty on panel edits but never on panel switching", async () => {
    renderPreview();
    fireEvent.click(await screen.findByText("从零构建 Agent 的记忆系统"));
    await waitFor(() => expect(screen.getByTestId("article-editor")).toBeInTheDocument());

    const saveButton = screen.getByRole("button", { name: /保存/ }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);

    fireEvent.click(screen.getByText("提纲"));
    fireEvent.click(screen.getByTestId("outline-add-open"));
    fireEvent.change(screen.getByTestId("outline-add-input"), { target: { value: "多出来的一节" } });
    fireEvent.click(screen.getByTestId("outline-add-submit"));
    await waitFor(() => expect(saveButton.disabled).toBe(false));

    fireEvent.click(saveButton);
    await waitFor(() =>
      expect(screen.getByTestId("save-phase-label").textContent).toContain("已保存"),
    );
    expect(saveButton.disabled).toBe(true);

    // 面板开关与标签切换都不进快照：不重新变脏、不推进版本。
    fireEvent.click(screen.getByText("素材"));
    fireEvent.click(screen.getByText("提纲"));
    fireEvent.click(screen.getByText("素材"));
    fireEvent.click(screen.getByText("提纲"));
    expect(saveButton.disabled).toBe(true);
    expect(screen.getByTestId("save-phase-label").textContent).toContain("已保存");
  });

  it("material edits reach the saved snapshot and reopen with stable ids", async () => {
    renderPreview();
    fireEvent.click(await screen.findByText("从零构建 Agent 的记忆系统"));
    await waitFor(() => expect(screen.getByTestId("article-editor")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("material-add-open"));
    fireEvent.change(screen.getByTestId("material-title-input"), { target: { value: "新加的观点" } });
    fireEvent.change(screen.getByTestId("material-content-input"), { target: { value: "观点内容" } });
    fireEvent.click(screen.getByTestId("material-form-submit"));
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() =>
      expect(screen.getByTestId("save-phase-label").textContent).toContain("已保存"),
    );

    fireEvent.click(screen.getByLabelText("Back"));
    await waitFor(() =>
      expect(screen.getByText("从零构建 Agent 的记忆系统")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("从零构建 Agent 的记忆系统"));
    await waitFor(() => expect(screen.getByTestId("material-add-open")).toBeInTheDocument());

    const rows = Array.from(document.querySelectorAll("[data-material-id]"));
    expect(rows.length).toBe(4);
    expect(rows.map((row) => row.getAttribute("data-material-id"))[3]).toBeTruthy();
    expect(screen.getByText("新加的观点")).toBeInTheDocument();
  });
});
