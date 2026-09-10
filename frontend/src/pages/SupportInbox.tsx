import { useEffect, useMemo, useState } from "react";
import { api } from "@/api/client";
import { buildLabelLookup, buildTypeLookup, formatTimestamp, statusLabel } from "@/lib/labels";
import type { ComplaintSchema, TicketDetail, TicketSummary } from "@/types/api";

/**
 * The other half of the product: what support actually receives once a
 * conversation is complete.
 *
 * This reads the public demo endpoints, which the backend restricts to
 * conversations flagged as demo. The real staff endpoints require an API key
 * that this browser app deliberately does not hold - a key shipped in a
 * bundle would not be a secret. So status here is read-only.
 */
export function SupportInbox({ refreshToken }: { refreshToken?: number }) {
  const [tickets, setTickets] = useState<TicketSummary[] | null>(null);
  const [selected, setSelected] = useState<TicketDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [schemas, setSchemas] = useState<ComplaintSchema[]>([]);

  const labelFor = useMemo(() => buildLabelLookup(schemas), [schemas]);
  const typeLabel = useMemo(() => buildTypeLookup(schemas), [schemas]);

  useEffect(() => {
    api.listSchemas().then(setSchemas).catch(() => setSchemas([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .listTickets()
      .then((t) => !cancelled && setTickets(t))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  async function open(reference: string) {
    setDetailError(null);
    try {
      setSelected(await api.getTicket(reference));
    } catch (e) {
      setSelected(null);
      setDetailError(
        e instanceof Error && e.message.includes("404")
          ? `Ticket ${reference} is no longer available.`
          : "Could not load that ticket.",
      );
    }
  }

  return (
    <div className="layout-2col">
      <div className="card">
        <header className="mail-header">
          <div>
            <h2>Support inbox</h2>
            <span className="muted-note">
              Internal view — structured tickets created from email conversations
            </span>
          </div>
        </header>

        {error && (
          <p className="error">Could not load tickets: {error}</p>
        )}
        {!tickets && !error && <p className="empty">Loading tickets…</p>}
        {tickets?.length === 0 && (
          <p className="empty">
            No tickets yet. Run a scenario in the Customer mailbox tab and one will appear
            here.
          </p>
        )}

        {tickets && tickets.length > 0 && (
          <div className="ticket-list">
            {tickets.map((t) => (
              <button
                key={t.reference}
                className={`ticket-card ${selected?.reference === t.reference ? "active" : ""}`}
                onClick={() => open(t.reference)}
              >
                <div className="ticket-card-top">
                  <span className="ticket-ref">#{t.reference}</span>
                  <span className="badge">{typeLabel(t.type)}</span>
                  <span className={`badge status-${t.status}`}>{statusLabel(t.status)}</span>
                </div>
                <div className="ticket-card-customer">
                  {t.customer.name ? `${t.customer.name} · ` : ""}
                  {t.customer.email}
                </div>
                <div className="ticket-card-desc">{t.concise_description}</div>
                <div className="ticket-card-time">{formatTimestamp(t.created_at)}</div>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Ticket detail</h2>
        {detailError && <p className="error">{detailError}</p>}
        {!selected && !detailError && (
          <p className="empty">Select a ticket to see the structured complaint.</p>
        )}
        {selected && (
          <>
            <div className="ticket-detail-head">
              <span className="ticket-ref">#{selected.reference}</span>
              <span className="badge">{typeLabel(selected.type)}</span>
              <span className={`badge status-${selected.status}`}>
                {statusLabel(selected.status)}
              </span>
            </div>
            <p className="muted-note">
              Status changes are a staff action and need an API key, so they are not
              available in the public demo.
            </p>

            <h3>Customer</h3>
            <div className="field-row">
              <span className="k">Name</span>
              <span>{selected.customer.name ?? "(not provided)"}</span>
            </div>
            <div className="field-row">
              <span className="k">Email</span>
              <span>{selected.customer.email}</span>
            </div>
            <div className="field-row">
              <span className="k">Opened</span>
              <span>{formatTimestamp(selected.created_at)}</span>
            </div>

            <h3>Collected information</h3>
            <StructuredFields data={selected.structured_data} labelFor={labelFor} />

            <h3>Summary</h3>
            <p>{selected.concise_description}</p>
          </>
        )}
      </div>
    </div>
  );
}

function StructuredFields({
  data,
  labelFor,
}: {
  data: Record<string, unknown>;
  labelFor: (key: string) => string;
}) {
  const fields = (data.fields as Record<string, string> | undefined) ?? {};
  const entries = Object.entries(fields).filter(([, v]) => v);

  if (entries.length === 0) return <p className="empty">No structured fields captured.</p>;

  return (
    <>
      {entries.map(([key, value]) => (
        <div className="field-row" key={key}>
          <span className="k">{labelFor(key)}</span>
          <span>{value}</span>
        </div>
      ))}
    </>
  );
}
