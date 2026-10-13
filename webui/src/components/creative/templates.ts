import type { OutlineItem } from "./types";

export interface WritingTemplate {
  id: string;
  nameKey: string;
  descKey: string;
  outlineKeys: string[];
}

function outlineKeysFor(id: string, count: number): string[] {
  return Array.from({ length: count }, (_, index) => `creative.template.${id}.outline.${index}`);
}

export const WRITING_TEMPLATES: WritingTemplate[] = [
  {
    id: "tech-explainer",
    nameKey: "creative.template.tech-explainer.name",
    descKey: "creative.template.tech-explainer.desc",
    outlineKeys: outlineKeysFor("tech-explainer", 5),
  },
  {
    id: "source-reading",
    nameKey: "creative.template.source-reading.name",
    descKey: "creative.template.source-reading.desc",
    outlineKeys: outlineKeysFor("source-reading", 5),
  },
  {
    id: "practice-retro",
    nameKey: "creative.template.practice-retro.name",
    descKey: "creative.template.practice-retro.desc",
    outlineKeys: outlineKeysFor("practice-retro", 5),
  },
  {
    id: "learning-summary",
    nameKey: "creative.template.learning-summary.name",
    descKey: "creative.template.learning-summary.desc",
    outlineKeys: outlineKeysFor("learning-summary", 5),
  },
];

export function findTemplate(id: string | undefined): WritingTemplate | undefined {
  if (!id) return undefined;
  return WRITING_TEMPLATES.find((template) => template.id === id);
}

export function buildTemplateOutline(
  template: WritingTemplate,
  translate: (key: string) => string,
  makeId: () => string,
): OutlineItem[] {
  return template.outlineKeys.map((key, index) => ({
    id: makeId(),
    text: translate(key),
    order: index,
  }));
}
