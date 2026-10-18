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
    <div className="mx-auto w-full max-w-[1080px] px-4 py-8 sm:px-8 lg:px-10">
      <header className="mb-6 flex flex-col gap-2">
        <p className="text-[13px] font-medium text-primary">
          {t("settings.memory.pageEyebrow", { defaultValue: "Memory center" })}
        </p>
        <h1 className="text-[26px] font-semibold leading-tight text-foreground">
          {t("settings.nav.memory", { defaultValue: "Memory" })}
        </h1>
        <p className="max-w-[640px] text-sm text-muted-foreground">
          {t("settings.memory.pageSubtitle", {
            defaultValue:
              "Long-term facts and conversation excerpts the agent keeps across sessions. Toggle automatic extraction here, or just browse and curate what's already there.",
          })}
        </p>
      </header>
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
