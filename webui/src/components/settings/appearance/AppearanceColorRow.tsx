import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { RotateCcw } from "lucide-react";

import { SettingsRow } from "@/components/settings/shared/SettingsControls";
import { Input } from "@/components/ui/input";
import { normalizeHex, type ColorField } from "@/lib/appearance";

export function AppearanceColorRow({ field, value, overridden, onChange, onReset }: {
  field: ColorField; value: string; overridden: boolean;
  onChange: (hex: string) => void; onReset: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [draft, setDraft] = useState(value);
  const [invalid, setInvalid] = useState(false);
  const focused = useRef(false);
  const label = t(`appearance.${field}`, { defaultValue: { accent: "Accent", background: "Background", foreground: "Text" }[field] });
  useEffect(() => {
    if (!focused.current) { setDraft(value); setInvalid(false); }
  }, [value, overridden]);

  return <SettingsRow title={label}>
    <div className="appearance-color-controls">
      <div className="appearance-color-input">
        <input type="color" value={value} aria-label={t("appearance.chooseColor", { defaultValue: "Choose {{label}} color", label })}
          onChange={(event) => { onChange(event.target.value); setDraft(event.target.value.toUpperCase()); setInvalid(false); }} />
        <Input value={draft} aria-label={`${label} HEX`} aria-invalid={invalid} aria-describedby={invalid ? id : undefined}
          spellCheck={false} autoComplete="off" maxLength={7} className="appearance-hex"
          onFocus={() => { focused.current = true; }}
          onChange={(event) => {
            setDraft(event.target.value);
            setInvalid(false);
            const hex = normalizeHex(event.target.value);
            if (hex) onChange(hex);
          }}
          onBlur={() => {
            focused.current = false;
            const hex = normalizeHex(draft);
            setInvalid(!hex);
            if (hex) setDraft(hex);
          }} />
      </div>
      <button type="button" disabled={!overridden} onClick={() => { onReset(); setInvalid(false); }}
        aria-label={t("appearance.resetColor", { defaultValue: "Restore {{label}} theme value", label })}
        title={t("appearance.resetColor", { defaultValue: "Restore {{label}} theme value", label })}
        className="appearance-reset-color"><RotateCcw size={14} aria-hidden="true" /></button>
    </div>
    {invalid && <p id={id} role="alert" className="appearance-recovery mt-2 text-[12px]">
      {t("appearance.invalidColor", { defaultValue: "Enter a HEX color, such as #0169CC or #09C." })}
    </p>}
  </SettingsRow>;
}
