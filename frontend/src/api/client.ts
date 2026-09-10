import type {
  ComplaintSchema,
  ConversationOut,
  IntakeResult,
  TicketDetail,
  TicketSummary,
} from "@/types/api";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${detail}`);
  }
  return (await resp.json()) as T;
}

export interface InboundEmailIn {
  from_addr: string;
  body: string;
  subject?: string;
  customer_name?: string;
  conversation_id?: number;
}

export const api = {
  health: () => request<Record<string, unknown>>("/health"),
  listSchemas: () => request<ComplaintSchema[]>("/schemas"),

  sendCustomerEmail: (payload: InboundEmailIn) =>
    request<IntakeResult>("/inbox", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // These are the public demo endpoints. The server restricts them to
  // conversations flagged as demo, so this app cannot reach real customer
  // data. The staff endpoints (/tickets, /conversations) require an API key,
  // which this browser app deliberately does not hold: a key shipped inside a
  // JavaScript bundle is not a secret, and pretending otherwise would be a
  // fake boundary.
  getConversation: (id: number) => request<ConversationOut>(`/demo/conversations/${id}`),

  listTickets: () => request<TicketSummary[]>("/demo/tickets"),
  getTicket: (reference: string) => request<TicketDetail>(`/demo/tickets/${reference}`),
};
