import { useEffect, useMemo, useState } from "react";
import { staffApi } from "@/api/client";
import { useI18n } from "@/i18n";
import { formatTimestamp, type LabelLookup } from "@/lib/labels";
import type { ComplaintSchema, Feedback, TicketDetail } from "@/types/api";

interface CorrectionsPanelProps {
  staffKey: string;
  ticket: TicketDetail;
  schemas: ComplaintSchema[];
  typeLabel: LabelLookup;
  fieldLabel: LabelLookup;
  onCorrected: (ticket: TicketDetail) => void;
}

/**
 * Correct the complaint type or one collected value.
 *
 * A correction is a staff decision applied to the ticket, and the original
 * prediction is kept next to it as training feedback. Collapsed by default:
 * most tickets need no correction, and the form should not crowd the facts.
 */
export function CorrectionsPanel({
  staffKey,
  ticket,
  schemas,
  typeLabel,
  fieldLabel,
  onCorrected,
}: CorrectionsPanelProps) {
  const { t, lang } = useI18n();
  const [history, setHistory] = useState<Feedback[]>([]);
  const [type, setType] = useState(ticket.type);
  const fields = useMemo(
    () => schemas.find((s) => s.type === ticket.type)?.common_fields ?? [],
    [schemas, ticket.type],
  );
  const [fieldKey, setFieldKey] = useState("");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setType(ticket.type);
    setFieldKey(fields[0]?.key ?? "");
    staffApi
      .ticketCorrections(staffKey, ticket.reference)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [staffKey, ticket.reference, ticket.type, fields]);

  async function submit(payload: Parameters<typeof staffApi.correctTicket>[2]) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const result = await staffApi.correctTicket(staffKey, ticket.reference, payload);
      setHistory((prev) => [result.feedback, ...prev]);
      setValue("");
      setNote("");
      setSaved(true);
      onCorrected(result.ticket);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function source(value: string | null): string {
    return (value && t.ml.sources[value]) || value || t.ml.sources.unknown;
  }

  return (
    <details className="corrections-panel">
      <summary>
        {t.ml.correctTitle}
        {history.length > 0 && <span className="muted-note"> · {t.ml.correctionCount(history.length)}</span>}
      </summary>
      <p className="muted-note">{t.ml.correctNote}</p>

      <div className="correction-row">
        <label>
          <span>{t.ml.correctType}</span>
          <select value={type} onChange={(e) => setType(e.target.value)} disabled={busy}>
            {schemas.map((s) => (
              <option key={s.type} value={s.type}>
                {typeLabel(s.type)}
              </option>
            ))}
          </select>
        </label>
        <button
          className="nav-btn"
          disabled={busy || type === ticket.type}
          onClick={() => submit({ kind: "classification", corrected_value: type, note: note || undefined })}
        >
          {t.ml.saveCorrection}
        </button>
      </div>

      <div className="correction-row">
        <label>
          <span>{t.ml.correctField}</span>
          <select value={fieldKey} onChange={(e) => setFieldKey(e.target.value)} disabled={busy}>
            {fields.map((f) => (
              <option key={f.key} value={f.key}>
                {fieldLabel(f.key)}
              </option>
            ))}
          </select>
        </label>
        <label className="grow">
          <span>{t.ml.correctValue}</span>
          <input value={value} onChange={(e) => setValue(e.target.value)} disabled={busy} maxLength={500} />
        </label>
        <button
          className="nav-btn"
          disabled={busy || !fieldKey || !value.trim()}
          onClick={() =>
            submit({ kind: "field", field_key: fieldKey, corrected_value: value, note: note || undefined })
          }
        >
          {t.ml.saveCorrection}
        </button>
      </div>

      <label className="correction-note">
        <span>{t.ml.correctNoteLabel}</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} disabled={busy} maxLength={500} />
      </label>

      {error && <p className="error">{error}</p>}
      {saved && <p className="muted-note">{t.ml.correctionSaved}</p>}

      {history.length > 0 && (
        <ul className="correction-history">
          {history.map((f) => (
            <li key={f.id}>
              <strong>{f.kind === "classification" ? t.ml.correctType : fieldLabel(f.field_key ?? "")}</strong>
              {": "}
              <span className="was">
                {f.kind === "classification" ? typeLabel(f.original_value ?? "") : f.original_value ?? "—"}
              </span>
              {" → "}
              {f.kind === "classification" ? typeLabel(f.corrected_value) : f.corrected_value}
              <span className="muted-note">
                {" "}
                · {t.ml.originalFrom(source(f.original_source))}
                {f.model_version && <> ({f.model_version})</>} · {formatTimestamp(f.created_at, lang)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
