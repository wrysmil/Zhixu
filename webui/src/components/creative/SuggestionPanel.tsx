import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Lightbulb, Check, Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Article, Suggestion } from "./types";
import { canAcceptSuggestion } from "./article-model";

interface Props {
  article: Article;
  suggestions: Suggestion[];
  onAccept: (s: Suggestion) => boolean;
  onUndo: (s: Suggestion) => boolean;
}

export function SuggestionPanel({
  article,
  suggestions,
  onAccept,
  onUndo,
}: Props) {
  const { t } = useTranslation();
  const [accepted, setAccepted] = useState<Suggestion[]>([]);
  const [result, setResult] = useState<string | null>(null);

  const handleAccept = (s: Suggestion) => {
    if (!canAcceptSuggestion(article, s)) {
      setResult("建议已过期（文章内容已变更），无法采用。");
      return;
    }
    const ok = onAccept(s);
    if (ok) {
      setAccepted((prev) => [...prev, s]);
      setResult(null);
    }
  };

  const handleUndo = (s: Suggestion) => {
    const ok = onUndo(s);
    if (ok) {
      setAccepted((prev) => prev.filter((a) => a !== s));
      setResult(null);
    } else {
      setResult("无法撤销：文章已被进一步修改。");
    }
  };

  return (
    <div>
      <div className="mb-3 flex items-center gap-1.5 text-[12px] font-medium text-foreground">
        <Lightbulb className="h-3.5 w-3.5 text-accent-foreground" />
        {t("creative.suggestions_title", { defaultValue: "AI 建议" })}
      </div>
      <p className="mb-3 text-[11px] leading-relaxed text-muted-foreground">
        {t("creative.suggestions_intro", {
          defaultValue: "基于当前文章内容生成的本地确定性建议，采用前可查看差异。",
        })}
      </p>

      {result ? (
        <p className="mb-2 rounded-control bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300">
          {result}
        </p>
      ) : null}

      {suggestions.length === 0 && accepted.length === 0 ? (
        <p className="py-3 text-center text-[11px] text-muted-foreground">
          {t("creative.no_suggestions", { defaultValue: "暂无建议" })}
        </p>
      ) : null}

      {suggestions.map((s, idx) => (
        <div
          key={idx}
          className="mb-3 rounded-control border border-border bg-settings-surface p-3"
        >
          <p className="mb-1 text-[12px] font-medium text-foreground">{s.label}</p>
          <p className="mb-2 text-[10px] text-muted-foreground">
            基于版本 #{s.baseRevision}，当前版本 #{article.revision}
          </p>
          <div className="mb-2 space-y-1">
            <div>
              <span className="text-[10px] font-medium text-muted-foreground">当前</span>
              <pre className="max-h-[100px] overflow-auto rounded bg-muted/50 p-2 text-[11px] leading-relaxed text-muted-foreground">
                {s.before}
              </pre>
            </div>
            <div>
              <span className="text-[10px] font-medium text-accent-foreground">建议</span>
              <pre className="max-h-[100px] overflow-auto rounded bg-accent p-2 text-[11px] leading-relaxed text-foreground">
                {s.after}
              </pre>
            </div>
          </div>
          <Button
            size="sm"
            onClick={() => handleAccept(s)}
            className="h-6 text-[11px]"
          >
            <Check className="mr-1 h-3 w-3" />
            采用
          </Button>
        </div>
      ))}

      {accepted.map((s, idx) => (
        <div
          key={idx}
          className="mb-2 rounded-control border border-accent-foreground/20 bg-accent p-2.5"
        >
          <p className="text-[11px] text-accent-foreground">
            已采用：{s.label}
          </p>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => handleUndo(s)}
            className="mt-1 h-5 text-[10px]"
          >
            <Undo2 className="mr-1 h-3 w-3" />
            撤销
          </Button>
        </div>
      ))}
    </div>
  );
}
