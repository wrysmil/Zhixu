import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export function useWorkbenchText() {
  const { i18n } = useTranslation();
  return (zh: string, en: string) => i18n.language.startsWith("zh") ? zh : en;
}
export function WorkbenchSection({ title, count, defaultOpen = false, children }: {
  title: string; count?: number; defaultOpen?: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return <section className="border-b border-border/45 last:border-b-0">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
      className="flex w-full items-center gap-1.5 px-4 py-3 text-left text-xs font-medium hover:bg-muted/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
      <ChevronRight aria-hidden className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground", open && "rotate-90")} />
      <span className="min-w-0 flex-1 truncate">{title}</span>
      {count !== undefined && <span className="text-[11px] tabular-nums text-muted-foreground">{count}</span>}
    </button>
    {open && <div className="px-4 pb-3 text-xs">{children}</div>}
  </section>;
}
export function WorkbenchEmpty({ children }: { children: ReactNode }) {
  return <p className="text-xs leading-5 text-muted-foreground">{children}</p>;
}
export function WorkbenchBadge({ children }: { children: ReactNode }) {
  return <span className="inline-flex shrink-0 rounded border border-border/60 bg-muted/40 px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">{children}</span>;
}
