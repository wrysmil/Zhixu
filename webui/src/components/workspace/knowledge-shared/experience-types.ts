import type { ReactNode } from "react";

export interface KnowledgePreviewProps {
  title: string;
  summary?: string;
  meta?: ReactNode;
  children: ReactNode;
}

export type KnowledgeTone = "neutral" | "pending" | "success" | "error";

export interface KnowledgeStatusProps {
  label: string;
  tone: KnowledgeTone;
  busy?: boolean;
}
