import { useEffect, useMemo, useState } from "react";
import { api } from "@/api/client";
import { useI18n } from "@/i18n";
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
  const { t } = useI18n();
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

  const labelFor = useMemo(() => buildLabelLookup(schemas, t), [schemas, t]);
  const typeLabel = useMemo(() => buildTypeLookup(schemas, t), [schemas, t]);

  /** Scenario name and summary come from the translation, keyed by id. */
  const describe = (item: Scenario) =>
    t.demo.scenarios[item.id as keyof typeof t.demo.scenarios] ?? {
      name: item.name,
      teaches: item.teaches,
    };

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
      setError(e instanceof Error ? t.demo.unreachable(e.message) : t.demo.sendFailed);
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
        <strong>{t.demo.bannerLead}</strong> {t.demo.bannerBody}{" "}
        <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
      </div>
      <div className="demo-layout">
        <aside className="card scenario-picker">
          <h2>{t.demo.scenariosTitle}</h2>
          <p className="muted-note">{t.demo.scenariosNote}</p>
          {[...SCENARIOS, FREEFORM].map((s) => (
            <button
              key={s.id}
              className={`scenario-btn ${s.id === scenario.id ? "active" : ""}`}
              onClick={() => selectScenario(s)}
            >
              <strong>{describe(s).name}</strong>
              <span>{describe(s).teaches}</span>
            </button>
          ))}
        </aside>

        <section className="card mail-pane">
          <header className="mail-header">
            <div>
              <h2>{scenario.subject}</h2>
              <span className="muted-note">
                {conversationId ? t.demo.threadWith(scenario.from) : t.demo.noMessages}
              </span>
            </div>
            {result && (
              <span className="badge">{statusLabel(result.conversation.status, t)}</span>
            )}
          </header>

          {messages.length === 0 && !busy && (
            <p className="empty mail-empty">{t.demo.mailboxEmpty}</p>
          )}

          <div className="thread">
            {messages.map((m) => (
              <EmailMessage key={m.id} message={m} subject={scenario.subject} />
            ))}
            {busy && <p className="empty">{t.demo.delivering}</p>}
          </div>

          {error && (
            <p className="error">
              {error}{" "}
              <button className="nav-btn" onClick={() => selectScenario(scenario)}>
                {t.common.reset}
              </button>
            </p>
          )}

          <footer className="composer">
            {isFreeform ? (
              <>
                <label>{t.demo.writeAsCustomer}</label>
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={t.demo.writePlaceholder}
                />
                <button
                  className="primary"
                  disabled={busy || !draft.trim()}
                  onClick={() => sendMessage(draft)}
                >
                  {busy ? t.common.sending : t.demo.sendEmail}
                </button>
              </>
            ) : finished ? (
              <div className="composer-done">
                <p className="muted-note">
                  {t.demo.scenarioComplete}
                  {result?.ticket_reference
                    ? t.demo.scenarioCompleteTicket(result.ticket_reference)
                    : "."}
                </p>
                <button className="nav-btn" onClick={() => selectScenario(scenario)}>
                  {t.demo.runAgain}
                </button>
              </div>
            ) : (
              <>
                <label>{t.demo.nextEmailFrom(scenario.customerName || scenario.from)}</label>
                <blockquote className="queued">{nextScripted}</blockquote>
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => sendMessage(nextScripted ?? "")}
                >
                  {busy ? t.common.sending : step === 0 ? t.demo.sendFirst : t.demo.sendReply}
                </button>
              </>
            )}
          </footer>
        </section>

        <aside className="card extraction-pane">
          <h2>{t.demo.understoodTitle}</h2>
          {!result && (
            <p className="empty">{t.demo.understoodEmpty}</p>
          )}
          {result && (
            <>
              <div className="field-row">
                <span className="k">{t.demo.complaintType}</span>
                <span>
                  {result.complaint_type ? (
                    typeLabel(result.complaint_type)
                  ) : (
                    <em className="pill-missing">{t.demo.notClassified}</em>
                  )}
                </span>
              </div>

              <h3>{t.demo.collectedSoFar}</h3>
              {collected.length === 0 ? (
                <p className="empty">{t.demo.nothingExtracted}</p>
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
                  {t.demo.needsCorrecting(result.invalid_fields.map(labelFor).join(", "))}
                </p>
              )}
              {result.missing_fields.length > 0 && (
                <>
                  <h3>{t.demo.stillNeeded}</h3>
                  <p className="muted-note">
                    {t.demo.stillNeededNote(result.missing_fields.map(labelFor).join(", "))}
                  </p>
                </>
              )}

              {result.ticket_reference && (
                <p className="ticket-created">
                  {t.demo.ticketCreated} <strong>{result.ticket_reference}</strong>{" "}
                  {t.demo.ticketCreatedSuffix}
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
  const { t, lang } = useI18n();
  const inbound = message.direction === "inbound";
  return (
    <article className={`email ${inbound ? "inbound" : "outbound"}`}>
      <header>
        <span className="email-dir">{inbound ? t.demo.customer : t.demo.supportSystem}</span>
        <span className="email-time">{formatTimestamp(message.created_at, lang)}</span>
      </header>
      <dl className="email-meta">
        <div>
          <dt>{t.demo.from}</dt>
          <dd>{message.sender}</dd>
        </div>
        <div>
          <dt>{t.demo.to}</dt>
          <dd>{message.recipient ?? SUPPORT_EMAIL}</dd>
        </div>
        <div>
          <dt>{t.demo.subject}</dt>
          <dd>{message.subject ?? (inbound ? subject : `Re: ${subject}`)}</dd>
        </div>
      </dl>
      <p className="email-body">{message.body}</p>
    </article>
  );
}
