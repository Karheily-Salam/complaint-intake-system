import { useState } from "react";
import { Overview } from "@/pages/Overview";
import { MailboxDemo } from "@/pages/MailboxDemo";
import { SupportInbox } from "@/pages/SupportInbox";

type View = "overview" | "mailbox" | "support";

const GITHUB_URL = "https://github.com/Karheily-Salam/complaint-intake-system";

const TABS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "mailbox", label: "Customer mailbox" },
  { id: "support", label: "Support inbox" },
];

export function App() {
  // Overview first: a visitor should learn what this is before being handed an
  // interface. The demo is one click away.
  const [view, setView] = useState<View>("overview");
  // Bumped when the demo creates a ticket, so the support inbox reloads and
  // the two halves of the product stay in step.
  const [ticketsVersion, setTicketsVersion] = useState(0);

  return (
    <>
      <header className="app-header">
        <div className="brand">
          <h1>Complaint Intake System</h1>
          <span className="tagline">Email-based intake · AI-assisted · deterministic engine</span>
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
        {view === "overview" && <Overview onOpenDemo={() => setView("mailbox")} />}
        {view === "mailbox" && (
          <MailboxDemo onTicketCreated={() => setTicketsVersion((v) => v + 1)} />
        )}
        {view === "support" && <SupportInbox refreshToken={ticketsVersion} />}
      </main>
      <footer className="app-footer">
        Portfolio project · FastAPI · React · Docker ·{" "}
        <a href={GITHUB_URL} target="_blank" rel="noreferrer">
          source
        </a>
      </footer>
    </>
  );
}
