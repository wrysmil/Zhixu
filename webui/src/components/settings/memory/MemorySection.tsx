import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { SegmentedControl } from "@/components/ui/segmented-control";
import { notifySaveFailed, notifySaved } from "@/lib/save-feedback";
import { useClient } from "@/providers/ClientProvider";
import { EpisodeListView } from "./EpisodeListView";
import { ExperienceListView } from "./ExperienceListView";
import { LongTermMemoryView } from "./LongTermMemoryView";
import type { SettingsPayload } from "@/lib/types";

type MemoryTab = "doc" | "experience" | "episode";

const MEMORY_TABS: Array<{ value: MemoryTab; labelKey: string; defaultLabel: string }> = [
  { value: "doc", labelKey: "settings.memory.tabLongTerm", defaultLabel: "Long-term memory" },
  { value: "experience", labelKey: "settings.memory.tabExperience", defaultLabel: "Experience library" },
  { value: "episode", labelKey: "settings.memory.tabEpisode", defaultLabel: "Conversation excerpts" },
];

export function MemorySection({
  settings,
  onSettingsChange,
}: {
  settings: SettingsPayload | null;
  onSettingsChange: (payload: SettingsPayload) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { client } = useClient();
  const [tab, setTab] = useState<MemoryTab>("doc");
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
  const toggleHint = t("settings.memory.masterToggleDescription");

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border/60 pb-2">
        <SegmentedControl
          mode="tabs"
          ariaLabel={tx("settings.memory.tabsLabel", "Memory sections")}
          value={tab}
          options={MEMORY_TABS.map(({ value, labelKey, defaultLabel }) => ({
            value,
            label: tx(labelKey, defaultLabel),
          }))}
          onChange={setTab}
          className="w-fit max-w-full bg-primary/[0.06]"
          itemClassName="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <div className="flex items-center gap-2.5" data-testid="memory-master-toggle">
          <span className="text-[12px] font-medium text-[hsl(var(--text-body))]">
            {t("settings.memory.masterToggleTitle")}
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
      {tab === "doc" && <LongTermMemoryView />}
      {tab === "experience" && <ExperienceListView />}
      {tab === "episode" && <EpisodeListView />}
    </div>
  );
}
