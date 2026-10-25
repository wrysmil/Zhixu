import { useTranslation } from "react-i18next";

import type { UsagePage } from "@/lib/evaluation-api";

function token(value: number | null, unavailable: string): string {
  return value == null ? unavailable : value.toLocaleString();
}

export function UsageLedgerTable({ page }: { page: UsagePage | null }) {
  const { t } = useTranslation();
  const unavailable = t("settings.evaluation.observability.unavailable", {
    defaultValue: "Unavailable",
  });
  if (!page) {
    return (
      <p className="rounded-floating border border-dashed border-border/70 p-6 text-center text-[13px] text-muted-foreground">
        {t("settings.evaluation.observability.noUsage", {
          defaultValue: "Token attribution is unavailable for this run.",
        })}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["Input", token(page.totals.input_tokens, unavailable)],
          ["Output", token(page.totals.output_tokens, unavailable)],
          ["Total", token(page.totals.total_tokens, unavailable)],
          ["Requests", page.totals.physical_requests.toLocaleString()],
        ].map(([label, count]) => (
          <div key={label} className="rounded-control border border-border/60 bg-muted/20 p-3">
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
            <p className="mt-1 text-base font-semibold text-foreground">{count}</p>
          </div>
        ))}
      </div>
      <div className="max-w-full overflow-x-auto rounded-floating border border-border/70">
        <table className="min-w-[760px] w-full text-left text-[12px]" aria-label={t("settings.evaluation.observability.usage", { defaultValue: "Token ledger" })}>
          <thead className="bg-muted/30 text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Agent / role</th>
              <th scope="col" className="px-3 py-2 font-medium">Model</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Input</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Output</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Total</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Requests</th>
              <th scope="col" className="px-3 py-2 font-medium">Source</th>
              <th scope="col" className="px-3 py-2 font-medium">Coverage</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {page.data.map((row) => (
              <tr key={row.key}>
                <td className="px-3 py-2">
                  {/* 按角色分组时没有 agent_name：主标签退化成角色，
                      否则会出现「Unavailable agent · agent」这种自相矛盾的文案。 */}
                  <span className="font-medium text-foreground">
                    {row.agent_name || row.role}
                  </span>
                  {row.agent_name ? (
                    <span className="ml-2 text-muted-foreground">{row.role}</span>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-muted-foreground">{row.model || unavailable}</td>
                <td className="px-3 py-2 text-right tabular-nums">{token(row.input_tokens, unavailable)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{token(row.output_tokens, unavailable)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{token(row.total_tokens, unavailable)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{row.physical_requests}</td>
                <td className="px-3 py-2 text-muted-foreground">{row.source}</td>
                <td className="px-3 py-2 text-muted-foreground">{row.coverage}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(page.totals.missing_usage_calls ?? 0) > 0 ? (
        <p className="text-[11px] text-amber-700 dark:text-amber-300">
          {t("settings.evaluation.observability.missingUsage", {
            count: page.totals.missing_usage_calls,
            defaultValue: `${page.totals.missing_usage_calls} physical requests have no reported usage.`,
          })}
        </p>
      ) : null}
    </div>
  );
}
