import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { SettingsRow } from "@/components/settings/shared/SettingsControls";
import { Input } from "@/components/ui/input";
import { normalizeFontFamily, type FontChoice } from "@/lib/appearance";

const FONT_OPTIONS = {
  ui: [["system", "System default"], ["sans", "Sans serif"], ["serif", "Serif"], ["custom", "Custom local font"]],
  code: [["default", "Default monospace"], ["consolas", "Consolas"], ["cascadia", "Cascadia Code"], ["jetbrains", "JetBrains Mono"], ["custom", "Custom local font"]],
};

export function AppearanceFontRow({ kind, value, onChange }: {
  kind: "ui" | "code"; value: FontChoice; onChange: (choice: FontChoice) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [family, setFamily] = useState(value.family);
  const [invalid, setInvalid] = useState(false);
  useEffect(() => { setFamily(value.family); setInvalid(false); }, [value.family, value.id]);
  const label = t(`appearance.${kind}Font`, { defaultValue: kind === "ui" ? "Interface font" : "Code font" });

  return <SettingsRow title={label}>
    <div className="appearance-font-control">
      <select className="appearance-select" aria-label={label} value={value.id}
        onChange={(event) => onChange({ id: event.target.value,
          family: event.target.value === "custom" ? family || (kind === "ui" ? "Microsoft YaHei" : "Consolas") : "" })}>
        {FONT_OPTIONS[kind].map(([fontId, fallback]) => <option key={fontId} value={fontId}>
          {t(`appearance.font_${fontId}`, { defaultValue: fallback })}
        </option>)}
      </select>
      {value.id === "custom" && <>
        <Input value={family} aria-label={t("appearance.fontFamily", { defaultValue: "{{label}} family name", label })}
          maxLength={80} placeholder={kind === "ui" ? "Microsoft YaHei" : "Consolas"} aria-invalid={invalid} aria-describedby={id}
          onChange={(event) => {
            setFamily(event.target.value); setInvalid(false);
            const normalized = normalizeFontFamily(event.target.value);
            if (normalized) onChange({ id: "custom", family: normalized });
          }}
          onBlur={() => setInvalid(!normalizeFontFamily(family))} />
        <p id={id} role={invalid ? "alert" : undefined} className="text-[11px] leading-5 text-muted-foreground">
          {invalid ? t("appearance.invalidFont", { defaultValue: "Use a single font family name without style punctuation." })
            : t("appearance.fontFallback", { defaultValue: "Use a font installed on this device. Missing fonts or characters use the fallback fonts." })}
        </p>
      </>}
    </div>
  </SettingsRow>;
}
