import { cn } from "@/lib/utils";

/** A lightweight typographic mark that stays legible in both rail widths. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      aria-label="知序 Zhixu"
      className={cn(
        "inline-flex select-none items-center whitespace-nowrap text-[13px] leading-none",
        className,
      )}
      style={{
        fontFamily: '"Century Gothic", "Avenir Next", Futura, "Segoe UI Variable Display", sans-serif',
        fontWeight: 600,
        letterSpacing: "0.015em",
      }}
    >
      ZhiXu~
    </span>
  );
}
