import { useState } from "react";
import { useTranslation } from "react-i18next";

import { SegmentedControl } from "@/components/ui/segmented-control";
import { useClient } from "@/providers/ClientProvider";
import { EpisodeListView } from "./EpisodeListView";
import { MemoryListView } from "./MemoryListView";
import { MemoryGuide } from "./MemoryGuide";
import type { SettingsPayload } from "@/lib/types";

type MemoryTab = "semantic" | "episode";

const MEMORY_TABS: Array<{ value: MemoryTab; labelKey: string; defaultLabel: string }> = [
  { value: "semantic", labelKey: "settings.memory.tabSemantic", defaultLabel: "Semantic memory" },
  { value: "episode", labelKey: "settings.memory.tabEpisode", defaultLabel: "Episode memory" },
];

export function MemorySection({
  settings,
  onSettingsChange,
}: {
  settings: SettingsPayload | null;
  onSettingsChange: (payload: SettingsPayload) => void;
}) {
  const { t, i18n } = useTranslation();
  const zh = i18n.resolvedLanguage?.startsWith("zh");
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { client } = useClient();
  const [tab, setTab] = useState<MemoryTab>("semantic");
  const initialEnabled = settings?.runtime?.memory_enabled ?? false;
  const [enabled, setEnabled] = useState<boolean>(initialEnabled);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const persist = async (next: boolean) => {
    setSaving(true);
    setError(null);
    try {
      const payload = await client.requestMutation<Record<string, unknown>>(
        "settings.agent.update",
        { memory_enabled: next },
      );
      if (payload && typeof payload === "object" && "runtime" in payload) {
        onSettingsChange(payload as unknown as SettingsPayload);
      }
    } catch (err) {
      setEnabled(!next);
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const onToggle = (next: boolean) => {
    if (next === enabled || saving) return;
    setEnabled(next);
    void persist(next);
  };

  const onLabel = tx("settings.memory.masterToggleOn", "On");
  const offLabel = tx("settings.memory.masterToggleOff", "Off");

  return (
    <div className="flex flex-col gap-5">
      <MemoryGuide />
      <div
        className="flex items-start justify-between gap-4 rounded-xl border border-border/65 bg-gradient-to-r from-card/85 to-primary/[0.025] px-5 py-4"
        data-testid="memory-master-toggle"
      >
        <div className="flex flex-col gap-1">
          <span className="text-[13px] font-medium">
            {zh ? "自动记住有用的信息" : "Remember useful details automatically"}
          </span>
          <span className="text-xs text-muted-foreground">
            {zh
              ? "从对话中整理有用的信息，在之后需要时参考。关闭后，仍可查看和管理已有内容。"
              : "Save useful details from chats for future conversations. You can still review and manage saved content when this is off."}
          </span>
          <span className="mt-1 text-[11px] font-medium text-primary">{enabled ? onLabel : offLabel}</span>
          {error ? (
            <span role="alert" className="text-xs text-destructive">{error}</span>
          ) : null}
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label={tx("settings.memory.masterToggleAria", "Toggle memory extraction")}
          disabled={saving}
          onClick={() => onToggle(!enabled)}
          className={
            "mt-0.5 inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border transition-colors duration-150 ease-out " +
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 " +
            "disabled:cursor-not-allowed disabled:opacity-60 " +
            (enabled
              ? "border-primary bg-primary"
              : "border-input bg-input")
          }
        >
          <span
            className={
              "inline-block h-5 w-5 transform rounded-full bg-background shadow-sm ring-0 transition-transform duration-150 ease-out " +
              (enabled ? "translate-x-5" : "translate-x-0.5")
            }
          />
        </button>
      </div>
      <div className="border-b border-border/60 pb-4">
        <SegmentedControl
          mode="tabs"
          ariaLabel={tx("settings.memory.tabsLabel", "Memory sections")}
          value={tab}
          options={MEMORY_TABS.map(({ value, labelKey, defaultLabel }) => ({
            value,
            label: zh ? (value === "semantic" ? "长期信息" : "对话记录") : tx(labelKey, defaultLabel),
          }))}
          onChange={setTab}
          className="w-fit max-w-full bg-primary/[0.06]"
          itemClassName="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>
      {tab === "semantic" && <MemoryListView />}
      {tab === "episode" && <EpisodeListView />}
    </div>
  );
}
