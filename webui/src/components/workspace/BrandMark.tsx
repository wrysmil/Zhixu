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
        "shrink-0 object-contain dark:[filter:invert(1)_hue-rotate(180deg)_brightness(1.15)]",
        className,
      )}
    />
  );
}
