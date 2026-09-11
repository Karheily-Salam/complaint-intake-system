import { useEffect, useState } from "react";
import { Overview, SUPPORT_EMAIL } from "@/pages/Overview";
import { MailboxDemo } from "@/pages/MailboxDemo";
import { SupportInbox } from "@/pages/SupportInbox";
import { SupportDashboard } from "@/pages/SupportDashboard";

type View = "home" | "demo" | "support";

/** The internal dashboard lives off the tab bar, at #/support. */
const STAFF_HASH = "#/support";

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
  // Hash routing rather than a router dependency: one internal route does not
  // justify adding react-router to a three-view app.
  const [isStaffRoute, setIsStaffRoute] = useState(
    () => window.location.hash === STAFF_HASH,
  );

  useEffect(() => {
    const sync = () => setIsStaffRoute(window.location.hash === STAFF_HASH);
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  if (isStaffRoute) {
    return (
      <>
        <header className="app-header">
          <div className="brand">
            <h1>Complaint Intake System</h1>
            <span className="tagline">Internal support dashboard</span>
          </div>
          <nav className="tabs">
            <a className="nav-btn" href="#/">
              ← Back to site
            </a>
          </nav>
        </header>
        <main>
          <SupportDashboard />
        </main>
      </>
    );
  }

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
        {" · "}
        {/* Deliberately understated: an internal tool, not a customer CTA.
            Access is enforced by the server, not by this link being quiet. */}
        <a className="staff-link" href={STAFF_HASH}>
          Staff
        </a>
      </footer>
    </>
  );
}
