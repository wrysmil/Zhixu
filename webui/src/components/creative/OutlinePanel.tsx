import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, X, ChevronUp, ChevronDown, Check } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type { OutlineItem } from "./types";

interface Props {
  outline: OutlineItem[];
  onChange: (outline: OutlineItem[]) => void;
}

/** `order` 只承载顺序，id 永远跟着条目走（A-04 稳定 ID）。 */
function sortedOutline(outline: OutlineItem[]): OutlineItem[] {
  return [...outline].sort((a, b) => a.order - b.order);
}

function resequence(items: OutlineItem[]): OutlineItem[] {
  return items.map((item, index) => ({ ...item, order: index }));
}

function newOutlineId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `o-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function OutlinePanel({ outline, onChange }: Props) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState(false);
  const [newItem, setNewItem] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");

  const items = useMemo(() => sortedOutline(outline), [outline]);

  const handleAdd = () => {
    const text = newItem.trim();
    if (!text) return;
    const next: OutlineItem = {
      id: newOutlineId(),
      text,
      order: items.length === 0 ? 0 : Math.max(...items.map((item) => item.order)) + 1,
    };
    onChange(resequence([...items, next]));
    setNewItem("");
    setAdding(false);
  };

  const handleRemove = (id: string) => {
    onChange(resequence(items.filter((item) => item.id !== id)));
  };

  const handleMove = (id: string, dir: -1 | 1) => {
    const index = items.findIndex((item) => item.id === id);
    const target = index + dir;
    if (index < 0 || target < 0 || target >= items.length) return;
    const arr = [...items];
    [arr[index], arr[target]] = [arr[target], arr[index]];
    // 只交换位置后重排 order，条目对象本身不变。
    onChange(resequence(arr));
  };

  const startEdit = (item: OutlineItem) => {
    setEditingId(item.id);
    setEditingText(item.text);
  };

  const submitEdit = () => {
    if (!editingId) return;
    const text = editingText.trim();
    if (!text) return;
    onChange(items.map((item) => (item.id === editingId ? { ...item, text } : item)));
    setEditingId(null);
    setEditingText("");
  };

  return (
    <div>
      {items.length === 0 ? (
        <p
          role="status"
          data-testid="outline-empty"
          className="py-4 text-center text-[11px] text-muted-foreground"
        >
          {t("creative.outline.empty", { defaultValue: "暂无提纲" })}
        </p>
      ) : (
        <ul className="space-y-1" role="list">
          {items.map((item, idx) => (
            <li
              key={item.id}
              data-outline-id={item.id}
              className="group rounded-control border border-border bg-settings-surface px-2.5 py-2"
            >
              {editingId === item.id ? (
                <div className="flex items-center gap-1">
                  <Input
                    value={editingText}
                    data-testid="outline-edit-input"
                    aria-label={t("creative.outline.field.text", { defaultValue: "提纲要点" })}
                    onChange={(e) => setEditingText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") submitEdit();
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    className="h-7 flex-1 text-[12px]"
                  />
                  <Button
                    size="sm"
                    data-testid="outline-edit-submit"
                    onClick={submitEdit}
                    className="h-7 text-[11px]"
                  >
                    <Check className="mr-1 h-3 w-3" />
                    {t("creative.outline.save", { defaultValue: "确定" })}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    data-testid="outline-edit-cancel"
                    onClick={() => setEditingId(null)}
                    className="h-7 text-[11px]"
                  >
                    {t("creative.outline.cancel", { defaultValue: "取消" })}
                  </Button>
                </div>
              ) : (
                <div className="flex items-start gap-1.5">
                  <span className="mt-0.5 shrink-0 text-[10px] font-medium text-muted-foreground">
                    {idx + 1}
                  </span>
                  <button
                    type="button"
                    data-testid={`outline-edit-${item.id}`}
                    onClick={() => startEdit(item)}
                    className="flex-1 cursor-pointer text-left text-[12px] text-foreground hover:text-primary"
                  >
                    {item.text}
                  </button>
                  <div className="flex shrink-0 items-center gap-0.5">
                    <button
                      type="button"
                      data-testid={`outline-up-${item.id}`}
                      aria-label={t("creative.outline.moveUp", { defaultValue: "上移" })}
                      disabled={idx === 0}
                      onClick={() => handleMove(item.id, -1)}
                      className="cursor-pointer rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                    >
                      <ChevronUp className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      data-testid={`outline-down-${item.id}`}
                      aria-label={t("creative.outline.moveDown", { defaultValue: "下移" })}
                      disabled={idx === items.length - 1}
                      onClick={() => handleMove(item.id, 1)}
                      className="cursor-pointer rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                    >
                      <ChevronDown className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      data-testid={`outline-remove-${item.id}`}
                      aria-label={t("creative.outline.remove", { defaultValue: "移除章节" })}
                      onClick={() => handleRemove(item.id)}
                      className="cursor-pointer rounded p-0.5 text-destructive hover:text-destructive"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {adding ? (
        <div className="mt-2 flex gap-1">
          <Input
            value={newItem}
            data-testid="outline-add-input"
            aria-label={t("creative.outline.field.text", { defaultValue: "提纲要点" })}
            onChange={(e) => setNewItem(e.target.value)}
            placeholder={t("creative.outline.newPlaceholder", { defaultValue: "新章节" })}
            className="h-7 flex-1 text-[12px]"
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAdd();
            }}
          />
          <Button size="sm" data-testid="outline-add-submit" onClick={handleAdd} className="h-7 text-[11px]">
            {t("creative.outline.addConfirm", { defaultValue: "确定" })}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            data-testid="outline-add-cancel"
            onClick={() => setAdding(false)}
            className="h-7 text-[11px]"
          >
            {t("creative.outline.cancel", { defaultValue: "取消" })}
          </Button>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          data-testid="outline-add-open"
          onClick={() => setAdding(true)}
          className="mt-2 h-6 text-[11px]"
        >
          <Plus className="mr-1 h-3 w-3" />
          {t("creative.outline.add", { defaultValue: "添加章节" })}
        </Button>
      )}
    </div>
  );
}
