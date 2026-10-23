import { describe, it } from "vitest";

// Memory moved out of settings into a standalone page (see MemoryPage).
// The legacy "memory in SettingsView" tests no longer match the live UI.
// Kept as a skipped placeholder so the diff stays reversible.
describe.skip("Settings memory section (moved to standalone MemoryPage)", () => {
  it.skip("placeholder", () => {
    /* intentionally empty */
  });
});
