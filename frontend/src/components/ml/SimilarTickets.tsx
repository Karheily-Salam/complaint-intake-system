import { useEffect, useState } from "react";
import { staffApi } from "@/api/client";
import { useI18n } from "@/i18n";
import { formatDate, statusLabel, type LabelLookup } from "@/lib/labels";
import type { SimilarTicket } from "@/types/api";

interface SimilarTicketsProps {
  staffKey: string;
  reference: string;
  typeLabel: LabelLookup;
  fieldLabel: LabelLookup;
  onOpen: (reference: string) => void;
}

/**
 * Tickets that look like the open one, with the reasons the model gives.
 *
 * Advisory by design: an agent sees "possible duplicate of #000123 - same
 * customer, same transaction ID" and decides; nothing here can merge, link or
 * close a ticket. Loaded separately from the ticket so a slow model never
 * delays the ticket itself.
 */
export function SimilarTickets({ staffKey, reference, typeLabel, fieldLabel, onOpen }: SimilarTicketsProps) {
  const { t, lang } = useI18n();
  const [items, setItems] = useState<SimilarTicket[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setFailed(false);
    staffApi
      .similarTickets(staffKey, reference)
      .then((result) => !cancelled && setItems(result.items))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [staffKey, reference]);

  function reasonText(reason: string): string {
    if (t.ml.reasons[reason]) return t.ml.reasons[reason];
    if (reason.startsWith("same_")) return t.ml.sameField(fieldLabel(reason.slice(5)));
    return reason;
  }

  return (
    <div className="similar-tickets">
      <h3>{t.ml.similarTitle}</h3>
      <p className="muted-note">{t.ml.similarNote}</p>
      {failed && <p className="error">{t.ml.similarError}</p>}
      {items === null && !failed && <p className="muted-note">{t.common.loading}</p>}
      {items !== null && items.length === 0 && <p className="empty">{t.ml.similarNone}</p>}
      {items !== null && items.length > 0 && (
        <ul className="similar-list">
          {items.map((item) => (
            <li key={item.reference} className={`similar-item ${item.relation}`}>
              <button className="link-btn" onClick={() => onOpen(item.reference)}>
                {t.tickets.ticketNumber(item.reference)}
              </button>
              <span className={`badge ${item.relation === "possible_duplicate" ? "warn" : ""}`}>
                {item.relation === "possible_duplicate" ? t.ml.possibleDuplicate : t.ml.similar}
              </span>
              <span className="similar-meta">
                {typeLabel(item.type)} · {statusLabel(item.status, t)} ·{" "}
                {formatDate(item.created_at, lang)}
                {item.similarity > 0 && <> · {t.ml.match(Math.round(item.similarity * 100))}</>}
              </span>
              <span className="similar-reasons">
                {item.reasons.filter((r) => r !== "same_type").map(reasonText).join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
