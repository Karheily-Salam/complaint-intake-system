import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { useI18n } from "@/i18n";
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
 * is actually deployed. Their *labels* come from the translation, so the page
 * reads in the visitor's language while the facts stay the deployment's own.
 */
export function Overview({ onOpenDemo }: { onOpenDemo: () => void }) {
  const { t } = useI18n();
  const [schemas, setSchemas] = useState<ComplaintSchema[]>([]);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.listSchemas().then(setSchemas).catch(() => setSchemas([]));
    api.health().then((h) => setHealth(h as Health)).catch(() => setHealth(null));
  }, []);

  const emailProvider = health?.email_provider;
  const about = t.home.about;

  return (
    <div className="overview">
      {/* ---------------- customer-facing ---------------- */}

      <section className="intake-hero">
        <h2>{t.home.heroTitle}</h2>
        <p className="intake-lede">{t.home.heroLede}</p>

        <a className="intake-address" href={`mailto:${SUPPORT_EMAIL}`}>
          {SUPPORT_EMAIL}
        </a>

        <div className="intake-actions">
          <a
            className="primary intake-cta"
            href={`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(t.home.heroSubject)}`}
          >
            {t.home.heroCta}
          </a>
        </div>

        <p className="intake-noform">{t.home.noForm}</p>
      </section>

      <p className="intake-explainer">{t.home.explainer}</p>

      <section className="steps">
        {t.home.steps.map((step, index) => (
          <div className="step" key={step.title}>
            <span className="step-number">{index + 1}</span>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
          </div>
        ))}
      </section>

      <section className="demo-invite">
        <div>
          <h3>{t.home.demoInviteTitle}</h3>
          <p className="muted-note">{t.home.demoInviteBody}</p>
        </div>
        <button className="primary" onClick={onOpenDemo}>
          {t.home.demoInviteCta}
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
          {about.summary}
          <span className="about-hint">{about.hint}</span>
        </summary>

        <div className="about-body">
          <header className="about-header">
            <p className="muted-note">{about.intro}</p>
            <a className="ghost-btn" href={GITHUB_URL} target="_blank" rel="noreferrer">
              {about.sourceLink}
            </a>
          </header>

          <section className="card">
            <h2>{about.howItWorks}</h2>
            <ol className="flow">
              {about.flow.map((item) => (
                <li key={item.lead}>
                  <strong>{item.lead}</strong>
                  {item.rest}
                </li>
              ))}
            </ol>
          </section>

          <div className="layout-2col">
            <section className="card">
              <h2>{about.boundaryTitle}</h2>
              <p className="muted-note">{about.boundaryNote}</p>
              <div className="split">
                <div>
                  <h3 className="ok-h">{about.aiAssists}</h3>
                  <ul>
                    {about.aiList.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h3 className="warn-h">{about.codeOwns}</h3>
                  <ul>
                    {about.codeList.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              </div>
              <p className="muted-note">{about.boundaryFooter}</p>
            </section>

            <section className="card">
              <h2>{about.typesTitle}</h2>
              <p className="muted-note">{about.typesNote}</p>
              {schemas.length === 0 ? (
                <p className="empty">{t.common.loading}</p>
              ) : (
                <ul className="type-list">
                  {schemas.map((s) => (
                    <li key={s.type}>
                      {/* The type key is an API value and stays as it is; the
                          name beside it is translated. */}
                      <span className="badge">{s.type}</span>{" "}
                      <strong>{t.complaintTypes[s.type] ?? s.label}</strong>
                      <div className="muted-note">
                        {s.open_schema
                          ? about.openSchema
                          : about.requiredFields(s.common_fields.length)}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <section className="card">
            <h2>{about.reliabilityTitle}</h2>
            <div className="grid-3">
              {about.reliability.map((item) => (
                <div key={item.title}>
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                </div>
              ))}
            </div>
          </section>

          <div className="layout-2col">
            <section className="card">
              <h2>{about.architectureTitle}</h2>
              {/* Deliberately not translated: an ASCII diagram of component
                  names, which are code identifiers. Forced LTR so it keeps its
                  shape when the page is right-to-left. */}
              <pre className="diagram" dir="ltr">{`Customer mailbox
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
              <h2>{about.stackTitle}</h2>
              <ul className="stack">
                {about.stack.map((item) => (
                  <li key={item.lead}>
                    <strong>{item.lead}</strong>
                    {item.rest}
                  </li>
                ))}
              </ul>
              {emailProvider && (
                <p className="muted-note status-line">
                  <strong>{about.deploymentStatus}</strong>{" "}
                  {about.providerLine(health?.ai_provider ?? "", emailProvider)}
                  {emailProvider === "mock" && about.mockNote(SUPPORT_EMAIL)}
                </p>
              )}
            </section>
          </div>
        </div>
      </details>
    </div>
  );
}
