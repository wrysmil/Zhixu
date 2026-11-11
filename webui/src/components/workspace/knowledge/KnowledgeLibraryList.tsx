import { useTranslation } from "react-i18next";
import { MoreHorizontal, Library } from "lucide-react";
import type { KnowledgeKb } from "@/lib/api";
import { KnowledgePreview } from "../knowledge-shared/KnowledgePreview";
import { KnowledgeStatus } from "../knowledge-shared/KnowledgeStatus";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";

export function KnowledgeLibraryList({ kbs, busy, onOpen, onDelete }: {
  kbs: KnowledgeKb[]; busy: boolean;
  onOpen: (kb: KnowledgeKb, button: HTMLButtonElement) => void;
  onDelete: (kb: KnowledgeKb) => void;
}) {
  const { t } = useTranslation();
  return <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
    {kbs.map(kb => <li key={kb.id} className="knowledge-library-card relative flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <Library className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
        <KnowledgePreview title={kb.name} summary={kb.description}>
          <button type="button" aria-label={kb.name} data-kb-id={kb.id}
            className="min-w-0 flex-1 rounded text-left text-base font-semibold [overflow-wrap:anywhere] after:absolute after:inset-0 after:content-['']"
            onClick={event => onOpen(kb, event.currentTarget)}>{kb.name}</button>
        </KnowledgePreview>
        <DropdownMenu><DropdownMenuTrigger asChild>
          <button type="button" disabled={busy} aria-label={t("knowledge.experience.more", { defaultValue: "更多操作" })}
            className="relative z-10 rounded p-1 text-muted-foreground hover:bg-muted"><MoreHorizontal className="h-5 w-5" /></button>
        </DropdownMenuTrigger><DropdownMenuContent className="knowledge-experience-surface knowledge-motion">
          <DropdownMenuItem tone="destructive" onSelect={() => onDelete(kb)}>{t("knowledge.deleteKb")}</DropdownMenuItem>
        </DropdownMenuContent></DropdownMenu>
      </div>
      <p className="min-h-10 text-sm leading-6 text-muted-foreground line-clamp-2">{kb.description || t("knowledge.experience.noDescription", { defaultValue: "暂无描述" })}</p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-3 text-sm text-muted-foreground">
        <span>{kb.visibility === "global" ? t("knowledge.visibilityGlobal") : t("knowledge.visibilityWorkspace")}</span>
        <span>{t("knowledge.docCount", { count: kb.documentCount })}</span>
        <KnowledgeStatus label={kb.isEnabled ? t("knowledge.enabled") : t("knowledge.disabled")} tone="neutral" />
      </div>
    </li>)}
  </ul>;
}
