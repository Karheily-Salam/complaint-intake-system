import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { ComplaintSchema } from "@/types/api";

const GITHUB_URL = "https://github.com/Karheily-Salam/complaint-intake-system";

/** The real customer interface. Not a form - the mailbox itself. */
export const SUPPORT_EMAIL = "complaints@startplus.tech";

interface Health {
  email_provider?: string;
  ai_provider?: string;
  environment?: string;
}

/**
 * The public homepage.
 *
 * Ordered for the person it is actually for: a customer with a problem, who
 * needs one thing from this page - the address to write to. Everything about
 * how the system works sits below that, for anyone who wants it.
 *
 * The complaint types and provider status further down are fetched from the
 * running backend rather than hard-coded, so the page cannot drift from what
 * is actually deployed.
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
      {/* ---------------- customer-facing ---------------- */}

      <section className="intake-hero">
        <h2>Have a problem?</h2>
        <p className="intake-lede">Send us an email describing your problem.</p>

        <a className="intake-address" href={`mailto:${SUPPORT_EMAIL}`}>
          {SUPPORT_EMAIL}
        </a>

        <div className="intake-actions">
          <a
            className="primary intake-cta"
            href={`mailto:${SUPPORT_EMAIL}?subject=Complaint`}
          >
            Send a complaint
          </a>
        </div>

        <p className="intake-noform">No form. No account. Just email us.</p>
      </section>

      <p className="intake-explainer">
        Describe your problem in your own words. We'll reply by email and ask for any
        information we still need, one question at a time.
      </p>

      <section className="steps">
        <div className="step">
          <span className="step-number">1</span>
          <h3>Send your complaint</h3>
          <p>Email us and describe what happened.</p>
        </div>
        <div className="step">
          <span className="step-number">2</span>
          <h3>Answer a few questions</h3>
          <p>We'll ask only for the information needed to process your complaint.</p>
        </div>
        <div className="step">
          <span className="step-number">3</span>
          <h3>Get a ticket</h3>
          <p>
            Once everything is complete, your case is turned into a structured ticket for
            our support team.
          </p>
        </div>
      </section>

      <section className="demo-invite">
        <div>
          <h3>Want to see it work first?</h3>
          <p className="muted-note">
            Watch an example conversation play out in your browser. It's a preview — to
            send a real complaint, email the address above.
          </p>
        </div>
        <button className="primary" onClick={onOpenDemo}>
          Try the demo
        </button>
      </section>

      {/* ---------------- project / technical ----------------
          Collapsed by default and kept out of the customer's way. A native
          <details> gives keyboard and screen-reader behaviour for free, and
          works with JavaScript disabled - no library, no state to manage. */}

      <details className="about">
        <summary>
          <span className="about-chevron" aria-hidden="true">
            ▸
          </span>
          About this project
          <span className="about-hint">engineering notes for developers &amp; recruiters</span>
        </summary>

        <div className="about-body">
          <header className="about-header">
            <p className="muted-note">
              An email-only complaint intake system: unstructured customer emails become
              structured support tickets through a deterministic workflow engine with
              AI-assisted extraction. Built as a portfolio project — the engineering notes
              below are for developers and recruiters.
            </p>
            <a className="ghost-btn" href={GITHUB_URL} target="_blank" rel="noreferrer">
              Source on GitHub ↗
            </a>
          </header>

          <section className="card">
            <h2>How it works</h2>
            <ol className="flow">
              <li>
                <strong>Customer emails</strong> the complaints mailbox — free-form, in any of
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
              <h2>AI vs deterministic logic</h2>
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
            <h2>Reliability &amp; security</h2>
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
                  Real complaints require an API key. The demo reads only synthetic
                  conversations, filtered in the database query rather than hidden by the UI —
                  so what you type there is sandboxed, and real customer data is unreachable
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
              <h2>Technology stack</h2>
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
                  <strong>Deployment status:</strong> AI provider <code>{health?.ai_provider}</code>,
                  email provider <code>{emailProvider}</code>.
                  {emailProvider === "mock" && (
                    <>
                      {" "}
                      The IMAP/SMTP integration is implemented and tested, and the mailbox{" "}
                      <code>{SUPPORT_EMAIL}</code> exists with inbound mail reachable — but
                      this deployment is not yet attached to it, because outbound SMTP is
                      currently blocked on the host. Until that is lifted, the demo stands in
                      for the customer’s mail client and no real mail is processed.
                    </>
                  )}
                </p>
              )}
              </section>
            </div>
        </div>
      </details>
    </div>
  );
}
