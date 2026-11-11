import {useTranslation} from "react-i18next";
import {useRef} from "react";
import { KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription } from "../knowledge-shared/KnowledgeSurfaces";
import type {KnowledgeEvalCaseResult} from "@/lib/knowledge-eval-api";
export function KnowledgeEvalCaseSheet({result,query,onClose}:{result:KnowledgeEvalCaseResult|null;query?:string;onClose:()=>void}) {
 const {t}=useTranslation();
 const returnFocus=useRef<HTMLElement|null>(null);
 return <KnowledgeSheet open={!!result} onOpenChange={v=>{if(!v)onClose();}}><KnowledgeSheetContent onOpenAutoFocus={()=>{returnFocus.current=document.activeElement as HTMLElement;}} onCloseAutoFocus={e=>{e.preventDefault();returnFocus.current?.focus();}} className="p-6 sm:p-8">
 <KnowledgeSheetTitle className="break-words pr-6">{query??result?.caseId}</KnowledgeSheetTitle><KnowledgeSheetDescription>{result?.caseId} · {result?.labelSource} · {result?.status}</KnowledgeSheetDescription>
 {result?.error?<p role="alert" className="text-destructive">{result.error}</p>:null}
 <p className="text-sm text-muted-foreground">{t("knowledge.experience.hitEvidence",{defaultValue:"以下为本次报告已有的命中证据，片段标识不代表正文。"})}</p>
 {!result?.hits.length?<p>{t("knowledge.experience.noHits",{defaultValue:"没有命中证据"})}</p>:null}
 <ol className="space-y-4">{result?.hits.map(hit=><li key={hit.rank+":"+hit.chunkId} className="knowledge-panel space-y-3">
 <div className="flex flex-wrap justify-between gap-2 text-sm"><span>{t("knowledge.experience.rank",{defaultValue:"排名"})} {hit.rank}</span><span>{t("knowledge.experience.grade",{defaultValue:"相关性评分"})}: <strong>{hit.grade===null?t("knowledge.eval.result.notScored"):hit.grade}</strong></span></div>
 <code className="block break-all text-sm">{hit.chunkId}</code><p className="whitespace-pre-wrap break-words text-base">{hit.reason||"—"}</p></li>)}</ol>
 </KnowledgeSheetContent></KnowledgeSheet>;
}
