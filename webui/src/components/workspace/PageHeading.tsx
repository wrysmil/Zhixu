import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface PageHeadingProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

/** Shared page-level heading: 28–32px title, supporting copy, action row. */
export function PageHeading({ title, description, actions, className }: PageHeadingProps) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-end justify-between gap-x-6 gap-y-3 pb-1",
        className,
      )}
    >
      <div className="min-w-0">
        <h1 className="text-[28px] font-semibold leading-tight tracking-tight text-foreground">
          {title}
        </h1>
        {description ? (
          <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2 pb-0.5">{actions}</div> : null}
    </div>
  );
}
