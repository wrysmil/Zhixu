/**
 * Tiny structured-Markdown codec for the new IdentityPage form.
 *
 * The previous IdentityView exposed SOUL.md as a raw text editor; the redesigned
 * page translates between the friendly form fields and a stable Markdown body
 * that the existing backend can still consume. The format is intentionally
 * simple — sections are `## <heading>` followed by lines of plain text — so a
 * user can copy/paste content into the advanced editor without surprises.
 */

export type ToneKey = "friendly" | "professional" | "concise" | "detailed";
export type LanguageKey = "zh" | "en" | "auto";

export interface IdentityFormState {
  name: string;
  role: string;
  tone: ToneKey;
  language: LanguageKey;
  rules: string;
  notes: string;
}

export const DEFAULT_FORM_STATE: IdentityFormState = {
  name: "",
  role: "",
  tone: "friendly",
  language: "auto",
  rules: "",
  notes: "",
};

const TONE_HEADING = "说话风格";
const LANGUAGE_HEADING = "使用语言";

const TONE_TO_HEADING: Record<ToneKey, string> = {
  friendly: "友好",
  professional: "专业",
  concise: "简短",
  detailed: "详细",
};

const TONE_TO_HEADING_EN: Record<ToneKey, string> = {
  friendly: "Friendly",
  professional: "Professional",
  concise: "Concise",
  detailed: "Detailed",
};

const LANGUAGE_TO_HEADING: Record<LanguageKey, string> = {
  zh: "中文",
  en: "English",
  auto: "跟随用户",
};

const LANGUAGE_TO_HEADING_EN: Record<LanguageKey, string> = {
  zh: "Chinese",
  en: "English",
  auto: "Match the user",
};

function detectLanguage(text: string): "zh" | "en" {
  return /[\u4e00-\u9fa5]/.test(text) ? "zh" : "en";
}

function stripHeading(value: string): string {
  return value.replace(/^#+\s*/, "").trim();
}

interface ParsedSection {
  heading: string;
  body: string;
}

function parseSections(markdown: string): ParsedSection[] {
  const lines = markdown.split(/\r?\n/);
  const sections: ParsedSection[] = [];
  let current: ParsedSection | null = null;

  for (const line of lines) {
    const headingMatch = line.match(/^#{2,}\s*(.+?)\s*$/);
    if (headingMatch) {
      if (current) sections.push(current);
      current = { heading: stripHeading(headingMatch[1]), body: "" };
      continue;
    }
    if (current) {
      current.body = current.body ? `${current.body}\n${line}` : line;
    } else {
      const trimmed = line.trim();
      if (trimmed) {
        current = { heading: "", body: trimmed };
      }
    }
  }
  if (current) sections.push(current);
  return sections.map((section) => ({ ...section, body: section.body.trim() }));
}

/** Pull the first non-empty line that starts with `# ` (markdown H1). */
export function readDisplayName(markdown: string): string {
  const lines = markdown.split(/\r?\n/);
  for (const line of lines) {
    const match = line.match(/^#\s+(.+?)\s*$/);
    if (match) return match[1].trim();
  }
  return "";
}

function findSection(sections: ParsedSection[], headings: string[]): string {
  for (const section of sections) {
    if (headings.some((heading) => section.heading.toLowerCase() === heading.toLowerCase())) {
      return section.body;
    }
  }
  return "";
}

const ROLE_HEADINGS_ZH = ["我的角色", "角色", "职责"];
const ROLE_HEADINGS_EN = ["my role", "role", "what i do"];
const RULES_HEADINGS_ZH = ["必须遵守的规则", "规则", "原则"];
const RULES_HEADINGS_EN = ["rules", "principles", "boundaries"];
const NOTES_HEADINGS_ZH = ["其他说明", "补充", "备注"];
const NOTES_HEADINGS_EN = ["notes", "additional notes"];

export function parseForm(markdown: string): IdentityFormState {
  const sections = parseSections(markdown);
  const lang = detectLanguage(markdown);
  const name = readDisplayName(markdown);
  const role = findSection(sections, lang === "zh" ? ROLE_HEADINGS_ZH : ROLE_HEADINGS_EN);
  const toneBody = findSection(sections, lang === "zh" ? [TONE_HEADING] : ["tone", "style"]);
  const langBody = findSection(sections, lang === "zh" ? [LANGUAGE_HEADING] : ["language"]);
  const rules = findSection(sections, lang === "zh" ? RULES_HEADINGS_ZH : RULES_HEADINGS_EN);
  const notes = findSection(sections, lang === "zh" ? NOTES_HEADINGS_ZH : NOTES_HEADINGS_EN);

  const tone = (Object.keys(TONE_TO_HEADING) as ToneKey[]).find((key) =>
    lang === "zh"
      ? toneBody.includes(TONE_TO_HEADING[key])
      : toneBody.toLowerCase().includes(TONE_TO_HEADING_EN[key].toLowerCase()),
  ) ?? "friendly";

  const language = (Object.keys(LANGUAGE_TO_HEADING) as LanguageKey[]).find((key) =>
    lang === "zh"
      ? langBody.includes(LANGUAGE_TO_HEADING[key])
      : langBody.toLowerCase().includes(LANGUAGE_TO_HEADING_EN[key].toLowerCase()),
  ) ?? "auto";

  return {
    name,
    role,
    tone,
    language,
    rules,
    notes,
  };
}

interface ComposeOptions {
  /** Force language for the headings even when the body looks like another language. */
  language: "zh" | "en";
}

function toneHeading(lang: "zh" | "en", tone: ToneKey): string {
  return lang === "zh" ? TONE_TO_HEADING[tone] : TONE_TO_HEADING_EN[tone];
}

function languageHeading(lang: "zh" | "en", language: LanguageKey): string {
  return lang === "zh" ? LANGUAGE_TO_HEADING[language] : LANGUAGE_TO_HEADING_EN[language];
}

export function composeForm(state: IdentityFormState, options: ComposeOptions): string {
  const lang = options.language;
  const roleHeading = lang === "zh" ? "我的角色" : "My role";
  const toneLabel = lang === "zh" ? TONE_HEADING : "Tone";
  const langLabel = lang === "zh" ? LANGUAGE_HEADING : "Language";
  const rulesHeading = lang === "zh" ? "必须遵守的规则" : "Rules to follow";
  const notesHeading = lang === "zh" ? "其他说明" : "Additional notes";

  const nameLine = state.name.trim() || (lang === "zh" ? "你的助手" : "Your Agent");
  const sections: string[] = [`# ${nameLine}`];

  const role = state.role.trim();
  if (role) sections.push(`## ${roleHeading}\n${role.trim()}`);

  sections.push(`## ${toneLabel}\n${toneHeading(lang, state.tone)}`);
  sections.push(`## ${langLabel}\n${languageHeading(lang, state.language)}`);

  const rules = state.rules.trim();
  if (rules) sections.push(`## ${rulesHeading}\n${rules}`);

  const notes = state.notes.trim();
  if (notes) sections.push(`## ${notesHeading}\n${notes}`);

  return `${sections.join("\n\n")}\n`;
}

/** Detect the dominant language to use for the Markdown headings on save. */
export function chooseLanguage(state: IdentityFormState, fallback: "zh" | "en" = "zh"): "zh" | "en" {
  if (state.language === "zh") return "zh";
  if (state.language === "en") return "en";
  const haystack = `${state.name}\n${state.role}\n${state.rules}\n${state.notes}`;
  return detectLanguage(haystack) || fallback;
}
