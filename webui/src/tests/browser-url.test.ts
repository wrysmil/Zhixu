import { describe, expect, it } from "vitest";

import { normalizeBrowserUrl } from "@/lib/browser-url";

// 用例表来自计划 Task E Step 1（契约 §4：仅 http/https；loopback 允许；其余私网/保留拒绝）
describe("normalizeBrowserUrl", () => {
  it("accepts a public https URL", () => {
    const result = normalizeBrowserUrl("https://x.com");
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.url).toMatch(/^https:\/\/x\.com/);
  });

  it("accepts http on localhost with a port", () => {
    const result = normalizeBrowserUrl("http://localhost:3000");
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.url).toMatch(/^http:\/\/localhost:3000/);
  });

  it("accepts http on loopback 127.0.0.1 with a port", () => {
    const result = normalizeBrowserUrl("http://127.0.0.1:8765");
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.url).toContain("127.0.0.1:8765");
  });

  it.each([
    ["file:///etc/passwd", "scheme"],
    ["javascript:alert(1)", "scheme"],
    ["data:text/html,<script>alert(1)</script>", "scheme"],
    ["ftp://example.com", "scheme"],
    ["http://192.168.1.1", "private"],
    ["http://169.254.1.1", "private"],
    ["notaurl", "malformed"],
  ])("rejects %s with reason %s", (raw, reason) => {
    const result = normalizeBrowserUrl(raw);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toBe(reason);
  });

  it.each([
    "http://10.0.0.1",
    "http://10.255.255.255",
    "http://172.16.0.1",
    "http://172.31.255.255",
    "http://192.168.0.1",
    "http://169.254.169.254/latest/meta-data",
    "http://0.0.0.0",
    "http://[::ffff:192.168.1.1]",
  ])("rejects private/reserved ipv4 target %s", (raw) => {
    const result = normalizeBrowserUrl(raw);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toBe("private");
  });

  it("accepts ipv6 loopback ::1", () => {
    const result = normalizeBrowserUrl("http://[::1]:8080");
    expect(result.ok).toBe(true);
  });

  it("accepts ipv4-mapped ipv6 loopback", () => {
    expect(normalizeBrowserUrl("http://[::ffff:127.0.0.1]:9000").ok).toBe(true);
  });

  it.each([
    "http://[fd00::1234]",
    "http://[fc00::1]",
    "http://[fe80::1]",
    "http://[::]",
    "http://[::ffff:c0a8:101]",
  ])("rejects ipv6 unique-local/link-local/unspecified/mapped-private %s", (raw) => {
    const result = normalizeBrowserUrl(raw);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toBe("private");
  });

  it("accepts addresses outside the 172.16/12 range", () => {
    expect(normalizeBrowserUrl("http://172.32.0.1").ok).toBe(true);
    expect(normalizeBrowserUrl("http://172.15.0.1").ok).toBe(true);
  });

  it.each([
    "http://printer.local",
    "http://db.internal",
    "http://intranet",
    "http://localhost.localdomain",
  ])("rejects non-public local name resolution %s", (raw) => {
    const result = normalizeBrowserUrl(raw);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toBe("private");
  });

  it("accepts bare localhost", () => {
    expect(normalizeBrowserUrl("http://localhost").ok).toBe(true);
  });

  it.each([
    "",
    "   ",
    "http://",
    "https://user:pass@example.com",
    "http://256.1.1.1",
  ])("rejects malformed input %js", (raw) => {
    const result = normalizeBrowserUrl(raw);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toBe("malformed");
  });

  it("trims surrounding whitespace before parsing", () => {
    const result = normalizeBrowserUrl("  https://example.com/path  ");
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.url).toContain("example.com/path");
  });

  it("rejects non-string input", () => {
    const result = normalizeBrowserUrl(undefined as unknown as string);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toBe("malformed");
  });

  it("accepts bare public hosts with https", () => {
    expect(normalizeBrowserUrl("example.com")).toEqual({
      ok: true,
      url: "https://example.com/",
    });
    expect(normalizeBrowserUrl("example.com:8080/docs")).toEqual({
      ok: true,
      url: "https://example.com:8080/docs",
    });
  });

  it("accepts bare loopback hosts with http", () => {
    expect(normalizeBrowserUrl("localhost:5173")).toEqual({
      ok: true,
      url: "http://localhost:5173/",
    });
    expect(normalizeBrowserUrl("127.0.0.1:8765")).toEqual({
      ok: true,
      url: "http://127.0.0.1:8765/",
    });
  });

  it("keeps single-label input malformed and rejects bare private hosts", () => {
    expect(normalizeBrowserUrl("notaurl")).toEqual({ ok: false, reason: "malformed" });
    expect(normalizeBrowserUrl("192.168.1.1")).toEqual({ ok: false, reason: "private" });
  });

  it("keeps non-http schemes rejected", () => {
    expect(normalizeBrowserUrl("mailto:user@example.com")).toEqual({
      ok: false,
      reason: "scheme",
    });
  });
});
