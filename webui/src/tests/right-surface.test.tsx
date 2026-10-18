import { describe, expect, it } from "vitest";

import {
  WORKBENCH_SURFACE,
  popRightSurface,
  pushRightSurface,
  type RightSurface,
} from "@/components/thread/SubagentExecutionPanel";

describe("single right surface stack", () => {
  it("returns from file preview to the execution detail it came from", () => {
    const execution: RightSurface = { kind: "execution", invocationId: "inv-a" };
    const file: RightSurface = { kind: "file", path: "reports/sources.md" };

    const withExecution = pushRightSurface([WORKBENCH_SURFACE], execution);
    const withFile = pushRightSurface(withExecution, file);

    expect(withFile).toEqual([WORKBENCH_SURFACE, execution, file]);
    expect(popRightSurface(withFile)).toEqual([WORKBENCH_SURFACE, execution]);
    expect(popRightSurface(popRightSurface(withFile))).toEqual([WORKBENCH_SURFACE]);
  });

  it("does not duplicate the same adjacent surface and never pops the workbench base", () => {
    const execution: RightSurface = { kind: "execution", invocationId: "inv-a" };
    const once = pushRightSurface([WORKBENCH_SURFACE], execution);
    expect(pushRightSurface(once, execution)).toEqual(once);
    expect(popRightSurface([WORKBENCH_SURFACE])).toEqual([WORKBENCH_SURFACE]);
  });

  it("returns from tools to the execution detail underneath", () => {
    const execution: RightSurface = { kind: "execution", invocationId: "inv-a" };
    const tools: RightSurface = { kind: "tools" };
    const stack = pushRightSurface(pushRightSurface([WORKBENCH_SURFACE], execution), tools);

    expect(stack.at(-1)).toEqual(tools);
    expect(popRightSurface(stack)).toEqual([WORKBENCH_SURFACE, execution]);
  });

  it("allows only one active file or execution surface at the top of the stack", () => {
    const executionA: RightSurface = { kind: "execution", invocationId: "inv-a" };
    const fileB: RightSurface = { kind: "file", path: "b.md" };

    const stack = pushRightSurface(pushRightSurface([WORKBENCH_SURFACE], executionA), fileB);
    expect(stack.at(-1)).toEqual(fileB);
    expect(stack.filter((surface) => surface.kind === "file")).toHaveLength(1);
    expect(stack.filter((surface) => surface.kind === "execution")).toHaveLength(1);
  });
});
