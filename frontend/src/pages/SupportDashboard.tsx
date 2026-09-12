import { useCallback, useEffect, useMemo, useState } from "react";
import { api, staffApi } from "@/api/client";
import { CorrectionsPanel } from "@/components/ml/CorrectionsPanel";
import { IncidentsPanel } from "@/components/ml/IncidentsPanel";
import { ModelHealthPanel } from "@/components/ml/ModelHealthPanel";
import { SimilarTickets } from "@/components/ml/SimilarTickets";
import { TicketBrowser } from "@/components/tickets/TicketBrowser";
import { TicketDetailPanel } from "@/components/tickets/TicketDetailPanel";
import {
  orderSchemas,
  type TicketGroupData,
  type TicketQueryState,
} from "@/components/tickets/ticketGroups";
import { useI18n } from "@/i18n";
import { buildLabelLookup, buildTypeLookup, replySubject, statusLabel } from "@/lib/labels";
import {
  StaffRequestError,
  clearStaffKey,
  getStaffKey,
  setStaffKey,
  type StaffAuthState,
} from "@/lib/staffAuth";
import type {
  ComplaintSchema,
  TicketDetail,
  TicketReplyIn,
  TicketReplyOut,
} from "@/types/api";

const STATUSES = ["new", "in_progress", "resolved", "closed"];
const PAGE_SIZE = 20;
const NO_QUERY: TicketQueryState = { status: "", q: "" };

/**
 * Internal support dashboard: the real tickets produced by email intake.
 *
 * Everything on this page comes from the authenticated staff API. The key is
 * entered here by the agent rather than shipped in the bundle, and the server
 * enforces access on every request - hiding the page would not be a boundary.
 *
 * Grouping and pagination
 * ----------------------
 * Each complaint-type group is fetched as its own server-filtered,
 * server-paginated request (`?type=...&status=...&page=...`). Grouping a single
 * mixed page in the browser would have been simpler but wrong: the per-group
 * counts would describe only the rows that happened to land on the current
 * page, and a group could look empty while holding tickets on page 3. Fetching
 * everything and grouping client-side would be worse - this is real customer
 * data. One bounded request per group keeps every count true and every filter
 * server-side, and needs no change to the existing API.
 *
 * The presentation is shared with the public demo (see components/tickets).
 * Only the data source and the available actions differ.
 */
export function SupportDashboard() {
  const { t } = useI18n();
  const [key, setKey] = useState<string | null>(() => getStaffKey());
  const [authState, setAuthState] = useState<StaffAuthState>("ok");
  const [authMessage, setAuthMessage] = useState<string | null>(null);

  const [groups, setGroups] = useState<Record<string, TicketGroupData>>({});
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState<TicketQueryState>(NO_QUERY);

  const [selected, setSelected] = useState<TicketDetail | null>(null);
  const [schemas, setSchemas] = useState<ComplaintSchema[]>([]);
  const [busy, setBusy] = useState(false);

  const typeLabel = useMemo(() => buildTypeLookup(schemas, t), [schemas, t]);
  const fieldLabel = useMemo(() => buildLabelLookup(schemas, t), [schemas, t]);
  const types = useMemo(() => orderSchemas(schemas).map((s) => s.type), [schemas]);
  // A stable dependency for the load effect: the array identity changes on
  // every render, the joined string only when the schema set actually changes.
  const typesKey = types.join(",");

  useEffect(() => {
    api.listSchemas().then(setSchemas).catch(() => setSchemas([]));
  }, []);

  const signOut = useCallback(() => {
    clearStaffKey();
    setKey(null);
    setGroups({});
    setLoaded(false);
    setSelected(null);
    setAuthState("ok");
    setAuthMessage(null);
  }, []);

  const handleError = useCallback((e: unknown) => {
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
  }, []);

  /** One group, one bounded request. Filtering and paging stay on the server. */
  const fetchGroup = useCallback(
    async (type: string, page: number, q: TicketQueryState) => {
      if (!key) return;
      const result = await staffApi.listTickets(key, {
        q: q.q,
        status: q.status,
        type,
        page,
        pageSize: PAGE_SIZE,
      });
      setGroups((prev) => ({ ...prev, [type]: { items: result.items, total: result.total, page } }));
    },
    [key],
  );

  const loadAll = useCallback(
    async (q: TicketQueryState) => {
      if (!key || types.length === 0) return;
      setBusy(true);
      try {
        await Promise.all(types.map((type) => fetchGroup(type, 1, q)));
        setAuthState("ok");
        setAuthMessage(null);
        setLoaded(true);
      } catch (e) {
        handleError(e);
      } finally {
        setBusy(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, typesKey, fetchGroup, handleError],
  );

  useEffect(() => {
    void loadAll(query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, typesKey, query]);

  async function changeGroupPage(type: string, page: number) {
    setBusy(true);
    try {
      await fetchGroup(type, page, query);
    } catch (e) {
      handleError(e);
    } finally {
      setBusy(false);
    }
  }

  async function openTicket(reference: string) {
    if (!key) return;
    try {
      setSelected(await staffApi.getTicket(key, reference));
    } catch (e) {
      handleError(e);
    }
  }

  async function sendReply(reference: string, payload: TicketReplyIn) {
    if (!key) throw new Error(t.dashboard.notSignedIn);
    try {
      return await staffApi.replyToTicket(key, reference, payload);
    } catch (e) {
      // An expired or revoked key should return the agent to the sign-in
      // screen rather than being reported as a failed send; anything else is
      // the composer's to display.
      if (e instanceof StaffRequestError && e.state !== "error") handleError(e);
      throw e;
    }
  }

  async function changeStatus(reference: string, next: string) {
    if (!key) return;
    setBusy(true);
    try {
      setSelected(await staffApi.updateStatus(key, reference, next));
      // The row may now belong to a different status filter, so refresh rather
      // than leaving a stale badge behind.
      await loadAll(query);
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
        }}
      />
    );
  }

  if (selected) {
    return (
      <TicketDetailPanel
        ticket={selected}
        schemas={schemas}
        typeLabel={typeLabel}
        onBack={() => setSelected(null)}
        statusControl={
          <label className="status-control">
            <span className="sr-only">{t.tickets.status}</span>
            <select
              value={selected.status}
              disabled={busy}
              onChange={(e) => changeStatus(selected.reference, e.target.value)}
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {statusLabel(s, t)}
                </option>
              ))}
            </select>
          </label>
        }
        insights={
          <>
            <SimilarTickets
              staffKey={key}
              reference={selected.reference}
              typeLabel={typeLabel}
              fieldLabel={fieldLabel}
              onOpen={openTicket}
            />
            <CorrectionsPanel
              staffKey={key}
              ticket={selected}
              schemas={schemas}
              typeLabel={typeLabel}
              fieldLabel={fieldLabel}
              onCorrected={setSelected}
            />
          </>
        }
        actions={
          <StaffReplyActions
            ticket={selected}
            onReply={(payload) => sendReply(selected.reference, payload)}
            onReplied={() => openTicket(selected.reference)}
          />
        }
      />
    );
  }

  return (
    <TicketBrowser
      title={t.dashboard.title}
      subtitle={t.dashboard.subtitle}
      headerAction={
        <button className="nav-btn" onClick={signOut}>
          {t.dashboard.signOut}
        </button>
      }
      schemas={schemas}
      typeLabel={typeLabel}
      groups={groups}
      pageSize={PAGE_SIZE}
      busy={busy}
      loaded={loaded}
      notice={
        <>
          {authState === "error" && <p className="error">{authMessage}</p>}
          <IncidentsPanel staffKey={key} typeLabel={typeLabel} onOpen={openTicket} />
          <ModelHealthPanel staffKey={key} />
        </>
      }
      emptyMessage={t.dashboard.empty}
      onQueryChange={setQuery}
      onOpen={openTicket}
      onGroupPage={changeGroupPage}
    />
  );
}

// ------------------------------------------------------------ staff actions

function StaffReplyActions({
  ticket,
  onReply,
  onReplied,
}: {
  ticket: TicketDetail;
  onReply: (payload: TicketReplyIn) => Promise<TicketReplyOut>;
  onReplied: () => void;
}) {
  const { t } = useI18n();
  const [composing, setComposing] = useState(false);
  const [sent, setSent] = useState<TicketReplyOut | null>(null);

  async function send(payload: TicketReplyIn) {
    const outcome = await onReply(payload);
    setSent(outcome);
    setComposing(false);
    // Reload so the agent's own message appears in the history below.
    onReplied();
  }

  return (
    <>
      {sent && (
        <p className={sent.delivered ? "reply-sent" : "reply-simulated"}>
          <strong>
            {sent.delivered ? t.dashboard.replySent : t.dashboard.replyNotDelivered}
          </strong>{" "}
          {sent.detail}
        </p>
      )}
      {composing ? (
        <ReplyComposer ticket={ticket} onCancel={() => setComposing(false)} onSend={send} />
      ) : (
        <button
          className="primary reply-open"
          onClick={() => {
            setSent(null);
            setComposing(true);
          }}
        >
          {t.dashboard.replyOpen}
        </button>
      )}
    </>
  );
}

/**
 * The reply composer.
 *
 * The recipient is shown but not editable, and is not sent to the server at
 * all: the backend addresses the message from the ticket. That makes replying
 * to the wrong person impossible rather than merely discouraged.
 */
function ReplyComposer({
  ticket,
  onCancel,
  onSend,
}: {
  ticket: TicketDetail;
  onCancel: () => void;
  onSend: (payload: TicketReplyIn) => Promise<void>;
}) {
  const { t } = useI18n();
  const [subject, setSubject] = useState(() => replySubject(ticket.conversation?.subject));
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!body.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      await onSend({ body: body.trim(), subject: subject.trim() || undefined });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSending(false);
    }
  }

  return (
    <form className="reply-composer" onSubmit={submit}>
      <h3>{t.dashboard.replyTitle}</h3>

      <div className="field-row">
        <span className="k">{t.dashboard.replyTo}</span>
        <span>
          {ticket.customer.email}
          <span className="muted-note"> {t.dashboard.replyFromTicket}</span>
        </span>
      </div>

      <label htmlFor="reply-subject">{t.dashboard.replySubject}</label>
      <input
        id="reply-subject"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        disabled={sending}
      />

      <label htmlFor="reply-body">{t.dashboard.replyMessage}</label>
      <textarea
        id="reply-body"
        rows={9}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        disabled={sending}
        placeholder={t.dashboard.replyPlaceholder}
      />

      {error && <p className="error">{error}</p>}

      <div className="composer-actions">
        <button type="button" className="nav-btn" onClick={onCancel} disabled={sending}>
          {t.common.cancel}
        </button>
        <button type="submit" className="primary" disabled={sending || !body.trim()}>
          {sending ? t.common.sending : t.dashboard.replySend}
        </button>
      </div>
    </form>
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
  const { t } = useI18n();
  const [value, setValue] = useState("");

  return (
    <div className="staff-signin card">
      <h2>{t.dashboard.signInTitle}</h2>

      {state === "not-configured" ? (
        <>
          <p className="error">{t.dashboard.notConfigured}</p>
          <p className="muted-note">
            {message ?? t.dashboard.noKeySet} {t.dashboard.notConfiguredBody}
          </p>
        </>
      ) : (
        <>
          <p className="muted-note">{t.dashboard.signInBody}</p>
          {state === "unauthorized" && (
            <p className="error">{message ?? t.dashboard.keyRejected}</p>
          )}
          {state === "error" && message && <p className="error">{message}</p>}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (value.trim()) onSubmit(value.trim());
            }}
          >
            <label htmlFor="staff-key">{t.dashboard.keyLabel}</label>
            <input
              id="staff-key"
              type="password"
              autoComplete="off"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={t.dashboard.keyPlaceholder}
            />
            <button className="primary" type="submit" disabled={!value.trim()}>
              {t.dashboard.signIn}
            </button>
          </form>
        </>
      )}
    </div>
  );
}
