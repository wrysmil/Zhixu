import {useRef,useState} from "react";
import {useTranslation} from "react-i18next";
import { KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription } from "../knowledge-shared/KnowledgeSurfaces";
import {AlertDialog,AlertDialogContent,AlertDialogTitle,AlertDialogDescription,AlertDialogCancel,AlertDialogAction} from "@/components/ui/alert-dialog";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Textarea} from "@/components/ui/textarea";
import type {KnowledgeEvalDataset} from "@/lib/knowledge-eval-api";
export interface DatasetSheetProps {
 error?:string|null;
 dataset: KnowledgeEvalDataset | null; onClose:()=>void; busy:boolean;
 onCreate:(input:{name:string;notes?:string;cases?:Array<Record<string,unknown>>})=>Promise<boolean>;
 onUpdateCases:(dataset:KnowledgeEvalDataset,caseId:string,expectAny:string[])=>Promise<boolean>;
}
export function KnowledgeEvalDatasetSheet({dataset,onClose,busy,onCreate,onUpdateCases,error:actionError}:DatasetSheetProps) {
 const {t}=useTranslation();
 const [name,setName]=useState(""); const [notes,setNotes]=useState("");
 const [drafts,setDrafts]=useState([{query:"",expectAny:""}]);
 const [answers,setAnswers]=useState<Record<string,string>>(()=>Object.fromEntries(dataset?.cases.map(c=>[c.id,c.expectAny.join(", ")])??[]));
 const [saved,setSaved]=useState(answers);
 const [discard,setDiscard]=useState(false);
 const [error,setError]=useState("");
 const lock=useRef(false);
 const returnFocus=useRef<HTMLElement|null>(null);
 const dirty=dataset?Object.keys(answers).some(id=>answers[id]!==saved[id]):!!(name||notes||drafts.some(d=>d.query||d.expectAny));
 const close=()=>{if(busy)return; if(dirty)setDiscard(true);else onClose();};
 const split=(s:string)=>s.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
 const save=async(id?:string)=>{
   if(lock.current)return;
   lock.current=true;setError("");
   try {
    const ok=dataset&&id?await onUpdateCases(dataset,id,split(answers[id])):await onCreate({name:name.trim(),notes:notes.trim(),cases:drafts.filter(d=>d.query.trim()).map(d=>({query:d.query.trim(),expect_any:split(d.expectAny)}))});
    if(ok){if(id)setSaved(s=>({...s,[id]:answers[id]}));else onClose();}
    else setError(t("knowledge.experience.draftSaveError",{defaultValue:"保存失败，草稿已保留。版本冲突时请先复制草稿，关闭侧栏并刷新题集，核对后再修改。"}));
   } finally {lock.current=false;}
 };
 return <><KnowledgeSheet open onOpenChange={v=>{if(!v)close();}}>
 <KnowledgeSheetContent onOpenAutoFocus={()=>{returnFocus.current=document.activeElement as HTMLElement;}} onCloseAutoFocus={e=>{e.preventDefault();returnFocus.current?.focus();}} className="p-6 sm:p-8">
 <KnowledgeSheetTitle>{dataset?.name??t("knowledge.eval.action.createDataset")}</KnowledgeSheetTitle>
 <KnowledgeSheetDescription>{dataset?t("knowledge.eval.dataset.caseCount",{count:dataset.cases.length}):t("knowledge.eval.dataset.hint")}</KnowledgeSheetDescription>
 {error||actionError?<p role="alert" className="rounded-control border border-destructive/30 p-3 text-sm text-destructive">{actionError} {error}</p>:null}
 {dataset?<ul className="space-y-5">{dataset.cases.map(item=><li key={item.id} className="knowledge-panel space-y-3">
 <p className="text-base font-medium">{item.query}</p><p className="text-sm text-muted-foreground">{item.mode} · K={item.limit} · {item.labelSource}</p>
 <label className="block space-y-2 text-sm"><span>{t("knowledge.eval.dataset.expect")}</span><Input aria-label={t("knowledge.eval.dataset.expect")} value={answers[item.id]} onChange={e=>setAnswers(a=>({...a,[item.id]:e.target.value}))}/></label>
 <Button variant="outline" disabled={busy||answers[item.id]===saved[item.id]} onClick={()=>void save(item.id)}>{t("knowledge.eval.action.save")}</Button>
 </li>)}</ul>:<form className="space-y-5" onSubmit={e=>{e.preventDefault();void save();}}>
 <label className="block space-y-2 text-sm"><span>{t("knowledge.eval.dataset.name")}</span><Input value={name} onChange={e=>setName(e.target.value)} required/></label>
 <label className="block space-y-2 text-sm"><span>{t("knowledge.eval.dataset.notes")}</span><Textarea value={notes} onChange={e=>setNotes(e.target.value)}/></label>
 {drafts.map((draft,index)=><div key={index} className="knowledge-panel grid gap-3">
 <label className="space-y-2 text-sm"><span>{t("knowledge.eval.dataset.query")}</span><Input value={draft.query} onChange={e=>setDrafts(ds=>ds.map((d,i)=>i===index?{...d,query:e.target.value}:d))}/></label>
 <label className="space-y-2 text-sm"><span>{t("knowledge.eval.dataset.expect")}</span><Input value={draft.expectAny} onChange={e=>setDrafts(ds=>ds.map((d,i)=>i===index?{...d,expectAny:e.target.value}:d))}/></label>
 </div>)}
 <div className="flex flex-wrap gap-3"><Button type="button" variant="outline" onClick={()=>setDrafts(ds=>[...ds,{query:"",expectAny:""}])}>{t("knowledge.eval.action.addCase")}</Button><Button disabled={busy||!name.trim()||!drafts.some(d=>d.query.trim())}>{t("knowledge.eval.action.createDataset")}</Button></div>
 </form>}
 </KnowledgeSheetContent></KnowledgeSheet>
 <AlertDialog open={discard} onOpenChange={setDiscard}><AlertDialogContent className="knowledge-experience-surface knowledge-dialog knowledge-motion">
 <AlertDialogTitle>{t("knowledge.experience.discardTitle",{defaultValue:"放弃未保存的修改？"})}</AlertDialogTitle>
 <AlertDialogDescription>{t("knowledge.experience.discardDescription",{defaultValue:"关闭后，本次未保存的草稿会丢失。"})}</AlertDialogDescription>
 <AlertDialogCancel>{t("knowledge.experience.keepEditing",{defaultValue:"继续编辑"})}</AlertDialogCancel><AlertDialogAction onClick={onClose}>{t("knowledge.experience.discard",{defaultValue:"放弃修改"})}</AlertDialogAction>
 </AlertDialogContent></AlertDialog></>;
}
