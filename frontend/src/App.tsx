import { useState } from "react";
import { MailboxSimulator } from "@/pages/MailboxSimulator";
import { EmployeeDashboard } from "@/pages/EmployeeDashboard";

type View = "mailbox" | "dashboard";

export function App() {
  const [view, setView] = useState<View>("mailbox");

  return (
    <>
      <header className="app-header">
        <h1>Complaint Intake System</h1>
        <button
          className={`nav-btn ${view === "mailbox" ? "active" : ""}`}
          onClick={() => setView("mailbox")}
        >
          Mailbox Simulator
        </button>
        <button
          className={`nav-btn ${view === "dashboard" ? "active" : ""}`}
          onClick={() => setView("dashboard")}
        >
          Employee Dashboard
        </button>
      </header>
      <main>{view === "mailbox" ? <MailboxSimulator /> : <EmployeeDashboard />}</main>
    </>
  );
}
