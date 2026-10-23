// 会话级 Agent 选择的本地持久化。
//
// 选择本身是「这个会话由谁接管主回合」，属于服务端逐回合解析的语义（帧里的 agentId）；
// 这里只负责让 WebUI 刷新后仍记得用户上次的选择，不做跨设备/跨渠道同步。

export const SESSION_AGENT_STORAGE_KEY = "nanobot.webui.sessionAgent.v1";

/** chatId → 档案 id；空串是「还没建出 chat 的新会话屏」草稿槽，也作为新会话的兜底。 */
export type SessionAgentSelection = Record<string, string>;

export const SESSION_AGENT_DRAFT_SCOPE = "";

export function readSessionAgentSelection(): SessionAgentSelection {
  try {
    const raw = window.localStorage.getItem(SESSION_AGENT_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const selection: SessionAgentSelection = {};
    for (const [scope, agentId] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof agentId === "string" && agentId) selection[scope] = agentId;
    }
    return selection;
  } catch {
    // 脏数据或读到非 JSON 时按「没存过」处理，不能因此挡住聊天。
    return {};
  }
}

export function writeSessionAgentSelection(selection: SessionAgentSelection): void {
  try {
    window.localStorage.setItem(SESSION_AGENT_STORAGE_KEY, JSON.stringify(selection));
  } catch {
    // 隐私模式/配额写满时静默放弃：记不住选择不该影响本回合能不能发。
  }
}
