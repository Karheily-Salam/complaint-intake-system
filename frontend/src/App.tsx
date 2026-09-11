import { useState } from "react";
import { Overview, SUPPORT_EMAIL } from "@/pages/Overview";
import { MailboxDemo } from "@/pages/MailboxDemo";
import { SupportInbox } from "@/pages/SupportInbox";

type View = "home" | "demo" | "support";

const GITHUB_URL = "https://github.com/Karheily-Salam/complaint-intake-system";

const TABS: { id: View; label: string }[] = [
  { id: "home", label: "Home" },
  { id: "demo", label: "Try the demo" },
  { id: "support", label: "Support inbox" },
];

export function App() {
  // Home first: a visitor with a complaint needs the email address, not an
  // interface. The demo is one click away for anyone who wants to see it work.
  const [view, setView] = useState<View>("home");
  // Bumped when the demo creates a ticket, so the support inbox reloads and
  // the two halves of the product stay in step.
  const [ticketsVersion, setTicketsVersion] = useState(0);

  return (
    <>
      <header className="app-header">
        <div className="brand">
          <h1>Complaint Intake System</h1>
          <span className="tagline">Report a problem by email — no form, no account</span>
        </div>
        <nav className="tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`nav-btn ${view === tab.id ? "active" : ""}`}
              onClick={() => setView(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <a className="gh-link" href={GITHUB_URL} target="_blank" rel="noreferrer">
          GitHub ↗
        </a>
      </header>
      <main>
        {view === "home" && <Overview onOpenDemo={() => setView("demo")} />}
        {view === "demo" && (
          <MailboxDemo onTicketCreated={() => setTicketsVersion((v) => v + 1)} />
        )}
        {view === "support" && <SupportInbox refreshToken={ticketsVersion} />}
      </main>
      <footer className="app-footer">
        To report a problem, email{" "}
        <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>. Portfolio project ·
        FastAPI · React · Docker ·{" "}
        <a href={GITHUB_URL} target="_blank" rel="noreferrer">
          source
        </a>
      </footer>
    </>
  );
}
