import "@testing-library/jest-dom/vitest";
import i18n from "@/i18n";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkbenchMemoryPreview } from "@/components/workbench/WorkbenchMemoryPreview";
beforeEach(async () => { await i18n.changeLanguage("zh-CN"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("读取全局记忆并显示截断提示", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ resource: { id: "id", kind: "note", scope: "global", name: "a", title: "全局笔记" }, content: "测试内容", updated_at: null, truncated: true })));
  render(<WorkbenchMemoryPreview token="t" sessionKey="s" resourceId="id" onClose={() => {}} />);
  expect(await screen.findByText("测试内容")).toBeInTheDocument();
  expect(screen.getByText(/截断/)).toBeInTheDocument();
});
it("权限错误可读且允许关闭", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("forbidden", { status: 403 })));
  const close = vi.fn(); render(<WorkbenchMemoryPreview token="t" sessionKey="s" resourceId="id" onClose={close} />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/权限/));
  fireEvent.click(screen.getByRole("button", { name: /关闭/ })); expect(close).toHaveBeenCalled();
});
it("切换资源时旧请求不能覆盖新内容", async () => {
  let resolveOld!: (r: Response) => void;
  const preview = (id: string) => Response.json({ resource: { id, kind: "note", scope: "global", name: id, title: id }, content: id, updated_at: null, truncated: false });
  vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise<Response>(r => { resolveOld = r; })).mockResolvedValueOnce(preview("new")));
  const { rerender } = render(<WorkbenchMemoryPreview token="t" sessionKey="old" resourceId="old" onClose={() => {}} />);
  rerender(<WorkbenchMemoryPreview token="t" sessionKey="new" resourceId="new" onClose={() => {}} />);
  await screen.findAllByText("new");
  resolveOld(preview("old"));
  await waitFor(() => expect(screen.queryByText("old")).toBeNull());
});
it("已删除资源显示可读提示", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("missing", { status: 404 })));
  render(<WorkbenchMemoryPreview token="t" sessionKey="s" resourceId="id" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/已删除/));
});
