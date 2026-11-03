import {
  Heart,
  History,
  NotebookPen,
  MessageCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";

interface ThreadWelcomeIntroProps {
  /** Compatibility with existing callers; the memory title comes from i18n. */
  greeting?: string;
}

interface ThreadWelcomeExtrasProps {
  onChoosePrompt: (text: string) => void;
}

const MASCOT_SRC = `${import.meta.env.BASE_URL}brand/zhixu-robot.webp`;

/**
 * Top half of the landing: greeting and mascot. It sits above the composer, so it
 * carries only the copy — the shortcuts render *below* the composer to match the
 * assistant demo's reading order. Recent conversations stay in the sidebar only;
 * repeating them here competed with the sidebar for the same job.
 */
export function ThreadWelcomeIntro(props: ThreadWelcomeIntroProps) {
  // Retain the greeting prop for existing callers; use one localized brand title.
  void props.greeting;
  const { t } = useTranslation();

  return (
    <div className="thread-welcome-intro-block">
      <div className="thread-welcome-intro">
        <div className="thread-welcome-copy">
          <p className="thread-welcome-eyebrow">
            <span className="thread-welcome-eyebrow-dot" aria-hidden />
            {t("thread.welcome.eyebrow")}
          </p>
          <h1
            data-testid="hero-greeting"
            className="thread-welcome-title select-none"
          >
            {t("thread.welcome.title")}
          </h1>
          <p className="thread-welcome-description">
            {t("thread.welcome.description")}
          </p>
        </div>
        <div className="thread-welcome-illustration" aria-hidden>
          <img src={MASCOT_SRC} alt="" width={600} height={450} draggable={false} />
        </div>
      </div>
    </div>
  );
}

/** Bottom half of the landing: starter chips. */
export function ThreadWelcomeExtras({ onChoosePrompt }: ThreadWelcomeExtrasProps) {
  const { t } = useTranslation();
  const starters = [
    { key: "preferences", icon: Heart },
    { key: "experience", icon: NotebookPen },
    { key: "continue", icon: MessageCircle },
    { key: "reflect", icon: History },
  ] as const;

  return (
    <div className="thread-welcome-extras-block">
      <section className="thread-welcome-starters" aria-label={t("thread.welcome.startersLabel")}>
        <p className="thread-welcome-starters-label">
          {t("thread.welcome.startersLabel")}
        </p>
        <div className="thread-welcome-chips">
          {starters.map(({ key, icon: Icon }) => (
            <button
              key={key}
              type="button"
              onClick={() => onChoosePrompt(t(`thread.welcome.starters.${key}.prompt`))}
              className="thread-welcome-chip"
            >
              <Icon size={14} strokeWidth={1.7} aria-hidden />
              {t(`thread.welcome.starters.${key}.label`)}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

interface ThreadWelcomeProps extends ThreadWelcomeExtrasProps {
  /** Compatibility with existing callers; the memory title comes from i18n. */
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
