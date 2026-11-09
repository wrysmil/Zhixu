import type {
  ApiServicePayload,
  AutomationsPayload,
  AutomationUpdatePayload,
  ChannelConfigurePayload,
  ChannelConnectPayload,
  ChannelValidationPayload,
  ChatSummary,
  CliAppsPayload,
  EpisodePayload,
  FilePreviewPayload,
  FileSavePayload,
  IdentityFileEntry,
  ImageGenerationSettingsUpdate,
  McpPresetsPayload,
  McpOAuthFlowPayload,
  MarketplaceProvider,
  MemoryPayload,
  MemoryListResult,
  MemoryOrigin,
  MemoryPriority,
  MemoryStats,
  MemoryType,
  ZhixuFeaturesPayload,
  ModelConfigurationCreate,
  ModelConfigurationUpdate,
  NetworkSafetySettingsUpdate,
  PairingPayload,
  ProviderCreationUpdate,
  ProviderModelsPayload,
  ProviderOAuthCompletionResult,
  ProviderOAuthLoginResult,
  ProviderSettingsUpdate,
  RecoveryState,
  ScratchpadPayload,
  SessionDeleteResult,
  SessionDirectoryPayload,
  SessionHandle,
  SessionAutomationsPayload,
  SettingsPayload,
  SidebarStatePayload,
  SkillDetail,
  SkillActionPayload,
  SkillInstallPayload,
  SkillsPayload,
  SkillsSearchPayload,
  SkillsTrendsPayload,
  SkillsTrendingPayload,
  SlashCommand,
  SlashCommandLifecycle,
  TeamBinding,
  TranscriptionSettingsUpdate,
  WebSearchSettingsUpdate,
  WorkspacesPayload,
  WorkspaceSearchPayload,
  WebuiThreadPersistedPayload,
  WorkspaceScopePayload,
} from "./types";
import { fetchWithTimeout } from "./http";

const API_READ_TIMEOUT_MS = 20_000;
const API_MUTATION_TIMEOUT_MS = 20_000;
const PACKAGE_MUTATION_TIMEOUT_MS = 150_000;
const SLASH_COMMAND_LIFECYCLES = new Set<SlashCommandLifecycle>([
  "side_channel",
  "finalize_active_turn",
  "stop_active_turn",
  "agent_turn",
  "agent_turn_with_args",
]);

function isSlashCommandLifecycle(value: unknown): value is SlashCommandLifecycle {
  return (
    typeof value === "string"
    && SLASH_COMMAND_LIFECYCLES.has(value as SlashCommandLifecycle)
  );
}
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export interface WebUIMutationTransport {
  requestMutation<T>(
    action: string,
    payload?: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T>;
}

export async function request<T>(
  url: string,
  token: string,
  init?: RequestInit,
  timeoutMs: number = 0,
): Promise<T> {
  const res = await fetchWithTimeout(
    url,
    {
      ...(init ?? {}),
      headers: {
        ...(init?.headers ?? {}),
        Authorization: `Bearer ${token}`,
      },
      credentials: "same-origin",
    },
    timeoutMs,
  );
  if (!res.ok) {
    const text = typeof res.text === "function" ? (await res.text()).trim() : "";
    let message = text;
    if (text.startsWith("{")) {
      try {
        const payload: unknown = JSON.parse(text);
        if (payload && typeof payload === "object") {
          const error = (payload as { error?: unknown }).error;
          if (typeof error === "string" && error.trim()) message = error.trim();
        }
      } catch {
        // Preserve non-JSON error bodies exactly as returned by the gateway.
      }
    }
    throw new ApiError(res.status, message || `HTTP ${res.status}`);
  }
  const contentType = res.headers?.get?.("content-type") ?? "";
  if (contentType && !contentType.toLowerCase().includes("application/json")) {
    const text = typeof res.text === "function" ? await res.text() : "";
    const isHtml = text.trimStart().toLowerCase().startsWith("<!doctype");
    throw new ApiError(
      res.status,
      isHtml
        ? "Gateway returned WebUI HTML instead of JSON. Restart nanobot gateway and try again."
        : "Gateway returned a non-JSON response.",
    );
  }
  return (await res.json()) as T;
}

export async function mutation<T>(
  transport: WebUIMutationTransport,
  action: string,
  payload: Record<string, unknown> = {},
  timeoutMs: number = API_MUTATION_TIMEOUT_MS,
): Promise<T> {
  try {
    return await transport.requestMutation<T>(action, payload, timeoutMs);
  } catch (reason) {
    const status = (
      typeof reason === "object"
      && reason !== null
      && "status" in reason
      && typeof reason.status === "number"
    ) ? reason.status : 500;
    const message = reason instanceof Error ? reason.message : "WebUI mutation failed";
    throw new ApiError(status, message);
  }
}

function compactMcpValues(values: Record<string, unknown>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  Object.entries(values).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) payload[key] = trimmed;
      return;
    }
    payload[key] = value;
  });
  return payload;
}

function splitKey(key: string): { channel: string; chatId: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { channel: "", chatId: key };
  return { channel: key.slice(0, idx), chatId: key.slice(idx + 1) };
}

function normalizeSessionHandle(value: unknown): SessionHandle | null {
  if (!value || typeof value !== "object") return null;
  const handle = value as Partial<SessionHandle>;
  const id = typeof handle.id === "string" ? handle.id.trim() : "";
  const name = typeof handle.name === "string" ? handle.name.trim() : "";
  if (
    !/^handle_[a-f0-9]{32}$/i.test(id)
    || !name
    || !/^[\p{L}\p{N}_-]+$/u.test(name)
  ) return null;
  return { id, name };
}

export async function listSessions(
  token: string,
  base: string = "",
): Promise<ChatSummary[]> {
  type Row = {
    key: string;
    created_at: string | null;
    updated_at: string | null;
    title?: string;
    preview?: string;
    model_preset?: string | null;
    run_started_at?: number | null;
    recovery_state?: RecoveryState | null;
    workspace_scope?: WorkspaceScopePayload | null;
    handle?: SessionHandle | null;
    team_binding?: TeamBinding | null;
  };
  const body = await request<{ sessions: Row[] }>(
    `${base}/api/sessions`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return body.sessions.map((s) => {
    const handle = normalizeSessionHandle(s.handle);
    return {
      key: s.key,
      ...splitKey(s.key),
      createdAt: s.created_at,
      updatedAt: s.updated_at,
      title: s.title ?? "",
      preview: s.preview ?? "",
      modelPreset: s.model_preset ?? null,
      runStartedAt: s.run_started_at ?? null,
      recoveryState: s.recovery_state ?? null,
      workspaceScope: s.workspace_scope ?? null,
      handle,
      // 服务端确认的绑定才是真值；列表不带这个键时按普通聊天解释（契约 §4）。
      teamBinding: s.team_binding ?? null,
    };
  });
}

/** Disk-backed WebUI display thread snapshot (separate from agent session). */
export interface FetchWebuiThreadOptions {
  limit?: number;
  direction?: "latest";
  before?: string | null;
  signal?: AbortSignal;
}

export async function fetchWebuiThread(
  token: string,
  key: string,
  optionsOrBase?: FetchWebuiThreadOptions | string,
  base: string = "",
): Promise<WebuiThreadPersistedPayload | null> {
  const options = typeof optionsOrBase === "string" ? undefined : optionsOrBase;
  const resolvedBase = typeof optionsOrBase === "string" ? optionsOrBase : base;
  const params = new URLSearchParams();
  if (options?.limit !== undefined) params.set("limit", String(options.limit));
  if (options?.direction) params.set("direction", options.direction);
  if (options?.before) params.set("before", options.before);
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  const url = `${resolvedBase}/api/sessions/${encodeURIComponent(key)}/webui-thread${suffix}`;
  const res = await fetchWithTimeout(url, {
    headers: { Authorization: `Bearer ${token}` },
    credentials: "same-origin",
    cache: "no-store",
    signal: options?.signal,
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return (await res.json()) as WebuiThreadPersistedPayload;
}

/**
 * Fetch a read-only file preview.
 *
 * ``scope`` mirrors the workspace endpoints: when provided (e.g. ``"default"``
 * from the home Start panel) the gateway anchors the path at that workspace
 * instead of the session's own scope, so a session key is only needed to pass
 * the route's WebUI-namespace format check.
 */
export async function fetchFilePreview(
  token: string,
  key: string,
  path: string,
  base: string = "",
  scope: string | null = null,
): Promise<FilePreviewPayload> {
  const query = new URLSearchParams();
  query.set("path", path);
  if (scope) query.set("scope", scope);
  return request<FilePreviewPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-preview?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchFilePreviewAvailability(
  token: string,
  key: string,
  path: string,
  base: string = "",
): Promise<boolean> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("probe", "1");
  const payload = await request<{ available?: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-preview?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return payload.available !== false;
}

/** Save editor content back to a workspace file through the authenticated WebSocket. */
export async function saveFileContent(
  transport: WebUIMutationTransport,
  key: string,
  path: string,
  content: string,
  scope: string | null = null,
): Promise<FileSavePayload> {
  return mutation<FileSavePayload>(transport, "workspace.file.save", {
    key,
    path,
    content,
    ...(scope ? { scope } : {}),
  });
}

export async function fetchSessionDirectory(
  token: string,
  key: string,
  path: string | null,
  base: string = "",
  showHidden: boolean = false,
): Promise<SessionDirectoryPayload> {
  const query = new URLSearchParams();
  if (path) query.set("path", path);
  if (showHidden) query.set("show_hidden", "1");
  return request<SessionDirectoryPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/files?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Workspace-anchored (no session) read-only directory listing used by the home
 * "Start" panel. Mirrors ``fetchSessionDirectory``; the payload shape is
 * identical (contract §2). 404 ``no_default_workspace`` surfaces as an
 * ``ApiError`` so callers can render the pick-a-project empty state.
 */
export async function fetchWorkspaceFiles(
  token: string,
  path: string | null = null,
  base: string = "",
  showHidden: boolean = false,
): Promise<SessionDirectoryPayload> {
  const query = new URLSearchParams({ scope: "default" });
  if (path) query.set("path", path);
  if (showHidden) query.set("show_hidden", "1");
  return request<SessionDirectoryPayload>(
    `${base}/api/workspace/files?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSessionSearch(
  token: string,
  key: string,
  text: string,
  base: string = "",
  showHidden: boolean = false,
): Promise<WorkspaceSearchPayload> {
  const query = new URLSearchParams({ q: text });
  if (showHidden) query.set("show_hidden", "1");
  return request<WorkspaceSearchPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/files/search?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchWorkspaceSearch(
  token: string,
  text: string,
  base: string = "",
  showHidden: boolean = false,
): Promise<WorkspaceSearchPayload> {
  const query = new URLSearchParams({ scope: "default", q: text });
  if (showHidden) query.set("show_hidden", "1");
  return request<WorkspaceSearchPayload>(
    `${base}/api/workspace/files/search?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** One entry of the gateway's terminal shell whitelist (contract v2). */
export interface TerminalShellEntry {
  id: string;
  label: string;
  path: string;
}

/** Payload of ``GET /webui/terminal/shells`` used by the dock shell dropdown. */
export interface TerminalShellsPayload {
  shells: TerminalShellEntry[];
  default: string | null;
}

/**
 * Fetch the host shells the gateway allows for PTY terminals.
 *
 * Non-OK responses (401 without an api token, 404 on gateways older than the
 * endpoint) surface as ``ApiError`` so callers can fall back to the platform
 * default shell.
 */
export async function fetchTerminalShells(
  token: string,
  base: string = "",
): Promise<TerminalShellsPayload> {
  return request<TerminalShellsPayload>(
    `${base}/webui/terminal/shells`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSessionAutomations(
  token: string,
  key: string,
  base: string = "",
): Promise<SessionAutomationsPayload> {
  return request<SessionAutomationsPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/automations`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchAutomations(
  token: string,
  base: string = "",
): Promise<AutomationsPayload> {
  return request<AutomationsPayload>(
    `${base}/api/webui/automations`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runAutomationAction(
  transport: WebUIMutationTransport,
  action: "enable" | "disable" | "delete" | "run",
  id: string,
): Promise<AutomationsPayload> {
  return mutation<AutomationsPayload>(transport, `automation.${action}`, { id });
}

export async function updateAutomation(
  transport: WebUIMutationTransport,
  id: string,
  values: AutomationUpdatePayload,
): Promise<AutomationsPayload> {
  return mutation<AutomationsPayload>(transport, "automation.update", { id, values });
}

export async function fetchSkills(
  token: string,
  base: string = "",
): Promise<SkillsPayload> {
  return request<SkillsPayload>(
    `${base}/api/webui/skills`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSkillDetail(
  token: string,
  name: string,
  base: string = "",
): Promise<SkillDetail> {
  return request<SkillDetail>(
    `${base}/api/webui/skills/${encodeURIComponent(name)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateSkillEnabled(
  transport: WebUIMutationTransport,
  name: string,
  enabled: boolean,
): Promise<SkillActionPayload> {
  return mutation<SkillActionPayload>(transport, "skill.update", { name, enabled });
}

export async function deleteSkill(
  transport: WebUIMutationTransport,
  name: string,
): Promise<SkillActionPayload> {
  return mutation<SkillActionPayload>(transport, "skill.delete", { name });
}

export async function searchMarketplaceSkills(
  token: string,
  query: string,
  provider: MarketplaceProvider = "all",
  base: string = "",
): Promise<SkillsSearchPayload> {
  const params = new URLSearchParams({ q: query, provider });
  return request<SkillsSearchPayload>(
    `${base}/api/webui/skills/search?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchTrendingMarketplaceSkills(
  token: string,
  provider: MarketplaceProvider = "all",
  base: string = "",
): Promise<SkillsTrendingPayload> {
  const params = new URLSearchParams({ provider });
  return request<SkillsTrendingPayload>(
    `${base}/api/webui/skills/trending?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMarketplaceSkillTrends(
  token: string,
  skillIds: string[],
  base: string = "",
): Promise<SkillsTrendsPayload> {
  const params = new URLSearchParams();
  skillIds.forEach((id) => params.append("id", id));
  return request<SkillsTrendsPayload>(
    `${base}/api/webui/skills/trends?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function installMarketplaceSkill(
  transport: WebUIMutationTransport,
  provider: Exclude<MarketplaceProvider, "all">,
  source: string,
  skill: string,
  version: string = "",
): Promise<SkillInstallPayload> {
  return mutation<SkillInstallPayload>(
    transport,
    "skill.install",
    { provider, source, skill, ...(version ? { version } : {}) },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function deleteSession(
  transport: WebUIMutationTransport,
  key: string,
  optionsOrBase?: { deleteAutomations?: boolean } | string,
): Promise<SessionDeleteResult> {
  const options = typeof optionsOrBase === "string" ? undefined : optionsOrBase;
  return mutation<SessionDeleteResult>(
    transport,
    "session.delete",
    {
      key,
      ...(options?.deleteAutomations ? { delete_automations: true } : {}),
    },
  );
}

export async function fetchSettings(
  token: string,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    `${base}/api/settings`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSettingsUsage(
  token: string,
  base: string = "",
): Promise<NonNullable<SettingsPayload["usage"]>> {
  return request<NonNullable<SettingsPayload["usage"]>>(
    `${base}/api/settings/usage`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export interface VersionCheckResult {
  updateAvailable: {
    currentVersion: string;
    latestVersion: string;
    pypiUrl?: string;
  } | null;
}

export async function checkVersion(
  token: string,
  base: string = "",
): Promise<VersionCheckResult> {
  return request<VersionCheckResult>(
    `${base}/api/settings/version-check`,
    token,
    undefined,
    10_000,
  );
}

export async function fetchWorkspaces(
  token: string,
  base: string = "",
): Promise<WorkspacesPayload> {
  return request<WorkspacesPayload>(
    `${base}/api/workspaces`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCliApps(
  token: string,
  base: string = "",
): Promise<CliAppsPayload> {
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchInstalledCliApps(
  token: string,
  base: string = "",
): Promise<CliAppsPayload> {
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps?installed_only=1`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchZhixuFeatures(
  token: string,
  base: string = "",
): Promise<ZhixuFeaturesPayload> {
  return request<ZhixuFeaturesPayload>(
    `${base}/api/settings/zhixu-features`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchApiService(token: string, base: string = ""): Promise<ApiServicePayload> {
  return request<ApiServicePayload>(`${base}/api/settings/api-service`, token);
}

export async function startApiService(
  transport: WebUIMutationTransport,
  values: { host: string; port: number; timeout: number; apiKey?: string },
): Promise<ApiServicePayload> {
  return mutation<ApiServicePayload>(
    transport,
    "settings.api_service.start",
    {
      host: values.host,
      port: values.port,
      timeout: values.timeout,
      ...(values.apiKey !== undefined ? { api_key: values.apiKey } : {}),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function stopApiService(
  transport: WebUIMutationTransport,
): Promise<ApiServicePayload> {
  return mutation<ApiServicePayload>(transport, "settings.api_service.stop");
}

export async function enableZhixuFeature(
  transport: WebUIMutationTransport,
  name: string,
  options: { instanceId?: string } = {},
): Promise<ZhixuFeaturesPayload> {
  return mutation<ZhixuFeaturesPayload>(
    transport,
    "settings.feature.enable",
    { name, ...(options.instanceId ? { instance_id: options.instanceId } : {}) },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function disableZhixuFeature(
  transport: WebUIMutationTransport,
  name: string,
  options: { instanceId?: string } = {},
): Promise<ZhixuFeaturesPayload> {
  return mutation<ZhixuFeaturesPayload>(
    transport,
    "settings.feature.disable",
    { name, ...(options.instanceId ? { instance_id: options.instanceId } : {}) },
  );
}

export async function fetchPairingRequests(
  token: string,
  base: string = "",
): Promise<PairingPayload> {
  return request<PairingPayload>(
    `${base}/api/settings/pairing`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runPairingAction(
  transport: WebUIMutationTransport,
  action: "approve" | "deny",
  code: string,
): Promise<PairingPayload> {
  return mutation<PairingPayload>(transport, `settings.pairing.${action}`, { code });
}

export async function startChannelConnect(
  transport: WebUIMutationTransport,
  channel: string,
  options: {
    domain?: string;
    instanceId?: string;
    mode?: "replace" | "create";
    force?: boolean;
  } = {},
): Promise<ChannelConnectPayload> {
  return mutation<ChannelConnectPayload>(
    transport,
    "settings.channel.connect.start",
    {
      channel,
      ...(options.domain ? { domain: options.domain } : {}),
      ...(options.instanceId ? { instance_id: options.instanceId } : {}),
      ...(options.mode ? { mode: options.mode } : {}),
      ...(options.force ? { force: true } : {}),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function pollChannelConnect(
  transport: WebUIMutationTransport,
  channel: string,
  sessionId: string,
  params: Readonly<Record<string, string>> = {},
): Promise<ChannelConnectPayload> {
  const values = Object.fromEntries(
    Object.entries(params).filter(([key]) => key !== "session_id"),
  );
  return mutation<ChannelConnectPayload>(
    transport,
    "settings.channel.connect.poll",
    { channel, session_id: sessionId, ...values },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function cancelChannelConnect(
  transport: WebUIMutationTransport,
  channel: string,
  sessionId: string,
): Promise<ChannelConnectPayload> {
  return mutation<ChannelConnectPayload>(
    transport,
    "settings.channel.connect.cancel",
    { channel, session_id: sessionId },
  );
}

export async function configureChannel(
  transport: WebUIMutationTransport,
  name: string,
  values: Record<string, string>,
  options: { enable?: boolean; instanceId?: string } = {},
): Promise<ChannelConfigurePayload> {
  return mutation<ChannelConfigurePayload>(
    transport,
    "settings.channel.configure",
    {
      name,
      values,
      ...(options.enable !== undefined ? { enable: options.enable } : {}),
      ...(options.instanceId ? { instance_id: options.instanceId } : {}),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function validateChannel(
  transport: WebUIMutationTransport,
  name: string,
  values: Record<string, string> = {},
  options: { instanceId?: string } = {},
): Promise<ChannelValidationPayload> {
  return mutation<ChannelValidationPayload>(
    transport,
    "settings.channel.validate",
    { name, values, ...(options.instanceId ? { instance_id: options.instanceId } : {}) },
  );
}

export async function runCliAppAction(
  transport: WebUIMutationTransport,
  action: "install" | "update" | "uninstall" | "test",
  name: string,
): Promise<CliAppsPayload> {
  return mutation<CliAppsPayload>(
    transport,
    `settings.cli_app.${action}`,
    { name },
    action === "install" || action === "update"
      ? PACKAGE_MUTATION_TIMEOUT_MS
      : API_MUTATION_TIMEOUT_MS,
  );
}

export async function fetchMcpPresets(
  token: string,
  base: string = "",
): Promise<McpPresetsPayload> {
  return request<McpPresetsPayload>(
    `${base}/api/settings/mcp-presets`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function startMcpOAuth(
  transport: WebUIMutationTransport,
  name: string,
  reset: boolean = false,
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_start",
    { name, ...(reset ? { reset: true } : {}) },
    30_000,
  );
}

export async function fetchMcpOAuthStatus(
  token: string,
  flowId: string,
  base: string = "",
): Promise<McpOAuthFlowPayload> {
  const query = new URLSearchParams({ flow_id: flowId });
  return request<McpOAuthFlowPayload>(
    `${base}/api/settings/mcp-oauth/status?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function completeMcpOAuth(
  transport: WebUIMutationTransport,
  flowId: string,
  callbackUrl: string,
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_complete",
    { flow_id: flowId, callback_url: callbackUrl },
  );
}

export async function cancelMcpOAuth(
  transport: WebUIMutationTransport,
  flowId: string,
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_cancel",
    { flow_id: flowId },
  );
}

export async function fetchProviderModels(
  token: string,
  provider: string,
  base: string = "",
): Promise<ProviderModelsPayload> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  return request<ProviderModelsPayload>(
    `${base}/api/settings/provider-models?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runMcpPresetAction(
  transport: WebUIMutationTransport,
  action: "enable" | "disable" | "remove" | "test" | "reconnect",
  name: string,
  values: Record<string, string> = {},
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    `settings.mcp.${action}`,
    { name, ...compactMcpValues(values) },
  );
}

export async function saveCustomMcpServer(
  transport: WebUIMutationTransport,
  values: Record<string, string>,
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    "settings.mcp.custom",
    compactMcpValues(values),
  );
}

export async function importMcpConfig(
  transport: WebUIMutationTransport,
  config: string,
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(transport, "settings.mcp.import", { config });
}

export async function updateMcpServerTools(
  transport: WebUIMutationTransport,
  name: string,
  enabledTools: string[],
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    "settings.mcp.tools",
    { name, enabled_tools: enabledTools },
  );
}

export async function listSlashCommands(
  token: string,
  base: string = "",
): Promise<SlashCommand[]> {
  type Row = {
    command: string;
    title: string;
    description: string;
    icon: string;
    arg_hint?: string;
    lifecycle?: unknown;
    accepts_args?: unknown;
  };
  const body = await request<{ commands: Row[] }>(
    `${base}/api/commands`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return body.commands
    .flatMap((command) => {
      if (!isSlashCommandLifecycle(command.lifecycle)) return [];
      return [{
        command: command.command,
        title: command.title,
        description: command.description,
        icon: command.icon,
        argHint: command.arg_hint ?? "",
        lifecycle: command.lifecycle,
        acceptsArgs: command.accepts_args === true,
      }];
    });
}

export async function fetchSidebarState(
  token: string,
  base: string = "",
): Promise<SidebarStatePayload> {
  return request<SidebarStatePayload>(
    `${base}/api/webui/sidebar-state`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateSidebarState(
  transport: WebUIMutationTransport,
  state: SidebarStatePayload,
): Promise<SidebarStatePayload> {
  return mutation<SidebarStatePayload>(transport, "sidebar.update", { state });
}

function modelGenerationSettingsPayload(
  configuration: Pick<
    ModelConfigurationCreate,
    "maxTokens" | "contextWindowTokens" | "temperature" | "reasoningEffort"
  >,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (configuration.maxTokens !== undefined) {
    payload.max_tokens = configuration.maxTokens;
  }
  if (configuration.contextWindowTokens !== undefined) {
    payload.context_window_tokens = configuration.contextWindowTokens;
  }
  if (configuration.temperature !== undefined) {
    payload.temperature = configuration.temperature;
  }
  if (configuration.reasoningEffort !== undefined) {
    payload.reasoning_effort = configuration.reasoningEffort ?? "";
  }
  return payload;
}

export async function createModelConfiguration(
  transport: WebUIMutationTransport,
  configuration: ModelConfigurationCreate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.create",
    {
      name: configuration.name,
      provider: configuration.provider,
      model: configuration.model,
      ...modelGenerationSettingsPayload(configuration),
    },
  );
}

export async function updateModelConfiguration(
  transport: WebUIMutationTransport,
  configuration: ModelConfigurationUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.update",
    {
      name: configuration.name,
      ...(configuration.newName !== undefined ? { new_name: configuration.newName } : {}),
      ...(configuration.provider !== undefined ? { provider: configuration.provider } : {}),
      ...(configuration.model !== undefined ? { model: configuration.model } : {}),
      ...modelGenerationSettingsPayload(configuration),
    },
  );
}

export async function deleteModelConfiguration(
  transport: WebUIMutationTransport,
  name: string,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.delete",
    { name },
  );
}

export async function migrateModelConfigurations(
  transport: WebUIMutationTransport,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.model_configuration.migrate");
}

export async function updateModelCallOrder(
  transport: WebUIMutationTransport,
  order: string[],
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.model_call_order.update", { order });
}

export async function updateProviderSettings(
  transport: WebUIMutationTransport,
  update: ProviderSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.update", { ...update });
}

export async function createProviderSettings(
  transport: WebUIMutationTransport,
  update: ProviderCreationUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.create", { ...update });
}

export async function loginProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
  remoteBrowserAccess: boolean = false,
): Promise<ProviderOAuthLoginResult> {
  return mutation<ProviderOAuthLoginResult>(
    transport,
    "settings.provider.oauth_login",
    { provider, ...(remoteBrowserAccess ? { remote_browser: true } : {}) },
  );
}

export async function completeProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
  flowId: string,
  authorizationResponse?: string,
): Promise<ProviderOAuthCompletionResult> {
  return mutation<ProviderOAuthCompletionResult>(
    transport,
    "settings.provider.oauth_complete",
    {
      provider,
      flow_id: flowId,
      ...(authorizationResponse ? { authorization_response: authorizationResponse } : {}),
    },
  );
}

export async function logoutProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.oauth_logout", { provider });
}

export async function updateWebSearchSettings(
  transport: WebUIMutationTransport,
  update: WebSearchSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.web_search.update",
    {
      provider: update.provider,
      ...(update.apiKey !== undefined ? { api_key: update.apiKey } : {}),
      ...(update.baseUrl !== undefined ? { base_url: update.baseUrl } : {}),
      ...(update.maxResults !== undefined ? { max_results: update.maxResults } : {}),
      ...(update.timeout !== undefined ? { timeout: update.timeout } : {}),
      ...(update.useJinaReader !== undefined
        ? { use_jina_reader: update.useJinaReader }
        : {}),
    },
  );
}

export async function updateNetworkSafetySettings(
  transport: WebUIMutationTransport,
  update: NetworkSafetySettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.network_safety.update",
    {
      webui_allow_local_service_access: update.webuiAllowLocalServiceAccess,
      webui_default_access_mode: update.webuiDefaultAccessMode,
    },
  );
}

export async function updateImageGenerationSettings(
  transport: WebUIMutationTransport,
  update: ImageGenerationSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.image_generation.update",
    {
      enabled: update.enabled,
      provider: update.provider,
      model: update.model,
      default_aspect_ratio: update.defaultAspectRatio,
      default_image_size: update.defaultImageSize,
      max_images_per_turn: update.maxImagesPerTurn,
    },
  );
}

export async function updateTranscriptionSettings(
  transport: WebUIMutationTransport,
  update: TranscriptionSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.transcription.update",
    {
      enabled: update.enabled,
      provider: update.provider,
      model: update.model,
      language: update.language,
      max_duration_sec: update.maxDurationSec,
      max_upload_mb: update.maxUploadMb,
    },
  );
}

const MEMORY_BASE = "/api/settings/memory";

export interface ListMemoriesOpts {
  type?: MemoryType;
  order?: "created" | "updated" | "importance";
  priority?: MemoryPriority;
  origin?: MemoryOrigin;
  workspace?: string;
  q?: string;
  limit?: number;
}

export async function listMemories(
  token: string,
  opts: ListMemoriesOpts = {},
  base: string = "",
): Promise<MemoryListResult<MemoryPayload>> {
  const params = new URLSearchParams();
  if (opts.type) params.set("type", opts.type);
  if (opts.order) params.set("order", opts.order);
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.priority) params.set("priority", opts.priority);
  if (opts.origin) params.set("origin", opts.origin);
  if (opts.workspace) params.set("workspace", opts.workspace);
  if (opts.q) params.set("q", opts.q);
  const qs = params.toString();
  return request<MemoryListResult<MemoryPayload>>(
    `${base}${MEMORY_BASE}/memories${qs ? `?${qs}` : ""}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function searchMemories(
  token: string,
  query: string,
  opts: Omit<ListMemoriesOpts, "q"> = {},
  base: string = "",
): Promise<MemoryListResult<MemoryPayload> & { query: string }> {
  const params = new URLSearchParams({ q: query });
  if (opts.type) params.set("type", opts.type);
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.priority) params.set("priority", opts.priority);
  if (opts.origin) params.set("origin", opts.origin);
  if (opts.workspace) params.set("workspace", opts.workspace);
  if (opts.order) params.set("order", opts.order);
  return request<MemoryListResult<MemoryPayload> & { query: string }>(
    `${base}${MEMORY_BASE}/memories/search?${params.toString()}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMemory(
  token: string,
  id: string,
  base: string = "",
): Promise<{ memory: MemoryPayload }> {
  return request<{ memory: MemoryPayload }>(
    `${base}${MEMORY_BASE}/memories/get?id=${encodeURIComponent(id)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function listEpisodes(
  token: string,
  opts: { session_id?: string; limit?: number; origin?: MemoryOrigin; workspace?: string; q?: string; order?: "created" | "importance" } = {},
  base: string = "",
): Promise<MemoryListResult<EpisodePayload>> {
  const params = new URLSearchParams();
  if (opts.session_id) params.set("session_id", opts.session_id);
  if (opts.order) params.set("order", opts.order);
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.origin) params.set("origin", opts.origin);
  if (opts.workspace) params.set("workspace", opts.workspace);
  if (opts.q) params.set("q", opts.q);
  const qs = params.toString();
  return request<MemoryListResult<EpisodePayload>>(
    `${base}${MEMORY_BASE}/episodes${qs ? `?${qs}` : ""}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchEpisode(
  token: string,
  id: string,
  base: string = "",
): Promise<{ episode: EpisodePayload }> {
  return request<{ episode: EpisodePayload }>(
    `${base}${MEMORY_BASE}/episodes/get?id=${encodeURIComponent(id)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchScratchpad(
  token: string,
  base: string = "",
): Promise<{ scratchpad: ScratchpadPayload | null }> {
  return request<{ scratchpad: ScratchpadPayload | null }>(
    `${base}${MEMORY_BASE}/scratchpad`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMemoryStats(token: string, base: string = ""): Promise<MemoryStats> {
  return request<MemoryStats>(
    `${base}${MEMORY_BASE}/stats`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export interface CreateMemoryInput {
  content: string;
  type: MemoryType;
  priority?: MemoryPriority;
  importanceScore?: number;
  tags?: string[];
  subject?: string;
  predicate?: string;
  metadata?: Record<string, unknown>;
  workspace?: string;
}

export async function createMemory(
  transport: WebUIMutationTransport,
  input: CreateMemoryInput,
): Promise<{ memory: MemoryPayload }> {
  return mutation<{ memory: MemoryPayload }>(transport, "memory.create", {
    content: input.content,
    type: input.type,
    ...(input.priority !== undefined ? { priority: input.priority } : {}),
    ...(input.importanceScore !== undefined
      ? { importance_score: input.importanceScore }
      : {}),
    ...(input.tags !== undefined ? { tags: input.tags } : {}),
    ...(input.subject !== undefined ? { subject: input.subject } : {}),
    ...(input.predicate !== undefined ? { predicate: input.predicate } : {}),
    ...(input.metadata !== undefined ? { metadata: input.metadata } : {}),
    ...(input.workspace !== undefined ? { workspace: input.workspace } : {}),
  });
}

export interface MemoryPatch {
  content?: string;
  type?: MemoryType;
  priority?: MemoryPriority;
  importanceScore?: number;
  tags?: string[];
  subject?: string;
  predicate?: string;
  metadata?: Record<string, unknown>;
  workspace?: string;
}

export async function updateMemory(
  transport: WebUIMutationTransport,
  id: string,
  patch: MemoryPatch,
): Promise<{ memory: MemoryPayload }> {
  return mutation<{ memory: MemoryPayload }>(transport, "memory.update", {
    id,
    ...(patch.content !== undefined ? { content: patch.content } : {}),
    ...(patch.type !== undefined ? { type: patch.type } : {}),
    ...(patch.priority !== undefined ? { priority: patch.priority } : {}),
    ...(patch.importanceScore !== undefined
      ? { importance_score: patch.importanceScore }
      : {}),
    ...(patch.tags !== undefined ? { tags: patch.tags } : {}),
    ...(patch.subject !== undefined ? { subject: patch.subject } : {}),
    ...(patch.predicate !== undefined ? { predicate: patch.predicate } : {}),
    ...(patch.metadata !== undefined ? { metadata: patch.metadata } : {}),
    ...(patch.workspace !== undefined ? { workspace: patch.workspace } : {}),
  });
}

export async function deleteMemory(
  transport: WebUIMutationTransport,
  id: string,
): Promise<{ ok: true }> {
  return mutation<{ ok: true }>(transport, "memory.delete", { id });
}

export async function deleteEpisode(
  transport: WebUIMutationTransport,
  id: string,
): Promise<{ ok: true }> {
  return mutation<{ ok: true }>(transport, "episode.delete", { id });
}

export interface SaveScratchpadInput {
  content: string;
  activeProjects?: string[];
  currentFocus?: string;
  openQuestions?: string[];
  nextSteps?: string[];
}

export async function saveScratchpad(
  transport: WebUIMutationTransport,
  input: SaveScratchpadInput,
): Promise<{ scratchpad: ScratchpadPayload }> {
  return mutation<{ scratchpad: ScratchpadPayload }>(transport, "scratchpad.save", {
    content: input.content,
    ...(input.activeProjects !== undefined
      ? { active_projects: input.activeProjects }
      : {}),
    ...(input.currentFocus !== undefined ? { current_focus: input.currentFocus } : {}),
    ...(input.openQuestions !== undefined
      ? { open_questions: input.openQuestions }
      : {}),
    ...(input.nextSteps !== undefined ? { next_steps: input.nextSteps } : {}),
  });
}

// ── Identity Files ─────────────────────────────────────────────────────────────

const IDENTITY_BASE = "/api/settings/identity";

export interface IdentityCompileStatus {
  compiled: boolean;
  fresh: boolean;
  reason: string;
  compiled_at?: string | null;
}

export interface IdentityFilesResponse {
  files: IdentityFileEntry[];
  charLimit: number;
  /** P3: last compilation time and whether the sources changed since then. */
  compile?: IdentityCompileStatus;
}

export async function listIdentityFiles(
  token: string,
  base: string = "",
): Promise<IdentityFilesResponse> {
  return request<IdentityFilesResponse>(
    `${base}${IDENTITY_BASE}/files`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export interface IdentityFileResponse {
  name: string;
  content: string;
  exists?: boolean;
  fromTemplate?: boolean;
}

export async function fetchIdentityFile(
  token: string,
  name: string,
  base: string = "",
): Promise<IdentityFileResponse> {
  // encodeURIComponent 而非 URLSearchParams：后者把空格编码成 '+'，
  // 语义等价但可读性差，且部分代理会错误处理未转义的 '+'。
  const query = `name=${encodeURIComponent(name)}`;
  return request<IdentityFileResponse>(
    `${base}${IDENTITY_BASE}/file?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export interface SaveIdentityFileInput {
  name: string;
  content: string;
}

export async function saveIdentityFile(
  transport: WebUIMutationTransport,
  input: SaveIdentityFileInput,
): Promise<{ saved: boolean }> {
  return mutation<{ saved: boolean }>(transport, "identity.file.save", {
    name: input.name,
    content: input.content,
  });
}

export interface ReloadIdentityResponse {
  status: string;
}

export async function reloadIdentity(
  transport: WebUIMutationTransport,
): Promise<ReloadIdentityResponse> {
  return mutation<ReloadIdentityResponse>(transport, "identity.reload");
}

export interface ActivePersonaResponse {
  active: string;
  options: string[];
}

export async function getActivePersona(
  token: string,
  base: string = "",
): Promise<ActivePersonaResponse> {
  return request<ActivePersonaResponse>(
    `${base}${IDENTITY_BASE}/persona`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Set (or, with an empty stem, clear) the persona injected into the system prompt. */
export async function setActivePersona(
  transport: WebUIMutationTransport,
  stem: string,
): Promise<{ active: string }> {
  return mutation<{ active: string }>(transport, "identity.persona.set", { stem });
}

// ---- 知识中心（knowledge center）------------------------------------------
//
// 动作名与路径以 `nanobot/webui/knowledge_routes.py` 为唯一真相源：
// 读动作走认证 HTTP GET（`/api/settings/knowledge/*`），写动作走 WS mutation
// （`knowledge.kb.*` / `knowledge.doc.*` / `knowledge.repair`）。payload 一律
// camelCase，和现有 settings 客户端口径一致。

export const KNOWLEDGE_BASE = "/api/settings/knowledge";

export type KnowledgeVisibility = "workspace" | "global";
export type KnowledgeDocStatus = "pending" | "processing" | "ready" | "failed";
export type KnowledgeSearchMode = "hybrid" | "semantic" | "keyword";

export interface KnowledgeKb {
  id: string;
  name: string;
  description: string;
  workspaceId: string;
  visibility: KnowledgeVisibility | string;
  isEnabled: boolean;
  documentCount: number;
  embeddingBackend?: string;
  chunkSize?: number;
  chunkOverlap?: number;
  createdAt?: string;
  updatedAt?: string;
}

/**
 * 文档读模型。后端刻意不下发 `source_path`（路径泄露面），因此前端只能用
 * `originalName` / `mime` 充当「来源」列。
 */
export interface KnowledgeDoc {
  id: string;
  kbId: string;
  title: string;
  originalName: string;
  status: KnowledgeDocStatus | string;
  chunkCount: number;
  charCount: number;
  mime?: string;
  sourceKind?: string;
  errorCode?: string;
  error?: string;
  vectorStatus?: string;
  isEnabled?: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface KnowledgeChunk {
  id: string;
  docId: string;
  kbId: string;
  index: number;
  heading: string;
  page: number | null;
  locator: string;
  content: string;
  charCount: number;
}

export interface KnowledgeHit {
  chunkId: string;
  docId: string;
  kbId: string;
  score: number;
  content: string;
  heading: string;
  page: number | null;
  locator: string;
}

export interface KnowledgeSearchResult {
  enabled: boolean;
  modeUsed: string;
  degraded: string[];
  reason: string | null;
  routes: string[];
  hits: KnowledgeHit[];
}

export interface KnowledgeStats {
  enabled: boolean;
  kbs: number;
  documents: number;
  chunks: number;
  pending: number;
  vector: number | null;
  byStatus?: Record<string, number>;
  drift?: number | null;
  expiredLeases?: number;
  dbBytes?: number;
  uploadsBytes?: number;
}

export interface KnowledgeKbListPayload {
  enabled: boolean;
  kbs: KnowledgeKb[];
}

export interface KnowledgeDocListPayload {
  enabled: boolean;
  documents: KnowledgeDoc[];
  total: number;
}

export interface KnowledgeDocGetPayload {
  enabled: boolean;
  document: KnowledgeDoc | null;
  chunks: KnowledgeChunk[];
  chunkCount: number;
}

export interface KnowledgeChunkGetPayload {
  enabled: boolean;
  chunk: KnowledgeChunk | null;
}

/** 读动作与写动作共用的作用域过滤；缺省时后端按已认证可见集合处理。 */
export interface KnowledgeScope {
  workspaceIds?: string[];
}

function knowledgeQuery(
  params: Record<string, string | number | string[] | undefined | null>,
): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    if (Array.isArray(value)) {
      const joined = value.filter(Boolean).join(",");
      if (joined) search.set(key, joined);
      return;
    }
    if (typeof value === "number") {
      if (!Number.isFinite(value)) return;
      search.set(key, String(value));
      return;
    }
    const trimmed = value.trim();
    if (trimmed) search.set(key, trimmed);
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

export async function fetchKnowledgeKbs(
  token: string,
  scope: KnowledgeScope = {},
  base: string = "",
): Promise<KnowledgeKbListPayload> {
  const query = knowledgeQuery({ workspaceIds: scope.workspaceIds });
  const payload = await request<KnowledgeKbListPayload>(
    `${base}${KNOWLEDGE_BASE}/kbs${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return {
    enabled: payload.enabled !== false,
    kbs: Array.isArray(payload.kbs) ? payload.kbs : [],
  };
}

export async function fetchKnowledgeDocs(
  token: string,
  params: { kbId?: string; status?: string; limit?: number; offset?: number } & KnowledgeScope = {},
  base: string = "",
): Promise<KnowledgeDocListPayload> {
  const query = knowledgeQuery({
    kbId: params.kbId,
    status: params.status,
    limit: params.limit,
    offset: params.offset,
    workspaceIds: params.workspaceIds,
  });
  const payload = await request<KnowledgeDocListPayload>(
    `${base}${KNOWLEDGE_BASE}/docs${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return {
    enabled: payload.enabled !== false,
    documents: Array.isArray(payload.documents) ? payload.documents : [],
    total: typeof payload.total === "number" ? payload.total : 0,
  };
}

export async function fetchKnowledgeDoc(
  token: string,
  params: { id: string; chunkLimit?: number },
  base: string = "",
): Promise<KnowledgeDocGetPayload> {
  const query = knowledgeQuery({ id: params.id, chunkLimit: params.chunkLimit });
  return request<KnowledgeDocGetPayload>(
    `${base}${KNOWLEDGE_BASE}/doc${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchKnowledgeChunk(
  token: string,
  chunkId: string,
  base: string = "",
): Promise<KnowledgeChunkGetPayload> {
  const query = knowledgeQuery({ chunkId });
  return request<KnowledgeChunkGetPayload>(
    `${base}${KNOWLEDGE_BASE}/chunk${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function searchKnowledge(
  token: string,
  params: {
    query: string;
    mode?: KnowledgeSearchMode | string;
    limit?: number;
    kbId?: string;
    kbIds?: string[];
  } & KnowledgeScope,
  base: string = "",
): Promise<KnowledgeSearchResult> {
  const query = knowledgeQuery({
    query: params.query,
    mode: params.mode,
    limit: params.limit,
    kbId: params.kbId,
    kbIds: params.kbIds,
    workspaceIds: params.workspaceIds,
  });
  const payload = await request<KnowledgeSearchResult>(
    `${base}${KNOWLEDGE_BASE}/search${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return {
    enabled: payload.enabled !== false,
    modeUsed: payload.modeUsed ?? "none",
    degraded: Array.isArray(payload.degraded) ? payload.degraded : [],
    reason: payload.reason ?? null,
    routes: Array.isArray(payload.routes) ? payload.routes : [],
    hits: Array.isArray(payload.hits) ? payload.hits : [],
  };
}

export async function fetchKnowledgeStats(
  token: string,
  scope: KnowledgeScope = {},
  base: string = "",
): Promise<KnowledgeStats> {
  const query = knowledgeQuery({ workspaceIds: scope.workspaceIds });
  const payload = await request<Record<string, unknown>>(
    `${base}${KNOWLEDGE_BASE}/stats${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  const num = (value: unknown): number =>
    typeof value === "number" && Number.isFinite(value) ? value : 0;
  return {
    enabled: payload.enabled !== false,
    kbs: num(payload.kbs),
    documents: num(payload.documents),
    chunks: num(payload.chunks),
    pending: num(payload.pending),
    vector: typeof payload.vector === "number" ? payload.vector : null,
    byStatus: (payload.by_status ?? payload.byStatus) as Record<string, number> | undefined,
    drift: typeof payload.drift === "number" ? payload.drift : null,
    expiredLeases: num(payload.expired_leases ?? payload.expiredLeases),
    dbBytes: num(payload.db_bytes ?? payload.dbBytes),
    uploadsBytes: num(payload.uploads_bytes ?? payload.uploadsBytes),
  };
}

export interface CreateKnowledgeKbInput {
  name: string;
  description?: string;
  visibility?: KnowledgeVisibility | string;
  workspaceId?: string;
}

export async function createKnowledgeKb(
  transport: WebUIMutationTransport,
  input: CreateKnowledgeKbInput,
): Promise<{ id: string }> {
  return mutation<{ id: string }>(transport, "knowledge.kb.create", {
    name: input.name,
    ...(input.description ? { description: input.description } : {}),
    ...(input.visibility ? { visibility: input.visibility } : {}),
    ...(input.workspaceId ? { workspaceId: input.workspaceId } : {}),
  });
}

export interface UpdateKnowledgeKbInput {
  id: string;
  name?: string;
  description?: string;
  isEnabled?: boolean;
}

export async function updateKnowledgeKb(
  transport: WebUIMutationTransport,
  input: UpdateKnowledgeKbInput,
): Promise<{ ok: boolean; id: string }> {
  return mutation<{ ok: boolean; id: string }>(transport, "knowledge.kb.update", {
    id: input.id,
    ...(input.name !== undefined ? { name: input.name } : {}),
    ...(input.description !== undefined ? { description: input.description } : {}),
    ...(input.isEnabled !== undefined ? { isEnabled: input.isEnabled } : {}),
  });
}

export async function deleteKnowledgeKb(
  transport: WebUIMutationTransport,
  id: string,
): Promise<{ ok: boolean; id: string }> {
  return mutation<{ ok: boolean; id: string }>(transport, "knowledge.kb.delete", { id });
}

export async function reindexKnowledgeKb(
  transport: WebUIMutationTransport,
  id: string,
): Promise<{ ok: boolean; queued: number; documentIds?: string[] }> {
  return mutation<{ ok: boolean; queued: number; documentIds?: string[] }>(
    transport,
    "knowledge.kb.reindex",
    { id },
  );
}

export interface AddKnowledgeDocInput {
  kbId: string;
  name: string;
  /** `data:<mime>;base64,...`；服务端按后缀 + MIME 双校验。 */
  dataUrl: string;
  title?: string;
  workspaceId?: string;
}

export async function addKnowledgeDoc(
  transport: WebUIMutationTransport,
  input: AddKnowledgeDocInput,
): Promise<{ document: KnowledgeDoc }> {
  return mutation<{ document: KnowledgeDoc }>(transport, "knowledge.doc.add", {
    kbId: input.kbId,
    name: input.name,
    dataUrl: input.dataUrl,
    ...(input.title ? { title: input.title } : {}),
    ...(input.workspaceId ? { workspaceId: input.workspaceId } : {}),
  });
}

export interface AddKnowledgeDocFromPathInput {
  kbId: string;
  /** 服务端本地路径（仅本地已认证 WebUI 可用）。 */
  path: string;
  title?: string;
  workspaceId?: string;
}

export async function addKnowledgeDocFromPath(
  transport: WebUIMutationTransport,
  input: AddKnowledgeDocFromPathInput,
): Promise<{ document: KnowledgeDoc }> {
  return mutation<{ document: KnowledgeDoc }>(transport, "knowledge.doc.add_file", {
    kbId: input.kbId,
    path: input.path,
    ...(input.title ? { title: input.title } : {}),
    ...(input.workspaceId ? { workspaceId: input.workspaceId } : {}),
  });
}

export async function deleteKnowledgeDoc(
  transport: WebUIMutationTransport,
  id: string,
): Promise<{ ok: boolean; id: string }> {
  return mutation<{ ok: boolean; id: string }>(transport, "knowledge.doc.delete", { id });
}

export async function reindexKnowledgeDoc(
  transport: WebUIMutationTransport,
  id: string,
): Promise<{ ok: boolean; queued: boolean }> {
  return mutation<{ ok: boolean; queued: boolean }>(transport, "knowledge.doc.reindex", { id });
}

export interface RepairKnowledgeInput {
  fts?: boolean;
  vectors?: boolean;
  purge?: boolean;
  workspaceId?: string;
}

export async function repairKnowledge(
  transport: WebUIMutationTransport,
  input: RepairKnowledgeInput = {},
): Promise<Record<string, unknown> & { ok: boolean }> {
  return mutation<Record<string, unknown> & { ok: boolean }>(transport, "knowledge.repair", {
    ...(input.fts !== undefined ? { fts: input.fts } : {}),
    ...(input.vectors !== undefined ? { vectors: input.vectors } : {}),
    ...(input.purge !== undefined ? { purge: input.purge } : {}),
    ...(input.workspaceId ? { workspaceId: input.workspaceId } : {}),
  });
}
