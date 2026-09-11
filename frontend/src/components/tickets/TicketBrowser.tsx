import { useState, type ReactNode } from "react";
import { formatDate, statusLabel, type LabelLookup } from "@/lib/labels";
import {
  groupTitle,
  orderSchemas,
  type TicketGroupData,
  type TicketQueryState,
} from "@/components/tickets/ticketGroups";
import type { ComplaintSchema, TicketSummary } from "@/types/api";

const STATUSES = ["new", "in_progress", "resolved", "closed"];

/** The status filter, as a row of chips. "" means every status. */
const STATUS_FILTERS = [
  { value: "", label: "All" },
  ...STATUSES.map((value) => ({ value, label: statusLabel(value) })),
];

export interface TicketBrowserProps {
  title: string;
  subtitle?: ReactNode;
  /** Rendered at the top right - "Sign out" for staff, nothing for the demo. */
  headerAction?: ReactNode;
  schemas: ComplaintSchema[];
  typeLabel: LabelLookup;
  /** Keyed by complaint type. A missing entry renders as an empty group. */
  groups: Record<string, TicketGroupData>;
  pageSize: number;
  busy: boolean;
  loaded: boolean;
  notice?: ReactNode;
  /** Shown when nothing matches and no filter is active. */
  emptyMessage: ReactNode;
  onQueryChange: (query: TicketQueryState) => void;
  onOpen: (reference: string) => void;
  onGroupPage: (type: string, page: number) => void;
}

/**
 * The ticket list screen: status chips, search, and one section per complaint
 * type.
 *
 * Presentation only. It owns the filter *controls* and reports what they ask
 * for through `onQueryChange`; the caller decides how to satisfy that - the
 * staff dashboard re-queries the server, the demo re-filters a list it already
 * holds. That split is what lets both screens look identical without the
 * demo ever touching real ticket data.
 */
export function TicketBrowser({
  title,
  subtitle,
  headerAction,
  schemas,
  typeLabel,
  groups,
  pageSize,
  busy,
  loaded,
  notice,
  emptyMessage,
  onQueryChange,
  onOpen,
  onGroupPage,
}: TicketBrowserProps) {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");

  const ordered = orderSchemas(schemas);
  const grandTotal = ordered.reduce((sum, s) => sum + (groups[s.type]?.total ?? 0), 0);
  const filtering = Boolean(status || query);

  function apply(next: Partial<TicketQueryState>) {
    const resolved = { status, q: query, ...next };
    setStatus(resolved.status);
    setQuery(resolved.q);
    onQueryChange(resolved);
  }

  return (
    <div className="support-dash">
      <header className="dash-header">
        <div>
          <h2>{title}</h2>
          {subtitle && <span className="muted-note">{subtitle}</span>}
        </div>
        {headerAction}
      </header>

      <div className="dash-controls">
        <div className="status-tabs" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.value || "all"}
              type="button"
              className={`chip ${status === filter.value ? "active" : ""}`}
              aria-pressed={status === filter.value}
              onClick={() => apply({ status: filter.value })}
            >
              {filter.label}
            </button>
          ))}
        </div>

        <form
          className="dash-search"
          onSubmit={(e) => {
            e.preventDefault();
            apply({ q: search.trim() });
          }}
        >
          <input
            type="search"
            placeholder="Reference or customer email…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search tickets"
          />
          <button className="nav-btn" type="submit">
            Search
          </button>
          {query && (
            <button
              className="nav-btn"
              type="button"
              onClick={() => {
                setSearch("");
                apply({ q: "" });
              }}
            >
              Clear
            </button>
          )}
        </form>
      </div>

      {notice}
      {!loaded && busy && <p className="empty">Loading tickets…</p>}

      {loaded && grandTotal === 0 && (
        <p className="empty">
          {filtering ? "No tickets match those filters." : emptyMessage}
        </p>
      )}

      {loaded &&
        grandTotal > 0 &&
        ordered.map((schema) => {
          const group = groups[schema.type];
          const total = group?.total ?? 0;
          const pageCount = Math.max(1, Math.ceil(total / pageSize));
          const page = group?.page ?? 1;

          return (
            <section className="ticket-group" key={schema.type}>
              <h3 className="group-head">
                {groupTitle(schema)}
                <span className="group-count">({total})</span>
              </h3>

              {total === 0 ? (
                <p className="group-empty">No tickets</p>
              ) : (
                <ul className="ticket-rows">
                  {(group?.items ?? []).map((ticket) => (
                    <TicketRow
                      key={ticket.reference}
                      ticket={ticket}
                      typeLabel={typeLabel}
                      onOpen={() => onOpen(ticket.reference)}
                    />
                  ))}
                </ul>
              )}

              {pageCount > 1 && (
                <div className="group-pager">
                  <button
                    className="nav-btn"
                    disabled={page <= 1 || busy}
                    onClick={() => onGroupPage(schema.type, page - 1)}
                  >
                    ← Previous
                  </button>
                  <span className="muted-note">
                    Page {page} of {pageCount}
                  </span>
                  <button
                    className="nav-btn"
                    disabled={page >= pageCount || busy}
                    onClick={() => onGroupPage(schema.type, page + 1)}
                  >
                    Next →
                  </button>
                </div>
              )}
            </section>
          );
        })}
    </div>
  );
}

function TicketRow({
  ticket,
  typeLabel,
  onOpen,
}: {
  ticket: TicketSummary;
  typeLabel: LabelLookup;
  onOpen: () => void;
}) {
  return (
    <li>
      {/* One button per row: the whole line is the click target, and it stays
          keyboard-reachable without faking button semantics on a table row. */}
      <button className="ticket-row" onClick={onOpen}>
        <span className="row-ref">#{ticket.reference}</span>
        <span className="row-type">{typeLabel(ticket.type)}</span>
        <span className="row-email">{ticket.customer.email}</span>
        <span className="row-date">{formatDate(ticket.updated_at)}</span>
        <span className={`badge status-${ticket.status}`}>{statusLabel(ticket.status)}</span>
      </button>
    </li>
  );
}
