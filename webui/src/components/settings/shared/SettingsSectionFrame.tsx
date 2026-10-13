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
    <section className={cn("mx-auto w-full max-w-[1040px] px-5 py-6 sm:px-8", className)}>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h2 className="text-[22px] font-semibold leading-tight tracking-tight text-foreground">
            {title}
          </h2>
          {description ? (
            <p className="mt-1 max-w-[70ch] text-[13px] leading-relaxed text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}
