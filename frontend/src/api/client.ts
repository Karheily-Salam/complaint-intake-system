import { StaffRequestError, classifyStatus } from "@/lib/staffAuth";
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

// ---------------------------------------------------------------- staff API
//
// Separate from `api` above on purpose: every call here carries the staff key
// and returns real customer data. The key is supplied by the caller (typed by
// the agent, held in sessionStorage) and is never baked into this bundle.

export interface TicketFilters {
  q?: string;
  status?: string;
  type?: string;
  page?: number;
  pageSize?: number;
}

export interface TicketPage {
  items: TicketSummary[];
  total: number;
}

async function staffRequest(key: string, path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": key,
      ...(init?.headers ?? {}),
    },
  });
}

async function staffJson<T>(key: string, path: string, init?: RequestInit): Promise<T> {
  const resp = await staffRequest(key, path, init);
  if (!resp.ok) {
    throw new StaffRequestError(classifyStatus(resp.status), await describe(resp));
  }
  return (await resp.json()) as T;
}

async function describe(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    return typeof body?.detail === "string" ? body.detail : `${resp.status} ${resp.statusText}`;
  } catch {
    return `${resp.status} ${resp.statusText}`;
  }
}

export const staffApi = {
  async listTickets(key: string, filters: TicketFilters = {}): Promise<TicketPage> {
    const params = new URLSearchParams();
    if (filters.q?.trim()) params.set("q", filters.q.trim());
    if (filters.status) params.set("status", filters.status);
    if (filters.type) params.set("type", filters.type);
    params.set("page", String(filters.page ?? 1));
    params.set("page_size", String(filters.pageSize ?? 20));

    const resp = await staffRequest(key, `/tickets?${params.toString()}`);
    if (!resp.ok) {
      throw new StaffRequestError(classifyStatus(resp.status), await describe(resp));
    }
    return {
      items: (await resp.json()) as TicketSummary[],
      // Total match count travels in a header so the list response body keeps
      // its original shape.
      total: Number(resp.headers.get("X-Total-Count") ?? 0),
    };
  },

  getTicket: (key: string, reference: string) =>
    staffJson<TicketDetail>(key, `/tickets/${encodeURIComponent(reference)}`),

  updateStatus: (key: string, reference: string, status: string) =>
    staffJson<TicketDetail>(key, `/tickets/${encodeURIComponent(reference)}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
};
