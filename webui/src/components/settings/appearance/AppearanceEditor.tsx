import { useTranslation } from "react-i18next";
import { RotateCcw } from "lucide-react";

import { SettingsGroup, SettingsRow, SettingsSectionTitle } from "@/components/settings/shared/SettingsControls";
import { Button } from "@/components/ui/button";
import { useAppearance } from "@/hooks/useTheme";
import { contrastRatio, getPalette, THEME_PRESETS, type ThemeMode } from "@/lib/appearance";
import { AppearanceColorRow } from "./AppearanceColorRow";
import { AppearanceFontRow } from "./AppearanceFontRow";
import "./appearance.css";

export function AppearanceEditor({ preview = false }: { preview?: boolean }) {
  const appearance = useAppearance();
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(`appearance.${key}`, { defaultValue: fallback });
  if (!appearance) return null;
  const { preferences, theme } = appearance;
  const palette = getPalette(preferences, theme);
  const ratio = contrastRatio(palette.background, palette.foreground);
  const modes: { id: ThemeMode; label: string }[] = [
    { id: "system", label: tx("system", "System") },
    { id: "light", label: tx("light", "Light") },
    { id: "dark", label: tx("dark", "Dark") },
  ];

  return (
    <section className="appearance-editor min-w-0 space-y-4" aria-label={tx("visualStyle", "Visual style")}>
      <SettingsSectionTitle>{tx("visualStyle", "Visual style")}</SettingsSectionTitle>
      <SettingsGroup>
        <SettingsRow title={tx("mode", "Mode")}>
          <fieldset className="appearance-modes" aria-label={tx("mode", "Mode")}>
            {modes.map(({ id, label }) => (
              <label key={id} className="appearance-mode">
                <input type="radio" name="appearance-mode" value={id} checked={preferences.mode === id}
                  onChange={() => appearance.setMode(id)} />
                <span className={`appearance-mode-picture appearance-mode-${id}`} aria-hidden="true">
                  <span className="appearance-mini-sidebar" />
                  <span className="appearance-mini-content"><i /><i /><i /><b /></span>
                </span>
                <span className="appearance-mode-label">{label}</span>
              </label>
            ))}
          </fieldset>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup>
        <SettingsRow title={tx("theme", "Theme")}>
          <div className="flex min-w-0 items-center gap-2">
            <span className="h-4 w-4 shrink-0 rounded-full border border-border" style={{ backgroundColor: palette.accent }} aria-hidden="true" />
            <select className="appearance-select" aria-label={tx("theme", "Theme")} value={preferences.presetId}
              onChange={(event) => appearance.setPreset(event.target.value as typeof preferences.presetId)}>
              {THEME_PRESETS.map((preset) => <option key={preset.id} value={preset.id}>{tx(preset.id, preset.name)}</option>)}
              {preferences.custom && <option value="custom">{tx("custom", "Custom")}</option>}
            </select>
          </div>
        </SettingsRow>
        <div className="px-4 pt-3 text-[12px] text-muted-foreground sm:px-5">
          {theme === "dark" ? tx("editingDark", "Editing dark colors") : tx("editingLight", "Editing light colors")}
        </div>
        {(["accent", "background", "foreground"] as const).map((field) => (
          <AppearanceColorRow key={`${theme}-${field}`} field={field} value={palette[field]}
            overridden={Boolean(preferences.presetId === "custom" && preferences.custom?.[theme][field])}
            onChange={(hex) => appearance.setColor(field, hex)} onReset={() => appearance.resetColor(field)} />
        ))}
        <AppearanceFontRow kind="ui" value={preferences.uiFont} onChange={(value) => appearance.setFont("ui", value)} />
        <AppearanceFontRow kind="code" value={preferences.codeFont} onChange={(value) => appearance.setFont("code", value)} />
      </SettingsGroup>

      {ratio < 4.5 && <p role="status" className="appearance-recovery text-[12px] leading-5">
        {t("appearance.lowContrast", { defaultValue: "Low contrast ({{ratio}}:1). Try a darker text color or a lighter background.", ratio: ratio.toFixed(2) })}
      </p>}
      {appearance.storageError && <p role="alert" className="appearance-recovery text-[12px] leading-5">
        {tx("storageError", "Applied for this session, but could not save to this device.")}
      </p>}

      <div className="appearance-sample rounded-panel border border-border p-4 sm:p-5" aria-label={tx("preview", "Preview")}>
        <p className="text-[11px] text-muted-foreground">{tx("preview", "Preview")}</p>
        <p className="mt-2 text-[18px] font-medium">{tx("sampleTitle", "让想法有序生长 · Make room for ideas")}</p>
        <p className="mt-1 text-[13px] text-muted-foreground">{tx("sampleBody", "Your colors and fonts apply across the interface. 0123456789")}</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button type="button" size="sm">{tx("sampleButton", "Continue")}</Button>
          <input readOnly value={tx("sampleInput", "A new idea…")} aria-label={tx("sampleInputLabel", "Example input")}
            className="h-9 min-w-0 max-w-full flex-1 rounded-control border border-input bg-background px-3 text-[13px]" />
        </div>
        <pre className="mt-4 overflow-x-auto rounded-control bg-muted/60 p-3 font-mono text-[12px]"><code>{'const idea = "你好, Zhixu";'}</code></pre>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12px] text-muted-foreground">{preview
          ? tx("previewOnly", "Preview only. Appearance changes stay in this session and do not change your saved preferences.")
          : tx("instant", "Changes apply immediately and save on this device.")}</p>
        <button type="button" className="appearance-recovery appearance-reset" onClick={appearance.resetAppearance}>
          <RotateCcw size={14} aria-hidden="true" />{tx("reset", "Restore default appearance")}
        </button>
      </div>
    </section>
  );
}
