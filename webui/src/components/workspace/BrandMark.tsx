import { cn } from "@/lib/utils";

/** Generated folded-loop mark, adapted to the shell's light and dark themes. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <img
      src="/brand/zhixu-logo.png"
      alt=""
      aria-hidden="true"
      width={40}
      height={40}
      draggable={false}
      decoding="async"
      className={cn(
        "shrink-0 object-contain [filter:sepia(0.55)_hue-rotate(66deg)_saturate(0.75)] dark:[filter:invert(1)_sepia(0.3)_hue-rotate(80deg)_brightness(1.15)]",
        className,
      )}
    />
  );
}
