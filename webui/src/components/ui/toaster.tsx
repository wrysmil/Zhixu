import { AlertTriangle, Check } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { floatingSurfaceElevationClassName } from "@/components/ui/floating-surface";
import { isToastPayload, TOAST_EVENT, type ToastPayload } from "@/lib/toast-events";
import { cn } from "@/lib/utils";

const MAX_VISIBLE_TOASTS = 3;

/**
 * Global transient feedback channel. Mounted once at the app root; anything that
 * can reach `window` publishes through `notify()` in `@/lib/toast-events`.
 *
 * The live region stays mounted even while empty so assistive tech observes the
 * insertion instead of the region itself appearing (MDN: live regions must be
 * present before content changes to be announced reliably). It is `polite` and
 * non-atomic, and never takes focus.
 * Source: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Guides/Live_regions
 *
 * Portals to `document.body` so it stacks with Radix portals (dialogs portal
 * there too) instead of inheriting the app root's stacking context. The top
 * offset is large enough that the pill overlaps a centered modal's upper edge
 * rather than floating in the backdrop gap above it.
 */
export function Toaster() {
  const [toasts, setToasts] = useState<ToastPayload[]>([]);

  useEffect(() => {
    const timers = new Map<string, ReturnType<typeof setTimeout>>();
    const onToast = (event: Event) => {
      if (!(event instanceof CustomEvent) || !isToastPayload(event.detail)) return;
      const payload = event.detail;
      setToasts((prev) => [...prev, payload].slice(-MAX_VISIBLE_TOASTS));
      timers.set(
        payload.id,
        setTimeout(() => {
          timers.delete(payload.id);
          setToasts((prev) => prev.filter((item) => item.id !== payload.id));
        }, payload.durationMs),
      );
    };
    window.addEventListener(TOAST_EVENT, onToast);
    return () => {
      window.removeEventListener(TOAST_EVENT, onToast);
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  return createPortal(
    <div
      aria-live="polite"
      aria-relevant="additions text"
      className="pointer-events-none fixed left-1/2 top-[calc(2.5rem+env(safe-area-inset-top))] z-[70] flex w-[min(32rem,calc(100vw-1rem))] -translate-x-1/2 flex-col items-center gap-2"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          data-toast-tone={toast.tone}
          className={cn(
            floatingSurfaceElevationClassName,
            "inline-flex max-w-full animate-in items-center gap-2 rounded-full px-4 py-2 text-[13px] font-medium",
            "fade-in-0 slide-in-from-top-2 duration-200 ease-out",
            toast.tone === "error" ? "text-destructive" : "text-foreground",
          )}
        >
          {toast.tone === "error" ? (
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" strokeWidth={2.2} aria-hidden />
          ) : (
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary">
              <Check className="h-3 w-3" strokeWidth={2.5} aria-hidden />
            </span>
          )}
          <span className="min-w-0 truncate">{toast.message}</span>
        </div>
      ))}
    </div>,
    document.body,
  );
}
