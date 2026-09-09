import { useState } from "react";
import { api } from "@/api/client";
import type { IntakeResult } from "@/types/api";

const SAMPLE =
  "Hello, I have been trying to withdraw money since yesterday and it doesn't work. " +
  "I tried several times. My user id is U-482913 and my account email is jane.doe@example.com.";

export function MailboxSimulator() {
  const [fromAddr, setFromAddr] = useState("jane@example.com");
  const [name, setName] = useState("Jane");
  const [subject, setSubject] = useState("Cannot withdraw my funds");
  const [body, setBody] = useState(SAMPLE);
  const [result, setResult] = useState<IntakeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const conversationId = result?.conversation.id;

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.sendCustomerEmail({
        from_addr: fromAddr,
        customer_name: name || undefined,
        subject: subject || undefined,
        body,
        conversation_id: conversationId,
      });
      setResult(res);
      setBody("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setResult(null);
    setBody(SAMPLE);
    setError(null);
  }

  const complaint = result?.conversation.complaint;

  return (
    <div className="layout-2col">
      <div className="card">
        <h2>{conversationId ? `Reply in conversation #${conversationId}` : "New customer email"}</h2>
        <label>From</label>
        <input value={fromAddr} onChange={(e) => setFromAddr(e.target.value)} disabled={!!conversationId} />
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} disabled={!!conversationId} />
        <label>Subject</label>
        <input value={subject} onChange={(e) => setSubject(e.target.value)} disabled={!!conversationId} />
        <label>Message (write freely, no form required)</label>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} />
        <button className="primary" onClick={send} disabled={busy || !body.trim()}>
          {busy ? "Sending..." : "Send email"}
        </button>{" "}
        {conversationId && (
          <button className="nav-btn" onClick={reset}>
            Start new conversation
          </button>
        )}
        {error && <p className="error">{error}</p>}
      </div>

      <div className="card">
        <h2>System response</h2>
        {!result && <p className="empty">Send a message to see how the AI engine responds.</p>}
        {result && (
          <>
            <p>
              <span className="badge">type: {complaint?.type ?? "unclassified"}</span>{" "}
              <span className="badge">status: {result.conversation.status}</span>{" "}
              {result.is_complete && <span className="badge" style={{ color: "var(--ok)" }}>complete</span>}
              {result.ticket_reference && (
                <span className="badge" style={{ color: "var(--ok)" }}>
                  ticket {result.ticket_reference}
                </span>
              )}
            </p>

            <h3 style={{ fontSize: "0.85rem" }}>Collected information</h3>
            {complaint?.fields.length ? (
              complaint.fields.map((f) => (
                <div className="field-row" key={f.key}>
                  <span className="k">{f.key}</span>
                  <span>
                    {f.value ?? "-"}{" "}
                    {f.validation_error && <span className="pill-missing">({f.validation_error})</span>}
                  </span>
                </div>
              ))
            ) : (
              <p className="empty">Nothing extracted yet.</p>
            )}

            {result.missing_fields.length > 0 && (
              <p className="pill-missing">Still needed: {result.missing_fields.join(", ")}</p>
            )}

            <h3 style={{ fontSize: "0.85rem" }}>Conversation</h3>
            {result.conversation.messages.map((m) => (
              <div className={`msg ${m.direction}`} key={m.id}>
                <div className="meta">
                  {m.direction} - {m.sender} - {new Date(m.created_at).toLocaleString()}
                </div>
                {m.body}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
