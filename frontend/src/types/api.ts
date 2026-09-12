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
  /** The customer's own words the value was taken from (null for summaries and staff edits). */
  evidence_text?: string | null;
  evidence_start?: number | null;
  evidence_end?: number | null;
  /** exact | normalized | date | overlap - see backend app/domain/evidence.py. */
  evidence_method?: string | null;
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
  language_code: string | null;
  pending_field: string | null;
  created_at: string;
  updated_at: string;
  complaint: ComplaintOut | null;
  messages: MessageOut[];
}

export interface IntakeResult {
  conversation: ConversationOut;
  reply_body: string | null;
  complaint_type: ComplaintType | null;
  method_key: string | null;
  missing_fields: string[];
  invalid_fields: string[];
  awaiting_clarification: boolean;
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

/** Display grouping for a collected field, from the YAML schema registry. */
export type FieldGroup = "customer" | "transaction" | "issue" | "details";

export interface FieldSpec {
  key: string;
  label: string;
  type: string;
  group: FieldGroup;
  required: boolean;
  description: string;
  extraction_hint: string;
  example: string | null;
}

/** A support agent's reply. No recipient: the server reads it from the ticket. */
export interface TicketReplyIn {
  body: string;
  subject?: string;
}

export interface TicketReplyOut {
  to_addr: string;
  subject: string;
  provider: string;
  /** True when the provider only simulated the send (the mock adapter). */
  simulated: boolean;
  delivered: boolean;
  detail: string;
  sent_at: string;
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

// ---------------------------------------------------------------- ML assistance

/** A ticket that looks like the one open. Advisory only: nothing is merged. */
export interface SimilarTicket {
  reference: string;
  type: string;
  status: string;
  created_at: string;
  similarity: number;
  relation: "possible_duplicate" | "similar";
  same_customer: boolean;
  /** Keys (never values) of the extracted fields both tickets share. */
  matched_fields: string[];
  /** e.g. semantic_similarity, same_customer, same_type, same_<field_key>. */
  reasons: string[];
}

export interface SimilarTickets {
  reference: string;
  model_version: string;
  similar_threshold: number;
  duplicate_threshold: number;
  items: SimilarTicket[];
}

export interface Incident {
  id: string;
  is_demo: boolean;
  size: number;
  expected: number;
  ratio: number;
  p_value: number;
  severity: "high" | "medium";
  first_seen: string;
  last_seen: string;
  top_terms: string[];
  types: Record<string, number>;
  languages: Record<string, number>;
  ticket_references: string[];
  open_complaints: number;
  cohesion: number;
  hourly_counts: number[];
}

export interface VolumeSpike {
  is_demo: boolean;
  complaint_type: string;
  count: number;
  expected: number;
  ratio: number;
  p_value: number;
}

export interface IncidentReport {
  generated_at: string;
  window_hours: number;
  baseline_days: number;
  model_version: string;
  cluster_threshold: number;
  complaints_in_window: number;
  incidents: Incident[];
  volume_spikes: VolumeSpike[];
}

/** A staff correction to the complaint type or one collected field. */
export interface CorrectionIn {
  kind: "classification" | "field";
  field_key?: string;
  corrected_value: string;
  note?: string;
}

/** A recorded correction: the original prediction kept next to the fix. */
export interface Feedback {
  id: number;
  kind: "classification" | "field";
  field_key: string | null;
  original_value: string | null;
  corrected_value: string;
  /** rules | ml | provider | customer_message | employee | missing | unknown */
  original_source: string | null;
  original_confidence: number | null;
  model_name: string | null;
  model_version: string | null;
  language_code: string | null;
  note: string | null;
  exported: boolean;
  created_at: string;
}

export interface CorrectionOut {
  feedback: Feedback;
  ticket: TicketDetail;
}

/** Live model behaviour on real traffic (GET /ml/monitoring). Counts only. */
export interface MonitoringSnapshot {
  window_days: number;
  include_demo: boolean;
  since: string;
  models: {
    classifier: {
      name: string | null;
      version: string | null;
      mode: string;
      threshold: number | null;
      labels: string[];
      available: boolean;
    };
    embedder: { name: string; version: string; dim: number; fallback_active: boolean };
    extraction: { evidence_policy: string; require_evidence: boolean };
  };
  predictions: { total: number; by_task: Record<string, number> };
  classification: {
    n: number;
    by_decided_by: Record<string, number>;
    abstention_rate: number | null;
    label_distribution: Record<string, number>;
    confidence: { mean: number | null; p50: number | null };
    latency_ms: { p50: number | null; p95: number | null };
    shadow_agreement: { compared: number; agreed: number; rate: number | null };
  };
  extraction: {
    n: number;
    values_proposed: number;
    values_rejected: number;
    rejection_rate: number | null;
  };
  drift: { status: string; label_psi: number | null; confidence_psi: number | null };
  feedback: { total: number; by_kind: Record<string, number> };
  worker: { last_run_at: string | null; total_embedded: number };
}
