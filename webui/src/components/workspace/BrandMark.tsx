import { cn } from "@/lib/utils";

/** Tanuki mark rendered as a transparent PNG so it sits cleanly on both the
 *  light and dark shells without any theme-tinted CSS filter. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <img
      src="/brand/zhixu_logo.png"
      alt=""
      aria-hidden="true"
      width={40}
      height={40}
      draggable={false}
      decoding="async"
      className={cn("shrink-0 object-contain", className)}
    />
  );
}
