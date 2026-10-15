import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { DeleteConfirm } from "@/components/DeleteConfirm";
import { Input } from "@/components/ui/input";
import { deleteEpisode, fetchEpisode, listEpisodes } from "@/lib/api";
import type { EpisodePayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { EpisodeDetailPanel } from "./EpisodeDetailPanel";

export function EpisodeListView() {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { client, token } = useClient();

  const [items, setItems] = useState<EpisodePayload[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [details, setDetails] = useState<EpisodePayload | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<EpisodePayload | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listEpisodes(token);
      setItems(result.items);
      setLoadError(null);
    } catch (reason) {
      setLoadError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((episode) => episode.summary.toLowerCase().includes(needle));
  }, [items, query]);

  const toggleExpand = useCallback(
    async (episode: EpisodePayload) => {
      if (expandedId === episode.id) {
        setExpandedId(null);
        setDetails(null);
        return;
      }
      setExpandedId(episode.id);
      setDetails(null);
      setDetailError(null);
      try {
        const result = await fetchEpisode(token, episode.id);
        setDetails(result.episode);
      } catch (reason) {
        setDetailError((reason as Error).message);
      }
    },
    [expandedId, token],
  );

  const confirmDelete = useCallback(async () => {
    if (!deleteCandidate) return;
    await deleteEpisode(client, deleteCandidate.id);
    if (expandedId === deleteCandidate.id) {
      setExpandedId(null);
      setDetails(null);
    }
    setDeleteCandidate(null);
    await reload();
  }, [client, deleteCandidate, expandedId, reload]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={tx("settings.memory.episodeSearchPlaceholder", "Filter by summary...")}
            className="pl-9"
            aria-label={tx("settings.memory.episodeSearchPlaceholder", "Filter by summary...")}
          />
        </div>
        <span className="shrink-0 text-[12.5px] text-muted-foreground">
          {t("settings.memory.episodeCount", {
            defaultValue: "{{count}} episodes (newest first)",
            count: visible.length,
          })}
        </span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
        </div>
      ) : loadError ? (
        <div className="rounded-panel bg-settings-surface px-4 py-12 text-center text-[13px] text-destructive">
          {loadError}
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-panel bg-settings-surface px-4 py-12 text-center text-[13px] text-muted-foreground">
          {query.trim()
            ? tx("settings.memory.episodesNoMatches", "No matching episodes")
            : tx("settings.memory.episodesEmpty", "No episodes recorded yet")}
        </div>
      ) : (
        <ul className="divide-y divide-border/60 overflow-hidden rounded-xl border border-border/65 bg-card/65">
          {visible.map((episode) => (
            <li
              key={episode.id}
              className="px-4 py-4 sm:px-5"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="break-words text-[13px] font-medium leading-6 text-foreground">
                    {episode.summary || tx("settings.memory.noSummary", "(no summary)")}
                  </div>
                  <div className="mt-0.5 truncate text-[12px] leading-5 text-muted-foreground">
                    {episode.session_id} · {episode.started_at} · {episode.outcome} ·{" "}
                    {episode.importance_score.toFixed(2)}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <button
                    type="button"
                    onClick={() => void toggleExpand(episode)}
                    aria-expanded={expandedId === episode.id}
                    className="rounded-sm text-[12.5px] text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {expandedId === episode.id
                      ? tx("settings.memory.collapse", "Collapse")
                      : tx("settings.memory.expand", "View details")}
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleteCandidate(episode)}
                    className="rounded-sm text-[12.5px] text-destructive underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {tx("settings.memory.delete", "Delete")}
                  </button>
                </div>
              </div>

              {expandedId === episode.id && (
                detailError ? (
                  <div className="mt-3 text-[12.5px] text-destructive">{detailError}</div>
                ) : details ? (
                  <EpisodeDetailPanel episode={details} onClose={() => setExpandedId(null)} />
                ) : (
                  <div className="mt-3 flex items-center gap-2 text-[12.5px] text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    {tx("settings.memory.loading", "Loading...")}
                  </div>
                )
              )}
            </li>
          ))}
        </ul>
      )}

      <DeleteConfirm
        open={deleteCandidate !== null}
        title={tx("settings.memory.episodeDeleteTitle", "Delete this episode?")}
        onCancel={() => setDeleteCandidate(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
