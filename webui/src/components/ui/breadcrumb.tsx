import { Fragment, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

export interface BreadcrumbItem {
  label: ReactNode;
  /** Clicking a non-current crumb navigates back. */
  onClick?: () => void;
  current?: boolean;
  /** Overrides the accessible name, e.g. "Back to knowledge bases". */
  ariaLabel?: string;
}

export interface BreadcrumbProps {
  items: BreadcrumbItem[];
  ariaLabel?: string;
  className?: string;
  itemClassName?: string;
}

/**
 * Pill breadcrumb (StyleKit `sidebar-fixed-pill-breadcrumb`).
 *
 * Every path segment becomes a touch-friendly pill; the current page is the
 * only filled one, so the hierarchy stays readable at a glance without adding
 * a second navigation column.
 */
export function Breadcrumb({
  items,
  ariaLabel = "Breadcrumb",
  className,
  itemClassName,
}: BreadcrumbProps) {
  if (items.length === 0) return null;
  return (
    <nav
      aria-label={ariaLabel}
      className={cn("flex min-w-0 flex-wrap items-center gap-1.5", className)}
    >
      {items.map((item, index) => {
        const isCurrent = item.current ?? index === items.length - 1;
        return (
          <Fragment key={index}>
            {index > 0 ? (
              <ChevronRight
                className="h-3 w-3 shrink-0 text-muted-foreground"
                aria-hidden
              />
            ) : null}
            {isCurrent ? (
              <span
                aria-current="page"
                className={cn(
                  "max-w-[min(46vw,22rem)] truncate rounded-full bg-foreground px-2.5 py-1 text-[11px] font-medium text-background",
                  itemClassName,
                )}
              >
                {item.label}
              </span>
            ) : (
              <button
                type="button"
                onClick={item.onClick}
                disabled={!item.onClick}
                aria-label={item.ariaLabel}
                className={cn(
                  "max-w-[min(46vw,22rem)] truncate rounded-full bg-muted px-2.5 py-1 text-[11px] text-muted-foreground transition-colors duration-150",
                  "hover:bg-muted/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  !item.onClick && "cursor-default opacity-70",
                  itemClassName,
                )}
              >
                {item.label}
              </button>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}

/**
 * Title-linked breadcrumb (StyleKit `sidebar-fixed-title-breadcrumb`): a quiet
 * path line stacked directly above the page title.
 */
export function BreadcrumbTitle({
  items,
  eyebrow,
  title,
  description,
  actions,
  className,
}: {
  items: BreadcrumbItem[];
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <Breadcrumb items={items} />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {eyebrow ? (
            <div className="mb-1 text-[11px] font-medium uppercase tracking-[0.16em] text-primary/80">
              {eyebrow}
            </div>
          ) : null}
          <h1 className="truncate text-xl font-semibold tracking-tight text-foreground md:text-2xl">
            {title}
          </h1>
          {description ? (
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
