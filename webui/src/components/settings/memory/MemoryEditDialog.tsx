import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formControlFocusClassName } from "@/components/ui/form-control";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { createMemory, updateMemory } from "@/lib/api";
import type { MemoryPayload, MemoryPriority, MemoryType, MemoryWorkspace } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";
import { useMemoryCopy } from "./MemoryPresentation";

const MEMORY_TYPES: MemoryType[] = ["fact", "preference", "skill", "error", "rule", "experience"];
const MEMORY_PRIORITIES: MemoryPriority[] = ["short_term", "long_term"];

const FIELD_CLASS = cn(
  "border-border/45 bg-settings-surface transition-colors hover:border-border/70 focus-visible:bg-background",
);

interface MemoryEditDialogProps {
  memory: MemoryPayload | null;
  onClose: () => void;
  onSaved: () => void;
  workspaces?: MemoryWorkspace[];
  initialWorkspace?: string;
}

export function MemoryEditDialog({ memory, onClose, onSaved, workspaces = [], initialWorkspace }: MemoryEditDialogProps) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { client } = useClient();
  const { text } = useMemoryCopy();
  const [workspace, setWorkspace] = useState(initialWorkspace ?? "");

  const [content, setContent] = useState(memory?.content ?? "");
  const [type, setType] = useState<MemoryType>(memory?.type ?? "fact");
  const [priority, setPriority] = useState<MemoryPriority>(memory?.priority ?? "long_term");
  const [importance, setImportance] = useState(memory?.importance_score ?? 0.5);
  const [tags, setTags] = useState(memory?.tags.join(" ") ?? "");
  const [subject, setSubject] = useState(memory?.subject ?? "");
  const [predicate, setPredicate] = useState(memory?.predicate ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!content.trim()) {
      setError(tx("settings.memory.errorContentRequired", "Content is required"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const tagList = tags.split(/\s+/).filter(Boolean);
      const input = {
        content: content.trim(),
        type,
        priority,
        importanceScore: importance,
        tags: tagList,
        subject: subject.trim(),
        predicate: predicate.trim(),
      };
      if (memory) {
        await updateMemory(client, memory.id, input);
      } else {
        await createMemory(client, { ...input, ...(workspace ? { workspace } : {}) });
      }
      onSaved();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => (!open && !busy ? onClose() : undefined)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {memory ? tx("settings.memory.editTitle", "Edit memory") : tx("settings.memory.newTitle", "New memory")}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {memory
              ? tx("settings.memory.editDescription", "Edit this memory entry")
              : tx("settings.memory.newDescription", "Create a new memory entry")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          {!memory && <label className="grid gap-1.5 text-[12.5px] font-medium text-muted-foreground">{text("工作区", "Workspace")}<select value={workspace} onChange={(event) => setWorkspace(event.target.value)} className={cn(FIELD_CLASS, "h-9 rounded-control border border-input bg-background px-2 text-[13px] text-foreground", formControlFocusClassName)}><option value="">{text("默认工作区", "Default workspace")}</option>{workspaces.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}<option value="unassigned">{text("全局记忆", "Global memory")}</option></select></label>}
          <div className="grid gap-1.5">
            <span className="block text-[12.5px] font-medium text-muted-foreground">{tx("settings.memory.fieldContent", "Content")} *</span>
            <Textarea
              rows={4}
              value={content}
              onChange={(event) => setContent(event.target.value)}
              className={cn(FIELD_CLASS, "bg-background")}
              placeholder={tx("settings.memory.contentPlaceholder", "What should be remembered?")}
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="grid gap-1.5">
              <span className="block text-[12.5px] font-medium text-muted-foreground">{tx("settings.memory.fieldType", "Type")}</span>
              <select
                value={type}
                onChange={(event) => setType(event.target.value as MemoryType)}
                className={cn(FIELD_CLASS, "h-9 rounded-control border border-input bg-background px-2 text-[13px] text-foreground", formControlFocusClassName)}
              >
                {MEMORY_TYPES.map((value) => (
                  <option key={value} value={value}>
                    {tx(`settings.memory.type.${value}`, value)}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <span className="block text-[12.5px] font-medium text-muted-foreground">{tx("settings.memory.fieldPriority", "Priority")}</span>
              <select
                value={priority}
                onChange={(event) => setPriority(event.target.value as MemoryPriority)}
                className={cn(FIELD_CLASS, "h-9 rounded-control border border-input bg-background px-2 text-[13px] text-foreground", formControlFocusClassName)}
              >
                {MEMORY_PRIORITIES.map((value) => (
                  <option key={value} value={value}>
                    {tx(`settings.memory.priority.${value}`, value)}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <span className="block text-[12.5px] font-medium text-muted-foreground">{tx("settings.memory.fieldImportance", "Importance")}</span>
              <Input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={importance}
                onChange={(event) => setImportance(Number(event.target.value))}
                className={FIELD_CLASS}
              />
            </div>
          </div>

          <div className="grid gap-1.5">
            <span className="block text-[12.5px] font-medium text-muted-foreground">{tx("settings.memory.fieldTags", "Tags (space separated)")}</span>
            <Input value={tags} onChange={(event) => setTags(event.target.value)} className={FIELD_CLASS} />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <span className="block text-[12.5px] font-medium text-muted-foreground">{tx("settings.memory.fieldSubject", "Subject (optional)")}</span>
              <Input value={subject} onChange={(event) => setSubject(event.target.value)} className={FIELD_CLASS} />
            </div>
            <div className="grid gap-1.5">
              <span className="block text-[12.5px] font-medium text-muted-foreground">{tx("settings.memory.fieldPredicate", "Predicate (optional)")}</span>
              <Input value={predicate} onChange={(event) => setPredicate(event.target.value)} className={FIELD_CLASS} />
            </div>
          </div>

          {error && <div className="text-[13px] text-destructive">{error}</div>}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {tx("settings.memory.cancel", "Cancel")}
          </Button>
          <Button onClick={() => void save()} disabled={busy}>
            {busy ? tx("settings.memory.saving", "Saving...") : tx("settings.memory.save", "Save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
