import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, X, Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import type { Material, MaterialType } from "./types";
import { MATERIAL_TYPES, MATERIAL_TYPE_LABEL_KEY } from "./types";
import { isSafeUrl } from "./article-model";

interface Props {
  materials: Material[];
  onChange: (materials: Material[]) => void;
}

interface FormState {
  type: MaterialType;
  title: string;
  content: string;
  url: string;
  source: string;
  note: string;
  language: string;
}

const EMPTY_FORM: FormState = {
  type: "note",
  title: "",
  content: "",
  url: "",
  source: "",
  note: "",
  language: "",
};

/** 契约 §5.3：`Material.type=link` 的 `url` 必须以 http(s) 开头，服务端还会再校验一次。 */
function isHttpUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) return false;
  return isSafeUrl(trimmed);
}

function newMaterialId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `m-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 列表按 `order` 呈现；`order` 在每次变更后重新收敛为 0..n-1，id 始终不变。 */
function sortedMaterials(materials: Material[]): Material[] {
  return [...materials].sort((a, b) => a.order - b.order);
}

function resequence(materials: Material[]): Material[] {
  return materials.map((item, index) => ({ ...item, order: index }));
}

export function MaterialPanel({ materials, onChange }: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Material | null>(null);

  const items = useMemo(() => sortedMaterials(materials), [materials]);

  const openAdd = () => {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (material: Material) => {
    setForm({
      type: material.type,
      title: material.title,
      content: material.content,
      url: material.url ?? (material.type === "link" ? material.content : ""),
      source: material.source ?? "",
      note: material.note ?? "",
      language: material.language ?? "",
    });
    setEditingId(material.id);
    setFormError(null);
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
  };

  const handleSubmit = () => {
    const title = form.title.trim();
    if (!title) {
      setFormError(t("creative.material.error.title", { defaultValue: "请填写素材标题" }));
      return;
    }
    // `code` 保留换行与缩进，不做 trim；其余类型去掉首尾空白。
    const content = form.type === "code" ? form.content : form.content.trim();
    const url = form.url.trim();
    if (form.type === "link") {
      if (!isHttpUrl(url)) {
        setFormError(
          t("creative.material.error.linkUrl", {
            defaultValue: "链接必须以 http:// 或 https:// 开头",
          }),
        );
        return;
      }
    } else if (!content) {
      setFormError(t("creative.material.error.content", { defaultValue: "请填写素材内容" }));
      return;
    }

    const at = new Date().toISOString();
    const previous = editingId ? items.find((item) => item.id === editingId) : undefined;
    const next: Material = {
      id: previous?.id ?? newMaterialId(),
      type: form.type,
      title,
      content,
      included: previous?.included ?? true,
      order: previous?.order ?? (items.length === 0 ? 0 : Math.max(...items.map((i) => i.order)) + 1),
      createdAt: previous?.createdAt ?? at,
      updatedAt: at,
    };
    if (form.type === "link") next.url = url;
    const source = form.source.trim();
    if (source) next.source = source;
    const note = form.note.trim();
    if (note) next.note = note;
    const language = form.language.trim();
    if (form.type === "code" && language) next.language = language;

    const merged = previous
      ? items.map((item) => (item.id === previous.id ? next : item))
      : [...items, next];
    onChange(resequence(merged));
    closeForm();
  };

  const confirmDelete = () => {
    if (!pendingDelete) return;
    onChange(resequence(items.filter((item) => item.id !== pendingDelete.id)));
    setPendingDelete(null);
  };

  const toggleIncluded = (id: string) => {
    onChange(
      items.map((item) =>
        item.id === id ? { ...item, included: !item.included, updatedAt: new Date().toISOString() } : item,
      ),
    );
  };

  const inputClass = "h-7 text-[12px]";
  const labelClass = "text-[10px] font-medium text-muted-foreground";

  return (
    <div>
      {items.length === 0 && !formOpen ? (
        <p
          role="status"
          data-testid="material-empty"
          className="py-4 text-center text-[11px] text-muted-foreground"
        >
          {t("creative.material.empty", { defaultValue: "暂无素材" })}
        </p>
      ) : (
        <ul className="space-y-2" role="list">
          {items.map((m) => (
            <li
              key={m.id}
              data-material-id={m.id}
              className={cn(
                "rounded-control border border-border bg-settings-surface p-2.5",
                !m.included && "opacity-60",
              )}
            >
              <div className="mb-1 flex items-center justify-between gap-1">
                <span className="text-[10px] font-medium text-primary">
                  {t(MATERIAL_TYPE_LABEL_KEY[m.type])}
                </span>
                <div className="flex items-center gap-0.5">
                  <button
                    type="button"
                    data-testid={`material-edit-${m.id}`}
                    aria-label={t("creative.material.edit", { defaultValue: "编辑素材" })}
                    onClick={() => openEdit(m)}
                    className="rounded p-0.5 text-muted-foreground hover:text-foreground"
                  >
                    <Pencil className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    data-testid={`material-remove-${m.id}`}
                    aria-label={t("creative.material.remove", { defaultValue: "移除素材" })}
                    onClick={() => setPendingDelete(m)}
                    className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              </div>
              <p className="text-[12px] font-medium text-foreground">{m.title}</p>
              {m.type === "code" ? (
                <pre
                  data-testid={`material-content-${m.id}`}
                  className="mt-0.5 overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/50 px-1.5 py-1 text-[11px] leading-snug text-muted-foreground"
                >
                  {m.content}
                </pre>
              ) : (
                <p
                  data-testid={`material-content-${m.id}`}
                  className="mt-0.5 line-clamp-3 text-[11px] text-muted-foreground"
                >
                  {m.type === "link" ? (
                    <a
                      href={isSafeUrl(m.url ?? m.content) ? (m.url ?? m.content) : "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary underline"
                    >
                      {m.url || m.content}
                    </a>
                  ) : (
                    m.content
                  )}
                </p>
              )}
              {m.source ? (
                <p className="mt-0.5 truncate text-[10px] text-muted-foreground" title={m.source}>
                  {m.source}
                </p>
              ) : null}
              {m.note ? (
                <p className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground">{m.note}</p>
              ) : null}
              <label className="mt-1.5 flex cursor-pointer items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={m.included}
                  data-testid={`material-toggle-${m.id}`}
                  onChange={() => toggleIncluded(m.id)}
                  className="h-3 w-3 shrink-0 cursor-pointer accent-primary"
                />
                <span className="text-[10px] text-muted-foreground">
                  {t("creative.material.included", { defaultValue: "本次协作可用" })}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}

      {formOpen ? (
        <div className="mt-3 space-y-2 rounded-control border border-border bg-background p-2.5">
          <div className="flex gap-1">
            {MATERIAL_TYPES.map((kind) => (
              <button
                key={kind}
                type="button"
                data-testid={`material-type-${kind}`}
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] cursor-pointer",
                  form.type === kind
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground",
                )}
                onClick={() => setForm((prev) => ({ ...prev, type: kind }))}
              >
                {t(MATERIAL_TYPE_LABEL_KEY[kind])}
              </button>
            ))}
          </div>

          <label className="block">
            <span className={labelClass}>
              {t("creative.material.field.title", { defaultValue: "标题" })}
            </span>
            <Input
              value={form.title}
              data-testid="material-title-input"
              onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
              placeholder={t("creative.material.field.title", { defaultValue: "标题" })}
              className={inputClass}
            />
          </label>

          {form.type === "link" ? (
            <label className="block">
              <span className={labelClass}>
                {t("creative.material.field.url", { defaultValue: "链接地址" })}
              </span>
              <Input
                value={form.url}
                data-testid="material-url-input"
                onChange={(e) => setForm((prev) => ({ ...prev, url: e.target.value }))}
                placeholder="https://..."
                className={inputClass}
              />
            </label>
          ) : null}

          <label className="block">
            <span className={labelClass}>
              {form.type === "link"
                ? t("creative.material.field.excerpt", { defaultValue: "摘录（可选）" })
                : t("creative.material.field.content", { defaultValue: "内容" })}
            </span>
            <textarea
              value={form.content}
              data-testid="material-content-input"
              onChange={(e) => setForm((prev) => ({ ...prev, content: e.target.value }))}
              placeholder={
                form.type === "code"
                  ? t("creative.material.field.codePlaceholder", { defaultValue: "粘贴代码，缩进会保留" })
                  : t("creative.material.field.contentPlaceholder", { defaultValue: "内容" })
              }
              className="w-full resize-none rounded-control border border-border bg-background p-2 text-[12px] outline-none"
              rows={form.type === "code" ? 4 : 2}
              spellCheck={form.type === "code" ? false : undefined}
            />
          </label>

          {form.type === "code" ? (
            <label className="block">
              <span className={labelClass}>
                {t("creative.material.field.language", { defaultValue: "语言（可选）" })}
              </span>
              <Input
                value={form.language}
                data-testid="material-language-input"
                onChange={(e) => setForm((prev) => ({ ...prev, language: e.target.value }))}
                placeholder="python"
                className={inputClass}
              />
            </label>
          ) : null}

          {form.type === "text" || form.type === "code" ? (
            <label className="block">
              <span className={labelClass}>
                {t("creative.material.field.source", { defaultValue: "来源（可选）" })}
              </span>
              <Input
                value={form.source}
                data-testid="material-source-input"
                onChange={(e) => setForm((prev) => ({ ...prev, source: e.target.value }))}
                className={inputClass}
              />
            </label>
          ) : null}

          <label className="block">
            <span className={labelClass}>
              {t("creative.material.field.note", { defaultValue: "备注（可选）" })}
            </span>
            <Input
              value={form.note}
              data-testid="material-note-input"
              onChange={(e) => setForm((prev) => ({ ...prev, note: e.target.value }))}
              className={inputClass}
            />
          </label>

          {formError ? (
            <p role="alert" data-testid="material-form-error" className="text-[11px] text-destructive">
              {formError}
            </p>
          ) : null}

          <div className="flex gap-1.5">
            <Button
              size="sm"
              data-testid="material-form-submit"
              onClick={handleSubmit}
              className="h-6 text-[11px]"
            >
              {editingId
                ? t("creative.material.save", { defaultValue: "保存修改" })
                : t("creative.material.addConfirm", { defaultValue: "添加" })}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              data-testid="material-form-cancel"
              onClick={closeForm}
              className="h-6 text-[11px]"
            >
              {t("creative.material.cancel", { defaultValue: "取消" })}
            </Button>
          </div>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          data-testid="material-add-open"
          onClick={openAdd}
          className="mt-2 h-6 text-[11px]"
        >
          <Plus className="mr-1 h-3 w-3" />
          {t("creative.material.add", { defaultValue: "添加素材" })}
        </Button>
      )}

      <AlertDialog open={!!pendingDelete} onOpenChange={(open) => (!open ? setPendingDelete(null) : undefined)}>
        <AlertDialogContent data-testid="material-delete-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("creative.material.confirm.title", { defaultValue: "删除这条素材？" })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("creative.material.confirm.description", {
                defaultValue: "删除只会影响这篇素材，正文与已保存的内容不会被改写。",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="material-delete-cancel">
              {t("creative.material.confirm.cancel", { defaultValue: "取消" })}
            </AlertDialogCancel>
            <AlertDialogAction
              data-testid="material-delete-confirm-action"
              onClick={confirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {t("creative.material.confirm.confirm", { defaultValue: "删除" })}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
