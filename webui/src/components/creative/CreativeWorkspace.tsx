import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";

import type { Article, ArticleStatus, Suggestion } from "./types";
import {
  createDraftInput,
  duplicateTitle,
  searchArticles,
  filterByStatus,
  canAcceptSuggestion,
} from "./article-model";
import { demoArticles, demoSuggestions } from "./demo-data";
import { ArticleList } from "./ArticleList";
import { ArticleEditor } from "./ArticleEditor";

type View = "list" | "editor";

export default function CreativeWorkspace() {
  const { t } = useTranslation();
  const [articles, setArticles] = useState<Article[]>(demoArticles);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [view, setView] = useState<View>("list");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<ArticleStatus | "all">(
    "all",
  );
  const [suggestions, setSuggestions] = useState<Suggestion[]>(() =>
    demoSuggestions(demoArticles()),
  );
  const [undoStack, setUndoStack] = useState<
    { articleId: string; revisionBeforeApply: number; snapshot: Article }[]
  >([]);

  const activeArticle = articles.find((a) => a.id === activeId) ?? null;

  const openEditor = useCallback((id: string) => {
    setActiveId(id);
    setView("editor");
  }, []);

  const backToList = useCallback(() => setView("list"), []);

  const handleCreate = useCallback(
    (title: string, templateId?: string) => {
      if (!title.trim()) return { error: "请输入文章主题" };
      const input = createDraftInput(title, templateId, t);
      // Demo-mode placeholder until A3 wires `article.create`: the server owns
      // id/revision/timestamps, so mirror its shape (revision starts at 1).
      const now = new Date().toISOString();
      const article: Article = {
        id: crypto.randomUUID(),
        title: input.title,
        writingType: input.writingType ?? "",
        templateId: input.templateId,
        status: "idea",
        summary: input.summary ?? "",
        brief: input.brief ?? {},
        body: "",
        outline: input.outline ?? [],
        materials: input.materials ?? [],
        tags: input.tags ?? [],
        revision: 1,
        createdAt: now,
        updatedAt: now,
      };
      setArticles((prev) => [article, ...prev]);
      setActiveId(article.id);
      setView("editor");
      const newSuggestions = demoSuggestions([article]);
      if (newSuggestions.length) setSuggestions((prev) => [...prev, ...newSuggestions]);
      return { error: null };
    },
    [t],
  );

  const handleDuplicate = useCallback((id: string) => {
    setArticles((prev) => {
      const src = prev.find((a) => a.id === id);
      if (!src) return prev;
      const now = new Date().toISOString();
      const copy: Article = {
        ...src,
        id: crypto.randomUUID(),
        title: duplicateTitle(src.title),
        revision: 1,
        createdAt: now,
        updatedAt: now,
        outline: src.outline.map((item) => ({ ...item, id: crypto.randomUUID() })),
        materials: src.materials.map((m) => ({ ...m, id: crypto.randomUUID() })),
      };
      return [copy, ...prev];
    });
  }, []);

  const handleDelete = useCallback((id: string) => {
    setArticles((prev) => prev.filter((a) => a.id !== id));
    setActiveId((prev) => (prev === id ? null : prev));
    setSuggestions((prev) => prev.filter((s) => s.articleId !== id));
  }, []);

  const updateArticle = useCallback(
    (id: string, patch: Partial<Article>) => {
      setArticles((prev) =>
        prev.map((a) =>
          a.id === id
            ? // Demo-mode simulation of the server CAS round (article.save
              // advances `revision`); the model layer no longer bumps it.
              {
                ...a,
                ...patch,
                revision: a.revision + 1,
                updatedAt: new Date().toISOString(),
              }
            : a,
        ),
      );
    },
    [],
  );

  const acceptSuggestion = useCallback(
    (suggestion: Suggestion): boolean => {
      if (!activeArticle) return false;
      if (!canAcceptSuggestion(activeArticle, suggestion)) return false;

      const snapshot = { ...activeArticle };
      setUndoStack((prev) => [
        ...prev.filter((u) => u.articleId !== suggestion.articleId),
        {
          articleId: suggestion.articleId,
          revisionBeforeApply: activeArticle.revision,
          snapshot,
        },
      ]);

      if (suggestion.target === "outline") {
        updateArticle(activeArticle.id, {
          outline: suggestion.after
            .split("\n")
            .filter(Boolean)
            .map((text, index) => ({ id: crypto.randomUUID(), text, order: index })),
        });
      } else if (suggestion.target === "body") {
        updateArticle(activeArticle.id, { body: suggestion.after });
      }
      setSuggestions((prev) => prev.filter((s) => s !== suggestion));
      return true;
    },
    [activeArticle, updateArticle],
  );

  const undoSuggestion = useCallback(
    (suggestion: Suggestion): boolean => {
      if (!activeArticle) return false;
      const entry = undoStack.find(
        (u) =>
          u.articleId === suggestion.articleId &&
          activeArticle.revision === u.revisionBeforeApply + 1,
      );
      if (!entry) return false;
      setArticles((prev) =>
        prev.map((a) => (a.id === entry.articleId ? entry.snapshot : a)),
      );
      setUndoStack((prev) => prev.filter((u) => u !== entry));
      setSuggestions((prev) => [...prev, suggestion]);
      return true;
    },
    [activeArticle, undoStack],
  );

  const restoreDemo = useCallback(() => {
    const demo = demoArticles();
    setArticles(demo);
    setSuggestions(demoSuggestions(demo));
  }, []);

  const filtered = searchArticles(filterByStatus(articles, statusFilter), query);

  if (view === "editor" && activeArticle) {
    return (
      <ArticleEditor
        article={activeArticle}
        suggestions={suggestions.filter((s) => s.articleId === activeArticle.id)}
        onUpdate={(patch) => updateArticle(activeArticle.id, patch)}
        onBack={backToList}
        onAcceptSuggestion={acceptSuggestion}
        onUndoSuggestion={undoSuggestion}
      />
    );
  }

  return (
    <ArticleList
      articles={filtered}
      totalCount={articles.length}
      query={query}
      statusFilter={statusFilter}
      onQueryChange={setQuery}
      onStatusFilterChange={setStatusFilter}
      onOpen={openEditor}
      onCreate={handleCreate}
      onDuplicate={handleDuplicate}
      onDelete={handleDelete}
      onRestoreDemo={restoreDemo}
    />
  );
}
