import { Bookmark, History } from "lucide-react";
import { useTranslation } from "react-i18next";

/** A shared explanation of the two existing memory stores, without new capabilities. */
export function MemoryGuide({ preview = false }: { preview?: boolean }) {
  const { i18n } = useTranslation();
  const zh = preview || i18n.resolvedLanguage?.startsWith("zh");

  return (
    <div className="flex items-start gap-5 overflow-hidden rounded-2xl border border-primary/15 bg-gradient-to-br from-primary/[0.07] via-card/80 to-background px-5 py-5 sm:px-6 sm:py-6">
      <div className="min-w-0 max-w-[650px] flex-1">
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">
          {zh ? "与你有关，始终由你掌握" : "Useful context, under your control"}
        </p>
        <h3 className="font-serif text-[24px] leading-snug tracking-tight text-foreground sm:text-[28px]">
          {zh ? "值得记住的，留在这里。" : "Keep what matters for next time."}
        </h3>
        <p className="mt-2 max-w-[50ch] text-[13px] leading-6 text-muted-foreground">
          {zh
            ? "少一些重复解释，多一点默契。查看知序记住了什么，也可以随时修改或删除。"
            : "Repeat yourself less. Review what Zhixu remembers, then edit or remove anything that no longer fits."}
        </p>
        <dl className="mt-5 grid gap-4 text-[12px] leading-5 lg:grid-cols-2">
          <div className="flex items-start gap-2.5">
            <Bookmark className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
            <div>
              <dt className="font-medium text-foreground">{zh ? "长期信息" : "Lasting details"}</dt>
              <dd className="mt-0.5 text-muted-foreground">{zh ? "你的偏好、事实与约定，用于之后的对话。" : "Preferences, facts and instructions for future chats."}</dd>
            </div>
          </div>
          <div className="flex items-start gap-2.5">
            <History className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
            <div>
              <dt className="font-medium text-foreground">{zh ? "对话记录" : "Conversation records"}</dt>
              <dd className="mt-0.5 text-muted-foreground">{zh ? "聊过的事情与结果，需要时可以回看。" : "What you discussed and how it went, ready to revisit."}</dd>
            </div>
          </div>
        </dl>
      </div>
      <img
        src="/illustrations/memory-notes.svg"
        alt=""
        aria-hidden="true"
        width="160"
        height="160"
        className="pointer-events-none mt-3 hidden h-32 w-24 shrink-0 object-contain opacity-80 sm:block lg:h-36 lg:w-28"
      />
    </div>
  );
}
