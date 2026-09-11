import { useEffect, useMemo, useState } from "react";
import { api } from "@/api/client";
import { buildLabelLookup, buildTypeLookup, formatTimestamp, statusLabel } from "@/lib/labels";
import { FREEFORM, SCENARIOS, type Scenario } from "@/lib/scenarios";
import { SUPPORT_EMAIL } from "@/pages/Overview";
import type { ComplaintSchema, IntakeResult, MessageOut } from "@/types/api";

/**
 * The customer's side of the product, shown as what it actually is: an email
 * thread. Nothing here is a complaint form - the messages are emails, and
 * every system reply is produced by the backend engine.
 */
export function MailboxDemo({ onTicketCreated }: { onTicketCreated?: () => void }) {
  const [scenario, setScenario] = useState<Scenario>(SCENARIOS[0]);
  const [step, setStep] = useState(0);
  const [result, setResult] = useState<IntakeResult | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [schemas, setSchemas] = useState<ComplaintSchema[]>([]);

  useEffect(() => {
    api.listSchemas().then(setSchemas).catch(() => setSchemas([]));
  }, []);

  const labelFor = useMemo(() => buildLabelLookup(schemas), [schemas]);
  const typeLabel = useMemo(() => buildTypeLookup(schemas), [schemas]);

  const isFreeform = scenario.id === FREEFORM.id;
  const conversationId = result?.conversation.id;
  const nextScripted = isFreeform ? null : (scenario.messages[step] ?? null);
  const finished = !isFreeform && step >= scenario.messages.length;

  function selectScenario(next: Scenario) {
    setScenario(next);
    setStep(0);
    setResult(null);
    setError(null);
    setDraft("");
  }

  async function sendMessage(body: string) {
    if (!body.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.sendCustomerEmail({
        from_addr: scenario.from,
        customer_name: scenario.customerName || undefined,
        subject: scenario.subject,
        body,
        conversation_id: conversationId,
      });
      setResult(res);
      if (!isFreeform) setStep((s) => s + 1);
      setDraft("");
      if (res.ticket_reference) onTicketCreated?.();
    } catch (e) {
      setError(
        e instanceof Error
          ? `Could not reach the intake service: ${e.message}`
          : "Something went wrong sending that email.",
      );
    } finally {
      setBusy(false);
    }
  }

  const messages = result?.conversation.messages ?? [];
  const complaint = result?.conversation.complaint;
  const collected = (complaint?.fields ?? []).filter((f) => f.value);

  return (
    <>
      <div className="demo-banner">
        <strong>This is a demo.</strong> It simulates the email conversation in your
        browser using the same engine that handles real mail. To send a real complaint,
        email <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
      </div>
      <div className="demo-layout">
        <aside className="card scenario-picker">
          <h2>Demo scenarios</h2>
          <p className="muted-note">
            Each one sends real emails through the real engine. Nothing is scripted on the
            system's side.
          </p>
          {[...SCENARIOS, FREEFORM].map((s) => (
            <button
              key={s.id}
              className={`scenario-btn ${s.id === scenario.id ? "active" : ""}`}
              onClick={() => selectScenario(s)}
            >
              <strong>{s.name}</strong>
              <span>{s.teaches}</span>
            </button>
          ))}
        </aside>

        <section className="card mail-pane">
          <header className="mail-header">
            <div>
              <h2>{scenario.subject}</h2>
              <span className="muted-note">
                {conversationId
                  ? `Thread with ${scenario.from}`
                  : "No messages yet — send the first email"}
              </span>
            </div>
            {result && (
              <span className="badge">{statusLabel(result.conversation.status)}</span>
            )}
          </header>

          {messages.length === 0 && !busy && (
            <p className="empty mail-empty">
              This is a mailbox, not a form. Send the first email to start the conversation.
            </p>
          )}

          <div className="thread">
            {messages.map((m) => (
              <EmailMessage key={m.id} message={m} subject={scenario.subject} />
            ))}
            {busy && <p className="empty">Delivering email…</p>}
          </div>

          {error && (
            <p className="error">
              {error}{" "}
              <button className="nav-btn" onClick={() => selectScenario(scenario)}>
                Reset
              </button>
            </p>
          )}

          <footer className="composer">
            {isFreeform ? (
              <>
                <label>Write an email as the customer</label>
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="e.g. My withdrawal has not arrived and I am worried."
                />
                <button
                  className="primary"
                  disabled={busy || !draft.trim()}
                  onClick={() => sendMessage(draft)}
                >
                  {busy ? "Sending…" : "Send email"}
                </button>
              </>
            ) : finished ? (
              <div className="composer-done">
                <p className="muted-note">
                  Scenario complete
                  {result?.ticket_reference
                    ? ` — ticket ${result.ticket_reference} was created. Open the Support inbox tab to see what support received.`
                    : "."}
                </p>
                <button className="nav-btn" onClick={() => selectScenario(scenario)}>
                  Run it again
                </button>
              </div>
            ) : (
              <>
                <label>Next email from {scenario.customerName || scenario.from}</label>
                <blockquote className="queued">{nextScripted}</blockquote>
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => sendMessage(nextScripted ?? "")}
                >
                  {busy ? "Sending…" : step === 0 ? "Send first email" : "Send this reply"}
                </button>
              </>
            )}
          </footer>
        </section>

        <aside className="card extraction-pane">
          <h2>What the engine understood</h2>
          {!result && (
            <p className="empty">
              Structured data appears here as the engine extracts it from the emails.
            </p>
          )}
          {result && (
            <>
              <div className="field-row">
                <span className="k">Complaint type</span>
                <span>
                  {result.complaint_type ? (
                    typeLabel(result.complaint_type)
                  ) : (
                    <em className="pill-missing">not yet classified</em>
                  )}
                </span>
              </div>

              <h3>Collected so far</h3>
              {collected.length === 0 ? (
                <p className="empty">Nothing extracted yet.</p>
              ) : (
                collected.map((f) => (
                  <div className="field-row" key={f.key}>
                    <span className="k">{labelFor(f.key)}</span>
                    <span>
                      {f.value}
                      {f.validation_error && (
                        <div className="pill-missing">{f.validation_error}</div>
                      )}
                    </span>
                  </div>
                ))
              )}

              {result.invalid_fields.length > 0 && (
                <p className="pill-missing">
                  Needs correcting: {result.invalid_fields.map(labelFor).join(", ")}
                </p>
              )}
              {result.missing_fields.length > 0 && (
                <>
                  <h3>Still needed</h3>
                  <p className="muted-note">
                    {result.missing_fields.map(labelFor).join(", ")} — asked for one at a
                    time, in order.
                  </p>
                </>
              )}

              {result.ticket_reference && (
                <p className="ticket-created">
                  Ticket <strong>{result.ticket_reference}</strong> created
                </p>
              )}
            </>
          )}
          </aside>
      </div>
    </>
  );
}

function EmailMessage({ message, subject }: { message: MessageOut; subject: string }) {
  const inbound = message.direction === "inbound";
  return (
    <article className={`email ${inbound ? "inbound" : "outbound"}`}>
      <header>
        <span className="email-dir">{inbound ? "Customer" : "Support system"}</span>
        <span className="email-time">{formatTimestamp(message.created_at)}</span>
      </header>
      <dl className="email-meta">
        <div>
          <dt>From</dt>
          <dd>{message.sender}</dd>
        </div>
        <div>
          <dt>To</dt>
          <dd>{message.recipient ?? SUPPORT_EMAIL}</dd>
        </div>
        <div>
          <dt>Subject</dt>
          <dd>{message.subject ?? (inbound ? subject : `Re: ${subject}`)}</dd>
        </div>
      </dl>
      <p className="email-body">{message.body}</p>
    </article>
  );
}
