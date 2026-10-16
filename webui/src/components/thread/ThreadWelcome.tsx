import { ArrowUpRight, ListChecks, NotebookPen } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ThreadWelcomeProps {
  greeting?: string;
  onChoosePrompt: (text: string) => void;
}

/** Shared orientation for an empty conversation. Suggestions prepare a draft;
 * the existing composer remains responsible for sending it. */
export function ThreadWelcome({ greeting, onChoosePrompt }: ThreadWelcomeProps) {
  const { t, i18n } = useTranslation();
  const zh = i18n.resolvedLanguage?.startsWith("zh");
  const prompts = [
    {
      title: zh ? "把零散笔记，理出头绪" : "Organize notes",
      description: zh ? "提炼重点、待解决的问题和下一步" : "Find the key points, open questions, and next steps",
      text: zh ? "帮我把这些笔记整理成重点、待解决的问题和下一步行动。" : "Help me organize these notes into key points, open questions, and next steps.",
      icon: NotebookPen,
    },
    {
      title: zh ? "把一个想法，拆成行动" : "Make a plan",
      description: zh ? "说清目标，一起找到可执行的起点" : "Turn a goal into a clear, practical starting point",
      text: zh ? "我有一个想完成的目标，请帮我明确需要的信息，并拆成可以执行的步骤。" : "Help me clarify my goal, identify the information I need, and turn it into practical steps.",
      icon: ListChecks,
    },
  ];

  return (
    <div className="thread-welcome">
      <div className="thread-welcome-intro">
        <div className="thread-welcome-copy">
          <p className="thread-welcome-eyebrow">
            <img src="/illustrations/workflow.svg" width={16} height={16} alt="" aria-hidden />
            {zh ? "知序 · 你的工作台" : "ZHIXU / YOUR WORKSPACE"}
          </p>
          <h1 data-testid="hero-greeting" className="thread-welcome-title select-none whitespace-nowrap">
            {greeting ?? t("thread.empty.greetings.workOn")}
          </h1>
          <p className="thread-welcome-description">
            {zh ? "写下你想完成的事。带上资料，我们从这里开始。" : "Bring a question, a few notes, or a goal. Let’s take the next step."}
          </p>
        </div>
        <div className="thread-welcome-illustration" aria-hidden>
          <span className="thread-welcome-orbit" />
          <img src="/illustrations/agent-workspace-generated.webp" alt="" width={240} height={190} draggable={false} />
          <span className="thread-welcome-caption">{zh ? "想法，在这里连成线" : "A little space to connect the dots"}</span>
        </div>
      </div>
      <div className="thread-welcome-starts">
        <p className="thread-welcome-starts-label">{zh ? "也可以，从这里开始" : "A FEW WAYS TO GET STARTED"}</p>
        <div className="thread-welcome-prompt-list">
          {prompts.map(({ title, description, text, icon: Icon }, index) => (
            <button key={title} type="button" onClick={() => onChoosePrompt(text)} className="thread-welcome-prompt">
              <span className="thread-welcome-prompt-number" aria-hidden>0{index + 1}</span>
              <Icon size={18} strokeWidth={1.6} aria-hidden />
              <span className="thread-welcome-prompt-copy"><strong>{title}</strong><span>{description}</span></span>
              <ArrowUpRight size={17} strokeWidth={1.6} aria-hidden className="thread-welcome-prompt-arrow" />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
