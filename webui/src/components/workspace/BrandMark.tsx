import { cn } from "@/lib/utils";

/** Outlined wordmark stays consistent without requiring an installed font. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <img
      src={`${import.meta.env.BASE_URL}brand/zhixu-wordmark.svg`}
      alt="知序 Zhixu"
      width={100}
      height={50}
      draggable={false}
      className={cn("brand-image w-auto shrink-0 select-none object-contain", className)}
    />
  );
}
