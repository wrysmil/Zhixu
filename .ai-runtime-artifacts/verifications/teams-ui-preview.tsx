// 仅用于本地视觉验收：真实页面组件 + 内存样例，不连接网关，不读认证信息。
import React from "react";
import { createRoot } from "react-dom/client";
import "/src/globals.css";
import { initializeI18n, setAppLanguage } from "/src/i18n";
import { TeamsView } from "/src/components/settings/teams/TeamsView";
import { WorkspaceNavigation } from "/src/components/workspace/WorkspaceNavigation";
import { ClientProvider } from "/src/providers/ClientProvider";

const params = new URLSearchParams(location.search);
await initializeI18n();
await setAppLanguage(params.get("lang") === "en" ? "en" : "zh-CN");
if (params.has("dark")) document.documentElement.classList.add("dark");

const agent = (id: string, name: string, description: string, color: string) => ({
  id, name, description, color, icon: "", type: "custom", customized: false,
  categoryId: "general", prompt: "", modelId: null,
  tools: {mode:"all",entries:[]}, skills: {mode:"all",entries:[]},
  subAgents: {mode:"all",entries:[]}, hidden: false, updatedAt: "2026-10-16T01:00:00Z",
});
const agents = params.has("noagents") ? [] : [
  agent("coordinator", "项目队长", "拆解目标，安排任务，整理最后的交付", "#47775b"),
  agent("researcher", "资料研究员", "搜索资料、核实来源，提供可用的依据", "#ba8b51"),
  agent("builder", "开发工程师", "将需求实现为可运行的代码", "#668a9d"),
  agent("reviewer", "质量检查员", "检查结果，找出遗漏和需要修正的地方", "#859a72"),
  agent("writer", "内容编辑", "整理观点，写出清楚易读的内容", "#a37e68"),
];
let teams = params.has("empty") ? [] : [{
  id:"product-team", revision:2, name:"产品交付小队", description:"从一个想法，到可以交付的作品。",
  instructions:"先确认目标，再分工；交付前由质量检查员复核。", leaderAgentId:"coordinator",
  members:[{memberId:"m-researcher",agentId:"researcher",role:"调研资料，核实关键结论"},{memberId:"m-builder",agentId:"builder",role:"实现功能，处理工程细节"},{memberId:"m-reviewer",agentId:"reviewer",role:"检查结果并提出修改意见"}],
  limits:{maxConcurrency:3,maxTasks:16,maxDispatchRounds:4,taskTimeoutSeconds:600,runTimeoutSeconds:1800},
  archived:false, createdAt:"2026-10-01T00:00:00Z", updatedAt:"2026-10-16T00:00:00Z",
}];
const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.origin);
  if (url.pathname === "/api/settings/teams") return Response.json({teams, nextCursor:null});
  if (url.pathname === "/api/settings/agents") return Response.json({agents});
  return nativeFetch(input, init);
};
const client = {requestMutation: async (action: string, payload: Record<string, any>) => {
  if(action === "teams.save") {
    const now = new Date().toISOString();
    const previous = teams.find(t=>t.id===payload.team.id);
    if((previous?.revision ?? null) !== payload.expectedRevision) throw new Error("TEAM_REVISION_CONFLICT");
    const saved = {...payload.team, revision:(previous?.revision??0)+1, createdAt:previous?.createdAt??now, updatedAt:now};
    teams = [...teams.filter(t=>t.id!==saved.id),saved];
    return {team:saved};
  }
  if(action === "teams.archive") {
    const found=teams.find(t=>t.id===payload.id);
    if(!found) throw new Error("Fixture team not found");
    found.archived=true; found.revision+=1;
    return {id:found.id,archived:true,revision:found.revision};
  }
  if(action === "teams.clone") {
    const found=teams.find(t=>t.id===payload.id);
    if(!found) throw new Error("Fixture team not found");
    const now = new Date().toISOString();
    const cloned={...structuredClone(found),id:`team-${crypto.randomUUID().slice(0,8)}`,name:payload.name,revision:1,archived:false,createdAt:now,updatedAt:now};
    teams=[cloned,...teams];
    return {team:cloned};
  }
  throw new Error("Unsupported fixture action: "+action);
}};

function FixturePage() {
  if(params.has("mobile")) {
    const url=new URL(location.href); url.searchParams.delete("mobile");
    return <iframe title="390px 窄屏验收" src={url.href} style={{width:390,height:844,border:0,display:"block"}}/>;
  }
  return <ClientProvider client={client as any} token="fixture-only">
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <div className="w-rail shrink-0"><WorkspaceNavigation activeView="teams" collapsed={false} preview={true} onNavigate={()=>{}} onToggleCollapsed={()=>{}} /></div>
      <main className="min-w-0 flex-1 overflow-auto px-4 py-8 sm:px-8 lg:px-10">
        <div className="mx-auto max-w-[1120px]">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-2"><h1 className="text-[28px] font-medium tracking-tight">{params.get("lang")==="en"?"Teams":"团队"}</h1><span className="rounded-full border border-border px-2.5 py-1 text-[10px] text-muted-foreground">页面验收 · 样例数据</span></div>
          <TeamsView />
        </div>
      </main>
    </div>
  </ClientProvider>;
}
createRoot(document.getElementById("root")!).render(<FixturePage/>);
