import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GenericToolRun } from "@/components/thread/activity/GenericToolRun";
import { activityPathPreview } from "@/components/thread/activity/activity-text";

describe("activity path preview", () => {
  it.each(["/home/user/", "C:\\Users\\person\\"])("retains the filename for long paths rooted at %s", root => {
    const path = root + "a-very-long-directory/".repeat(15) + "parent/MEMORY.md";
    expect(activityPathPreview(path)).toBe("…/parent/MEMORY.md");
  });
  it.each(["/private/tmp/session/subdir/report.json", "/var/folders/ab/session/cache/report.json"])("retains the basename of temporary path %s", path => {
    expect(activityPathPreview(path)).toMatch(/report\.json$/);
  });
  it("shows a redacted tool error without opening secondary details", () => {
    render(<GenericToolRun items={[{ status: "error", error: "Permission denied TOKEN=private-value", trace: { name: "read_file", family: "read", groupKey: "failed", collectedSource: false, fields: [{ key: "path", value: "/project/target.ts" }] } }]} />);
    expect(screen.getByText(/Permission denied/)).toBeVisible();
    expect(screen.queryByText(/private-value/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("activity-step-detail")).not.toBeInTheDocument();
  });
  it.each(["read_file", "list_dir"])("keeps the %s error outside the truncated path while details stay closed", name => {
    const filename = "very-long-filename-".repeat(20) + ".ts";
    const errorMessage = "Permission denied: choose a readable file before retrying.";
    render(<div style={{ width: 240 }}><GenericToolRun items={[{
      status: "error", error: errorMessage,
      trace: { name, family: name === "read_file" ? "read" : "list", groupKey: name, collectedSource: false,
        fields: [{ key: "path", value: `/project/nested/${filename}` }] },
    }]} /></div>);
    const error = screen.getByText(errorMessage);
    expect(screen.getByText(`…/${filename}`)).toBeVisible();
    expect(error).toBeVisible();
    // A visible DOM node can still be clipped by its path-preview ancestor.
    // Keep the failure outside that clipping boundary, even before opening details.
    expect(error.closest(".line-clamp-2, .truncate, .overflow-hidden")).toBeNull();
    expect(screen.getByTestId("activity-step-details-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("activity-step-detail")).not.toBeInTheDocument();
  });
  it("retains the complete original path in disclosed details", () => {
    const path = "/project/" + "nested-directory/".repeat(25) + "target.ts";
    render(<GenericToolRun items={[{ status: "done", trace: { name: "read_file", family: "read", groupKey: "read", collectedSource: false, fields: [{ key: "path", value: path }] } }]} />);
    expect(screen.getByText("…/nested-directory/target.ts")).toBeVisible();
    const toggle = screen.getByTestId("activity-step-details-toggle");
    fireEvent.click(toggle);
    expect(document.getElementById(toggle.getAttribute("aria-controls")!)).toHaveTextContent(path);
  });
});
