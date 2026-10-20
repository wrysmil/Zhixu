import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { SegmentedControl } from "@/components/ui/segmented-control";
import { notifySaveFailed, notifySaved } from "@/lib/save-feedback";
import { useClient } from "@/providers/ClientProvider";
import { EpisodeListView } from "./EpisodeListView";
import { MemoryListView } from "./MemoryListView";
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
  // A deep link can mount this section before the settings payload lands; adopt
  // the backend value once it does, unless the user already flipped the switch.
  const userToggled = useRef(false);
  useEffect(() => {
    if (userToggled.current || !settings) return;
    setEnabled(settings.runtime?.memory_enabled ?? false);
  }, [settings]);

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
      notifySaved();
    } catch (err) {
      setEnabled(!next);
      setError((err as Error).message);
      notifySaveFailed((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const onToggle = (next: boolean) => {
    if (next === enabled || saving) return;
    userToggled.current = true;
    setEnabled(next);
    void persist(next);
  };

  const onLabel = tx("settings.memory.masterToggleOn", "On");
  const offLabel = tx("settings.memory.masterToggleOff", "Off");
  const toggleHint = zh
    ? "从对话中整理有用的信息，之后需要时参考。关闭后仍可查看和管理已有内容。"
    : "Save useful details from chats for future conversations. You can still review and manage saved content when this is off.";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-b border-border/60 pb-3">
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
        <div className="flex items-center gap-2.5" data-testid="memory-master-toggle">
          <span className="text-[12px] font-medium text-[hsl(var(--text-body))]">
            {zh ? "自动记住" : "Auto-remember"}
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label={tx("settings.memory.masterToggleAria", "Toggle memory extraction")}
            title={`${toggleHint}（${enabled ? onLabel : offLabel}）`}
            disabled={saving}
            onClick={() => onToggle(!enabled)}
            className={
              "inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors duration-150 ease-out " +
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 " +
              "disabled:cursor-not-allowed disabled:opacity-60 " +
              (enabled
                ? "border-primary bg-primary"
                : "border-input bg-input")
            }
          >
            <span
              className={
                "inline-block h-4 w-4 transform rounded-full bg-background shadow-sm ring-0 transition-transform duration-150 ease-out " +
                (enabled ? "translate-x-4" : "translate-x-0.5")
              }
            />
          </button>
        </div>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-destructive">{error}</p>
      ) : null}
      {tab === "semantic" && <MemoryListView />}
      {tab === "episode" && <EpisodeListView />}
    </div>
  );
}
