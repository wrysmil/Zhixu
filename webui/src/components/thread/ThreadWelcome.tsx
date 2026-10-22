import {
  Files,
  ListChecks,
  NotebookPen,
  Telescope,
} from "lucide-react";
import { useTranslation } from "react-i18next";

interface ThreadWelcomeIntroProps {
  greeting?: string;
}

interface ThreadWelcomeExtrasProps {
  onChoosePrompt: (text: string) => void;
}

const MASCOT_SRC = `${import.meta.env.BASE_URL}illustrations/zhixu-welcome.webp`;

function useWelcomeCopy() {
  const { t, i18n } = useTranslation();
  const zh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));
  return { t, zh };
}

/**
 * Top half of the landing: greeting and mascot. It sits above the composer, so it
 * carries only the copy — the shortcuts render *below* the composer to match the
 * assistant demo's reading order. Recent conversations stay in the sidebar only;
 * repeating them here competed with the sidebar for the same job.
 */
export function ThreadWelcomeIntro({ greeting }: ThreadWelcomeIntroProps) {
  const { t, zh } = useWelcomeCopy();

  return (
    <div className="thread-welcome-intro-block">
      <div className="thread-welcome-intro">
        <div className="thread-welcome-copy">
          <h1
            data-testid="hero-greeting"
            className="thread-welcome-title select-none whitespace-nowrap"
          >
            {zh ? "今天，想做点什么？" : greeting ?? t("thread.empty.greetings.workOn")}
          </h1>
          <p className="thread-welcome-description">
            {zh
              ? "查资料、写点东西，或把手头的任务交给我。"
              : "Find an answer, shape a draft, or work through a task together."}
          </p>
        </div>
        <div className="thread-welcome-illustration" aria-hidden>
          <img src={MASCOT_SRC} alt="" width={552} height={368} draggable={false} />
        </div>
      </div>
    </div>
  );
}

/** Bottom half of the landing: starter chips. */
export function ThreadWelcomeExtras({ onChoosePrompt }: ThreadWelcomeExtrasProps) {
  const { zh } = useWelcomeCopy();

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
