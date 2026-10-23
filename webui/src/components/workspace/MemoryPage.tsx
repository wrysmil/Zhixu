import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { PageHeading } from "@/components/workspace/PageHeading";
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
      <PageHeading
        kicker={t("settings.memory.pageEyebrow", { defaultValue: "Memory center" })}
        title={t("settings.nav.memory", { defaultValue: "Memory" })}
        description={t("settings.memory.pageSubtitle", {
          defaultValue:
            "Long-term facts and conversation excerpts the agent keeps across sessions. Toggle automatic extraction here, or just browse and curate what's already there.",
        })}
        illustration={{ src: "/illustrations/zhixu-toolbox.webp", size: "compact" }}
      />
      <div className="mt-5">
        <MemorySection
          settings={payload}
          onSettingsChange={(next) => {
            setPayload(next);
            onSettingsChange(next);
          }}
        />
      </div>
    </div>
  );
}
