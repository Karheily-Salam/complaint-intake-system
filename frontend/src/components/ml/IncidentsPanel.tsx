import { useEffect, useState } from "react";
import { staffApi } from "@/api/client";
import { useI18n } from "@/i18n";
import type { LabelLookup } from "@/lib/labels";
import type { IncidentReport } from "@/types/api";

interface IncidentsPanelProps {
  staffKey: string;
  typeLabel: LabelLookup;
  onOpen: (reference: string) => void;
}

const WINDOW_HOURS = 6;

/**
 * Possible emerging incidents, above the ticket list.
 *
 * One outage produces many tickets that each look ordinary on their own; this
 * is the only place the pattern is visible. Quiet by default - a single muted
 * line when nothing is unusual - so it never competes with the tickets.
 */
export function IncidentsPanel({ staffKey, typeLabel, onOpen }: IncidentsPanelProps) {
  const { t } = useI18n();
  const [report, setReport] = useState<IncidentReport | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    staffApi
      .incidents(staffKey, WINDOW_HOURS)
      .then((r) => !cancelled && setReport(r))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [staffKey]);

  if (failed) return <p className="muted-note">{t.ml.incidentsError}</p>;
  if (!report) return null;

  const quiet = report.incidents.length === 0 && report.volume_spikes.length === 0;
  if (quiet) {
    return (
      <p className="muted-note incidents-quiet">
        {t.ml.incidentsNone(report.complaints_in_window, report.window_hours)}
      </p>
    );
  }

  return (
    <section className="incidents-panel" aria-label={t.ml.incidentsTitle}>
      <h3>{t.ml.incidentsTitle}</h3>
      <p className="muted-note">{t.ml.incidentsNote}</p>
      <ul className="incident-list">
        {report.incidents.map((incident) => (
          <li key={incident.id} className={`incident ${incident.severity}`}>
            <div className="incident-head">
              <span className={`badge ${incident.severity === "high" ? "danger" : "warn"}`}>
                {t.ml.severity[incident.severity] ?? incident.severity}
              </span>
              <strong>
                {t.ml.incidentSummary(incident.size, incident.expected, report.window_hours)}
              </strong>
              {incident.is_demo && <span className="badge">{t.ml.demoTag}</span>}
              <Sparkline counts={incident.hourly_counts} />
            </div>
            {incident.top_terms.length > 0 && (
              <p className="incident-terms">
                {t.ml.keywords}: {incident.top_terms.join(", ")}
              </p>
            )}
            <p className="incident-meta">
              {Object.entries(incident.types)
                .map(([type, n]) => `${typeLabel(type)} ×${n}`)
                .join(" · ")}
              {" · "}
              {Object.keys(incident.languages).join(", ").toUpperCase()}
              {incident.open_complaints > 0 && <> · {t.ml.openComplaints(incident.open_complaints)}</>}
            </p>
            {incident.ticket_references.length > 0 && (
              <p className="incident-tickets">
                {incident.ticket_references.map((reference) => (
                  <button key={reference} className="link-btn" onClick={() => onOpen(reference)}>
                    #{reference}
                  </button>
                ))}
              </p>
            )}
          </li>
        ))}
        {report.volume_spikes.map((spike) => (
          <li key={`${spike.is_demo}-${spike.complaint_type}`} className="incident medium">
            <span className="badge warn">{t.ml.volumeSpikeLabel}</span>{" "}
            {t.ml.volumeSpike(typeLabel(spike.complaint_type), spike.count, spike.expected)}
            {spike.is_demo && <span className="badge">{t.ml.demoTag}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Complaints per hour across the window, oldest on the left. */
function Sparkline({ counts }: { counts: number[] }) {
  const max = Math.max(1, ...counts);
  return (
    <span className="sparkline" aria-hidden="true">
      {counts.map((count, i) => (
        <span key={i} style={{ height: `${Math.max(8, (count / max) * 100)}%` }} className={count ? "on" : ""} />
      ))}
    </span>
  );
}
