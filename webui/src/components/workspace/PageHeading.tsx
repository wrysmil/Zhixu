import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface PageHeadingIllustration {
  src: string;
  /** Home keeps the largest mascot; capability pages stay compact. */
  size?: "compact" | "default" | "home";
}

interface PageHeadingProps {
  kicker?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  illustration?: PageHeadingIllustration;
  className?: string;
}

/**
 * Shared page header for the assistant workbench pages: kicker, sans heading,
 * supporting copy, an optional action row and a small decorative illustration.
 * The illustration is decorative only — it never carries page copy.
 */
export function PageHeading({
  kicker,
  title,
  description,
  actions,
  illustration,
  className,
}: PageHeadingProps) {
  return (
    <header className={cn("assistant-page-header", className)}>
      <div className="assistant-page-heading-group">
        {kicker ? <p className="assistant-page-kicker">{kicker}</p> : null}
        <h1 className="assistant-page-heading">{title}</h1>
        {description ? <p className="assistant-page-description">{description}</p> : null}
        {actions ? <div className="assistant-page-actions">{actions}</div> : null}
      </div>
      {illustration ? (
        <div
          className="assistant-page-illustration"
          data-size={illustration.size ?? "default"}
          aria-hidden
        >
          <img src={illustration.src} alt="" width={176} height={150} draggable={false} />
        </div>
      ) : null}
    </header>
  );
}
