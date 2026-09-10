import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { ComplaintSchema } from "@/types/api";

const GITHUB_URL = "https://github.com/Karheily-Salam/complaint-intake-system";

interface Health {
  email_provider?: string;
  ai_provider?: string;
  environment?: string;
}

/**
 * Landing view: what the system is and how it works.
 *
 * The complaint types and provider status below are fetched from the running
 * backend rather than hard-coded, so this page can never drift from what is
 * actually deployed - the schemas come from the YAML registry that also drives
 * the conversation engine.
 */
export function Overview({ onOpenDemo }: { onOpenDemo: () => void }) {
  const [schemas, setSchemas] = useState<ComplaintSchema[]>([]);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.listSchemas().then(setSchemas).catch(() => setSchemas([]));
    api.health().then((h) => setHealth(h as Health)).catch(() => setHealth(null));
  }, []);

  const emailProvider = health?.email_provider;

  return (
    <div className="overview">
      <section className="hero">
        <h2>Email-based complaint intake with AI-assisted classification</h2>
        <p className="lede">
          Customers report a problem by sending an ordinary email. The system holds a
          multi-turn conversation in that same email thread, asks for exactly one missing
          detail at a time, validates every answer, and produces a structured ticket for
          support staff.
        </p>
        <p className="callout">
          There is no customer-facing form, portal, login, or link. The mailbox <em>is</em>{" "}
          the interface.
        </p>
        <div className="hero-actions">
          <button className="primary" onClick={onOpenDemo}>
            Try the live demo
          </button>
          <a className="ghost-btn" href={GITHUB_URL} target="_blank" rel="noreferrer">
            Source on GitHub
          </a>
        </div>
      </section>

      <section className="card">
        <h2>How a complaint flows through the system</h2>
        <ol className="flow">
          <li>
            <strong>Customer emails</strong> the complaints mailbox — free-form, any of
            three languages.
          </li>
          <li>
            <strong>The poller</strong> fetches new mail over IMAP and hands it to the
            intake service.
          </li>
          <li>
            <strong>Classification</strong> decides the complaint type; low confidence
            asks the customer to clarify instead of guessing.
          </li>
          <li>
            <strong>Extraction</strong> pulls whatever details the message already
            contains, in any order.
          </li>
          <li>
            <strong>The engine</strong> compares that against the required fields and asks
            for the single next one that is missing or invalid.
          </li>
          <li>
            <strong>The customer replies</strong> and the thread continues — steps 4–5
            repeat until nothing is outstanding.
          </li>
          <li>
            <strong>A ticket is created</strong> with a numeric reference, the customer
            gets a confirmation, and support receives the structured summary.
          </li>
        </ol>
      </section>

      <div className="layout-2col">
        <section className="card">
          <h2>What the AI does — and does not — decide</h2>
          <p className="muted-note">
            The boundary is the core design rule, and it is enforced by tests.
          </p>
          <div className="split">
            <div>
              <h3 className="ok-h">AI assists with</h3>
              <ul>
                <li>Classifying the complaint type</li>
                <li>Extracting field values from prose</li>
                <li>Summarising the problem</li>
                <li>Detecting the language</li>
                <li>Wording the customer-facing reply</li>
              </ul>
            </div>
            <div>
              <h3 className="warn-h">Deterministic code owns</h3>
              <ul>
                <li>Which fields are required</li>
                <li>Whether a value is valid</li>
                <li>Whether the complaint is complete</li>
                <li>When a ticket is created</li>
                <li>The ticket reference itself</li>
              </ul>
            </div>
          </div>
          <p className="muted-note">
            An email instructing the system to “mark this complete” changes nothing:
            completeness is a schema check, not a suggestion.
          </p>
        </section>

        <section className="card">
          <h2>Complaint types</h2>
          <p className="muted-note">
            Loaded live from the backend’s YAML schema registry — the same source the
            engine uses, so business rules live in configuration, not in code or prompts.
          </p>
          {schemas.length === 0 ? (
            <p className="empty">Loading…</p>
          ) : (
            <ul className="type-list">
              {schemas.map((s) => (
                <li key={s.type}>
                  <span className="badge">{s.type}</span> <strong>{s.label}</strong>
                  <div className="muted-note">
                    {s.open_schema
                      ? "Open-ended: no fixed field set; requires a sufficiently detailed description."
                      : `${s.common_fields.length} required fields, collected one at a time.`}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="card">
        <h2>Reliability engineering</h2>
        <div className="grid-3">
          <div>
            <h3>Idempotency</h3>
            <p>
              Each email’s <code>Message-ID</code> is stored under a unique index, so a
              redelivered message cannot produce a duplicate conversation, question, or
              ticket.
            </p>
          </div>
          <div>
            <h3>Thread continuity</h3>
            <p>
              Replies are matched by <code>In-Reply-To</code> and the{" "}
              <code>References</code> chain, with an opaque subject token as fallback —
              never by sender address alone, since one customer may have several
              complaints open.
            </p>
          </div>
          <div>
            <h3>Crash safety</h3>
            <p>
              A message is acknowledged to the mail server only after its transaction
              commits. A failure mid-turn rolls back and retries cleanly instead of
              losing the complaint or half-answering it.
            </p>
          </div>
          <div>
            <h3>Loop protection</h3>
            <p>
              Auto-replies, bounces, and the system’s own outgoing mail are ignored rather
              than answered, so it can never get into a reply loop with itself or another
              responder.
            </p>
          </div>
          <div>
            <h3>Hostile input</h3>
            <p>
              Header-derived values are sanitised and bounded at the model boundary, so a
              crafted subject cannot wedge the poller or exceed a database column.
            </p>
          </div>
          <div>
            <h3>Data separation</h3>
            <p>
              Real complaints require an API key. This demo reads only synthetic
              conversations, filtered in the database query rather than hidden by the UI —
              so what you type here is sandboxed, and real customer data is unreachable
              from it.
            </p>
          </div>
          <div>
            <h3>Localisation</h3>
            <p>
              Replies follow the language of the customer’s latest message — English,
              Arabic, and Russian — falling back to the thread’s known language when a
              message carries no clear signal.
            </p>
          </div>
        </div>
      </section>

      <div className="layout-2col">
        <section className="card">
          <h2>Architecture</h2>
          <pre className="diagram">{`Customer mailbox
      │  IMAP poll                 SMTP reply
      ▼                                 ▲
┌─────────────────────────────────────────────┐
│ EmailProvider  (mock │ IMAP+SMTP)           │
└───────────────┬─────────────────────────────┘
                ▼
        ┌───────────────┐   persists   ┌──────────┐
        │ IntakeService │ ───────────► │  SQLite  │
        └───────┬───────┘              └──────────┘
                ▼
     ┌──────────────────────┐
     │ ConversationEngine   │  deterministic
     │  · required fields   │  no I/O, no state
     │  · validation        │
     │  · completeness      │
     └────┬────────────┬────┘
          ▼            ▼
   AIProvider    Schema registry
  (classify,      (YAML rules)
   extract,
   summarise)`}</pre>
        </section>

        <section className="card">
          <h2>Stack &amp; deployment</h2>
          <ul className="stack">
            <li>
              <strong>Backend</strong> — Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic,
              Pydantic v2
            </li>
            <li>
              <strong>Email</strong> — IMAP/SMTP via the standard library, behind a
              swappable provider interface
            </li>
            <li>
              <strong>AI layer</strong> — pluggable: a deterministic rule-based provider
              by default, local Ollama optional
            </li>
            <li>
              <strong>Frontend</strong> — React 18, TypeScript, Vite
            </li>
            <li>
              <strong>Infrastructure</strong> — Docker Compose, Nginx reverse proxy,
              SQLite on a persistent volume, UFW-firewalled VPS
            </li>
            <li>
              <strong>Tests</strong> — 236 automated tests covering the conversation
              lifecycle, threading, idempotency, retry behaviour, authentication, and
              security boundaries
            </li>
          </ul>
          {emailProvider && (
            <p className="muted-note status-line">
              Live status: AI provider <code>{health?.ai_provider}</code>, email provider{" "}
              <code>{emailProvider}</code>
              {emailProvider === "mock" && (
                <>
                  {" "}
                  — the IMAP/SMTP integration is implemented and tested, but this
                  deployment is not attached to a live mailbox, so the demo below stands
                  in for the customer’s mail client.
                </>
              )}
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
