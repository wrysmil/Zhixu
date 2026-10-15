import { useState } from "react";
import { Bookmark, FileText, History, Search, SlidersHorizontal, X } from "lucide-react";

import { MemoryGuide } from "@/components/settings/memory/MemoryGuide";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { cn } from "@/lib/utils";

type MemoryTab = "semantic" | "episode";
type Category = "preference" | "fact" | "rule";

const CATEGORY_LABELS: Record<Category, string> = { preference: "偏好", fact: "事实", rule: "约定" };
const SAMPLE_MEMORIES: Array<{ id: string; content: string; category: Category; tags: string[] }> = [
  { id: "sample-1", content: "回复先给结论，需要时再展开说明。", category: "preference", tags: ["沟通方式", "表达"] },
  { id: "sample-2", content: "正在整理一份关于城市漫步的阅读清单。", category: "fact", tags: ["阅读", "进行中的事"] },
  { id: "sample-3", content: "修改文件前，先说明改动范围与原因。", category: "rule", tags: ["协作", "文件"] },
  { id: "sample-4", content: "每周日晚上留出时间，回顾这一周的笔记。", category: "preference", tags: ["复盘", "习惯"] },
  { id: "sample-5", content: "个人笔记按主题整理，而不是按日期分文件夹。", category: "fact", tags: ["笔记", "整理"] },
];
const SAMPLE_EPISODES = [
  { id: "sample-episode-1", content: "整理每周阅读清单", detail: "挑选了三篇文章，并按主题整理为下次阅读的参考。", date: "10 月 14 日 · 示例对话" },
  { id: "sample-episode-2", content: "一起调整项目说明", detail: "将长段说明拆成操作步骤，保留了需要进一步确认的问题。", date: "10 月 13 日 · 示例对话" },
  { id: "sample-episode-3", content: "回顾一周的随手笔记", detail: "把零散想法归入阅读、工作与生活三个主题。", date: "10 月 12 日 · 示例对话" },
];

/** Deliberately local: no client/provider import and no business requests. */
export function PreviewMemory() {
  const [tab, setTab] = useState<MemoryTab>("semantic");
  const [enabled, setEnabled] = useState(true);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | "">("");
  const needle = query.trim().toLocaleLowerCase();
  const memories = SAMPLE_MEMORIES.filter((memory) => (
    (!category || memory.category === category)
    && `${memory.content} ${memory.tags.join(" ")}`.toLocaleLowerCase().includes(needle)
  ));
  const episodes = SAMPLE_EPISODES.filter((episode) => `${episode.content} ${episode.detail}`.toLocaleLowerCase().includes(needle));
  const count = tab === "semantic" ? memories.length : episodes.length;

  return (
    <div className="space-y-6">
      <MemoryGuide preview />
      <div className="flex items-start justify-between gap-4 rounded-xl border border-border/65 bg-card/70 px-5 py-4">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-foreground">自动记住有用的信息</p>
          <p id="preview-memory-toggle-description" className="mt-1 text-[12px] leading-5 text-muted-foreground">
            {enabled ? "已开启（示例）。从对话中整理有用的信息，在之后需要时参考。" : "已暂停（示例）。仍可查看下方已有的内容。"}
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-label="自动记住有用的信息（示例）"
          aria-describedby="preview-memory-toggle-description"
          aria-checked={enabled}
          onClick={() => setEnabled(!enabled)}
          className={cn("mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2", enabled ? "border-primary bg-primary" : "border-input bg-input")}
        >
          <span className={cn("h-5 w-5 rounded-full bg-background shadow-sm transition-transform duration-150 ease-out", enabled ? "translate-x-5" : "translate-x-0.5")} />
        </button>
      </div>

      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 pb-4">
          <SegmentedControl
            mode="tabs"
            ariaLabel="记忆分类"
            value={tab}
            onChange={setTab}
            options={[
              { value: "semantic", label: <span className="inline-flex items-center gap-1.5"><Bookmark className="h-3.5 w-3.5" aria-hidden />长期信息</span> },
              { value: "episode", label: <span className="inline-flex items-center gap-1.5"><History className="h-3.5 w-3.5" aria-hidden />对话记录</span> },
            ]}
            className="bg-primary/[0.06]"
            itemClassName="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <span className="text-[11px] tracking-wide text-muted-foreground">示例记忆 · 仅在当前页面演示</span>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input type="search" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="搜索示例记忆" placeholder="搜索内容或标签" className="h-10 bg-card/75 pl-9" />
          </div>
          {tab === "semantic" ? (
            <label className="flex h-10 items-center gap-2 rounded-control border border-input bg-card/75 px-3 text-[12px]">
              <SlidersHorizontal className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              <select aria-label="筛选记忆分类" value={category} onChange={(event) => setCategory(event.target.value as Category | "")} className="min-w-0 bg-transparent pr-1 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <option value="">全部分类</option>
                {Object.entries(CATEGORY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
          ) : null}
        </div>

        <div className="flex min-h-5 items-center justify-between gap-3 text-[11px] text-muted-foreground">
          <span role="status">{count} 条示例{tab === "semantic" ? "信息" : "记录"}</span>
          {(query || category) ? <Button type="button" variant="ghost" size="sm" onClick={() => { setQuery(""); setCategory(""); }} className="h-6 px-1.5 text-[11px]"><X className="mr-1 h-3 w-3" aria-hidden />清除筛选</Button> : null}
        </div>

        {count === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-gradient-to-br from-card/80 to-primary/[0.03] px-5 py-9">
            <Search className="mb-3 h-5 w-5 text-muted-foreground" aria-hidden />
            <p className="text-[13px] font-medium text-foreground">没有找到匹配的示例</p>
            <p className="mt-1 text-[12px] text-muted-foreground">换个关键词，或清除筛选再试试。</p>
          </div>
        ) : (
          <ul aria-label="示例记忆列表" className="divide-y divide-border/60 overflow-hidden rounded-xl border border-border/65 bg-card/65">
            {tab === "semantic" ? memories.map((memory) => (
              <li key={memory.id} className="flex gap-3 px-4 py-4 sm:px-5">
                <Bookmark className="mt-0.5 h-4 w-4 shrink-0 text-primary/70" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] leading-6 text-foreground">{memory.content}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] leading-4 text-muted-foreground">
                    <span className="rounded bg-primary/[0.07] px-1.5 py-0.5 font-medium text-primary">{CATEGORY_LABELS[memory.category]}</span>
                    {memory.tags.map((tag) => <span key={tag}>{tag}</span>)}
                    <span className="ml-auto text-muted-foreground/75">示例</span>
                  </div>
                </div>
              </li>
            )) : episodes.map((episode) => (
              <li key={episode.id} className="flex gap-3 px-4 py-4 sm:px-5">
                <FileText className="mt-1 h-4 w-4 shrink-0 text-primary/70" aria-hidden />
                <div className="min-w-0">
                  <p className="text-[13px] font-medium leading-6 text-foreground">{episode.content}</p>
                  <p className="mt-1 text-[12px] leading-5 text-muted-foreground">{episode.detail}</p>
                  <p className="mt-2 text-[10px] text-muted-foreground/75">{episode.date}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="text-[11px] leading-5 text-muted-foreground">这里展示的是虚构示例。搜索、筛选和开关仅用于体验，刷新后重置。</p>
      </div>
    </div>
  );
}
