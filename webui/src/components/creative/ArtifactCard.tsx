// 六类协作产物卡（plan Task 12 / spec §12.3）。
//
// 边界口径（逐条对着 spec §12.3 表格实现）：
// - 选题角度：只更新协作目标，写文章 brief 是另一件事。
// - 资料卡：展示摘录与来源，加入素材是**显式作者动作**，不自动插入正文。
// - 提纲卡：可就地编辑；主操作是「保存提纲并继续」（原子确认交接 + 保存文章提纲）。
// - 初稿卡：先「查看并插入」，看完再显式插入；空正文才允许整篇替换。
// - 改写卡：先展示原文/改写与理由，采纳前作者读得到校验依据。
// - 审稿卡：默认突出 ≤5 项，剩余展开；不用「AI 味分数」替代具体问题。
//
// 卡片**从不自己写文章**：所有写动作都是回调，由集成层（WU-13）接管。
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, FileText, Lightbulb, ListTree, Quote, Repeat } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { CollabArtifact, CollabArtifactSummary } from "@/lib/writing-collab-api";
import {
  artifactActionLabelKey,
  artifactLabelKey,
  PARTNER_DEFAULTS,
} from "./collab-labels";

/** 审稿卡默认最多突出几条问题（spec §12.3）。 */
export const REVIEW_VISIBLE_LIMIT = 5;

export interface OutlinePoint {
  id: string;
  text: string;
  order: number;
}

export interface MaterialRef {
  id: string;
  title: string;
  excerpt?: string;
  source?: string;
  url?: string;
}

export interface ArticleAngle {
  title: string;
  rationale?: string;
  audience?: string;
}

export interface DraftSection {
  id: string;
  title?: string;
  body: string;
}

export interface ReviewIssue {
  id: string;
  severity?: string;
  title: string;
  detail?: string;
  location?: string;
  suggestion?: string;
}

export interface ArtifactContent {
  angles: ArticleAngle[];
  materials: MaterialRef[];
  outline: OutlinePoint[];
  sections: DraftSection[];
  before: string;
  after: string;
  summary: string;
  notes: string;
  issues: ReviewIssue[];
  empty: boolean;
}

export interface ArtifactCardProps {
  artifact: CollabArtifact | CollabArtifactSummary;
  /** BCP-47 语言标签；卡片内部只有可访问名使用它。 */
  locale?: string;
  busy?: boolean;
  defaultExpanded?: boolean;
  onUseAngle?(angle: ArticleAngle): void;
  onAddMaterial?(material: MaterialRef): void;
  onConfirmOutline?(outline: OutlinePoint[]): void;
  onInsertDraft?(payload: { sections: DraftSection[]; replaceWhole: boolean }): void;
  /** 改写卡把**完整建议载荷**交回：采纳要过原文与版本校验（契约 §3）。 */
  onApplyRewrite?(payload: {
    before: string;
    after: string;
    summary: string;
    notes: string;
  }): void;
  onRejectRewrite?(): void;
  onRetryRewrite?(): void;
  onApplyIssue?(issue: ReviewIssue): void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * 在 content（可能被 `data` / `content` / 自身的一层包装裹住）里找数组。
 * envelope 只承诺「kind/content/sourceIds/issues」，这里做宽容解析：
 * 认不出就退化成只显示摘要，不猜内容。
 */
function readArray(source: Record<string, unknown>, key: string): unknown[] {
  const direct = source[key];
  if (Array.isArray(direct)) return direct;
  if (isRecord(direct)) {
    const nested = direct[key] ?? direct.items ?? direct.data;
    if (Array.isArray(nested)) return nested;
  }
  for (const candidate of ["data", "content", "artifact"]) {
    const inner = source[candidate];
    if (isRecord(inner)) {
      const found = readArray(inner, key);
      if (found.length) return found;
    }
  }
  return [];
}

function readString(source: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value) return value;
  }
  for (const candidate of ["data", "content"]) {
    const inner = source[candidate];
    if (isRecord(inner)) {
      const found = readString(inner, ...keys);
      if (found) return found;
    }
  }
  return "";
}

function parsePoints(raw: unknown[]): OutlinePoint[] {
  const points: OutlinePoint[] = [];
  raw.forEach((entry, index) => {
    if (typeof entry === "string") {
      points.push({ id: `point-${index + 1}`, text: entry, order: index });
      return;
    }
    if (!isRecord(entry)) return;
    const text = str(entry.text) || str(entry.title);
    if (!text) return;
    points.push({
      id: str(entry.id) || `point-${index + 1}`,
      text,
      order: typeof entry.order === "number" ? entry.order : index,
    });
  });
  return points.sort((left, right) => left.order - right.order);
}

/** 解析产物内容；任何字段缺了都退化成空，不抛错。 */
export function parseArtifactContent(content: unknown): ArtifactContent {
  const source = isRecord(content) ? content : {};
  const angles = readArray(source, "angles").flatMap((entry) => {
    if (!isRecord(entry)) return [];
    const title = str(entry.title) || str(entry.angle) || str(entry.text);
    if (!title) return [];
    return [{
      title,
      rationale: str(entry.rationale) || str(entry.reason),
      audience: str(entry.audience),
    }];
  });
  const materials = readArray(source, "materials")
    .concat(readArray(source, "material_list"))
    .flatMap((entry) => {
      if (!isRecord(entry)) return [];
      const title = str(entry.title) || str(entry.name);
      if (!title) return [];
      return [{
        id: str(entry.id),
        title,
        excerpt: str(entry.excerpt) || str(entry.content),
        source: str(entry.source) || str(entry.source_name),
        url: str(entry.url),
      }];
    });
  const sections = readArray(source, "sections").flatMap((entry) => {
    if (typeof entry === "string") return [{ id: "", title: "", body: entry }];
    if (!isRecord(entry)) return [];
    const body = str(entry.body) || str(entry.content) || str(entry.text);
    if (!body) return [];
    return [{ id: str(entry.id), title: str(entry.title), body }];
  });
  const issues = readArray(source, "issues").flatMap((entry) => {
    if (typeof entry === "string") return [{ id: "", title: entry }];
    if (!isRecord(entry)) return [];
    const title = str(entry.title) || str(entry.issue) || str(entry.detail);
    if (!title) return [];
    return [{
      id: str(entry.id),
      severity: str(entry.severity),
      title,
      detail: str(entry.detail) === title ? "" : str(entry.detail),
      location: str(entry.location) || str(entry.anchor),
      suggestion: str(entry.suggestion) || str(entry.fix),
    }];
  });
  const before = readString(source, "before", "beforeText", "before_text");
  const after = readString(source, "after", "afterText", "after_text");
  const summary = readString(source, "summary", "reason");
  const outline = parsePoints(readArray(source, "outline").concat(readArray(source, "points")));
  return {
    angles,
    materials,
    outline,
    sections,
    before,
    after,
    summary,
    notes: readString(source, "notes"),
    issues,
    empty:
      !angles.length
      && !materials.length
      && !outline.length
      && !sections.length
      && !issues.length
      && !before
      && !after,
  };
}

export function artifactSummaryText(
  artifact: CollabArtifact | CollabArtifactSummary,
): string {
  return "summary" in artifact ? artifact.summary : "";
}

export function ArtifactCard({
  artifact,
  locale = "en",
  busy = false,
  defaultExpanded = false,
  onUseAngle,
  onAddMaterial,
  onConfirmOutline,
  onInsertDraft,
  onApplyRewrite,
  onRejectRewrite,
  onRetryRewrite,
  onApplyIssue,
}: ArtifactCardProps) {
  const { t } = useTranslation();
  const label = t(artifactLabelKey(artifact.kind), {
    defaultValue: artifact.kind,
  });
  const full = "content" in artifact ? artifact : null;
  const parsed = useMemo(() => parseArtifactContent(full?.content), [full?.content]);

  // 就地编辑只属于当前条目：列表用 `${id}:${version}` 做 key，换版本即重挂载。
  const [points, setPoints] = useState<OutlinePoint[]>(parsed.outline);
  const [draftVisible, setDraftVisible] = useState(defaultExpanded);
  const [issuesExpanded, setIssuesExpanded] = useState(false);

  // 产物先是摘要、随后才补到全文：作者还没动过提纲时跟着服务端内容走，
  // 一旦作者编辑过就地保留，不让异步刷新覆盖手上的输入。
  const [edited, setEdited] = useState(false);
  useEffect(() => {
    if (!edited) setPoints(parsed.outline);
  }, [edited, parsed.outline]);

  const actionable = artifact.status !== "pending" && artifact.status !== "failed";
  const primaryLabel = t(artifactActionLabelKey(artifact.kind), {
    defaultValue: PARTNER_DEFAULTS[artifactActionLabelKey(artifact.kind)] ?? label,
  });

  const heading = (
    <header className="flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-1.5">
        <KindIcon kind={artifact.kind} />
        <h3 className="truncate text-[12px] font-semibold text-foreground">{label}</h3>
      </div>
      <span className="shrink-0 text-[10.5px] text-muted-foreground">
        v{artifact.version}
      </span>
    </header>
  );

  const banner = artifact.status === "pending" ? (
    <p data-artifact-state="pending" className="mt-1 text-[11px] text-muted-foreground">
      {t("creative.partner.ui.pending", { defaultValue: PARTNER_DEFAULTS["creative.partner.ui.pending"] })}
    </p>
  ) : artifact.status === "failed" ? (
    <p data-artifact-state="failed" className="mt-1 text-[11px] text-destructive">
      {t("creative.partner.ui.failed", { defaultValue: PARTNER_DEFAULTS["creative.partner.ui.failed"] })}
    </p>
  ) : null;

  const body = () => {
    switch (artifact.kind) {
      case "brief":
        return (
          <div className="mt-2 space-y-1.5">
            {parsed.angles.map((angle, index) => (
              <div
                key={`${angle.title}-${index}`}
                className="rounded-control border border-border/60 px-2 py-1.5"
              >
                <p className="text-[11.5px] font-medium text-foreground">{angle.title}</p>
                {angle.rationale ? (
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                    {angle.rationale}
                  </p>
                ) : null}
              </div>
            ))}
            {actionable ? (
              <Button
                size="sm"
                className="h-7 text-[11px]"
                disabled={busy || !onUseAngle}
                onClick={() => onUseAngle?.(parsed.angles[0] ?? { title: label })}
              >
                {primaryLabel}
              </Button>
            ) : null}
          </div>
        );

      case "material_list":
        return (
          <div className="mt-2 space-y-1.5">
            {parsed.materials.map((material, index) => (
              <div
                key={`${material.title}-${index}`}
                data-material={material.id || index}
                className="rounded-control border border-border/60 px-2 py-1.5"
              >
                <p className="text-[11.5px] font-medium text-foreground">{material.title}</p>
                {material.excerpt ? (
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                    {material.excerpt}
                  </p>
                ) : null}
                {material.source || material.url ? (
                  <p className="mt-0.5 truncate text-[10.5px] text-muted-foreground">
                    {material.source || material.url}
                  </p>
                ) : null}
                {actionable ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="mt-1.5 h-6 text-[11px]"
                    disabled={busy || !onAddMaterial}
                    onClick={() => onAddMaterial?.(material)}
                  >
                    {primaryLabel}
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        );

      case "outline":
        return (
          <div className="mt-2 space-y-1.5">
            {points.map((point, index) => (
              <Input
                key={point.id || index}
                aria-label={t("creative.partner.ui.outlinePoint", {
                  defaultValue: PARTNER_DEFAULTS["creative.partner.ui.outlinePoint"],
                })}
                value={point.text}
                disabled={!actionable}
                onChange={(event) => {
                  const text = event.target.value;
                  setEdited(true);
                  setPoints((current) =>
                    current.map((entry, entryIndex) =>
                      entryIndex === index ? { ...entry, text } : entry,
                    ),
                  );
                }}
                className="h-7 text-[11.5px]"
              />
            ))}
            {actionable ? (
              <Button
                size="sm"
                className="h-7 text-[11px]"
                disabled={busy || !onConfirmOutline}
                onClick={() => onConfirmOutline?.(points)}
              >
                {primaryLabel}
              </Button>
            ) : null}
          </div>
        );

      case "draft":
        return (
          <div className="mt-2 space-y-1.5">
            {actionable ? (
              <Button
                size="sm"
                variant={draftVisible ? "outline" : "default"}
                className="h-7 text-[11px]"
                aria-expanded={draftVisible}
                onClick={() => setDraftVisible((visible) => !visible)}
              >
                {primaryLabel}
                {draftVisible ? (
                  <ChevronDown className="ml-1 h-3 w-3" aria-hidden="true" />
                ) : (
                  <ChevronRight className="ml-1 h-3 w-3" aria-hidden="true" />
                )}
              </Button>
            ) : null}
            {draftVisible
              ? parsed.sections.map((section, index) => (
                  <div
                    key={section.id || index}
                    className="rounded-control border border-border/60 px-2 py-1.5"
                  >
                    {section.title ? (
                      <p className="text-[11.5px] font-medium text-foreground">{section.title}</p>
                    ) : null}
                    <pre className="mt-0.5 whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-muted-foreground">
                      {section.body}
                    </pre>
                    {actionable && onInsertDraft ? (
                      <Button
                        size="sm"
                        variant="outline"
                        className="mt-1.5 h-6 text-[11px]"
                        disabled={busy}
                        onClick={() => onInsertDraft({ sections: [section], replaceWhole: false })}
                      >
                        {t("creative.partner.ui.insertSection", {
                          defaultValue: PARTNER_DEFAULTS["creative.partner.ui.insertSection"],
                        })}
                      </Button>
                    ) : null}
                  </div>
                ))
              : null}
            {draftVisible && actionable && onInsertDraft ? (
              <Button
                size="sm"
                className="h-7 text-[11px]"
                disabled={busy}
                onClick={() => onInsertDraft({ sections: parsed.sections, replaceWhole: false })}
              >
                {t("creative.partner.ui.insertIntoArticle", {
                  defaultValue: PARTNER_DEFAULTS["creative.partner.ui.insertIntoArticle"],
                })}
              </Button>
            ) : null}
          </div>
        );

      case "rewrite":
        return (
          <div className="mt-2 space-y-1.5">
            <div>
              <p className="text-[10.5px] font-medium text-muted-foreground">
                {t("creative.partner.ui.before", {
                  defaultValue: PARTNER_DEFAULTS["creative.partner.ui.before"],
                })}
              </p>
              <pre className="mt-0.5 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-1.5 font-sans text-[11px] leading-relaxed text-muted-foreground">
                {parsed.before}
              </pre>
            </div>
            <div>
              <p className="text-[10.5px] font-medium text-accent-foreground">
                {t("creative.partner.ui.after", {
                  defaultValue: PARTNER_DEFAULTS["creative.partner.ui.after"],
                })}
              </p>
              <pre
                data-rewrite-after
                className="mt-0.5 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-accent p-1.5 font-sans text-[11px] leading-relaxed text-foreground"
              >
                {parsed.after}
              </pre>
            </div>
            {parsed.summary ? (
              <p className="text-[11px] leading-relaxed text-muted-foreground">{parsed.summary}</p>
            ) : null}
            {parsed.notes ? (
              <p className="text-[10.5px] leading-relaxed text-muted-foreground">{parsed.notes}</p>
            ) : null}
            {actionable ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <Button
                  size="sm"
                  className="h-7 text-[11px]"
                  disabled={busy || !onApplyRewrite}
                  onClick={() =>
                    onApplyRewrite?.({
                      before: parsed.before,
                      after: parsed.after,
                      summary: parsed.summary,
                      notes: parsed.notes,
                    })
                  }
                >
                  {primaryLabel}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-[11px]"
                  disabled={busy || !onRejectRewrite}
                  onClick={() => onRejectRewrite?.()}
                >
                  {t("creative.partner.ui.rejectRewrite", {
                    defaultValue: PARTNER_DEFAULTS["creative.partner.ui.rejectRewrite"],
                  })}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-[11px]"
                  disabled={busy || !onRetryRewrite}
                  onClick={() => onRetryRewrite?.()}
                >
                  <Repeat className="mr-1 h-3 w-3" aria-hidden="true" />
                  {t("creative.partner.ui.retryRewrite", {
                    defaultValue: PARTNER_DEFAULTS["creative.partner.ui.retryRewrite"],
                  })}
                </Button>
              </div>
            ) : null}
          </div>
        );

      case "review":
      default: {
        const visibleIssues = issuesExpanded
          ? parsed.issues
          : parsed.issues.slice(0, REVIEW_VISIBLE_LIMIT);
        const hidden = Math.max(parsed.issues.length - REVIEW_VISIBLE_LIMIT, 0);
        return (
          <div className="mt-2 space-y-1.5">
            {actionable && parsed.issues.length ? (
              <Button
                size="sm"
                variant={issuesExpanded ? "outline" : "default"}
                className="h-7 text-[11px]"
                aria-expanded={issuesExpanded}
                onClick={() => setIssuesExpanded((expanded) => !expanded)}
              >
                {primaryLabel}
              </Button>
            ) : null}
            <ul className="space-y-1">
              {visibleIssues.map((issue, index) => (
                <li
                  key={issue.id || index}
                  data-issue-severity={issue.severity || "unspecified"}
                  className="rounded-control border border-border/60 px-2 py-1.5"
                >
                  <p className="text-[11.5px] font-medium text-foreground">{issue.title}</p>
                  {issue.detail ? (
                    <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                      {issue.detail}
                    </p>
                  ) : null}
                  {issue.location ? (
                    <p className="mt-0.5 text-[10.5px] text-muted-foreground">{issue.location}</p>
                  ) : null}
                  {onApplyIssue ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="mt-1 h-6 text-[11px]"
                      disabled={busy}
                      onClick={() => onApplyIssue(issue)}
                    >
                      {t("creative.partner.artifactAction.applyRewrite", {
                        defaultValue: PARTNER_DEFAULTS["creative.partner.artifactAction.applyRewrite"],
                      })}
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
            {hidden > 0 && !issuesExpanded ? (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 text-[11px]"
                onClick={() => setIssuesExpanded(true)}
              >
                {t("creative.partner.ui.showMoreIssues", {
                  count: hidden,
                  defaultValue: PARTNER_DEFAULTS["creative.partner.ui.showMoreIssues"],
                })}
              </Button>
            ) : null}
            {issuesExpanded && parsed.issues.length > REVIEW_VISIBLE_LIMIT ? (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 text-[11px]"
                onClick={() => setIssuesExpanded(false)}
              >
                {t("creative.partner.ui.showFewerIssues", {
                  defaultValue: PARTNER_DEFAULTS["creative.partner.ui.showFewerIssues"],
                })}
              </Button>
            ) : null}
          </div>
        );
      }
    }
  };

  return (
    <section
      data-artifact-kind={artifact.kind}
      data-artifact-status={artifact.status}
      aria-label={`${label} ${artifact.version}`}
      lang={locale}
      className={cn(
        "rounded-control border border-border bg-settings-surface px-2.5 py-2",
        artifact.status === "pending" && "border-amber-500/40",
        artifact.status === "failed" && "border-destructive/40",
      )}
    >
      {heading}
      {banner}
      {parsed.empty && !parsed.sections.length ? (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          {artifactSummaryText(artifact)
            || t("creative.partner.ui.empty", {
              defaultValue: PARTNER_DEFAULTS["creative.partner.ui.empty"],
            })}
        </p>
      ) : null}
      {body()}
    </section>
  );
}

function KindIcon({ kind }: { kind: CollabArtifact["kind"] }) {
  const className = "h-3.5 w-3.5 shrink-0 text-muted-foreground";
  switch (kind) {
    case "brief":
      return <Lightbulb className={className} aria-hidden="true" />;
    case "material_list":
      return <Quote className={className} aria-hidden="true" />;
    case "outline":
      return <ListTree className={className} aria-hidden="true" />;
    default:
      return <FileText className={className} aria-hidden="true" />;
  }
}
