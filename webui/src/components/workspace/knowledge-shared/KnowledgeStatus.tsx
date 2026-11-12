import { LoaderCircle } from "lucide-react";
import type { KnowledgeStatusProps } from "./experience-types";
import "./experience.css";

export type { KnowledgeStatusProps, KnowledgeTone } from "./experience-types";

export function KnowledgeStatus({ label, tone, busy = false }: KnowledgeStatusProps) {
  return (
    <span
      role="status"
      aria-label={label}
      aria-live="polite"
      aria-atomic="true"
      aria-busy={busy}
      data-tone={tone}
      className="knowledge-status"
    >
      {busy ? (
        <LoaderCircle
          aria-hidden="true"
          focusable="false"
          className="knowledge-status-spinner knowledge-motion"
        />
      ) : null}
      <span>{label}</span>
    </span>
  );
}
