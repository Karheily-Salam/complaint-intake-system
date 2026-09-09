// Mirrors the backend Pydantic schemas (app/schemas). Keep in sync manually for
// the prototype; a generator (openapi-typescript) can be added later.

export type ComplaintType = "withdrawal" | "deposit" | "other";

export interface CollectedField {
  key: string;
  value: string | null;
  status: string;
  source: string;
  confidence: number | null;
  validation_error: string | null;
}

export interface ComplaintOut {
  id: number;
  type: ComplaintType | null;
  method_key: string | null;
  concise_description: string | null;
  status: string;
  fields: CollectedField[];
}

export interface MessageOut {
  id: number;
  direction: "inbound" | "outbound";
  sender: string;
  recipient: string | null;
  subject: string | null;
  body: string;
  created_at: string;
}

export interface ConversationOut {
  id: number;
  customer_id: number;
  channel: string;
  subject: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  complaint: ComplaintOut | null;
  messages: MessageOut[];
}

export interface IntakeResult {
  conversation: ConversationOut;
  reply_body: string | null;
  missing_fields: string[];
  is_complete: boolean;
  ticket_reference: string | null;
}

export interface CustomerOut {
  id: number;
  email: string;
  name: string | null;
}

export interface TicketSummary {
  id: number;
  reference: string;
  type: string;
  status: string;
  priority: string;
  title: string;
  concise_description: string;
  created_at: string;
  updated_at: string;
  customer: CustomerOut;
}

export interface TicketDetail extends TicketSummary {
  structured_data: Record<string, unknown>;
  conversation: ConversationOut | null;
}

export interface FieldSpec {
  key: string;
  label: string;
  type: string;
  required: boolean;
  description: string;
  extraction_hint: string;
  example: string | null;
}

export interface ComplaintSchema {
  type: ComplaintType;
  label: string;
  description: string;
  open_schema: boolean;
  common_fields: FieldSpec[];
  method_selector_label: string | null;
  methods: { key: string; label: string; additional_fields: FieldSpec[] }[];
}
