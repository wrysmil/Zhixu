import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  INLINE_TOKEN_HIGHLIGHT_COLOR,
  InlineTokenHighlight,
} from "@/components/InlineTokenHighlight";
import { AgentGlyph } from "@/components/ui/agent-glyph";
import { useLogoFallback } from "@/hooks/useLogoFallback";
import { logoFallbackUrls } from "@/lib/provider-brand";
import { sessionHandleColor } from "@/lib/session-handle";
import type { AgentProfile } from "@/lib/agents/types";
import type { CliAppInfo, McpPresetInfo, SessionMention } from "@/lib/types";
import { cn } from "@/lib/utils";

type CliAppMentionSegment =
  | { kind: "text"; text: string }
  | { kind: "cli"; text: string; app: CliAppInfo };

export type CapabilityMentionSegment =
  | CliAppMentionSegment
  | { kind: "mcp"; text: string; preset: McpPresetInfo }
  | { kind: "session"; text: string; mention: SessionMention }
  | { kind: "agent"; text: string; agent: AgentProfile };

export function cliAppInitials(app: CliAppInfo): string {
  const value = app.display_name || app.name;
  return (
    value
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || app.name.slice(0, 2).toUpperCase()
  );
}
export function mcpPresetInitials(preset: Pick<McpPresetInfo, "name" | "display_name">): string {
  const value = preset.display_name || preset.name;
  return (
    value
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || preset.name.slice(0, 2).toUpperCase()
  );
}
export function splitCapabilityMentionSegments(
  value: string,
  cliApps: CliAppInfo[],
  mcpPresets: McpPresetInfo[] = [],
  sessionMentions: SessionMention[] = [],
  agents: AgentProfile[] = [],
): CapabilityMentionSegment[] {
  if (
    !value
    || (cliApps.length === 0
      && mcpPresets.length === 0
      && sessionMentions.length === 0
      && agents.length === 0)
  ) {
    return value ? [{ kind: "text", text: value }] : [];
  }
  const agentsById = new Map(
    agents.map((agent) => [agent.id.toLowerCase(), agent]),
  );
  const cliAppsByName = new Map(
    cliApps
      .filter((app) => app.installed)
      .map((app) => [app.name.toLowerCase(), app]),
  );
  const mcpPresetsByName = new Map(
    mcpPresets
      .filter((preset) => preset.installed && preset.configured)
      .map((preset) => [preset.name.toLowerCase(), preset]),
  );
  const sessionsByName = new Map(
    sessionMentions.map((mention) => [mention.name.toLowerCase(), mention]),
  );
  if (
    agentsById.size === 0
    && cliAppsByName.size === 0
    && mcpPresetsByName.size === 0
    && sessionsByName.size === 0
  ) {
    return [{ kind: "text", text: value }];
  }

  const segments: CapabilityMentionSegment[] = [];
  const mentionRe = /(^|[\s([{])@([\p{L}\p{N}_-]+)(?=$|[^\p{L}\p{N}_-])/giu;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = mentionRe.exec(value)) !== null) {
    const prefix = match[1] ?? "";
    const name = match[2] ?? "";
    const key = name.toLowerCase();
    // Agent 的 @ 是「本轮接管」语义，优先于同名会话/CLI/MCP 展示。
    const agent = agentsById.get(key);
    const session = agent ? null : sessionsByName.get(key);
    const app = session || agent ? null : cliAppsByName.get(key);
    const preset = session || app || agent ? null : mcpPresetsByName.get(key);
    if (!app && !preset && !session && !agent) continue;

    const mentionStart = match.index + prefix.length;
    const mentionEnd = mentionStart + name.length + 1;
    if (mentionStart > cursor) {
      segments.push({ kind: "text", text: value.slice(cursor, mentionStart) });
    }
    if (agent) {
      segments.push({ kind: "agent", text: value.slice(mentionStart, mentionEnd), agent });
    } else if (app) {
      segments.push({ kind: "cli", text: value.slice(mentionStart, mentionEnd), app });
    } else if (preset) {
      segments.push({ kind: "mcp", text: value.slice(mentionStart, mentionEnd), preset });
    } else if (session) {
      segments.push({
        kind: "session",
        text: value.slice(mentionStart, mentionEnd),
        mention: session,
      });
    }
    cursor = mentionEnd;
  }
  if (cursor < value.length) {
    segments.push({ kind: "text", text: value.slice(cursor) });
  }
  return segments.length ? segments : [{ kind: "text", text: value }];
}

export function CapabilityMentionToken({
  segment,
  variant,
}: {
  segment: Exclude<CapabilityMentionSegment, { kind: "text" }>;
  variant: "composer" | "message";
}) {
  if (segment.kind === "agent") {
    return <AgentMentionToken agent={segment.agent} label={segment.text} variant={variant} />;
  }
  if (segment.kind === "cli") {
    return <CliAppMentionToken app={segment.app} label={segment.text} variant={variant} />;
  }
  if (segment.kind === "mcp") {
    return <McpPresetMentionToken preset={segment.preset} label={segment.text} variant={variant} />;
  }
  return <SessionMentionToken mention={segment.mention} label={segment.text} variant={variant} />;
}

const MENTION_GLYPH_SIZE = "0.95em";

const MENTION_CHIP_CLASSES = cn(
  "relative inline-flex items-center gap-[0.18em] rounded-full bg-foreground/[0.055] px-[0.45em] py-[0.02em]",
  "align-middle font-[550] leading-[1.45] transition-colors duration-150",
);

/**
 * 输入框里的胶囊底色：padding 与等值负 margin 抵消，行内净宽度保持为 0。
 * 透明 textarea 的原生光标按它自己的纯文本排版绘制，覆盖层多出的任何宽度
 * 都会把光标挤离可见文字。
 */
const MENTION_COMPOSER_CHIP_CLASSES = cn(
  "rounded-full bg-foreground/[0.055] px-[0.32em] py-[0.1em] -mx-[0.32em]",
  "font-normal transition-colors duration-150",
);

function mentionName(label: string): string {
  return label.startsWith("@") ? label.slice(1) : label;
}

/**
 * `@` 始终可见（弱化色），名字用身份色。
 * composer 变体不能引入任何行内宽度（透明 textarea 的原生光标按自己的排版绘制），
 * 所以身份图标只出现在消息里渲染的胶囊上。
 */
function MentionToken({
  testId,
  badgeTestId,
  title,
  color,
  name,
  glyph,
  variant,
}: {
  testId: string;
  badgeTestId?: string;
  title: string;
  color: string;
  name: string;
  glyph?: ReactNode;
  variant: "composer" | "message";
}) {
  if (variant === "message") {
    return (
      <span
        data-testid={testId}
        title={title}
        className={MENTION_CHIP_CLASSES}
        style={{ color }}
      >
        {glyph ? (
          <span data-testid={badgeTestId} className="inline-flex shrink-0 items-center">
            {glyph}
          </span>
        ) : null}
        <span className="text-muted-foreground/70">@</span>
        <span>{name}</span>
      </span>
    );
  }
  return (
    <InlineTokenHighlight
      testId={testId}
      title={title}
      color={color}
      className={MENTION_COMPOSER_CHIP_CLASSES}
    >
      <span className="text-muted-foreground/70">@</span>
      {name}
    </InlineTokenHighlight>
  );
}

function AgentMentionToken({
  agent,
  label,
  variant,
}: {
  agent: AgentProfile;
  label: string;
  variant: "composer" | "message";
}) {
  const testIdPrefix = variant === "composer" ? "composer" : "message";
  return (
    <MentionToken
      testId={`${testIdPrefix}-agent-mention-${agent.id}`}
      badgeTestId={`${testIdPrefix}-agent-mention-glyph-${agent.id}`}
      title={`Agent: ${agent.name}`}
      color={agent.color || INLINE_TOKEN_HIGHLIGHT_COLOR}
      name={mentionName(label)}
      variant={variant}
      glyph={<AgentGlyph icon={agent.icon} size={MENTION_GLYPH_SIZE} />}
    />
  );
}

function SessionMentionToken({
  mention,
  label,
  variant,
}: {
  mention: SessionMention;
  label: string;
  variant: "composer" | "message";
}) {
  const testIdPrefix = variant === "composer" ? "composer" : "message";
  const color = mention.id
    ? sessionHandleColor(mention.id)
    : INLINE_TOKEN_HIGHLIGHT_COLOR;
  const token = (
    <MentionToken
      testId={`${testIdPrefix}-session-mention-${mention.name}`}
      title={`Session: ${mention.title || mention.name}`}
      color={color}
      name={mentionName(label)}
      variant={variant}
    />
  );
  if (variant === "composer") return token;
  return (
    <a
      href={`#/chat/${encodeURIComponent(mention.session_key)}`}
      className="rounded-sm underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      style={{ textDecorationColor: color }}
    >
      {token}
    </a>
  );
}

function CliAppMentionToken({
  app,
  label,
  variant,
}: {
  app: CliAppInfo;
  label: string;
  variant: "composer" | "message";
}) {
  const { t } = useTranslation();
  const testIdPrefix = variant === "composer" ? "composer" : "message";
  const logoUrls = useMemo(() => logoFallbackUrls(app.logo_url), [app.logo_url]);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  return (
    <MentionToken
      testId={`${testIdPrefix}-cli-mention-${app.name}`}
      badgeTestId={`${testIdPrefix}-cli-mention-logo-${app.name}`}
      title={t("thread.composer.mentions.cliTitle", { name: app.display_name || app.name })}
      color={app.brand_color || INLINE_TOKEN_HIGHLIGHT_COLOR}
      name={mentionName(label)}
      variant={variant}
      glyph={logoUrl ? (
        <img
          src={logoUrl}
          alt=""
          style={{ width: MENTION_GLYPH_SIZE, height: MENTION_GLYPH_SIZE }}
          className="shrink-0 object-contain"
          decoding="async"
          loading="lazy"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      ) : null}
    />
  );
}

function McpPresetMentionToken({
  preset,
  label,
  variant,
}: {
  preset: McpPresetInfo;
  label: string;
  variant: "composer" | "message";
}) {
  const { t } = useTranslation();
  const testIdPrefix = variant === "composer" ? "composer" : "message";
  const logoUrls = useMemo(() => logoFallbackUrls(preset.logo_url), [preset.logo_url]);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  return (
    <MentionToken
      testId={`${testIdPrefix}-mcp-mention-${preset.name}`}
      badgeTestId={`${testIdPrefix}-mcp-mention-logo-${preset.name}`}
      title={t("thread.composer.mentions.mcpTitle", { name: preset.display_name || preset.name })}
      color={preset.brand_color || INLINE_TOKEN_HIGHLIGHT_COLOR}
      name={mentionName(label)}
      variant={variant}
      glyph={logoUrl ? (
        <img
          src={logoUrl}
          alt=""
          style={{ width: MENTION_GLYPH_SIZE, height: MENTION_GLYPH_SIZE }}
          className="shrink-0 object-contain"
          decoding="async"
          loading="lazy"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      ) : null}
    />
  );
}
