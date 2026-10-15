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
  const { t, i18n } = useTranslation();
  const zh = i18n.resolvedLanguage?.startsWith("zh");
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
          {t("sidebar.memory.eyebrow", {
            defaultValue: zh ? "记忆中心" : "Memory center",
          })}
        </p>
        <h1 className="text-[26px] font-semibold leading-tight text-foreground">
          {t("sidebar.memory.title", { defaultValue: zh ? "记忆" : "Memory" })}
        </h1>
        <p className="max-w-[640px] text-sm text-muted-foreground">
          {t("sidebar.memory.subtitle", {
            defaultValue: zh
              ? "Agent 在对话中整理的长期信息和对话片段。打开总开关后会自动抽取；关闭后仍可查看和管理。"
              : "Long-term facts and conversation excerpts the agent keeps across sessions. Toggle automatic extraction here, or just browse and curate what's already there.",
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
