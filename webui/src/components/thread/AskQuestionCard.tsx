import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CornerDownLeft, PenLine } from "lucide-react";

import type { AskQuestionRequest } from "@/lib/types";

interface AskQuestionCardProps {
  question: AskQuestionRequest;
  onAnswer: (questionId: string, answer: string) => void;
}

/** Interactive question pushed by the agent's ``ask_question`` tool. */
export function AskQuestionCard({ question, onAnswer }: AskQuestionCardProps) {
  const { t } = useTranslation();
  const [customAnswer, setCustomAnswer] = useState("");

  const submit = (answer: string) => {
    const trimmed = answer.trim();
    if (trimmed) onAnswer(question.question_id, trimmed);
  };

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
      const index = Number.parseInt(event.key, 10);
      if (Number.isNaN(index) || index < 1 || index > question.options.length) return;
      event.preventDefault();
      submit(question.options[index - 1].label);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });

  return (
    <div className="mx-auto w-full max-w-[49.5rem] px-4 pb-2">
      <div
        role="group"
        aria-label={question.question}
        data-testid="ask-question-card"
        className="mt-2 rounded-2xl border bg-card text-card-foreground shadow-sm"
      >
        <h3 className="px-4 pt-4 text-sm font-semibold leading-6">
          {question.header ? `${question.header} · ` : ""}
          {question.question}
        </h3>
        <ol className="mt-2 space-y-1 px-2">
          {question.options.map((option, index) => (
            <li key={`${index}:${option.label}`}>
              <button
                type="button"
                data-testid={`ask-question-option-${index + 1}`}
                onClick={() => submit(option.label)}
                className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-accent focus-visible:bg-accent"
              >
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full border text-xs text-muted-foreground">
                  {index + 1}
                </span>
                <span className="shrink-0 text-sm font-medium">{option.label}</span>
                {option.recommended ? (
                  <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                    {t("askQuestion.recommended", { defaultValue: "Recommended" })}
                  </span>
                ) : null}
                {option.description ? (
                  <span className="min-w-0 truncate text-sm text-muted-foreground">
                    {option.description}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ol>
        <form
          data-testid="ask-question-form"
          className="mt-2 flex items-center gap-2 border-t px-4 py-3"
          onSubmit={(event) => {
            event.preventDefault();
            submit(customAnswer);
          }}
        >
          <PenLine className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <input
            data-testid="ask-question-custom-input"
            value={customAnswer}
            onChange={(event) => setCustomAnswer(event.target.value)}
            placeholder={t("askQuestion.otherPlaceholder", { defaultValue: "Type another answer" })}
            aria-label={t("askQuestion.otherPlaceholder", { defaultValue: "Type another answer" })}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <button
            type="button"
            data-testid="ask-question-no-preference"
            onClick={() =>
              submit(t("askQuestion.noPreference", { defaultValue: "No preference" }))
            }
            className="shrink-0 rounded-lg border px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent"
          >
            {t("askQuestion.noPreference", { defaultValue: "No preference" })}
          </button>
          <button
            type="submit"
            data-testid="ask-question-submit"
            disabled={!customAnswer.trim()}
            aria-label={t("askQuestion.submit", { defaultValue: "Submit answer" })}
            className="flex size-7 shrink-0 items-center justify-center rounded-lg border text-muted-foreground hover:bg-accent disabled:opacity-50"
          >
            <CornerDownLeft className="size-3.5" aria-hidden />
          </button>
        </form>
      </div>
    </div>
  );
}
