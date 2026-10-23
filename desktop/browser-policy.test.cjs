"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { MAX_URL_LENGTH, normalizeBrowserWindowUrl } = require("./browser-policy.cjs");

test("accepts http and https", () => {
  assert.equal(normalizeBrowserWindowUrl("https://example.com/a"), "https://example.com/a");
  assert.equal(normalizeBrowserWindowUrl("http://127.0.0.1:8765/"), "http://127.0.0.1:8765/");
});

test("rejects non-http schemes", () => {
  for (const value of ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x"]) {
    assert.equal(normalizeBrowserWindowUrl(value), null);
  }
});

test("rejects credentials and oversized input", () => {
  assert.equal(normalizeBrowserWindowUrl("https://user:pass@example.com"), null);
  assert.equal(normalizeBrowserWindowUrl(`https://example.com/${"a".repeat(MAX_URL_LENGTH)}`), null);
});

test("rejects non-strings and blanks", () => {
  for (const value of [null, undefined, 42, {}, "", "   "]) {
    assert.equal(normalizeBrowserWindowUrl(value), null);
  }
});
