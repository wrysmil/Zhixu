export type BrowserUrlRejectReason = "scheme" | "private" | "malformed";

export type BrowserUrlResult =
  | { ok: true; url: string }
  | { ok: false; reason: BrowserUrlRejectReason };

/**
 * Validate + normalize a user-typed URL for the embedded browser iframe.
 *
 * Rules (contract §4, spec §3): only http/https; public hosts and loopback
 * (127.0.0.1 / localhost / ::1) are allowed; every other private, reserved or
 * local-name-resolution host is rejected to keep the panel from aiming at
 * internal services. Pure URL/regex parsing — no new dependencies.
 */
export function normalizeBrowserUrl(raw: string): BrowserUrlResult {
  if (typeof raw !== "string") return { ok: false, reason: "malformed" };
  const trimmed = raw.trim();
  if (!trimmed) return { ok: false, reason: "malformed" };

  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return { ok: false, reason: "malformed" };
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, reason: "scheme" };
  }
  if (url.username || url.password) {
    return { ok: false, reason: "malformed" };
  }

  const hostname = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (!hostname) return { ok: false, reason: "malformed" };

  const reject = (reason: BrowserUrlRejectReason): BrowserUrlResult => ({ ok: false, reason });
  const accept: BrowserUrlResult = { ok: true, url: url.href };

  if (hostname === "localhost" || hostname.startsWith("localhost.")) {
    return hostname === "localhost" ? accept : reject("private");
  }

  if (isIpv4Literal(hostname)) return classifyIpv4(hostname) ? accept : reject("private");
  if (hostname.includes(":")) return classifyIpv6(hostname) ? accept : reject("private");
  if (isLocalName(hostname)) return reject("private");

  return accept;
}

function isIpv4Literal(hostname: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(hostname);
}

/** True when the dotted quad is public or loopback (what the iframe may load). */
function classifyIpv4(hostname: string): boolean {
  const octets = hostname.split(".").map(Number);
  if (octets.some((part) => !Number.isFinite(part) || part > 255)) return false;
  const [a = 0, b = 0] = octets;
  if (a === 127) return true; // loopback 127/8 allowed
  return !(
    a === 0 ||
    a === 10 ||
    (a === 100 && b >= 64 && b <= 127) || // CGNAT 100.64/10
    (a === 169 && b === 254) || // link-local (cloud metadata)
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168)
  );
}

function classifyIpv6(hostname: string): boolean {
  if (hostname === "::1") return true; // loopback allowed
  if (hostname === "::") return false; // unspecified
  if (hostname.startsWith("fc") || hostname.startsWith("fd")) return false; // unique-local fc00::/7
  if (hostname.startsWith("fe80:")) return false; // link-local fe80::/10
  const mapped = matchMappedIpv4(hostname);
  if (mapped !== null) return classifyIpv4(mapped);
  return true; // remaining forms treated as global unicast
}

/**
 * WHATWG URL serializes IPv4-mapped addresses in hex groups
 * (e.g. ::ffff:192.168.1.1 becomes "::ffff:c0a8:101"); decode both shapes.
 */
function matchMappedIpv4(hostname: string): string | null {
  const rest = hostname.startsWith("::ffff:") ? hostname.slice("::ffff:".length) : null;
  if (!rest) return null;
  const dotted = /^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/.exec(rest);
  if (dotted?.[1]) return dotted[1];
  const hex = /^([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(rest);
  if (!hex) return null;
  const high = Number.parseInt(hex[1], 16);
  const low = Number.parseInt(hex[2], 16);
  return `${(high >> 8) & 255}.${high & 255}.${(low >> 8) & 255}.${low & 255}`;
}

/** mDNS / search-domain / single-label names resolve on the local network. */
function isLocalName(hostname: string): boolean {
  if ([".local", ".localhost", ".internal", ".home", ".lan"].some((suffix) => hostname.endsWith(suffix))) {
    return true;
  }
  return !hostname.includes(".");
}
