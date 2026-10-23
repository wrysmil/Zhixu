import { useRef } from "react";
import { cn } from "@/lib/utils";

/** A right-anchored column boundary, shared by rails and the inline file tree. */
export function ResizeHandle({
  label, value, min, max, step = 32, valueAtPointer, onChange, onDraggingChange, className,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  valueAtPointer: (clientX: number) => number;
  onChange: (value: number) => void;
  onDraggingChange?: (dragging: boolean) => void;
  className?: string;
}) {
  const pointerRef = useRef<number | null>(null);
  const clamp = (next: number) => Math.round(Math.min(max, Math.max(min, next)));
  return <button
    type="button"
    role="separator"
    aria-orientation="vertical"
    aria-label={label}
    aria-valuemin={min}
    aria-valuemax={max}
    aria-valuenow={Math.round(value)}
    aria-keyshortcuts="ArrowLeft ArrowRight Home End"
    className={cn("group host-no-drag z-40 flex w-3 shrink-0 touch-none cursor-col-resize items-center justify-center bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}
    onPointerDown={(event) => {
      if (event.button !== 0 || pointerRef.current !== null) return;
      event.preventDefault();
      event.stopPropagation();
      pointerRef.current = event.pointerId;
      onDraggingChange?.(true);
      // Keep receiving moves after crossing an iframe or leaving this narrow handle.
      // https://developer.mozilla.org/en-US/docs/Web/API/Element/setPointerCapture
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={(event) => {
      if (pointerRef.current !== event.pointerId) return;
      event.preventDefault();
      onChange(clamp(valueAtPointer(event.clientX)));
    }}
    onPointerUp={(event) => {
      if (pointerRef.current !== event.pointerId) return;
      pointerRef.current = null;
      onDraggingChange?.(false);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    }}
    onPointerCancel={() => { pointerRef.current = null; onDraggingChange?.(false); }}
    onLostPointerCapture={() => { pointerRef.current = null; onDraggingChange?.(false); }}
    onKeyDown={(event) => {
      const next = event.key === "ArrowLeft" ? value + step
        : event.key === "ArrowRight" ? value - step
          : event.key === "Home" ? min : event.key === "End" ? max : null;
      if (next === null) return;
      event.preventDefault();
      event.stopPropagation();
      onChange(clamp(next));
    }}
  ><span aria-hidden className="h-full min-h-8 w-px bg-border/70 group-hover:bg-primary/60 group-focus-visible:bg-primary" /></button>;
}
