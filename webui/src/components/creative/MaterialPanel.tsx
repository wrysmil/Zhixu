import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { Material, MaterialType } from "./types";
import { MATERIAL_TYPES, MATERIAL_TYPE_LABEL_KEY } from "./types";
import { isSafeUrl } from "./article-model";

interface Props {
  materials: Material[];
  onChange: (materials: Material[]) => void;
}

export function MaterialPanel({ materials, onChange }: Props) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState(false);
  const [newKind, setNewKind] = useState<MaterialType>("note");
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");
  const [linkError, setLinkError] = useState<string | null>(null);

  const handleAdd = () => {
    if (!newTitle.trim() || !newContent.trim()) return;
    if (newKind === "link" && !isSafeUrl(newContent)) {
      setLinkError("不支持该链接协议");
      return;
    }
    const at = new Date().toISOString();
    const item: Material = {
      id: crypto.randomUUID(),
      type: newKind,
      title: newTitle.trim(),
      content: newContent.trim(),
      included: true,
      order: materials.length,
      createdAt: at,
      updatedAt: at,
    };
    onChange([...materials, item]);
    setAdding(false);
    setNewTitle("");
    setNewContent("");
    setLinkError(null);
  };

  const handleRemove = (id: string) => {
    onChange(materials.filter((m) => m.id !== id));
  };

  const handleToggleIncluded = (id: string) => {
    onChange(
      materials.map((m) =>
        m.id === id ? { ...m, included: !m.included } : m,
      ),
    );
  };

  return (
    <div>
      {materials.length === 0 && !adding ? (
        <p className="py-4 text-center text-[11px] text-muted-foreground">
          {t("creative.no_materials", { defaultValue: "暂无素材" })}
        </p>
      ) : (
        <div className="space-y-2">
          {materials.map((m) => (
            <div
              key={m.id}
              className={cn(
                "rounded-control border border-border bg-settings-surface p-2.5",
                !m.included && "opacity-50",
              )}
            >
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] font-medium text-primary">{t(MATERIAL_TYPE_LABEL_KEY[m.type])}</span>
                <button
                  type="button"
                  aria-label="移除素材"
                  onClick={() => handleRemove(m.id)}
                  className="rounded p-0.5 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
              <button
                type="button"
                className="w-full text-left"
                onClick={() => handleToggleIncluded(m.id)}
              >
                <p className="text-[12px] font-medium text-foreground">{m.title}</p>
                <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
                  {m.type === "link" ? (
                    <a
                      href={isSafeUrl(m.content) ? m.content : "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {m.content}
                    </a>
                  ) : (
                    m.content
                  )}
                </p>
              </button>
            </div>
          ))}
        </div>
      )}

      {adding ? (
        <div className="mt-3 space-y-2 rounded-control border border-border bg-background p-2.5">
          <div className="flex gap-1">
            {MATERIAL_TYPES.map((k) => (
              <button
                key={k}
                type="button"
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px]",
                  newKind === k
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground",
                )}
                onClick={() => setNewKind(k)}
              >
                {t(MATERIAL_TYPE_LABEL_KEY[k])}
              </button>
            ))}
          </div>
          <Input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="标题"
            className="h-7 text-[12px]"
          />
          <textarea
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            placeholder={newKind === "link" ? "https://..." : "内容"}
            className="w-full resize-none rounded-control border border-border bg-background p-2 text-[12px] outline-none"
            rows={2}
          />
          {linkError ? (
            <p className="text-[11px] text-destructive">{linkError}</p>
          ) : null}
          <div className="flex gap-1.5">
            <Button size="sm" onClick={handleAdd} className="h-6 text-[11px]">
              添加
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setAdding(false);
                setLinkError(null);
              }}
              className="h-6 text-[11px]"
            >
              取消
            </Button>
          </div>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setAdding(true)}
          className="mt-2 h-6 text-[11px]"
        >
          <Plus className="mr-1 h-3 w-3" />
          添加素材
        </Button>
      )}
    </div>
  );
}
