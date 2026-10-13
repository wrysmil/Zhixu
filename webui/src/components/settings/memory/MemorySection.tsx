import { useState } from "react";
import { useTranslation } from "react-i18next";

import { SegmentedControl } from "@/components/ui/segmented-control";
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
  const { t } = useTranslation();
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
    <div className="flex flex-col gap-4">
      <div
        className="flex items-start justify-between gap-4 rounded-lg border border-border/60 bg-card p-4"
        data-testid="memory-master-toggle"
      >
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium">
            {tx("settings.memory.masterToggleTitle", "Memory auto extraction & retrieval")}
          </span>
          <span className="text-xs text-muted-foreground">
            {tx(
              "settings.memory.masterToggleDescription",
              "When on, Mira extracts facts from chats and retrieves relevant memories on the next turn. Hot-takes-effect — no restart needed.",
            )}
          </span>
          {error ? (
            <span className="text-xs text-destructive">{error}</span>
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
            "inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border transition-colors " +
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 " +
            "disabled:cursor-not-allowed disabled:opacity-60 " +
            (enabled
              ? "border-primary bg-primary"
              : "border-input bg-input")
          }
        >
          <span
            className={
              "inline-block h-5 w-5 transform rounded-full bg-background shadow ring-0 transition-transform " +
              (enabled ? "translate-x-5" : "translate-x-0.5")
            }
          />
        </button>
      </div>
      <p className="text-xs text-muted-foreground">
        {enabled ? onLabel : offLabel}
      </p>
      <SegmentedControl
        mode="tabs"
        ariaLabel={tx("settings.memory.tabsLabel", "Memory sections")}
        value={tab}
        options={MEMORY_TABS.map(({ value, labelKey, defaultLabel }) => ({
          value,
          label: tx(labelKey, defaultLabel),
        }))}
        onChange={setTab}
        className="w-fit max-w-full"
      />
      {tab === "semantic" && <MemoryListView />}
      {tab === "episode" && <EpisodeListView />}
    </div>
  );
}
