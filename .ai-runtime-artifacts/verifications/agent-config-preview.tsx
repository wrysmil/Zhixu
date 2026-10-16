// 真实组件与内存样例，用于视觉验收；不连接网关或读取认证信息。
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "/src/globals.css";
import { initializeI18n, setAppLanguage } from "/src/i18n";
import { AgentEditorDialog } from "/src/components/settings/agents/AgentEditorDialog";
import { createBlankAgent } from "/src/lib/agents/catalog";
const params = new URLSearchParams(location.search);
await initializeI18n();
await setAppLanguage(params.has("en") ? "en" : "zh-CN");
if(params.has("dark")) document.documentElement.classList.add("dark");
const catalog = {
  categories: [{id:"research",name:"研究",color:"#47765b",order:0}],
  tools: [
    {name:"read_file",label:"读取文件",description:"阅读工作区里的文件内容",category:"filesystem",risk:"low",scope:"subagent"},
    {name:"list_dir",label:"浏览目录",description:"查看文件夹与文件列表",category:"filesystem",risk:"low",scope:"subagent"},
    {name:"web_search",label:"搜索网页",description:"寻找资料并核对信息来源",category:"web",risk:"low",scope:"subagent"},
    {name:"web_fetch",label:"读取网页",description:"提取网页中的正文内容",category:"web",risk:"low",scope:"subagent"},
    {name:"exec",label:"运行命令",description:"执行本地命令",category:"execution",risk:"high",scope:"core",blocked:true,blockedReason:"仅主智能体可用"},
  ],
  skills:[
    {name:"资料调研",description:"查阅可靠来源，整理事实与依据",source:"workspace",tags:["研究"]},
    {name:"技术写作",description:"把复杂内容写得清楚易读",source:"workspace",tags:["写作"]},
  ],
  models:[{id:"research-model",label:"研究模型",provider:"OpenAI",contextWindow:128000,health:"healthy",vision:true,toolUse:true}],
  mcpServers:[],
};
function Preview(){
  const [agent,setAgent]=useState(()=>({...createBlankAgent(),id:"researcher",name:"资料研究员",icon:"",color:"#47765b",description:"帮你查资料、核实来源，把结论整理清楚。",categoryId:"research",prompt:"你是一名认真、可靠的资料研究员。\n\n收到任务后：\n1. 先确定需要回答的问题，列出待核实的信息。\n2. 优先查阅官方文档和原始资料，保留来源链接。\n3. 将事实与推测分开，遇到不确定的信息直接说明。\n\n交付时先给结论，再列出依据和需要继续确认的问题。",tools:{mode:"include",entries:["read_file","list_dir","web_search","web_fetch"]},skills:{mode:"include",entries:["资料调研"]}}));
  const [open,setOpen]=useState(true);
  const [saved,setSaved]=useState(false);
  return <main style={{minHeight:"100vh",background:"radial-gradient(ellipse at 10% 0%,#dfe9df,transparent 60%),#f1f1eb"}}><button onClick={()=>setOpen(true)}>打开配置</button><AgentEditorDialog open={open} agent={agent} isNew={false} catalog={catalog} globalModelLabel="研究模型" onOpenChange={setOpen} onSave={value=>{setAgent(value);setSaved(true)}} savedNotice={saved}/></main>;
}
createRoot(document.getElementById("root")!).render(<Preview/>);
