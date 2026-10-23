---
artifact: decision-record
route: api-and-interface-design
skills: [api-and-interface-design]
skills_evidence: [C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/api-and-interface-design/SKILL.md]
source: [.ai-runtime-artifacts/specs/2026-10-19-appearance-customization-spec.md]
created_at: 2026-10-19
status: approved
approved: true
approval_evidence: 用户「直接实现把」授权范围内的内部接口约定
---

# 外观自定义内部接口

本文件是实现时的 WU 共享契约，不新增外部 HTTP API。允许实现必要的私有辅助函数，不改变以下导出。

## 模型（webui/src/lib/appearance.ts）

```ts
type Theme = "light" | "dark";
type ThemeMode = Theme | "system";
type PresetId = "zhixu" | "graphite" | "ocean" | "sand";
type ColorField = "accent" | "background" | "foreground";
type Palette = Record<ColorField, string>; // #RRGGBB
type FontChoice = { id: string; family: string }; // family 仅用于 custom
interface AppearancePreferences {
  schemaVersion: 1;
  mode: ThemeMode;
  presetId: PresetId | "custom";
  custom: { basePreset: PresetId; light: Partial<Palette>; dark: Partial<Palette> } | null;
  uiFont: FontChoice; // system, sans, serif, custom
  codeFont: FontChoice; // default, consolas, cascadia, jetbrains, custom
}
interface AppearanceController {
  preferences: AppearancePreferences;
  theme: Theme;
  storageError: boolean;
  setMode(mode: ThemeMode): void;
  setPreset(id: PresetId | "custom"): void;
  setColor(field: ColorField, hex: string): void; // 当前解析模式
  resetColor(field: ColorField): void;
  setFont(kind: "ui" | "code", choice: FontChoice): void;
  resetAppearance(): void;
}
```

导出 Theme、ThemeMode、PresetId、ColorField、Palette、FontChoice、AppearancePreferences、AppearanceController；以及：

- `APPEARANCE_STORAGE_KEY = "zhixu-webui.appearance"`。
- `APPEARANCE_APPLIED_EVENT = "zhixu-webui.appearance-applied"`。
- `DEFAULT_APPEARANCE`：system、zhixu、custom=null、uiFont={id:"system",family:""}、codeFont={id:"default",family:""}。
- `THEME_PRESETS: Array<{id: PresetId; name: string; light: Palette; dark: Palette}>`，name 为英文稳定回退名，UI 负责翻译。
- `normalizeHex(value: string): string | null`、`normalizeFontFamily(value: string): string | null`。
- `getPalette(preferences: AppearancePreferences, theme: Theme): Palette`。
- `resolveAppearance(preferences, theme): Record<string,string>` 返回根节点 CSS 属性值。
- `contrastRatio(firstHex: string, secondHex: string): number`。

存储的归一化、迁移、快照函数由核心 WU 负责，UI 不读写存储。

## React（webui/src/hooks/useTheme.ts）

`useTheme(options?: {persist?: boolean}): AppearanceController & {toggle():void; setTheme(t:Theme):void}`。persist=false 时不读取/写入真实偏好、不监听 storage，使用默认外观；可应用到预览页面本身。

`ThemeProvider({theme, appearance?, children})`：兼容原有 theme 参数；appearance 为完整 controller，提供同一状态实例。

`useAppearance(): AppearanceController | null`：无提供者时返回 null；现有只渲染 SettingsPage 的测试保留旧明暗控件回退。正式 App 和 PreviewApp 必须提供 controller。

`useThemeValue(): Theme` 保持原接口。

App 中只调用一次 useTheme，将完整返回值传给 ThemeProvider 的 appearance 属性。PreviewApp 同样只有一个 useTheme({persist:false})，替换其原有独立 darkPreview 状态；外观设置与侧栏切换共用该实例。

## CSS 与终端

- 根变量：`--font-ui`、`--font-code`、`--font-display`、`--appearance-safe-foreground`。
- 继续使用现有 HSL 颜色变量；颜色派生由核心负责，组件不要重复计算主题。
- 根 `data-appearance-theme` 为当前 presetId，`data-appearance-font` 为 uiFont.id。
- 默认 zhixu 的原有色彩数值保持一致；其他预设/custom 完整覆盖语义颜色。
- 核心在 DOM 完成应用后派发 APPEARANCE_APPLIED_EVENT；独立 xterm 订阅此事件后重新读取根变量并更新 options，不重建实例；清理订阅。
- Teams/Agent 的配色适配使用 `html[data-appearance-theme]:not([data-appearance-theme="zhixu"])` 作用域，保留 zhixu 原始页面视觉。
- 全局字体无须条件作用域：默认栈复用原有值；显式用户字体覆盖固定标题字体。代码字体 CSS 变量同时用于 inline style 和 Tailwind font-mono。

## 文件归属

WU-01：appearance 模型、useTheme、App、index.html、核心/首屏测试。

WU-02：外观编辑组件、OverviewSettings 接入、PreviewApp/PreviewSettings、翻译资源、新组件测试。禁止修改核心接口和 globals.css。

WU-03：globals.css、tailwind.config.js、CodeBlock、DiffSyntaxHighlight、TerminalSurface、终端测试。必要的团队/Agent 适配规则统一写 globals.css，避免改动其他任务的文件。

UI WU 可以先按契约实现，核心文件到位后运行组件测试。接口修改必须先由 Leader 更新本契约并通知其他 WU。
