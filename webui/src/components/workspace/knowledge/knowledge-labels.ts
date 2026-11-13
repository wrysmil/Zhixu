import type { TFunction } from "i18next";

const MODE_LABEL_KEYS: Record<string, string> = {
  hybrid: "knowledge.modeHybrid",
  semantic: "knowledge.modeSemantic",
  keyword: "knowledge.modeKeyword",
};

const ROUTE_LABEL_KEYS: Record<string, string> = {
  vector: "knowledge.retrieval.routeVector",
  keyword: "knowledge.retrieval.routeKeyword",
  fts: "knowledge.retrieval.routeFts",
  graph: "knowledge.retrieval.routeGraph",
};

export function knowledgeModeLabel(t: TFunction, mode: string): string {
  const key = MODE_LABEL_KEYS[mode];
  return key ? t(key, { defaultValue: mode }) : mode;
}

export function knowledgeRouteLabel(t: TFunction, route: string): string {
  const key = ROUTE_LABEL_KEYS[route];
  return key ? t(key, { defaultValue: route }) : route;
}
