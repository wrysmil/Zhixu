import type { CSSProperties } from "react";

import {
  DEFAULT_AGENT_ICON,
  agentIconLucideComponent,
} from "@/lib/agents/icons";
import { cn } from "@/lib/utils";

/**
 * Agent 图标：支持 ``lucide:<name>``、emoji、本地/远程图片路径，空值回退内置图标。
 * 尺寸由调用方用 className 或 size 指定；图标只做身份展示。
 */
export function AgentGlyph({
  icon,
  size = 16,
  className,
  style,
}: {
  icon?: string | null;
  size?: number | string;
  className?: string;
  style?: CSSProperties;
}) {
  const value = (icon ?? "").trim();
  const Lucide = agentIconLucideComponent(value);
  if (Lucide) {
    return (
      <Lucide aria-hidden className={cn("shrink-0", className)} size={size} style={style} />
    );
  }
  if (value.startsWith("/") || value.startsWith("http") || value.startsWith("data:")) {
    return (
      <img
        src={value}
        alt=""
        className={cn("shrink-0 object-contain", className)}
        style={{ width: size, height: size, ...style }}
      />
    );
  }
  if (value) {
    return (
      <span aria-hidden className={cn("leading-none", className)} style={{ fontSize: size, ...style }}>
        {value}
      </span>
    );
  }
  return (
    <img
      src={DEFAULT_AGENT_ICON}
      alt=""
      className={cn("shrink-0", className)}
      style={{ width: size, height: size, ...style }}
    />
  );
}
