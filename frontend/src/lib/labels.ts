import type { ComplaintSchema, FieldGroup } from "@/types/api";

/**
 * Human-readable names for complaint fields.
 *
 * The labels come from the backend's YAML schema registry (via /schemas) - the
 * same source the conversation engine uses - so the UI cannot drift from the
 * business configuration, and adding a complaint type needs no frontend change.
 * The fallbacks below only cover keys that are not part of any schema.
 */
const FALLBACK_LABELS: Record<string, string> = {
  user_id: "User ID",
  account_email: "Account email",
  withdrawal_transaction_id: "Withdrawal transaction ID",
  source_wallet_or_account: "Source wallet/account",
  transaction_date: "Transaction date",
  deposit_method: "Deposit method",
  problem_description: "Problem description",
};

export type LabelLookup = (key: string) => string;

/** Turn `some_field_name` into `Some field name` as a last resort. */
function humanise(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function buildLabelLookup(schemas: ComplaintSchema[]): LabelLookup {
  const fromSchemas: Record<string, string> = {};
  for (const schema of schemas) {
    for (const field of schema.common_fields) {
      fromSchemas[field.key] = field.label;
    }
  }
  return (key) => fromSchemas[key] ?? FALLBACK_LABELS[key] ?? humanise(key);
}

/** Complaint type -> its display label, again from the schema registry. */
export function buildTypeLookup(schemas: ComplaintSchema[]): LabelLookup {
  const byType: Record<string, string> = {};
  for (const schema of schemas) byType[schema.type] = schema.label;
  return (type) => byType[type] ?? humanise(type);
}

const STATUS_LABELS: Record<string, string> = {
  open: "Open",
  collecting_info: "Collecting information",
  validating: "Validating",
  completed: "Completed",
  abandoned: "Abandoned",
  new: "New",
  in_progress: "In progress",
  resolved: "Resolved",
  closed: "Closed",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? humanise(status);
}

export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

// --------------------------------------------------------------- field groups
//
// Which group a field belongs to is business configuration and arrives from the
// backend schema registry. Only the heading text and the order an agent reads
// the groups in live here, because those are presentation.

const GROUP_HEADINGS: Record<FieldGroup, string> = {
  customer: "Customer information",
  transaction: "Transaction information",
  issue: "Issue",
  details: "Other details",
};

export interface GroupedField {
  key: string;
  label: string;
  value: string;
}

export interface FieldGroupView {
  group: FieldGroup;
  heading: string;
  fields: GroupedField[];
}

/**
 * Arrange a ticket's collected values into display groups.
 *
 * Fields keep their schema order within a group, and the caller chooses which
 * groups to render where. A value whose key is not in the schema still appears
 * (under "Other details") rather than being dropped: hiding data an agent may
 * need is worse than an imperfect heading.
 */
export function groupFields(
  schemas: ComplaintSchema[],
  complaintType: string,
  values: Record<string, string>,
  order: FieldGroup[],
): FieldGroupView[] {
  const schema = schemas.find((s) => s.type === complaintType);
  const specs = schema?.common_fields ?? [];
  const labelFor = buildLabelLookup(schemas);

  const buckets = new Map<FieldGroup, GroupedField[]>();
  const push = (group: FieldGroup, field: GroupedField) => {
    const bucket = buckets.get(group);
    if (bucket) bucket.push(field);
    else buckets.set(group, [field]);
  };

  const placed = new Set<string>();
  for (const spec of specs) {
    const value = values[spec.key];
    if (!value) continue;
    push(spec.group ?? "details", { key: spec.key, label: spec.label, value });
    placed.add(spec.key);
  }
  for (const [key, value] of Object.entries(values)) {
    if (placed.has(key) || !value) continue;
    push("details", { key, label: labelFor(key), value });
  }

  return order
    .filter((group) => buckets.get(group)?.length)
    .map((group) => ({ group, heading: GROUP_HEADINGS[group], fields: buckets.get(group) ?? [] }));
}

/** The subject the reply composer shows. The server applies the same rule. */
export function replySubject(conversationSubject: string | null | undefined): string {
  const base = (conversationSubject ?? "").trim() || "Your complaint";
  return /^re:/i.test(base) ? base : `Re: ${base}`;
}
