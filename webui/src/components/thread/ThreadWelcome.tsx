import { useTranslation } from "react-i18next";

interface ThreadWelcomeIntroProps {
  /** Compatibility with existing callers; the memory title comes from i18n. */
  greeting?: string;
}

interface ThreadWelcomeExtrasProps {
  onChoosePrompt: (text: string) => void;
}

/** Greeting above the composer; starter prompts appear below it. */
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
      </div>
    </div>
  );
}

/** Bottom half of the landing: starter chips. */
export function ThreadWelcomeExtras({ onChoosePrompt }: ThreadWelcomeExtrasProps) {
  const { t } = useTranslation();
  const starters = ["preferences", "experience", "continue", "reflect"] as const;

  return (
    <div className="thread-welcome-extras-block">
      <section className="thread-welcome-starters" aria-label={t("thread.welcome.startersLabel")}>
        <p className="thread-welcome-starters-label">
          {t("thread.welcome.startersLabel")}
        </p>
        <div className="thread-welcome-chips">
          {starters.map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => onChoosePrompt(t(`thread.welcome.starters.${key}.prompt`))}
              className="thread-welcome-chip"
            >
              <span>{t(`thread.welcome.starters.${key}.label`)}</span>
              <span className="thread-welcome-chip-arrow" aria-hidden>↗</span>
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
