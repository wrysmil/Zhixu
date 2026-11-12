import {useState} from "react";
import {useTranslation} from "react-i18next";
import {Button} from "@/components/ui/button";
import type {KnowledgeEvalDataset} from "@/lib/knowledge-eval-api";
import {KnowledgePreview} from "../knowledge-shared/KnowledgePreview";
import {KnowledgeEvalDatasetSheet,type DatasetSheetProps} from "./KnowledgeEvalDatasetSheet";
interface Props extends Pick<DatasetSheetProps,"busy"|"onCreate"|"onUpdateCases"|"error"> {
 datasets:KnowledgeEvalDataset[];onDelete:(id:string)=>Promise<unknown>;onDownloadTemplate:()=>void;onImportTemplate:(file:File)=>void;
}
export function KnowledgeEvalDatasets({datasets,busy,onCreate,onDelete,onUpdateCases,onDownloadTemplate,onImportTemplate,error}:Props) {
 const {t}=useTranslation();const [selected,setSelected]=useState<KnowledgeEvalDataset|null|undefined>(undefined);
 const editing=selected?datasets.find(d=>d.id===selected.id)??selected:selected;
 return <section data-testid="knowledge-eval-datasets" className="mt-5">
 <div className="knowledge-toolbar justify-between">
 <h2 className="text-base font-semibold">{t("knowledge.eval.tab.datasets")}</h2><div className="flex flex-wrap gap-2">
 <Button variant="outline" onClick={onDownloadTemplate}>{t("knowledge.eval.manual.download")}</Button>
 <label className="relative inline-flex cursor-pointer items-center rounded-control border px-3 py-2 text-sm focus-within:ring-2 focus-within:ring-ring">{t("knowledge.eval.manual.import")}<input className="absolute inset-0 w-full cursor-pointer opacity-0" type="file" accept=".csv,text/csv" disabled={busy} aria-label={t("knowledge.eval.manual.import")} onChange={e=>{const f=e.target.files?.[0];e.target.value="";if(f)onImportTemplate(f);}}/></label>
 <Button onClick={()=>setSelected(null)}>{t("knowledge.eval.action.createDataset")}</Button></div></div>
 {!datasets.length?<p className="knowledge-panel mt-4">{t("knowledge.eval.empty.datasets")}</p>:null}
 <ul className="mt-4 grid gap-4 md:grid-cols-2">{datasets.map(dataset=><li key={dataset.id} className="knowledge-library-card flex items-start justify-between gap-4">
 <div className="min-w-0 space-y-2"><KnowledgePreview title={dataset.name} summary={dataset.notes||t("knowledge.eval.dataset.hint")}><button className="text-left text-base font-semibold" onClick={()=>setSelected(dataset)}>{dataset.name}</button></KnowledgePreview>
 <p className="text-sm text-muted-foreground">{t("knowledge.eval.dataset.caseCount",{count:dataset.cases.length})} · {dataset.updatedAt?.slice(0,10)??"—"}</p></div>
 <Button variant="ghost" disabled={busy} onClick={()=>void onDelete(dataset.id)}>{t("knowledge.eval.action.deleteDataset")}</Button></li>)}</ul>
 {editing!==undefined?<KnowledgeEvalDatasetSheet key={editing?.id??"new"} dataset={editing} error={error} onClose={()=>setSelected(undefined)} busy={busy} onCreate={onCreate} onUpdateCases={onUpdateCases}/>:null}
 </section>;
}
