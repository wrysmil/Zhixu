import type { SettingsSectionKey } from "@/components/settings/contracts";
import type { WorkspaceRoute } from "@/components/workspace/contracts";

const SETTINGS_SECTION_KEYS: SettingsSectionKey[] = [
  "overview",
  "appearance",
  "models",
  "image",
  "voice",
  "browser",
  "channels",
  "apps",
  "automations",
  "skills",
  "agents",
  "memory",
  "identity",
  "runtime",
  "advanced",
];

function isSettingsSectionKey(value: string | null): value is SettingsSectionKey {
  return value !== null && SETTINGS_SECTION_KEYS.includes(value as SettingsSectionKey);
}

function assistantRoute(): WorkspaceRoute {
  return { view: "chat", chatKey: null };
}

function decodeRouteSegment(encoded: string): string | null {
  try {
    const decoded = decodeURIComponent(encoded).trim();
    return decoded || null;
  } catch {
    return null;
  }
}

const CAPABILITY_VIEWS = ["apps", "automations", "skills", "agents"] as const;

/**
 * Pure hash parser shared by the live App and the static preview. It never
 * throws on malformed input — anything unrecognized falls back to the
 * assistant so a bad deep link cannot blank the shell. Legacy home links also
 * resolve to a new conversation.
 */
export function parseWorkspaceHash(rawHash: string): WorkspaceRoute {
  const hash = rawHash.startsWith("#") ? rawHash.slice(1) : rawHash;
  if (!hash || hash === "/" || hash === "/home") return assistantRoute();

  const [path, query = ""] = hash.split("?", 2);
  const params = new URLSearchParams(query);
  const rawSection = params.get("section");
  const settingsSection = isSettingsSectionKey(rawSection) ? rawSection : "overview";
  const chatKey = params.get("chat")?.trim() || null;

  if (path === "/new") return { view: "chat", chatKey: null };

  if (path === "/settings") return { view: "settings", chatKey, settingsSection };

  for (const capability of CAPABILITY_VIEWS) {
    if (path === `/${capability}`) {
      return { view: capability, chatKey, settingsSection: capability };
    }
  }

  if (path === "/creative") return { view: "creative" };

  if (path.startsWith("/article/")) {
    const articleId = decodeRouteSegment(path.slice("/article/".length));
    return articleId ? { view: "article", articleId } : assistantRoute();
  }

  if (path.startsWith("/temporary/")) {
    const chatId = decodeRouteSegment(path.slice("/temporary/".length));
    return chatId
      ? { view: "chat", chatKey: `websocket:${chatId}`, temporary: true }
      : assistantRoute();
  }

  if (path.startsWith("/chat/")) {
    const key = decodeRouteSegment(path.slice("/chat/".length));
    return key ? { view: "chat", chatKey: key } : assistantRoute();
  }

  return assistantRoute();
}

export function workspaceRouteHash(route: WorkspaceRoute): string {
  if (route.view === "home") return "#/new";
  if (route.view === "chat") {
    if (route.temporary && route.chatKey?.startsWith("websocket:")) {
      return `#/temporary/${encodeURIComponent(route.chatKey.slice("websocket:".length))}`;
    }
    return route.chatKey ? `#/chat/${encodeURIComponent(route.chatKey)}` : "#/new";
  }
  if (route.view === "article") {
    return route.articleId
      ? `#/article/${encodeURIComponent(route.articleId)}`
      : "#/creative";
  }
  const params = new URLSearchParams();
  if (route.chatKey) params.set("chat", route.chatKey);
  if (route.view === "settings" && route.settingsSection && route.settingsSection !== "overview") {
    params.set("section", route.settingsSection);
  }
  const query = params.toString();
  return `#/${route.view}${query ? `?${query}` : ""}`;
}

/** Capability pages own their section key; other views keep the given one. */
export function settingsSectionForView(
  view: WorkspaceRoute["view"],
  section?: SettingsSectionKey,
): SettingsSectionKey {
  if (view === "apps" || view === "automations" || view === "skills" || view === "agents") {
    return view;
  }
  return section ?? "overview";
}
