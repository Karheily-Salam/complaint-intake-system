import { useEffect, useState } from "react";
import { staffApi } from "@/api/client";
import { useI18n } from "@/i18n";
import { formatTimestamp } from "@/lib/labels";
import type { MonitoringSnapshot } from "@/types/api";

const WINDOW_DAYS = 7;

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

/**
 * What the models are doing on live traffic, collapsed by default.
 *
 * Offline evaluation lives in the repository (docs/ml/evaluation.md); this is
 * the part that changes without anyone editing code - which layer is deciding,
 * how often the classifier abstains, whether it still agrees with the
 * deterministic rules, how often staff had to correct it, and whether the
 * traffic has drifted away from what the model was trained on.
 */
export function ModelHealthPanel({ staffKey }: { staffKey: string }) {
  const { t, lang } = useI18n();
  const [data, setData] = useState<MonitoringSnapshot | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    staffApi
      .monitoring(staffKey, WINDOW_DAYS)
      .then((snapshot) => !cancelled && setData(snapshot))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [staffKey]);

  if (failed || !data) return null;

  const { models, classification, extraction, drift, feedback, worker } = data;
  const rows: [string, string][] = [
    [t.ml.health.classifier, `${models.classifier.version ?? "—"} · ${models.classifier.mode}`],
    [t.ml.health.embedder, models.embedder.version],
    [t.ml.health.decisions, formatDecisions(classification.by_decided_by, t.ml.sources)],
    [t.ml.health.abstention, percent(classification.abstention_rate)],
    [
      t.ml.health.agreement,
      classification.shadow_agreement.compared
        ? `${percent(classification.shadow_agreement.rate)} (${classification.shadow_agreement.compared})`
        : "—",
    ],
    [
      t.ml.health.latency,
      classification.latency_ms.p95 === null ? "—" : `${classification.latency_ms.p95} ms`,
    ],
    [t.ml.health.rejected, percent(extraction.rejection_rate)],
    [t.ml.health.drift, t.ml.health.driftStatus[drift.status] ?? drift.status],
    [t.ml.health.corrections, String(feedback.total)],
    [
      t.ml.health.worker,
      worker.last_run_at ? formatTimestamp(worker.last_run_at, lang) : "—",
    ],
  ];

  return (
    <details className="model-health">
      <summary>{t.ml.health.title}</summary>
      <p className="muted-note">{t.ml.health.note(classification.n, WINDOW_DAYS)}</p>
      {models.embedder.fallback_active && <p className="error">{t.ml.health.embedderFallback}</p>}
      <dl className="health-grid">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function formatDecisions(
  byLayer: Record<string, number>,
  sources: Record<string, string>,
): string {
  const entries = Object.entries(byLayer);
  if (entries.length === 0) return "—";
  return entries.map(([layer, n]) => `${sources[layer] ?? layer}: ${n}`).join(" · ");
}
