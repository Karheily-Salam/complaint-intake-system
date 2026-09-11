import { useCallback, useEffect, useMemo, useState } from "react";
import { api, staffApi, type TicketFilters } from "@/api/client";
import { buildLabelLookup, buildTypeLookup, formatTimestamp, statusLabel } from "@/lib/labels";
import {
  StaffRequestError,
  clearStaffKey,
  getStaffKey,
  setStaffKey,
  type StaffAuthState,
} from "@/lib/staffAuth";
import type { ComplaintSchema, MessageOut, TicketDetail, TicketSummary } from "@/types/api";

const STATUSES = ["new", "in_progress", "resolved", "closed"];
const PAGE_SIZE = 20;

/**
 * Internal support dashboard: the real tickets produced by email intake.
 *
 * Everything on this page comes from the authenticated staff API. The key is
 * entered here by the agent rather than shipped in the bundle, and the server
 * enforces access on every request - hiding the page would not be a boundary.
 */
export function SupportDashboard() {
  const [key, setKey] = useState<string | null>(() => getStaffKey());
  const [authState, setAuthState] = useState<StaffAuthState>("ok");
  const [authMessage, setAuthMessage] = useState<string | null>(null);

  const [tickets, setTickets] = useState<TicketSummary[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");

  const [selected, setSelected] = useState<TicketDetail | null>(null);
  const [schemas, setSchemas] = useState<ComplaintSchema[]>([]);
  const [busy, setBusy] = useState(false);

  const labelFor = useMemo(() => buildLabelLookup(schemas), [schemas]);
  const typeLabel = useMemo(() => buildTypeLookup(schemas), [schemas]);

  useEffect(() => {
    api.listSchemas().then(setSchemas).catch(() => setSchemas([]));
  }, []);

  const signOut = useCallback(() => {
    clearStaffKey();
    setKey(null);
    setTickets(null);
    setSelected(null);
    setAuthState("ok");
    setAuthMessage(null);
  }, []);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof StaffRequestError) {
        setAuthState(e.state);
        setAuthMessage(e.message);
        if (e.state === "unauthorized") {
          clearStaffKey();
          setKey(null);
        }
      } else {
        setAuthState("error");
        setAuthMessage(e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  const load = useCallback(
    async (filters: TicketFilters) => {
      if (!key) return;
      setBusy(true);
      try {
        const result = await staffApi.listTickets(key, { ...filters, pageSize: PAGE_SIZE });
        setTickets(result.items);
        setTotal(result.total);
        setAuthState("ok");
        setAuthMessage(null);
      } catch (e) {
        setTickets(null);
        handleError(e);
      } finally {
        setBusy(false);
      }
    },
    [key, handleError],
  );

  useEffect(() => {
    if (key) void load({ q: search, status, type, page });
    // Re-runs when the agent changes a filter or pages; `search` is applied
    // via the form submit below rather than on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, status, type, page]);

  async function openTicket(reference: string) {
    if (!key) return;
    try {
      setSelected(await staffApi.getTicket(key, reference));
    } catch (e) {
      handleError(e);
    }
  }

  async function changeStatus(reference: string, next: string) {
    if (!key) return;
    setBusy(true);
    try {
      const updated = await staffApi.updateStatus(key, reference, next);
      setSelected(updated);
      await load({ q: search, status, type, page });
    } catch (e) {
      handleError(e);
    } finally {
      setBusy(false);
    }
  }

  // ---- not signed in, or the server has no key configured ----

  if (!key || authState === "unauthorized" || authState === "not-configured") {
    return (
      <StaffSignIn
        state={authState}
        message={authMessage}
        onSubmit={(entered) => {
          setStaffKey(entered);
          setKey(entered);
          setAuthState("ok");
          setAuthMessage(null);
          setPage(1);
        }}
      />
    );
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  if (selected) {
    return (
      <TicketDetailView
        ticket={selected}
        busy={busy}
        labelFor={labelFor}
        typeLabel={typeLabel}
        onBack={() => setSelected(null)}
        onStatusChange={(next) => changeStatus(selected.reference, next)}
      />
    );
  }

  return (
    <div className="support-dash">
      <header className="dash-header">
        <div>
          <h2>Support dashboard</h2>
          <span className="muted-note">
            Internal — real tickets created by email intake
          </span>
        </div>
        <button className="nav-btn" onClick={signOut}>
          Sign out
        </button>
      </header>

      <form
        className="dash-filters"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(1);
          void load({ q: search, status, type, page: 1 });
        }}
      >
        <input
          type="search"
          placeholder="Search ticket number or customer email…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search tickets"
        />
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {statusLabel(s)}
            </option>
          ))}
        </select>
        <select
          value={type}
          onChange={(e) => {
            setType(e.target.value);
            setPage(1);
          }}
          aria-label="Filter by complaint type"
        >
          <option value="">All types</option>
          {schemas.map((s) => (
            <option key={s.type} value={s.type}>
              {s.label}
            </option>
          ))}
        </select>
        <button className="nav-btn" type="submit">
          Search
        </button>
      </form>

      {authState === "error" && <p className="error">{authMessage}</p>}
      {!tickets && busy && <p className="empty">Loading tickets…</p>}
      {tickets?.length === 0 && (
        <p className="empty">
          {search || status || type
            ? "No tickets match those filters."
            : "No tickets yet. They appear here once an email conversation is complete."}
        </p>
      )}

      {tickets && tickets.length > 0 && (
        <>
          <table className="dash-table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Type</th>
                <th>Customer</th>
                <th>Status</th>
                <th>Created</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <tr key={t.reference} onClick={() => openTicket(t.reference)}>
                  <td className="ticket-ref">#{t.reference}</td>
                  <td>{typeLabel(t.type)}</td>
                  <td>{t.customer.email}</td>
                  <td>
                    <span className={`badge status-${t.status}`}>{statusLabel(t.status)}</span>
                  </td>
                  <td className="muted-note">{formatTimestamp(t.created_at)}</td>
                  <td className="muted-note">{formatTimestamp(t.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="dash-pager">
            <button
              className="nav-btn"
              disabled={page <= 1 || busy}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              ← Previous
            </button>
            <span className="muted-note">
              Page {page} of {pageCount} · {total} ticket{total === 1 ? "" : "s"}
            </span>
            <button
              className="nav-btn"
              disabled={page >= pageCount || busy}
              onClick={() => setPage((p) => p + 1)}
            >
              Next →
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ sign in

function StaffSignIn({
  state,
  message,
  onSubmit,
}: {
  state: StaffAuthState;
  message: string | null;
  onSubmit: (key: string) => void;
}) {
  const [value, setValue] = useState("");

  return (
    <div className="staff-signin card">
      <h2>Staff access</h2>

      {state === "not-configured" ? (
        <>
          <p className="error">
            The staff API is not configured on this server.
          </p>
          <p className="muted-note">
            {message ?? "No staff key is set."} The server refuses staff requests entirely
            rather than serving customer data without authentication, so there is nothing
            to sign in to until an operator sets <code>STAFF_API_KEY</code> in the
            deployment configuration.
          </p>
        </>
      ) : (
        <>
          <p className="muted-note">
            This dashboard shows real customer complaints. Enter the staff API key to
            continue — it is held for this browser tab only and is never stored in the
            application.
          </p>
          {state === "unauthorized" && (
            <p className="error">{message ?? "That key was not accepted."}</p>
          )}
          {state === "error" && message && <p className="error">{message}</p>}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (value.trim()) onSubmit(value.trim());
            }}
          >
            <label htmlFor="staff-key">Staff API key</label>
            <input
              id="staff-key"
              type="password"
              autoComplete="off"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="Paste the key"
            />
            <button className="primary" type="submit" disabled={!value.trim()}>
              Sign in
            </button>
          </form>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- detail

function TicketDetailView({
  ticket,
  busy,
  labelFor,
  typeLabel,
  onBack,
  onStatusChange,
}: {
  ticket: TicketDetail;
  busy: boolean;
  labelFor: (key: string) => string;
  typeLabel: (key: string) => string;
  onBack: () => void;
  onStatusChange: (status: string) => void;
}) {
  const fields = (ticket.structured_data?.fields as Record<string, string> | undefined) ?? {};
  const entries = Object.entries(fields).filter(([, v]) => v);
  const messages = ticket.conversation?.messages ?? [];

  return (
    <div className="support-dash">
      <button className="nav-btn back-link" onClick={onBack}>
        ← Back to tickets
      </button>

      <header className="dash-header">
        <div>
          <h2>Ticket #{ticket.reference}</h2>
          <span className="muted-note">{typeLabel(ticket.type)}</span>
        </div>
        <label className="status-control">
          Status
          <select
            value={ticket.status}
            disabled={busy}
            onChange={(e) => onStatusChange(e.target.value)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {statusLabel(s)}
              </option>
            ))}
          </select>
        </label>
      </header>

      <div className="layout-2col">
        <section className="card">
          <h3>Customer</h3>
          <div className="field-row">
            <span className="k">Email</span>
            <span>{ticket.customer.email}</span>
          </div>
          <div className="field-row">
            <span className="k">Name</span>
            <span>{ticket.customer.name ?? "(not provided)"}</span>
          </div>
          <div className="field-row">
            <span className="k">Created</span>
            <span>{formatTimestamp(ticket.created_at)}</span>
          </div>
          <div className="field-row">
            <span className="k">Updated</span>
            <span>{formatTimestamp(ticket.updated_at)}</span>
          </div>

          <h3>Collected information</h3>
          {entries.length === 0 ? (
            <p className="empty">No structured fields captured.</p>
          ) : (
            entries.map(([k, v]) => (
              <div className="field-row" key={k}>
                <span className="k">{labelFor(k)}</span>
                <span>{v}</span>
              </div>
            ))
          )}

          <h3>Summary</h3>
          <p>{ticket.concise_description}</p>
        </section>

        <section className="card">
          <h3>Conversation</h3>
          <p className="muted-note">
            The email exchange that produced this ticket, oldest first — useful for seeing
            how each value was collected.
          </p>
          {messages.length === 0 ? (
            <p className="empty">No messages recorded.</p>
          ) : (
            <div className="thread">
              {messages.map((m) => (
                <ConversationMessage key={m.id} message={m} />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function ConversationMessage({ message }: { message: MessageOut }) {
  const inbound = message.direction === "inbound";
  return (
    <article className={`email ${inbound ? "inbound" : "outbound"}`}>
      <header>
        <span className="email-dir">{inbound ? "Customer" : "System"}</span>
        <span className="email-time">{formatTimestamp(message.created_at)}</span>
      </header>
      <dl className="email-meta">
        <div>
          <dt>From</dt>
          <dd>{message.sender}</dd>
        </div>
        {message.subject && (
          <div>
            <dt>Subject</dt>
            <dd>{message.subject}</dd>
          </div>
        )}
      </dl>
      <p className="email-body">{message.body}</p>
    </article>
  );
}
