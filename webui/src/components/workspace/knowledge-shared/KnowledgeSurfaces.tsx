import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { modalSurfaceClassName } from "@/components/ui/floating-surface";
import { cn } from "@/lib/utils";
import "./experience.css";

export const KnowledgeSheet = DialogPrimitive.Root;
export const KnowledgeDialog = DialogPrimitive.Root;

export interface KnowledgeDialogContentProps
  extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  showCloseButton?: boolean;
}

export interface KnowledgeSheetContentProps extends KnowledgeDialogContentProps {
  side?: "top" | "right" | "bottom" | "left" | null;
  closeButtonClassName?: string;
}

const KnowledgeSurface = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  KnowledgeSheetContentProps & { kind: "sheet" | "dialog" }
>(({ kind, side = "right", className, children, showCloseButton = true, closeButtonClassName, ...props }, ref) => {
  const { t } = useTranslation();
  return (
    // Both animated nodes must be direct Portal children so its Presence can
    // observe their DOM animation, rather than unmounting an unanimated wrapper.
    // https://www.radix-ui.com/primitives/docs/guides/animation
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="knowledge-experience-surface knowledge-overlay knowledge-motion fixed inset-0 z-50 bg-black/40" />
      <DialogPrimitive.Content
        {...props}
        ref={ref}
        data-knowledge-surface=""
        data-side={kind === "sheet" ? side ?? "right" : undefined}
        className={cn(
          modalSurfaceClassName,
          "fixed z-50 gap-4 p-6",
          kind === "sheet" ? "flex flex-col" : "grid w-full max-w-lg rounded-modal",
          "knowledge-experience-surface knowledge-motion",
          kind === "sheet" ? "knowledge-sheet" : "knowledge-dialog",
          className,
        )}
      >
        {children}
        {showCloseButton ? (
          <DialogPrimitive.Close
            className={cn(
              "absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none",
              closeButtonClassName,
            )}
          >
            <X className="h-4 w-4" aria-hidden="true" />
            <span className="sr-only">{t("common.close")}</span>
          </DialogPrimitive.Close>
        ) : null}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
});
KnowledgeSurface.displayName = "KnowledgeSurface";

export const KnowledgeSheetContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>, KnowledgeSheetContentProps
>((props, ref) => <KnowledgeSurface {...props} kind="sheet" ref={ref} />);
KnowledgeSheetContent.displayName = "KnowledgeSheetContent";

export const KnowledgeDialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>, KnowledgeDialogContentProps
>((props, ref) => <KnowledgeSurface {...props} kind="dialog" ref={ref} />);
KnowledgeDialogContent.displayName = "KnowledgeDialogContent";

export const KnowledgeSheetTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title {...props} ref={ref} className={cn("text-lg font-semibold text-foreground", className)} />
));
KnowledgeSheetTitle.displayName = "KnowledgeSheetTitle";
export const KnowledgeDialogTitle = KnowledgeSheetTitle;

export const KnowledgeSheetDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description {...props} ref={ref} className={cn("text-sm text-muted-foreground", className)} />
));
KnowledgeSheetDescription.displayName = "KnowledgeSheetDescription";
export const KnowledgeDialogDescription = KnowledgeSheetDescription;
