import { useEffect, useMemo, useState } from "react";
import { api } from "@/api/client";
import { TicketBrowser } from "@/components/tickets/TicketBrowser";
import { TicketDetailPanel } from "@/components/tickets/TicketDetailPanel";
import {
  buildLocalGroups,
  orderSchemas,
  type TicketQueryState,
} from "@/components/tickets/ticketGroups";
import { buildTypeLookup, statusLabel } from "@/lib/labels";
import type { ComplaintSchema, TicketDetail, TicketSummary } from "@/types/api";

const PAGE_SIZE = 10;
const NO_QUERY: TicketQueryState = { status: "", q: "" };

/**
 * The other half of the product: what support actually receives once a
 * conversation is complete.
 *
 * This is the same screen a support agent uses - the list and detail
 * presentation are the shared components in components/tickets, so the demo
 * cannot drift away from the real dashboard's design. Only two things differ,
 * both deliberately:
 *
 *   - the data comes from the public demo endpoints, which the backend
 *     restricts to conversations flagged ``is_demo``. A real customer's ticket
 *     is unreachable here whatever reference is supplied, and no API key is
 *     needed or held.
 *   - status changes and replying are staff actions requiring that key, so
 *     they are shown as unavailable rather than offered.
 *
 * Filtering and paging happen in the browser here, which would be wrong for
 * the staff dashboard but is right for this: the demo dataset is synthetic,
 * small, and already returned in full by one bounded request.
 */
export function SupportInbox({ refreshToken }: { refreshToken?: number }) {
  const [tickets, setTickets] = useState<TicketSummary[] | null>(null);
  const [selected, setSelected] = useState<TicketDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [schemas, setSchemas] = useState<ComplaintSchema[]>([]);
  const [query, setQuery] = useState<TicketQueryState>(NO_QUERY);
  const [pages, setPages] = useState<Record<string, number>>({});

  const typeLabel = useMemo(() => buildTypeLookup(schemas), [schemas]);
  const types = useMemo(() => orderSchemas(schemas).map((s) => s.type), [schemas]);

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

  const groups = useMemo(
    () => buildLocalGroups(tickets ?? [], types, query, PAGE_SIZE, pages),
    [tickets, types, query, pages],
  );

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

  if (selected) {
    return (
      <TicketDetailPanel
        ticket={selected}
        schemas={schemas}
        typeLabel={typeLabel}
        onBack={() => setSelected(null)}
        backLabel="← Back to inbox"
        statusControl={
          <span className={`badge status-${selected.status}`}>{statusLabel(selected.status)}</span>
        }
        actions={
          <>
            <button className="primary reply-open" disabled>
              Reply to customer
            </button>
            <p className="muted-note demo-limit">
              Replying and changing status are staff actions that need an API key, so they
              are not available in this public demo. The support dashboard uses this same
              screen with those controls enabled.
            </p>
          </>
        }
      />
    );
  }

  return (
    <TicketBrowser
      title="Support inbox"
      subtitle="Demo view — structured tickets created from the email conversations above"
      schemas={schemas}
      typeLabel={typeLabel}
      groups={groups}
      pageSize={PAGE_SIZE}
      busy={tickets === null && !error}
      loaded={tickets !== null}
      notice={
        <>
          {error && <p className="error">Could not load tickets: {error}</p>}
          {detailError && <p className="error">{detailError}</p>}
        </>
      }
      emptyMessage="No tickets yet. Run a scenario in the Customer mailbox tab and one will appear here."
      onQueryChange={(next) => {
        setQuery(next);
        setPages({});
      }}
      onOpen={open}
      onGroupPage={(type, page) => setPages((prev) => ({ ...prev, [type]: page }))}
    />
  );
}
