export type ArticleStatus = "idea" | "draft" | "review" | "done";

export type MaterialType = "note" | "text" | "code" | "link";

export interface OutlineItem {
  id: string;
  text: string;
  order: number;
}

export interface Material {
  id: string;
  type: MaterialType;
  title: string;
  content: string;
  url?: string;
  source?: string;
  note?: string;
  language?: string;
  included: boolean;
  order: number;
  createdAt: string;
  updatedAt: string;
}

export interface ArticleBrief {
  goal?: string;
  audience?: string;
  style?: string;
}

export interface Suggestion {
  articleId: string;
  baseRevision: number;
  target: "outline" | "body" | "materials";
  label: string;
  before: string;
  after: string;
}

export interface Article {
  id: string;
  title: string;
  writingType: string;
  templateId?: string;
  status: ArticleStatus;
  summary: string;
  brief: ArticleBrief;
  body: string;
  outline: OutlineItem[];
  materials: Material[];
  tags: string[];
  revision: number;
  createdAt: string;
  updatedAt: string;
}

export type ArticleSummaryView = Pick<
  Article,
  | "id"
  | "title"
  | "summary"
  | "status"
  | "writingType"
  | "tags"
  | "revision"
  | "updatedAt"
>;

export const ARTICLE_STATUSES: ArticleStatus[] = [
  "idea",
  "draft",
  "review",
  "done",
];

export const MATERIAL_TYPES: MaterialType[] = ["note", "text", "code", "link"];

export const ARTICLE_STATUS_LABEL_KEY: Record<ArticleStatus, string> = {
  idea: "creative.status.idea",
  draft: "creative.status.draft",
  review: "creative.status.review",
  done: "creative.status.done",
};

export const MATERIAL_TYPE_LABEL_KEY: Record<MaterialType, string> = {
  note: "creative.material.type.note",
  text: "creative.material.type.text",
  code: "creative.material.type.code",
  link: "creative.material.type.link",
};
