import { cn } from "@/lib/utils";

/** The rail wordmark: the product name as type, not a logo image.
 *  Keeps the brand readable on both light and dark shells without an asset. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span className={cn("brand-wordmark", className)}>
      知序
    </span>
  );
}
