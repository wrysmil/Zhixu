import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface SettingsSectionFrameProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

/**
 * Shared section shell for settings and capability pages: title, grouped
 * description, aligned body width, and an explicit action row. Both the live
 * SettingsPage and PreviewSettings render through it; state (loading/error/
 * dirty/saving) stays in the caller-provided children/actions.
 */
export function SettingsSectionFrame({
  title,
  description,
  actions,
  children,
  className,
}: SettingsSectionFrameProps) {
  return (
    <section className={cn("assistant-page", className)}>
      <header className="assistant-page-header">
        <div className="assistant-page-heading-group">
          <h2 className="assistant-page-heading">
            {title}
          </h2>
          {description ? (
            <p className="assistant-page-description">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="assistant-page-actions">{actions}</div> : null}
      </header>
      <div className="mt-5">{children}</div>
    </section>
  );
}
