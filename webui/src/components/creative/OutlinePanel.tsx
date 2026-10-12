import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, X, ChevronUp, ChevronDown } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type { OutlineItem } from "./types";

interface Props {
  outline: OutlineItem[];
  onChange: (outline: OutlineItem[]) => void;
}

function resequence(items: OutlineItem[]): OutlineItem[] {
  return items.map((item, index) => ({ ...item, order: index }));
}

export function OutlinePanel({ outline, onChange }: Props) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState(false);
  const [newItem, setNewItem] = useState("");

  const handleAdd = () => {
    if (!newItem.trim()) return;
    onChange([
      ...outline,
      { id: crypto.randomUUID(), text: newItem.trim(), order: outline.length },
    ]);
    setNewItem("");
    setAdding(false);
  };

  const handleRemove = (idx: number) => {
    onChange(resequence(outline.filter((_, i) => i !== idx)));
  };

  const handleMove = (idx: number, dir: -1 | 1) => {
    const arr = [...outline];
    const target = idx + dir;
    if (target < 0 || target >= arr.length) return;
    [arr[idx], arr[target]] = [arr[target], arr[idx]];
    onChange(resequence(arr));
  };

  return (
    <div>
      {outline.length === 0 ? (
        <p className="py-4 text-center text-[11px] text-muted-foreground">
          {t("creative.no_outline", { defaultValue: "暂无提纲" })}
        </p>
      ) : (
        <div className="space-y-1">
          {outline.map((item, idx) => (
            <div
              key={item.id}
              className="group flex items-start gap-1.5 rounded-control border border-border bg-settings-surface px-2.5 py-2"
            >
              <span className="mt-0.5 shrink-0 text-[10px] font-medium text-muted-foreground">
                {idx + 1}
              </span>
              <span className="flex-1 text-[12px] text-foreground">{item.text}</span>
              <div className="flex shrink-0 items-center gap-0 opacity-0 transition-opacity group-hover:opacity-100">
                <button
                  type="button"
                  aria-label="上移"
                  disabled={idx === 0}
                  onClick={() => handleMove(idx, -1)}
                  className="rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                >
                  <ChevronUp className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  aria-label="下移"
                  disabled={idx === outline.length - 1}
                  onClick={() => handleMove(idx, 1)}
                  className="rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                >
                  <ChevronDown className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  aria-label="移除"
                  onClick={() => handleRemove(idx)}
                  className="rounded p-0.5 text-destructive hover:text-destructive"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {adding ? (
        <div className="mt-2 flex gap-1">
          <Input
            value={newItem}
            onChange={(e) => setNewItem(e.target.value)}
            placeholder="新章节"
            className="h-7 flex-1 text-[12px]"
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAdd();
            }}
          />
          <Button size="sm" onClick={handleAdd} className="h-7 text-[11px]">
            确定
          </Button>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setAdding(true)}
          className="mt-2 h-6 text-[11px]"
        >
          <Plus className="mr-1 h-3 w-3" />
          添加章节
        </Button>
      )}
    </div>
  );
}
