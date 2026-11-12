import { useTranslation } from "react-i18next";
import { FileText, ListTree, RefreshCcw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { KnowledgeDoc } from "@/lib/api";
import { KnowledgeStatus } from "../knowledge-shared/KnowledgeStatus";

const isDocActive = (doc: KnowledgeDoc) => doc.status === "pending" || doc.status === "processing";
export function KnowledgeDocumentTable({ docs, busy, onOpenChunkPreview, onReindex, onDelete }: {
 docs: KnowledgeDoc[]; busy: boolean; onOpenChunkPreview: (doc: KnowledgeDoc) => void;
 onReindex: (doc: KnowledgeDoc) => void; onDelete: (doc: KnowledgeDoc) => void;
}) {
 const { t } = useTranslation();
 const statusLabel = (doc: KnowledgeDoc) => {
   const key: Record<string, string> = { pending: "statusPending", processing: "statusProcessing", ready: "statusReady", failed: "statusFailed" };
   return key[doc.status] ? t(`knowledge.${key[doc.status]}`) : doc.status;
 };
 return (<div className="knowledge-table-scroll">
                    <table className="knowledge-table min-w-[760px]">
                      <thead>
                        <tr className="border-b border-border/60 text-[11px] text-muted-foreground">
                          <th className="py-2 pr-3 font-medium">{t("knowledge.columnTitle")}</th>
                          <th className="py-2 pr-3 font-medium">{t("knowledge.columnSource")}</th>
                          <th className="py-2 pr-3 font-medium">{t("knowledge.columnStatus")}</th>
                          <th className="py-2 pr-3 text-right font-medium">{t("knowledge.columnSize")}</th>
                          <th className="py-2 pr-3 text-right font-medium">{t("knowledge.columnChunks")}</th>
                          <th>{t("knowledge.experience.updated", { defaultValue: "更新日期" })}</th>
                          <th className="py-2 text-right font-medium">{t("knowledge.columnActions")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {docs.map((doc) => (
                          <tr
                            key={doc.id}
                            className="border-t border-border/40 align-top transition-colors hover:bg-muted/30"
                          >
                            <td className="py-3 pr-3">
                              <span className="flex min-w-0 items-start gap-1.5">
                                <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                                <span className="min-w-0">
                                  <button type="button" onClick={() => onOpenChunkPreview(doc)} className="block max-w-xs rounded text-left text-sm font-medium text-foreground [overflow-wrap:anywhere] hover:underline">
                                    {doc.title}
                                  </button>
                                  {doc.error ? (
                                    <span className="mt-1 block max-w-xs break-words text-sm text-destructive">
                                      {t("knowledge.errorLabel", { message: doc.error })}
                                    </span>
                                  ) : null}
                                </span>
                              </span>
                            </td>
                            <td className="py-3 pr-3">
                              <span className="block max-w-[180px] truncate font-mono text-[10.5px] text-muted-foreground">
                                {doc.originalName || doc.mime || "—"}
                              </span>
                            </td>
                            <td className="py-3 pr-3">
                              <KnowledgeStatus label={statusLabel(doc)} tone={doc.status === "failed" ? "error" : "neutral"} busy={isDocActive(doc)} />
                            </td>
                            <td className="py-3 pr-3 text-right tabular-nums text-muted-foreground">
                              {t("knowledge.sizeValue", { count: doc.charCount })}
                            </td>
                            <td className="py-3 pr-3 text-right tabular-nums text-muted-foreground">
                              {doc.chunkCount}
                            </td>
                            <td>{doc.updatedAt?.slice(0, 10) || "—"}</td>
                            <td className="py-3">
                              <div className="flex items-center justify-end gap-1">
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  disabled={busy || isDocActive(doc) || doc.chunkCount <= 0}
                                  onClick={() => void onOpenChunkPreview(doc)}
                                  className="h-7 rounded-full px-2.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                                >
                                  <ListTree className="h-3 w-3" aria-hidden />
                                  {t("knowledge.viewChunks")}
                                </Button>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  aria-label={t("knowledge.reindex")}
                                  title={t("knowledge.reindex")}
                                  disabled={busy || isDocActive(doc)}
                                  onClick={() => onReindex(doc)}
                                  className="h-7 w-7 rounded-full p-0 text-muted-foreground hover:bg-muted hover:text-foreground"
                                >
                                  <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
                                </Button>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  aria-label={t("knowledge.deleteDoc")}
                                  title={t("knowledge.deleteDoc")}
                                  disabled={busy}
                                  onClick={() => onDelete(doc)}
                                  className="h-7 w-7 rounded-full p-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                >
                                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                                </Button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>);
}
