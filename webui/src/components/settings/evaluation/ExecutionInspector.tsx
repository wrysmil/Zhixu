import { useTranslation } from "react-i18next";

import type { ProjectedExecutionNode } from "@/lib/execution-graph";

function value(value: string | number | null | undefined, unavailable: string): string {
  return value == null || value === "" ? unavailable : String(value);
}

export function ExecutionInspector({ node }: { node: ProjectedExecutionNode | null }) {
  const { t } = useTranslation();
  // Token 字段缺失显示 "Unavailable"；身份类字段缺失显示破折号，避免同一个词在面板里重复出现。
  const unavailable = t("settings.evaluation.observability.unavailable", {
    defaultValue: "Unavailable",
  });
  const notRecorded = t("settings.evaluation.observability.notRecorded", {
    defaultValue: "\u2014",
  });
  return (
    <section
      role="region"
      aria-label={t("settings.evaluation.observability.inspector", {
        defaultValue: "Node inspector",
      })}
      className="rounded-floating border border-border/70 bg-settings-surface p-4"
    >
      <h3 className="text-[13px] font-semibold text-foreground">
        {t("settings.evaluation.observability.inspector", { defaultValue: "Node inspector" })}
      </h3>
      {!node ? (
        <p className="mt-3 text-[12px] text-muted-foreground">
          {t("settings.evaluation.observability.selectNode", {
            defaultValue: "Select a node to inspect its recorded evidence.",
          })}
        </p>
      ) : (
        <dl className="mt-3 grid grid-cols-[minmax(6rem,0.8fr)_minmax(0,1.2fr)] gap-x-3 gap-y-2 text-[12px]">
          <dt className="text-muted-foreground">{t("settings.evaluation.observability.kind", { defaultValue: "Type" })}</dt>
          <dd className="break-words text-foreground">{node.kind}</dd>
          <dt className="text-muted-foreground">{t("settings.evaluation.observability.agent", { defaultValue: "Agent" })}</dt>
          <dd className="break-words text-foreground">{value(node.agent_name, notRecorded)}</dd>
          <dt className="text-muted-foreground">Agent ID</dt>
          <dd className="break-all font-mono text-foreground">{value(node.agent_id, notRecorded)}</dd>
          <dt className="text-muted-foreground">Execution ID</dt>
          <dd className="break-all font-mono text-foreground">{value(node.execution_id, notRecorded)}</dd>
          <dt className="text-muted-foreground">Parent</dt>
          <dd className="break-all font-mono text-foreground">{value(node.parent_execution_id, notRecorded)}</dd>
          <dt className="text-muted-foreground">Call ID</dt>
          <dd className="break-all font-mono text-foreground">{value(node.call_id, notRecorded)}</dd>
          <dt className="text-muted-foreground">{t("settings.evaluation.observability.model", { defaultValue: "Model" })}</dt>
          <dd className="break-words text-foreground">{value(node.model, notRecorded)}</dd>
          <dt className="text-muted-foreground">{t("settings.evaluation.observability.duration", { defaultValue: "Duration" })}</dt>
          <dd className="text-foreground">{node.duration_ms == null ? notRecorded : `${node.duration_ms} ms`}</dd>
          <dt className="text-muted-foreground">Input tokens</dt>
          <dd className="text-foreground">{value(node.input_tokens, unavailable)}</dd>
          <dt className="text-muted-foreground">Output tokens</dt>
          <dd className="text-foreground">{value(node.output_tokens, unavailable)}</dd>
          <dt className="text-muted-foreground">Total tokens</dt>
          <dd className="text-foreground">{value(node.total_tokens, unavailable)}</dd>
          <dt className="text-muted-foreground">Usage source</dt>
          <dd className="text-foreground">{node.usage_source}</dd>
          <dt className="text-muted-foreground">Evidence</dt>
          <dd className="break-all font-mono text-foreground">
            {node.evidence_refs.length > 0 ? node.evidence_refs.join(", ") : notRecorded}
          </dd>
        </dl>
      )}
    </section>
  );
}
