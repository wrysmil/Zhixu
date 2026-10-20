import {
  ArrowUpRight,
  Files,
  ListChecks,
  MessageSquare,
  NotebookPen,
  Telescope,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { relativeTime, visibleSessionPreview } from "@/lib/format";

/** One resumable conversation on the landing page. Keys are real session keys, so
 *  clicking a row opens that chat instead of staging a demo prompt. */
export interface WelcomeRecent {
  key: string;
  title: string;
  preview?: string;
  meta?: string | null;
  updatedAt?: string | null;
}

interface ThreadWelcomeIntroProps {
  greeting?: string;
}

interface ThreadWelcomeExtrasProps {
  onChoosePrompt: (text: string) => void;
  /** Most recent real conversations; empty when the workspace has no history yet. */
  recents?: WelcomeRecent[];
  onOpenRecent?: (key: string) => void;
  /** True while the session list is still loading, so the section can hold its shape. */
  recentsLoading?: boolean;
}

const MASCOT_SRC = `${import.meta.env.BASE_URL}illustrations/zhixu-mascot.webp`;

function useWelcomeCopy() {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage;
  const zh = Boolean(locale?.startsWith("zh"));
  return { t, i18n, locale, zh };
}

/**
 * Top half of the landing: greeting and mascot. It sits above the composer, so it
 * carries only the copy — the shortcuts and resume rows render *below* the
 * composer to match the assistant demo's reading order.
 */
export function ThreadWelcomeIntro({ greeting }: ThreadWelcomeIntroProps) {
  const { t, zh } = useWelcomeCopy();

  return (
    <div className="thread-welcome-intro-block">
      <div className="thread-welcome-intro">
        <div className="thread-welcome-copy">
          <p className="thread-welcome-eyebrow">
            <span className="thread-welcome-eyebrow-dot" aria-hidden />
            {zh ? "你好，我是知序" : "HI, I'M ZHIXU"}
          </p>
          <h1
            data-testid="hero-greeting"
            className="thread-welcome-title select-none whitespace-nowrap"
          >
            {greeting ?? t("thread.empty.greetings.workOn")}
          </h1>
          <p className="thread-welcome-description">
            {zh
              ? "找资料、理思路，或把一件事交给我。"
              : "Look things up, think it through, or hand a task over."}
          </p>
        </div>
        <div className="thread-welcome-illustration" aria-hidden>
          <span className="thread-welcome-halo" />
          <img src={MASCOT_SRC} alt="" width={186} height={158} draggable={false} />
        </div>
      </div>
    </div>
  );
}

/** Bottom half of the landing: starter chips, real resume rows, and the footnote. */
export function ThreadWelcomeExtras({
  onChoosePrompt,
  recents = [],
  onOpenRecent,
  recentsLoading = false,
}: ThreadWelcomeExtrasProps) {
  const { locale, zh } = useWelcomeCopy();

  const starters = [
    {
      label: zh ? "总结一份文档" : "Summarize a document",
      icon: Files,
      text: zh
        ? "帮我总结一份文档，提炼重点、问题和下一步行动。我会在这里粘贴内容或添加附件。"
        : "Summarize a document for me: pull out the key points, open questions, and next steps. I'll paste it or attach the file.",
    },
    {
      label: zh ? "修改一篇文章" : "Polish a draft",
      icon: NotebookPen,
      text: zh
        ? "帮我修改一篇文章，保留我的表达习惯，让结构更清楚、文字更自然。我会贴上草稿。"
        : "Help me revise a draft: keep my voice, make the structure clearer and the wording more natural. I'll paste it here.",
    },
    {
      label: zh ? "对比几个方案" : "Compare options",
      icon: Telescope,
      text: zh
        ? "帮我对比几个方案，从适用场景、成本和限制展开，并标明需要核实的信息。"
        : "Compare a few options for me: where each fits, what it costs, and what limits it — and flag what still needs verifying.",
    },
    {
      label: zh ? "制定行动计划" : "Plan next steps",
      icon: ListChecks,
      text: zh
        ? "帮我制定行动计划，先明确目标与约束，再拆成今天可以开始的小步骤。"
        : "Help me turn this into an action plan: clarify the goal and constraints, then break it into steps I can start today.",
    },
  ];

  return (
    <div className="thread-welcome-extras-block">
      <section className="thread-welcome-starters" aria-label={zh ? "快捷任务" : "Quick tasks"}>
        <p className="thread-welcome-starters-label">
          {zh ? "也可以从这里开始" : "OR START FROM HERE"}
        </p>
        <div className="thread-welcome-chips">
          {starters.map(({ label, icon: Icon, text }) => (
            <button
              key={label}
              type="button"
              onClick={() => onChoosePrompt(text)}
              className="thread-welcome-chip"
            >
              <Icon size={14} strokeWidth={1.7} aria-hidden />
              {label}
            </button>
          ))}
        </div>
      </section>

      {recentsLoading || recents.length > 0 ? (
        <section
          className="thread-welcome-recents"
          aria-label={zh ? "继续手头的工作" : "Pick up where you left off"}
        >
          <header className="thread-welcome-section-heading">
            <h2>{zh ? "继续手头的工作" : "Pick up where you left off"}</h2>
            <span>{recentsLoading ? (zh ? "正在加载" : "Loading") : zh ? "最近对话" : "Recent chats"}</span>
          </header>
          <div className="thread-welcome-recent-list" aria-busy={recentsLoading}>
            {recents.map((row) => {
              const preview = visibleSessionPreview(row.preview);
              const showPreview = Boolean(preview) && preview !== row.title;
              return (
                <button
                  key={row.key}
                  type="button"
                  className="thread-welcome-recent-row"
                  onClick={() => onOpenRecent?.(row.key)}
                  disabled={!onOpenRecent}
                >
                  <span className="thread-welcome-recent-icon" aria-hidden>
                    <MessageSquare size={15} strokeWidth={1.7} />
                  </span>
                  <span className="thread-welcome-recent-copy">
                    <strong>{row.title}</strong>
                    {showPreview || row.meta ? (
                      <span className="thread-welcome-recent-detail">
                        {showPreview ? preview : null}
                        {showPreview && row.meta ? <span aria-hidden> · </span> : null}
                        {row.meta ? (
                          <span className="thread-welcome-recent-meta">{row.meta}</span>
                        ) : null}
                      </span>
                    ) : null}
                  </span>
                  <span className="thread-welcome-recent-time">
                    {row.updatedAt ? relativeTime(row.updatedAt, locale) : ""}
                  </span>
                  <ArrowUpRight size={14} strokeWidth={1.7} aria-hidden className="thread-welcome-recent-arrow" />
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      <p className="thread-welcome-foot">
        <span className="thread-welcome-foot-dot" aria-hidden />
        {zh
          ? "想法不用一次说清楚，我们可以边聊边整理。"
          : "You don't have to have it all figured out — we can sort it out together."}
      </p>
    </div>
  );
}

interface ThreadWelcomeProps extends ThreadWelcomeExtrasProps {
  greeting?: string;
}

/** Single-block composition, for surfaces that cannot slot the composer in between. */
export function ThreadWelcome({ greeting, ...extras }: ThreadWelcomeProps) {
  return (
    <div className="thread-welcome">
      <ThreadWelcomeIntro greeting={greeting} />
      <ThreadWelcomeExtras {...extras} />
    </div>
  );
}
