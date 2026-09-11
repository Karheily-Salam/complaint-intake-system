import { useMemo, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { collectedFields, formatTimestamp, type LabelLookup } from "@/lib/labels";
import type { ComplaintSchema, MessageOut, TicketDetail } from "@/types/api";

/**
 * Collected fields the detail table leaves out.
 *
 * The problem description is prose, not a scannable value, and the ticket type
 * plus the conversation history already carry it. Presentation only - the field
 * remains in the API, the workflow and the stored conversation. Defined here,
 * once, so the staff dashboard and the demo cannot drift apart on it.
 */
const HIDDEN_FIELDS = ["problem_description"] as const;

export interface TicketDetailPanelProps {
  ticket: TicketDetail;
  schemas: ComplaintSchema[];
  typeLabel: LabelLookup;
  onBack: () => void;
  /** Defaults to the translated "Back to tickets". */
  backLabel?: string;
  /** Staff: a status <select>. Demo: a read-only badge. */
  statusControl?: ReactNode;
  /** Staff: the reply button and composer. Demo: a note explaining the limit. */
  actions?: ReactNode;
}

/**
 * One ticket, compact enough to take in at a glance.
 *
 * Deliberately one table rather than titled sections: with a handful of short
 * values per complaint, section headings cost more vertical space than the
 * grouping saves. The email thread that produced the values stays collapsed at
 * the bottom - investigation material, not the main event.
 *
 * The status control and the actions area are supplied by the caller, which is
 * the whole reason this component can be shared: the layout is identical for
 * staff and for the public demo, while only the demo's read-only affordances
 * differ.
 */
export function TicketDetailPanel({
  ticket,
  schemas,
  typeLabel,
  onBack,
  backLabel,
  statusControl,
  actions,
}: TicketDetailPanelProps) {
  const { t, lang } = useI18n();
  const fields = (ticket.structured_data?.fields as Record<string, string> | undefined) ?? {};
  const rows = useMemo(
    () => collectedFields(schemas, ticket.type, fields, t, HIDDEN_FIELDS),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [schemas, ticket, t],
  );
  const messages = ticket.conversation?.messages ?? [];

  return (
    <div className="support-dash ticket-detail">
      <button className="nav-btn back-link" onClick={onBack}>
        {backLabel ?? t.tickets.backToTickets}
      </button>

      <header className="ticket-head">
        <div className="head-line">
          <h2>{t.tickets.ticketNumber(ticket.reference)}</h2>
          {statusControl}
        </div>
        <p className="ticket-type">{typeLabel(ticket.type)}</p>
        <p className="ticket-meta">
          {ticket.customer.email} · {t.tickets.openedAt}{" "}
          {formatTimestamp(ticket.created_at, lang)} · {t.tickets.lastActivity}{" "}
          {formatTimestamp(ticket.updated_at, lang)}
        </p>
      </header>

      {rows.length === 0 ? (
        <p className="empty">{t.tickets.noStructured}</p>
      ) : (
        <dl className="detail-table">
          {rows.map((row) => (
            <div className="detail-row" key={row.key}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {actions && <section className="ticket-actions">{actions}</section>}

      <details className="conversation-history">
        <summary>
          {t.tickets.conversationHistory}
          {messages.length > 0 && (
            <span className="muted-note"> {t.tickets.messageCount(messages.length)}</span>
          )}
        </summary>
        {messages.length === 0 ? (
          <p className="empty">{t.tickets.noMessages}</p>
        ) : (
          <div className="thread">
            {messages.map((m) => (
              <ConversationMessage key={m.id} message={m} />
            ))}
          </div>
        )}
      </details>
    </div>
  );
}

function ConversationMessage({ message }: { message: MessageOut }) {
  const { t, lang } = useI18n();
  const inbound = message.direction === "inbound";
  return (
    <article className={`email ${inbound ? "inbound" : "outbound"}`}>
      <header>
        <span className="email-dir">{inbound ? t.tickets.customer : t.tickets.system}</span>
        <span className="email-time">{formatTimestamp(message.created_at, lang)}</span>
      </header>
      <dl className="email-meta">
        <div>
          <dt>{t.tickets.from}</dt>
          <dd>{message.sender}</dd>
        </div>
        {message.subject && (
          <div>
            <dt>{t.tickets.subject}</dt>
            <dd>{message.subject}</dd>
          </div>
        )}
      </dl>
      <p className="email-body">{message.body}</p>
    </article>
  );
}
