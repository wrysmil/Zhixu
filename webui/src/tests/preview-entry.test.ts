import { describe, expect, it } from "vitest";

import { isPreviewEntry } from "@/preview/preview-entry";

describe("preview entry detection", () => {
  it("enables preview only from an explicit query flag", () => {
    expect(isPreviewEntry("?preview=1")).toBe(true);
    expect(isPreviewEntry("?x=1&preview=1")).toBe(true);
    expect(isPreviewEntry("?preview=true")).toBe(false);
    expect(isPreviewEntry("")).toBe(false);
  });
});
