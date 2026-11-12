import { Fragment, isValidElement } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { KnowledgePreviewProps } from "./experience-types";
import "./experience.css";

export type { KnowledgePreviewProps } from "./experience-types";

// Supply a focusable, ref-forwarding trigger; summary/meta are supplementary, non-interactive content.
export function KnowledgePreview({ title, summary, meta, children }: KnowledgePreviewProps) {
  const trigger = isValidElement(children) && children.type !== Fragment
    ? children
    : <span tabIndex={0} className="knowledge-preview-trigger">{children}</span>;

  // Radix handles focus, Escape, activation and the pointer corridor into hoverable content.
  // https://www.radix-ui.com/primitives/docs/components/tooltip
  return (
    <TooltipProvider delayDuration={300} skipDelayDuration={100} disableHoverableContent={false}>
      <Tooltip disableHoverableContent={false}>
        <TooltipTrigger asChild>{trigger}</TooltipTrigger>
        <TooltipContent
          sideOffset={6}
          className="knowledge-experience-surface knowledge-preview knowledge-motion"
        >
          <div className="knowledge-preview-title">{title}</div>
          {summary ? <div className="knowledge-preview-summary">{summary}</div> : null}
          {meta != null ? <div className="knowledge-preview-meta">{meta}</div> : null}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
