import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ResizeHandle } from "@/components/workbench/ResizeHandle";
import { cn } from "@/lib/utils";

export function ResizableRail({ children, label, defaultWidth = 304, minWidth = 280,
  maxWidth = 860, className, onWidthChange,
}: {
  children: ReactNode;
  label: string;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  className?: string;
  onWidthChange?: (width: number) => void;
}) {
  const railRef = useRef<HTMLDivElement>(null);
  const [requestedWidth, setRequestedWidth] = useState(defaultWidth);
  const [availableWidth, setAvailableWidth] = useState(maxWidth);
  useEffect(() => {
    const parent = railRef.current?.parentElement;
    const measure = () => {
      const width = parent?.getBoundingClientRect().width || window.innerWidth;
      setAvailableWidth(width < 768 ? width : Math.max(minWidth, width - 360));
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (parent) observer?.observe(parent);
    window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, [minWidth]);
  const max = Math.max(0, Math.min(maxWidth, availableWidth));
  const min = Math.min(minWidth, max);
  const width = Math.min(max, Math.max(min, requestedWidth));
  useEffect(() => { onWidthChange?.(width); }, [onWidthChange, width]);
  return <div ref={railRef} data-testid="resizable-rail"
    className={cn("relative flex min-h-0 w-[var(--rail-width)] max-w-full shrink-0 flex-col", className)}
    style={{ "--rail-width": `${width}px` } as CSSProperties}>
    <ResizeHandle label={label} value={width} min={min} max={max}
      className="absolute inset-y-0 left-0"
      valueAtPointer={(x) => (railRef.current?.getBoundingClientRect().right ?? 0) - x}
      onChange={setRequestedWidth} />
    {children}
  </div>;
}
