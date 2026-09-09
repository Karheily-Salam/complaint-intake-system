import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { TicketDetail, TicketSummary } from "@/types/api";

const STATUSES = ["new", "in_progress", "resolved", "closed"];

export function EmployeeDashboard() {
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [selected, setSelected] = useState<TicketDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setTickets(await api.listTickets());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function open(reference: string) {
    setSelected(await api.getTicket(reference));
  }

  async function setStatus(reference: string, status: string) {
    const updated = await api.updateTicketStatus(reference, status);
    setSelected(updated);
    void refresh();
  }

  return (
    <div className="layout-2col">
      <div className="card">
        <h2>Tickets ({tickets.length})</h2>
        {error && <p className="error">{error}</p>}
        <table>
          <thead>
            <tr>
              <th>Ref</th>
              <th>Type</th>
              <th>Status</th>
              <th>Customer</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((t) => (
              <tr key={t.reference} onClick={() => open(t.reference)}>
                <td>{t.reference}</td>
                <td>{t.type}</td>
                <td>
                  <span className="badge">{t.status}</span>
                </td>
                <td>{t.customer.email}</td>
                <td>{new Date(t.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {tickets.length === 0 && <p className="empty">No tickets yet. Use the Mailbox Simulator.</p>}
      </div>

      <div className="card">
        <h2>Ticket detail</h2>
        {!selected && <p className="empty">Select a ticket to view its structured complaint.</p>}
        {selected && (
          <>
            <h3 style={{ fontSize: "0.9rem" }}>
              {selected.reference} - {selected.title}
            </h3>
            <p>
              {STATUSES.map((s) => (
                <button
                  key={s}
                  className={`nav-btn ${selected.status === s ? "active" : ""}`}
                  style={{ marginRight: 4 }}
                  onClick={() => setStatus(selected.reference, s)}
                >
                  {s}
                </button>
              ))}
            </p>

            <h4 style={{ fontSize: "0.8rem" }}>Concise problem description</h4>
            <p>{selected.concise_description}</p>

            <h4 style={{ fontSize: "0.8rem" }}>Structured complaint information</h4>
            {Object.entries(
              (selected.structured_data.fields as Record<string, string>) ?? {},
            ).map(([k, v]) => (
              <div className="field-row" key={k}>
                <span className="k">{k}</span>
                <span>{v}</span>
              </div>
            ))}

            <h4 style={{ fontSize: "0.8rem" }}>Original conversation</h4>
            {selected.conversation?.messages.map((m) => (
              <div className={`msg ${m.direction}`} key={m.id}>
                <div className="meta">
                  {m.direction} - {m.sender}
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
