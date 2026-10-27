import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { MemorySection } from "@/components/settings/memory/MemorySection";
import type { SettingsPayload } from "@/lib/types";

/**
 * Standalone "Memory" page. Reached directly from the rail (no Settings detour).
 * Internally still reuses the memory settings section: same toggle, same
 * semantic-memory and episode tabs, same backend payload wiring.
 */
export function MemoryPage({
  settingsSnapshot,
  onSettingsChange,
}: {
  settingsSnapshot: SettingsPayload | null;
  onSettingsChange: (payload: SettingsPayload) => void;
}) {
  const { t } = useTranslation();
  const [hydrated, setHydrated] = useState(false);
  // Snapshot is loaded asynchronously by the gateway; we keep our own copy so
  // the memory master toggle can update settings without remounting the tabs.
  const [payload, setPayload] = useState<SettingsPayload | null>(settingsSnapshot);

  useEffect(() => {
    setHydrated(true);
  }, []);

  if (!hydrated) return null;

  return (
    <div className="assistant-page">
      <p className="assistant-page-kicker mb-4">
        {t("settings.memory.pageEyebrow", { defaultValue: "Memory center" })}
      </p>
      <MemorySection
        settings={payload}
        onSettingsChange={(next) => {
          setPayload(next);
          onSettingsChange(next);
        }}
      />
    </div>
  );
}
