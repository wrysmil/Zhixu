import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Folder, FolderOpen } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, fetchWorkspaces } from "@/lib/api";
import { getRuntimeHost } from "@/lib/runtime";
import { cn } from "@/lib/utils";
import { normalizeWorkspacePath, projectNameFromPath } from "@/lib/workspace";
import { useClient } from "@/providers/ClientProvider";

const PICKER_TIMEOUT_MS = 300_000;

interface AddProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (projectPath: string, projectName: string) => void;
}

function errorStatus(reason: unknown): number {
  if (reason instanceof ApiError || (typeof reason === "object" && reason !== null && "status" in reason)) {
    const status = (reason as { status?: unknown }).status;
    if (typeof status === "number") return status;
  }
  return 0;
}

export function AddProjectDialog({ open, onOpenChange, onConfirm }: AddProjectDialogProps) {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [pickedPath, setPickedPath] = useState("");
  const [name, setName] = useState("");
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canPick, setCanPick] = useState(() => Boolean(getRuntimeHost().pickFolder));

  useEffect(() => {
    if (!open) {
      setPickedPath("");
      setName("");
      setPicking(false);
      setError(null);
      return;
    }
    let alive = true;
    fetchWorkspaces(getToken())
      .then((payload) => {
        if (alive && payload.controls?.can_pick_folder) setCanPick(true);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [open, getToken]);

  const chooseFolder = useCallback(async () => {
    setPicking(true);
    setError(null);
    try {
      const bridge = getRuntimeHost().pickFolder;
      const selected = bridge
        ? await bridge()
        : (await client.requestMutation<{ path?: unknown }>(
              "workspace.pick_folder",
              {},
              PICKER_TIMEOUT_MS,
            )).path;
      if (typeof selected === "string" && selected) {
        setPickedPath(selected);
        setName((current) => current || projectNameFromPath(selected));
      }
    } catch (reason) {
      const status = errorStatus(reason);
      if (status === 409) {
        setError(t("workspace.dialog.pickerBusy"));
      } else if (status === 403 || status === 503) {
        setCanPick(false);
        setError(t("workspace.dialog.unavailable"));
      } else {
        setError(t("workspace.dialog.pickFailed"));
      }
    } finally {
      setPicking(false);
    }
  }, [client, t]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    // Native pickers return a trailing slash; the sidebar keys projects without one.
    const path = normalizeWorkspacePath(pickedPath);
    if (!path || path === "/") return;
    onConfirm(path, name.trim() || projectNameFromPath(path));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-[17px]">{t("workspace.dialog.title")}</DialogTitle>
          <DialogDescription className="sr-only">
            {t("chat.groups.projectsHint")}
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-3.5" onSubmit={submit}>
          <div className="relative">
            <Folder
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/70"
              aria-hidden
            />
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={pickedPath ? projectNameFromPath(pickedPath) : t("workspace.dialog.projectName")}
              aria-label={t("workspace.dialog.projectName")}
              className="h-10 pl-9 text-[13.5px]"
            />
          </div>
          <div className="space-y-1.5">
            <p className="text-[12px] font-medium text-muted-foreground">
              {t("workspace.dialog.sourceFolder")}
            </p>
            <button
              type="button"
              onClick={() => { void chooseFolder(); }}
              disabled={!canPick || picking}
              aria-label={t("workspace.dialog.addFromThisComputer")}
              className={cn(
                "flex w-full flex-col items-center justify-center gap-2.5 rounded-xl border border-dashed px-4 py-6",
                "text-center transition-colors",
                canPick
                  ? "cursor-pointer hover:border-primary/40 hover:bg-accent/30"
                  : "cursor-not-allowed opacity-60",
              )}
            >
              {pickedPath ? (
                <>
                  <FolderOpen className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="max-w-full truncate text-[13px] font-medium text-foreground" title={pickedPath}>
                    {pickedPath}
                  </span>
                  <span className="text-[11.5px] text-muted-foreground">
                    {t("workspace.dialog.changeFolder")}
                  </span>
                </>
              ) : (
                <>
                  <span className="text-[12.5px] text-muted-foreground">
                    {canPick
                      ? t("workspace.dialog.addFromThisComputer")
                      : t("workspace.dialog.unavailable")}
                  </span>
                  {canPick ? (
                    <span
                      className={cn(
                        "inline-flex h-7 items-center gap-1.5 rounded-full border bg-background px-3",
                        "text-[12px] font-medium text-foreground",
                        picking && "opacity-60",
                      )}
                    >
                      <Folder className="h-3.5 w-3.5" aria-hidden />
                      {picking
                        ? t("workspace.dialog.picking")
                        : t("workspace.dialog.add")}
                    </span>
                  ) : null}
                </>
              )}
            </button>
            {error ? (
              <p className="text-[11.5px] text-destructive">{error}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onOpenChange(false)}
              className="h-8 px-2.5 text-[12px]"
            >
              {t("workspace.dialog.cancel")}
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={!pickedPath.trim()}
              className="h-8 px-2.5 text-[12px]"
            >
              {t("workspace.dialog.create")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
